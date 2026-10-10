import json
import os
from pathlib import Path


def size_text(value):
    if value is None:
        return 'unknown (USB absent)'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


def readable(report):
    device=report.get('device')
    drive=device['label'] or device['uuid'] if device else 'not connected'
    lines = ['Cyclops Backup', f"Run: {report['run_id']}",
             f"Status: {report['status']}", f"USB: {drive}",
             f"Included data: {size_text(report.get('total_bytes', 0))}",
             f"Estimated transfer: {size_text(report.get('estimated_transfer_bytes'))}",
             f"Free space: {size_text(report.get('free_bytes'))}",
             'Previous versions and source-deleted files are retained.',
             'Close project apps and recording tools before starting.', '']
    if device:
        lines[4:4]=[f"Mounted at: {device['mountpoint']}",f"Filesystem UUID: {device['uuid']}"]
    counters=report.get('counters')
    if counters:
        line=(f"Scanned {counters['scanned']:,} · Unchanged {counters['unchanged']:,} · Copied {counters['copied']:,} · "
              f"Verified {counters['verified']:,}")
        if counters.get('size_checked'):line+=f" · Size-checked {counters['size_checked']:,}"
        lines[3:3]=[f"Check: {report.get('check', 'full')}", line+f" · Written {size_text(counters['bytes_written'])}"]
    for p in report.get('projects', []):
        git = p.get('git')
        git_text=f"{git['branch']} / HEAD {git['head'] or 'no commit yet'}" if git else 'non-Git project/evidence'
        verification=p.get('verification')
        verification_text=(f"SHA-256: {verification['hashed_files']:,} files verified ({size_text(verification['hashed_bytes'])})"
                           if verification and verification.get('ok') else 'failed' if verification else 'not run')
        if verification and verification.get('ok') and verification.get('scope')=='quick':
            verification_text+=f"; {verification.get('size_checked_files',0):,} unchanged files checked by size (quick check)"
        lines.extend([p['name'], f"  Source: {p['source']}", f"  Destination: {p['destination']}",
                      f"  Files: {p.get('file_count', 0)}; links: {p.get('link_count', 0)}; data: {size_text(p.get('byte_count', 0))}",
                      f"  Estimate: {size_text(p.get('estimated_transfer_bytes'))}",
                      f"  Git: {git_text}",f"  Verification: {verification_text}"])
        if p.get('transfer_bytes') is not None:
            lines.append(f"  Written: {len(p.get('copied_files', [])):,} files ({size_text(p['transfer_bytes'])})")
        for why in p.get('retries', []):lines.append(f'  Retried once: {why}')
        for repo in p.get('git_repositories',[]):
            if repo['path']!='.':lines.append(f"  Nested Git {repo['path']}: {repo['branch']} / {repo['head']}")
        for e in p.get('excludes', []):
            lines.append(f"  Excluded: {e['path']} — {e['reason']}")
        for w in p.get('warnings', []):lines.append(f'  Note: {w}')
        for error in p.get('failures', []):lines.append(f'  FAILED: {error}')
        lines.append('')
    if report.get('failures'):
        lines.append('FAILED — do not treat this run as a completed backup:')
        lines.extend(f'  {f}' for f in report['failures'])
    elif report.get('safe_to_eject'):
        lines.append('SAFE TO EJECT USB — use Files → Eject, then unplug.')
    elif report.get('status') in ('complete','verified'):
        lines.append('Copy and SHA-256 verification finished. Use the on-screen result to confirm ejection after the final USB flush.')
    else:
        lines.append('No completed backup result. Review the status above.')
    return '\n'.join(lines) + '\n'


def atomic_write(path, text):
    path = Path(path)
    if path.is_symlink():
        raise OSError(f'Report path is a symlink: {path}')
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_reports(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report['local_json'] = str(folder / (report['run_id']+'.json'))
    report['local_text'] = str(folder / (report['run_id']+'.txt'))
    atomic_write(report['local_json'], json.dumps(report, indent=2) + '\n')
    atomic_write(report['local_text'], readable(report))


CHANGE_LABELS = {'new': 'NEW    ', 'changed': 'CHANGED', 'metadata': 'PERMS  '}


def changes_text(report, limit=None):
    """What the next run would copy, per folder. limit caps lines per folder."""
    lines = ['Cyclops Backup — copy preview (nothing has been copied)',
             f"Compared by: {report.get('change_mode', 'quick (size and time)')}",
             f"USB: {(report.get('device') or {}).get('label') or (report.get('device') or {}).get('uuid') or 'not connected'}",
             f"Estimated transfer: {size_text(report.get('estimated_transfer_bytes'))}",
             f"Free space: {size_text(report.get('free_bytes'))}", '',
             'NEW = not on the USB yet.  CHANGED = will be updated; the old USB copy is kept',
             'under Archives/PreviousVersions.  PERMS = only permissions/time are updated.',
             'Files removed from your computer stay on the USB. Nothing on the USB is deleted.', '']
    for p in report.get('projects', []):
        counts = p.get('change_counts')
        lines.append(f"{p['name']}  →  {p['destination']}")
        for error in p.get('failures', []):lines.append(f'  FAILED: {error}')
        if counts is None:
            lines.append('');continue
        lines.append(f"  {counts['new']} new, {counts['changed']} changed, {counts['metadata']} permission/time-only")
        for e in p.get('excludes', []):lines.append(f"  Left out: {e['path']} — {e['reason']}")
        changes = p.get('changes', [])
        if not changes:lines.append('  Already up to date — nothing to copy.')
        shown = changes if limit is None else changes[:limit]
        for c in shown:
            size = f"  ({size_text(c['size'])})" if c.get('size') is not None else ''
            suffix = '/' if c['kind'] == 'folder' else ''
            lines.append(f"  {CHANGE_LABELS[c['action']]} {c['path']}{suffix}{size}")
        if len(shown) < len(changes):
            lines.append(f"  … and {len(changes) - len(shown):,} more (full list saved in the local report)")
        lines.append('')
    if report.get('failures'):
        lines.append('PREVIEW BLOCKED — fix these before backing up:')
        lines.extend(f'  {f}' for f in report['failures'])
    return '\n'.join(lines) + '\n'


def write_change_list(report, folder):
    """Save the complete, uncapped change list next to the normal local reports."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(report['run_id'] + '.changes.txt')
    atomic_write(path, changes_text(report))
    return path
