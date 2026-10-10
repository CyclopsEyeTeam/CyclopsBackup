from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


class BackupError(Exception):
    pass


class SourceMoved(BackupError):
    """The source (or its copy) changed underneath a folder's copy/verify: worth one fresh retry."""


@dataclass(frozen=True)
class Device:
    mountpoint: Path
    uuid: str
    label: str
    fstype: str


def command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise BackupError(f'{args[0]} failed: {result.stderr.strip()}')
    return result.stdout


def mount_info(path):
    data = json.loads(command(['findmnt', '--json', '--target', str(path),
                              '--output', 'TARGET,SOURCE,FSTYPE,UUID,LABEL,OPTIONS']))
    return data['filesystems'][0]


def devices():
    """Transport is inherited from the disk. No device-name assumptions."""
    data = json.loads(command(['lsblk', '--json', '--paths', '--output',
                              'NAME,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,TRAN,RM']))
    rows = []
    def visit(node, usb=False):
        usb = usb or node.get('tran') == 'usb'
        for target in node.get('mountpoints') or []:
            if target and node.get('uuid'):
                rows.append({'uuid': node['uuid'], 'label': node.get('label') or '',
                             'fstype': node.get('fstype'), 'mountpoint': target, 'usb': usb})
        for child in node.get('children', []):
            visit(child, usb)
    for node in data['blockdevices']:
        visit(node)
    return rows


def resolve_device(config):
    expected = config.get('device', {})
    uuid, label = expected.get('uuid'), expected.get('label')
    if not (uuid or label):
        raise BackupError('Backup USB is not configured. Choose it in the launcher or run configure.')
    rows = [d for d in devices() if (d['uuid'] == uuid if uuid else d['label'] == label)]
    if not rows:
        raise BackupError('Expected USB is not mounted. Plug it in and open it in Files, then retry.')
    if len(rows) != 1:
        raise BackupError('USB identity is ambiguous; configure its unique filesystem UUID.')
    row = rows[0]
    if not row['usb']:
        raise BackupError('Expected filesystem is on an internal disk, not a USB device.')
    target = Path(row['mountpoint'])
    if target.is_symlink() or not target.is_dir() or not os.path.ismount(target):
        raise BackupError('USB mountpoint is absent or unsafe.')
    mounted = mount_info(target)
    if mounted.get('uuid') != row['uuid'] or mounted.get('target') != str(target):
        raise BackupError('USB mount identity changed. Retry with the expected device mounted.')
    if row['fstype'] not in ('ext4', 'xfs', 'btrfs'):
        raise BackupError('Use an ext4, xfs or btrfs USB filesystem to preserve Git, permissions and links. No disk was changed.')
    return Device(target, row['uuid'], row['label'], row['fstype'])


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise BackupError('Invalid relative path in registry.')
    path = PurePosixPath(value)
    if path.is_absolute() or any(s in ('..', '.') for s in value.split('/')):
        raise BackupError(f'Unsafe path in registry: {value!r}')
    return path


DEFAULT_PROFILE = 'Default'


def profile_name(value):
    """Profiles are labels in the settings file only; they never become paths."""
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 40
            or any(c in value for c in '/\\|\t\n\r\x00')):
        raise BackupError('Profile names: 1-40 characters, no slashes, | or tabs, no leading/trailing spaces.')
    return value


def with_profiles(config):
    """Copy of config with a profiles table whose active entry mirrors config['projects'].

    'projects' is always the active profile's folder list, so the copy and
    verification engine keeps seeing exactly the shape it always has.
    """
    config = dict(config)
    profiles = config.get('profiles')
    if not isinstance(profiles, dict) or not profiles:
        profiles = {config.get('profile') or DEFAULT_PROFILE: list(config.get('projects', []))}
    profiles = dict(profiles)
    active = config.get('profile')
    if active not in profiles:active = next(iter(profiles))
    if 'projects' in config:profiles[active] = list(config['projects'])
    config.update(profiles=profiles, profile=active, projects=list(profiles[active]))
    return config


