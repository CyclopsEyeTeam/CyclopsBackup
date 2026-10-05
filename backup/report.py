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
    for p in report.get('projects', []):
        git = p.get('git')
        git_text=f"{git['branch']} / HEAD {git['head'] or 'no commit yet'}" if git else 'non-Git project/evidence'
        verification=p.get('verification')
        verification_text=(f"SHA-256: {verification['hashed_files']:,} files verified ({size_text(verification['hashed_bytes'])})"
                           if verification and verification.get('ok') else 'failed' if verification else 'not run')
        lines.extend([p['name'], f"  Source: {p['source']}", f"  Destination: {p['destination']}",
                      f"  Files: {p.get('file_count', 0)}; links: {p.get('link_count', 0)}; data: {size_text(p.get('byte_count', 0))}",
                      f"  Estimate: {size_text(p.get('estimated_transfer_bytes'))}",
                      f"  Git: {git_text}",f"  Verification: {verification_text}"])
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
