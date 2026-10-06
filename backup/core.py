from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid

from .config import BackupError, mount_info, relative_path, resolve_device, validate_config
from .report import atomic_write, readable, write_reports


def run_id():
    return datetime.now().astimezone().strftime('%Y-%m-%d_%H-%M-%S') + '_' + uuid.uuid4().hex[:8]


def fingerprint(st):
    return [st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode),
            st.st_dev, st.st_ino, st.st_ctime_ns]


def git_info(source):
    metadata = source/'.git'
    if not metadata.exists():return None
    def git(*args):
        r = subprocess.run(['git', '-C', str(source), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    head = git('rev-parse', '--verify', 'HEAD')
    branch = git('symbolic-ref', '--short', '-q', 'HEAD')
    common=git('rev-parse','--path-format=absolute','--git-common-dir')
    if not common:raise BackupError(f'Git metadata is unreadable: {source}')
    return {'head': head, 'branch': branch or ('detached' if head else 'unborn'),
            'common_dir':str(Path(common).resolve()),'linked_worktree':metadata.is_file()}


def git_repositories(source, entries, config):
    repos=[]
    for e in entries:
        if e['kind']=='link' and '.git' in Path(e['path']).parts:
            raise BackupError(f'Symlinked Git metadata needs a self-contained repository: {e["path"]}')
        if e['kind']=='file' and e['path'].endswith('objects/info/alternates'):
            if (source/e['path']).read_text().strip():
                raise BackupError(f'External Git object store needs a self-contained clone: {e["path"]}')
        if Path(e['path']).name=='.git':
            folder=(source/e['path']).parent
            info=git_info(folder)
            if info is None:raise BackupError(f'Git metadata is absent: {folder}')
            common=Path(info['common_dir'])
            covered=any(common==Path(p['source'])/'.git' or Path(p['source']) in common.parents
                        for p in config['projects'])
            if not covered:raise BackupError(f'Git worktree common repository is not registered: {common}')
            owner=next(p for p in config['projects'] if common==Path(p['source'])/'.git' or Path(p['source']) in common.parents)
            if excluded(common.relative_to(Path(owner['source'])).as_posix(),owner):
                raise BackupError(f'Git worktree common metadata is excluded: {common}')
            repos.append(dict(info,path=str(folder.relative_to(source))))
    return repos


def excluded(rel, project):
    return any(rel == e['path'] or rel.startswith(e['path'] + '/') for e in project.get('excludes', []))


def inventory(project):
    source = Path(project['source'])
    if not source.is_dir():raise BackupError(f'Missing/moved project: {source}')
    if source.is_symlink():raise BackupError(f'Source is a symlink; register its actual location: {source}')
    if project.get('source_uuid'):
        if mount_info(source).get('uuid') != project['source_uuid']:
            raise BackupError(f'Source filesystem absent or changed: {source}')
    for marker in project.get('required_markers', []):
        if not (source/marker).exists():raise BackupError(f'Missing project marker {marker}: {source}')
    for e in project.get('excludes', []):
        if (source/'.git').is_dir():
            r = subprocess.run(['git', '-C', str(source), 'ls-files', '--', e['path']],
                               capture_output=True, text=True)
            if r.returncode or r.stdout:
                raise BackupError(f'Excluded path is tracked or Git check failed: {e["path"]}')
    entries = []
    def visit(folder):
        with os.scandir(folder) as items:
            for item in sorted(items, key=lambda i:i.name):
                path = Path(item.path)
                rel = path.relative_to(source).as_posix()
                if excluded(rel, project):continue
                st = item.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    entries.append({'path': rel, 'kind': 'link', 'target': os.readlink(path), 'snapshot': fingerprint(st)})
                elif stat.S_ISDIR(st.st_mode):
                    entries.append({'path': rel, 'kind': 'directory', 'mode': stat.S_IMODE(st.st_mode)})
                    visit(path)
                elif stat.S_ISREG(st.st_mode):
                    entries.append({'path': rel, 'kind': 'file', 'size': st.st_size, 'snapshot': fingerprint(st)})
                else:raise BackupError(f'Unsupported special file (close its owning app): {path}')
    visit(source)
    if not any(e['kind']=='file' for e in entries):raise BackupError(f'Empty project: {source}')
    return entries


def guard_path(root, path):
    """Refuse any existing symlink component below the pinned mountpoint."""
    if path != root and root not in path.parents:
        raise BackupError(f'Destination escapes USB: {path}')
    for part in [path, *path.parents]:
        if part == root:break
        if part.is_symlink():raise BackupError(f'Destination contains a symlink: {part}')


def rsync_args(project, destination, dry=False, history=None):
    args = ['rsync', '-aH', '--no-owner', '--no-group', '--stats']
    # Dry-run estimates use size/time only, avoiding multi-GB reads in the review.
    # Copy uses content checksums to repair same-size/time corrupted destinations.
    if dry:args.append('--dry-run')
    else:args.append('--checksum')
    if history:args.extend(['--backup', '--backup-dir='+str(history)])
    for e in project.get('excludes', []):args.append('--exclude=/'+e['path'])
    args.extend(['--', str(Path(project['source']))+'/', str(destination)+'/'])
    return args


def rsync_stats(output):
    import re
    match = re.search(r'Total transferred file size: ([\d,]+) bytes', output)
    if not match:raise BackupError('rsync transfer statistics missing; cannot estimate safely.')
    return int(match[1].replace(',', ''))


def assert_device(config, expected, mount_fd=None):
    current = resolve_device(config)
    if current != expected:raise BackupError('USB identity or mountpoint changed during backup.')
    if mount_fd is not None:
        pinned, current_stat = os.fstat(mount_fd), expected.mountpoint.stat()
        if (pinned.st_dev,pinned.st_ino)!=(current_stat.st_dev,current_stat.st_ino):
            raise BackupError('USB mountpoint was replaced during backup.')


def preview(config, device=None, progress=lambda message:None):
    validate_config(config)
    report = {'version': 1, 'run_id': run_id(), 'started_at': datetime.now(timezone.utc).isoformat(),
              'status': 'preview', 'safe_to_eject': False, 'device': None, 'projects': [],
              'failures': [], 'total_bytes': 0, 'estimated_transfer_bytes': 0 if device else None,
              'free_bytes': shutil.disk_usage(device.mountpoint).free if device else None,
              'registry': config, 'verification_scope': 'All included regular files: SHA-256; symlinks and modes; unchanged source inventory.'}
    if device:report['device'] = {'uuid': device.uuid, 'label': device.label, 'mountpoint': str(device.mountpoint), 'fstype': device.fstype}
    for project in config['projects']:
        progress(f"Reviewing {project['name']}")
        row = dict(project, failures=[], warnings=[], estimated_transfer_bytes=None)
        report['projects'].append(row)
        try:
            source = Path(project['source'])
            if device and (source == device.mountpoint or source in device.mountpoint.parents or device.mountpoint in source.parents):
                raise BackupError('Source and USB destination overlap.')
            entries = inventory(project)
            row['files'] = entries
            row['file_count'] = sum(e['kind']=='file' for e in entries)
            row['link_count'] = sum(e['kind']=='link' for e in entries)
            row['byte_count'] = sum(e.get('size', 0) for e in entries)
            row['git'] = git_info(source)
            row['git_repositories']=git_repositories(source,entries,config)
            if any(e['kind']=='link' and e['target'].startswith('/') for e in entries):
                row['warnings'].append('Absolute symlinks are preserved as links; restore their registered target projects too.')
            if any(e['kind']=='file' and e['path'].endswith('/.git') for e in entries):
                row['warnings'].append('Nested Git worktree pointers are preserved; repair worktree paths after restoring elsewhere.')
            if row['git'] and row['git']['linked_worktree']:
                row['warnings'].append('Linked Git worktree: its registered main repository holds the shared Git history. Repair paths after restoring elsewhere.')
            report['total_bytes'] += row['byte_count']
            if device:
                assert_device(config, device)
                destination = device.mountpoint/'CyclopsBackup'/project['destination']
                guard_path(device.mountpoint, destination)
                # rsync needs the destination parent even for dry-run. Point a
                # missing destination at a disposable empty LOCAL folder instead.
                import tempfile
                with tempfile.TemporaryDirectory(prefix='cyclops-preview-') as empty:
                    target = destination if destination.exists() else Path(empty)
                    result = subprocess.run(rsync_args(project, target, dry=True), capture_output=True,
                                            text=True, env=dict(os.environ, LC_ALL='C'))
                if result.returncode:raise BackupError(f'rsync preview failed ({result.returncode}): {result.stderr.strip()}')
                row['estimated_transfer_bytes'] = rsync_stats(result.stdout)
                report['estimated_transfer_bytes'] += row['estimated_transfer_bytes']
        except (BackupError, OSError, ValueError) as exc:
            row['failures'].append(str(exc));report['failures'].append(f'{project["name"]}: {exc}')
    if device and not report['failures']:
        # Retaining replaced versions means updates consume their full new size.
        # Reserve metadata, reports, and rsync temporary-file headroom.
        estimate = report['estimated_transfer_bytes']
        report['required_free_bytes'] = estimate + max(64*1024*1024, estimate//20)
        if report['free_bytes'] < report['required_free_bytes']:
            report['failures'].append('Not enough USB space for the estimated transfer plus 5%/64 MiB reserve. Choose a larger drive or explicitly revise the registry.')
    report['ok'] = not report['failures']
    return report


def sha256(path, expected=None):
    digest = hashlib.sha256()
    # O_NOFOLLOW prevents a replaced file from silently becoming an external link.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise BackupError(f'Not a regular file: {path}')
        before=fingerprint(os.fstat(stream.fileno()))
        if expected is not None and before!=expected:raise BackupError(f'Source identity changed before hashing: {path}')
        for chunk in iter(lambda: stream.read(1024*1024), b''):digest.update(chunk)
        if fingerprint(os.fstat(stream.fileno()))!=before:raise BackupError(f'File changed while hashing: {path}')
    return digest.hexdigest()


def verify_project(project, destination, progress):
    source = Path(project['source'])
    hashed = 0;hashed_bytes = 0
    for index, entry in enumerate(project['files']):
        rel = entry['path'];src = source/rel;dest = destination/rel
        guard_path(destination, dest.parent)
        st = dest.lstat()
        if entry['kind']=='file':
            if not stat.S_ISREG(st.st_mode):raise BackupError(f'Destination is not a regular file: {rel}')
            if fingerprint(src.lstat()) != entry['snapshot']:raise BackupError(f'Source changed during backup: {rel}')
            a, b = sha256(src,entry['snapshot']), sha256(dest)
            if fingerprint(src.lstat()) != entry['snapshot']:raise BackupError(f'Source changed during hashing: {rel}')
            if a != b:raise BackupError(f'SHA-256 mismatch: {rel}')
            if stat.S_IMODE(st.st_mode) != entry['snapshot'][2]:raise BackupError(f'File mode mismatch: {rel}')
            entry['sha256']=a;hashed+=1;hashed_bytes+=entry['size']
        elif entry['kind']=='link':
            if not stat.S_ISLNK(st.st_mode) or os.readlink(dest)!=entry['target']:
                raise BackupError(f'Symlink mismatch: {rel}')
        else:
            if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode)!=entry['mode']:
                raise BackupError(f'Directory/mode mismatch: {rel}')
        if index % 100 == 0:progress(f"Verifying {project['name']}: {hashed}/{project['file_count']} files")
    project['verification']={'ok': True, 'algorithm': 'SHA-256', 'hashed_files': hashed, 'hashed_bytes': hashed_bytes}


def snapshot(entries):
    return [{k:v for k,v in e.items() if k!='sha256'} for e in entries]


def write_usb_reports(report, root):
    for name in ('Manifests', 'Logs'):
        guard_path(root.parent, root/name)
        (root/name).mkdir(exist_ok=True)
    atomic_write(root/'Manifests'/(report['run_id']+'.json'), json.dumps(report, indent=2)+'\n')
    atomic_write(root/'Logs'/(report['run_id']+'.txt'), readable(report))
    atomic_write(root/'Manifests'/(report['run_id']+'.registry.json'), json.dumps(report['registry'], indent=2)+'\n')


def sync_device(root, pass_fds=()):
    result = subprocess.run(['sync', '-f', str(root)], capture_output=True, text=True, pass_fds=pass_fds)
    if result.returncode:raise BackupError(f'USB sync failed: {result.stderr.strip()}')


def run_backup(config, device, report_dir, progress=lambda message:None):
    report = preview(config, device, progress)
    root = device.mountpoint/'CyclopsBackup'
    lock = None;mount_fd = None
    try:
        if not report['ok']:raise BackupError('Preflight failed. Nothing was copied.')
        report['ok']=False
        assert_device(config, device)
        guard_path(device.mountpoint, root)
        if os.statvfs(device.mountpoint).f_flag & os.ST_RDONLY:
            raise BackupError('USB filesystem is read-only. Nothing was copied.')
        if not os.access(device.mountpoint, os.W_OK):raise BackupError('USB mountpoint is not writable.')
        # Hold the mounted filesystem itself, so an unplug/unmount cannot redirect
        # an in-flight pathname operation into the computer's empty mount folder.
        mount_fd = os.open(device.mountpoint,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        assert_device(config,device,mount_fd)
        pinned_mount=Path(f'/proc/self/fd/{mount_fd}')
        root=pinned_mount/'CyclopsBackup'
        guard_path(pinned_mount,root)
        root.mkdir(exist_ok=True)
        guard_path(pinned_mount, root/'.backup.lock')
        lock = open(root/'.backup.lock', 'a')
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise BackupError('Another backup is already running on this USB.')
        for name in ('Projects', 'Archives', 'Manifests', 'Logs'):
            guard_path(pinned_mount, root/name);(root/name).mkdir(exist_ok=True)
        report['status']='copying'
        for row in report['projects']:
            try:
                assert_device(config, device,mount_fd)
                if inventory(row)!=snapshot(row['files']):raise BackupError('Source changed since preview. Close apps and retry.')
                destination = root/row['destination']
                guard_path(pinned_mount, destination)
                destination.mkdir(exist_ok=True)
                history = root/'Archives/PreviousVersions'/report['run_id']/row['name']
                guard_path(pinned_mount, history)
                progress(f"Copying {row['name']}")
                # Do not follow destination links at any level during rsync.
                # A destination link matching a source link is legitimate;
                # everything else is refused before copying.
                for entry in row['files']:
                    target = destination/entry['path']
                    guard_path(pinned_mount, target.parent)
                    if target.is_symlink() and entry['kind']!='link':
                        raise BackupError(f'Destination file is a symlink: {entry["path"]}')
                result = subprocess.run(rsync_args(row, destination, history=history), capture_output=True,
                                        text=True, env=dict(os.environ, LC_ALL='C'),pass_fds=(mount_fd,))
                row['rsync_exit']=result.returncode
                log = root/'Logs'/(report['run_id']+'.'+row['name']+'.rsync.txt')
                atomic_write(log, result.stdout+'\n'+result.stderr)
                if result.returncode:raise BackupError(f'rsync failed ({result.returncode}): {result.stderr.strip()}')
                row['transfer_bytes']=rsync_stats(result.stdout)
                assert_device(config, device,mount_fd)
                progress(f"Verifying {row['name']} with SHA-256")
                verify_project(row, destination, progress)
                if inventory(row)!=snapshot(row['files']):raise BackupError('Source files changed during copy/verification. Retry with apps closed.')
                if git_repositories(Path(row['source']),row['files'],config)!=row['git_repositories']:
                    raise BackupError('Git HEAD/branch changed during backup.')
            except (BackupError, OSError, ValueError) as exc:
                row['verification']={'ok':False};row['failures'].append(str(exc))
                report['failures'].append(f'{row["name"]}: {exc}')
        if report['failures']:raise BackupError('One or more projects failed; successful copies are retained for the next run.')
        # Check every source again: an earlier project could change while a later
        # project was being copied. No success claim from a mixed live snapshot.
        for row in report['projects']:
            if inventory(row)!=snapshot(row['files']) or git_repositories(Path(row['source']),row['files'],config)!=row['git_repositories']:
                raise BackupError(f'{row["name"]}: Source changed before final verification.')
        assert_device(config, device,mount_fd)
        report['status']='verified';report['data_verified']=True
        report['eject_authorization']='Displayed on screen only after final filesystem sync, report save and device checks.'
        write_reports(report, report_dir)
        write_usb_reports(report, root)
        progress('Flushing data and reports to USB')
        sync_device(root,(mount_fd,))
        assert_device(config, device,mount_fd)
        report['status']='complete';report['ok']=True
        report['usb_flush']='succeeded'
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        write_reports(report, report_dir)
        assert_device(config, device,mount_fd)
        # Durable reports describe verification; eject permission is transient.
        # No saved report can promise an operation which has not happened yet.
        report['safe_to_eject']=True
    except (BackupError, OSError, ValueError, KeyboardInterrupt) as exc:
        report['safe_to_eject']=False;report['ok']=False
        report['status']='cancelled' if isinstance(exc,KeyboardInterrupt) else 'failed'
        report['failures'].append('Cancelled by user.' if isinstance(exc,KeyboardInterrupt) else str(exc))
        try:write_reports(report, report_dir)
        except OSError as error:report['failures'].append(f'Local report could not be saved: {error}')
        try:
            assert_device(config, device,mount_fd)
            if root.is_dir() and not root.is_symlink():
                write_usb_reports(report, root);sync_device(root,(mount_fd,) if mount_fd is not None else ())
        except (BackupError, OSError, ValueError):pass
    finally:
        if lock:lock.close()
        if mount_fd is not None:os.close(mount_fd)
    report['ok']=report['safe_to_eject']
    return report


def verify_manifest(path, root):
    import re
    root = Path(root)
    report=json.loads(Path(path).read_text())
    result={'ok':True,'hashed_files':0,'failures':[]}
    if root.is_symlink():return {'ok':False,'hashed_files':0,'failures':['Backup root is a symlink.']}
    if report.get('status') not in ('complete','verified') or not report.get('projects'):
        return {'ok':False,'hashed_files':0,'failures':['Manifest is not a completed backup.']}
    try:
        registry=validate_config(report['registry'])
        expected={p['name']:p for p in registry['projects']}
        names=[p['name'] for p in report['projects']]
        if len(names)!=len(set(names)) or set(names)!=set(expected):
            raise BackupError('Manifest project list does not match its registry.')
    except (BackupError,KeyError,TypeError,ValueError) as exc:
        return {'ok':False,'hashed_files':0,'failures':[f'Invalid manifest: {exc}']}
    for project in report['projects']:
        try:
            if project['destination']!=expected[project['name']]['destination']:
                raise BackupError('Manifest destination does not match its registry.')
            destination = root/relative_path(project['destination'])
            guard_path(root,destination)
            if not project.get('verification',{}).get('ok'):raise BackupError('Project was not verified in this manifest.')
            entries=project['files']
            if not isinstance(entries,list) or not entries:raise BackupError('Manifest inventory is empty.')
            paths=[e['path'] for e in entries]
            if len(paths)!=len(set(paths)):raise BackupError('Duplicate inventory paths in manifest.')
            if any(e['kind'] not in ('file','directory','link') for e in entries):raise BackupError('Unknown inventory entry type.')
            file_count=sum(e['kind']=='file' for e in entries)
            byte_count=sum(e['size'] for e in entries if e['kind']=='file')
            links=sum(e['kind']=='link' for e in entries)
            verification=project['verification']
            if not file_count or file_count!=project['file_count'] or byte_count!=project['byte_count'] or links!=project['link_count']:
                raise BackupError('Manifest inventory counts or bytes are incomplete.')
            if verification.get('algorithm')!='SHA-256' or verification.get('hashed_files')!=file_count or verification.get('hashed_bytes')!=byte_count:
                raise BackupError('Manifest hash coverage is incomplete.')
            for entry in entries:
                target=destination/relative_path(entry['path'])
                guard_path(root,target.parent)
                st=target.lstat()
                if entry['kind']=='file':
                    if not re.fullmatch(r'[0-9a-f]{64}',entry.get('sha256','')):
                        raise BackupError(f'Manifest contains an invalid/unverified hash: {entry["path"]}')
                    if sha256(target)!=entry['sha256']:raise BackupError(f'SHA-256 mismatch: {entry["path"]}')
                    if st.st_size!=entry['size'] or stat.S_IMODE(st.st_mode)!=entry['snapshot'][2]:
                        raise BackupError(f'File size/mode mismatch: {entry["path"]}')
                    result['hashed_files']+=1
                elif entry['kind']=='link':
                    if not stat.S_ISLNK(st.st_mode) or os.readlink(target)!=entry['target']:raise BackupError(f'Link mismatch: {entry["path"]}')
                elif not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode)!=entry['mode']:
                    raise BackupError(f'Directory/mode mismatch: {entry["path"]}')
        except (BackupError,OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            result['failures'].append(f'{project["name"]}: {exc}')
    result['ok']=not result['failures']
    return result


ITEM_KINDS = {'f': 'file', 'd': 'folder', 'L': 'link', 'D': 'device', 'S': 'special'}


def parse_itemized(output, sizes):
    """Turn rsync --out-format='%i %n' lines into new/changed/metadata entries."""
    import re
    changes = []
    for line in output.splitlines():
        match = re.fullmatch(r'([<>ch.])([fdLDS])([^ ]{9}) (.+)', line)
        if not match:continue
        flag, kind, attrs, name = match.groups()
        path = name.rstrip('/')
        if path in ('', '.'):continue
        if flag == '.':action = 'metadata'
        elif set(attrs) == {'+'}:action = 'new'
        else:action = 'changed'
        changes.append({'action': action, 'kind': ITEM_KINDS[kind], 'path': path,
                        'size': sizes.get(path) if kind == 'f' else None})
    return changes


def change_preview(config, device, progress=lambda message:None, exact=False):
    """Read-only: list exactly which items the next run would copy to the USB.

    Runs the full normal review first (all safety checks), then an itemized
    rsync dry-run per folder. Quick mode compares size/time like the estimate;
    exact mode compares checksums like the real copy, which reads all data.
    Nothing is written to the USB or the sources.
    """
    import tempfile
    report = preview(config, device, progress)
    report['status'] = 'change-preview'
    report['change_mode'] = 'exact (checksum)' if exact else 'quick (size and time)'
    if not report['ok'] or device is None:return report
    for row in report['projects']:
        progress(f"Listing changes for {row['name']}")
        try:
            assert_device(config, device)
            destination = device.mountpoint/'CyclopsBackup'/row['destination']
            guard_path(device.mountpoint, destination)
            sizes = {e['path']: e['size'] for e in row['files'] if e['kind'] == 'file'}
            with tempfile.TemporaryDirectory(prefix='cyclops-preview-') as empty:
                target = destination if destination.exists() else Path(empty)
                args = rsync_args(row, target, dry=True)
                split = args.index('--')
                args[split:split] = ['--out-format=%i %n'] + (['--checksum'] if exact else [])
                result = subprocess.run(args, capture_output=True, text=True,
                                        env=dict(os.environ, LC_ALL='C'))
            if result.returncode:raise BackupError(f'rsync preview failed ({result.returncode}): {result.stderr.strip()}')
            row['changes'] = parse_itemized(result.stdout, sizes)
            row['change_counts'] = {a: sum(c['action'] == a for c in row['changes'])
                                    for a in ('new', 'changed', 'metadata')}
            if exact:row['estimated_transfer_bytes'] = rsync_stats(result.stdout)
        except (BackupError, OSError, ValueError) as exc:
            row['failures'].append(str(exc));report['failures'].append(f'{row["name"]}: {exc}')
    if exact and not report['failures']:
        report['estimated_transfer_bytes'] = sum(r['estimated_transfer_bytes'] for r in report['projects'])
    report['ok'] = not report['failures']
    return report