def run_view(config):
    """What one backup run sees: the active profile only (also what manifests record)."""
    view = {k: v for k, v in config.items() if k != 'profiles'}
    view['profile'] = config.get('profile') or DEFAULT_PROFILE
    return view


def validate_config(data, allow_empty=False, canonical_sources=False):
    validate_projects(data, allow_empty, canonical_sources)
    if isinstance(data.get('profiles'), dict):
        if data.get('profile') not in data['profiles']:
            raise BackupError('Active profile is missing from the profile list.')
        owners = {}
        for name, projects in with_profiles(data)['profiles'].items():
            profile_name(name)
            validate_projects({'version': 1, 'projects': projects}, True, canonical_sources)
            for p in projects:
                # One USB folder per source: two profiles may share a folder, never a destination.
                seen = owners.setdefault(p['destination'], (p['source'], name))
                if seen[0] != p['source']:
                    raise BackupError(f"Profiles {seen[1]!r} and {name!r} both use {p['destination']} "
                                      'on the USB for different folders. Choose another backup name.')
    return data


def validate_projects(data, allow_empty=False, canonical_sources=False):
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('projects'), list):
        raise BackupError('Registry must have version 1 and a project list.')
    if not data['projects'] and not allow_empty:
        raise BackupError('No project folders configured. Launch the setup wizard or use project-add.')
    names, destinations, sources = set(), [], []
    for project in data['projects']:
        name = project.get('name', '')
        if not name:
            raise BackupError('Give the folder a backup name.')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
            raise BackupError(f'Backup name {name!r} can only use letters, numbers, dots, dashes and '
                              'underscores, and must start with a letter or number.')
        if name in names:
            raise BackupError(f'{name!r} is already used in this profile. Choose another backup name.')
        names.add(name)
        dest = relative_path(project.get('destination'))
        if len(dest.parts) != 2 or dest.parts[0] not in ('Projects', 'Archives'):
            raise BackupError('Destination must be Projects/name or Archives/name.')
        if dest.parts[1] == 'PreviousVersions' or dest in destinations:
            raise BackupError('Duplicate or reserved destination in registry.')
        destinations.append(dest)
        source = Path(project.get('source', ''))
        if not source.is_absolute() or '\x00' in str(source):
            raise BackupError('Every source must be an explicit absolute path.')
        # Saved manifests describe historical source paths on another computer.
        # Only live settings may depend on the current source filesystem.
        if canonical_sources and source != source.resolve():
            raise BackupError(f'Source must use its canonical location: {source.resolve()}')
        if any(source == s or source in s.parents or s in source.parents for s in sources):
            raise BackupError('Overlapping sources would create duplicate or recursive backups.')
        sources.append(source)
        for exclusion in project.get('excludes', []):
            path = relative_path(exclusion['path'])
            if '.git' in path.parts or not exclusion.get('reason'):
                raise BackupError('Exclusions need a reason and cannot exclude Git metadata.')
        for marker in project.get('required_markers', []):
            relative_path(marker)
    return data


def load_config(path, allow_empty=False):
    try:
        path = Path(path)
        if allow_empty and not path.exists() and not path.is_symlink():
            return with_profiles({'version': 1, 'device': {}, 'projects': []})
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get('profiles'), dict) and data.get('profile') in data['profiles']:
            data['projects'] = data['profiles'][data['profile']]
        return validate_config(with_profiles(data), allow_empty=allow_empty, canonical_sources=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f'Cannot read registry: {exc}') from exc


def save_config(path, config):
    config = with_profiles(config)
    validate_config(config, allow_empty=True, canonical_sources=True)
    path = Path(path)
    if path.is_symlink():raise BackupError('Settings file is a symlink; choose a regular file.')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp.exists():temp.unlink()
