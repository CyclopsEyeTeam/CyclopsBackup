"""Explicit project registration; no scanning for unrelated folders."""
from pathlib import Path
import re
import subprocess

from .config import BackupError, profile_name, relative_path, validate_config, with_profiles
from .core import inventory


def clean_name(text):
    """Turn any typed or folder name into a safe backup name (spaces etc. become -)."""
    return re.sub(r'[^A-Za-z0-9._-]+', '-', text or '').strip('-._')


def suggest_name(config, source):
    """Backup name for a folder: reuse the name another profile gives this same folder,
    otherwise the folder's own name, numbered if another folder already has it."""
    source = str(Path(source).expanduser().resolve())
    config = with_profiles(config)
    taken = {}
    for projects in config['profiles'].values():
        for p in projects:taken.setdefault(p['name'], p['source'])
    for name, owner in taken.items():
        if owner == source:return name
    base = clean_name(Path(source).name) or 'Folder'
    name, n = base, 2
    while name in taken:name, n = f'{base}-{n}', n + 1
    return name


def add_project(config, name, source, archive=False):
    source = Path(source).expanduser().absolute()
    if source.is_symlink():raise BackupError('Source is a symlink; register its actual location.')
    source = source.resolve()
    for p in config['projects']:
        if p['source'] == str(source):
            raise BackupError(f"That folder is already in this profile as {p['name']!r}.")
    project = {'name': name, 'source': str(source),
               'destination': ('Archives/' if archive else 'Projects/') + name,
               'required_markers': [], 'excludes': []}
    candidate = dict(config, projects=[*config['projects'], project])
    validate_config(candidate, canonical_sources=True)
    inventory(project)
    return candidate


def find_project(config, name):
    for project in config['projects']:
        if project['name'] == name:return project
    raise BackupError(f'No registered folder named {name!r}.')


def remove_project(config, name):
    """Drop one registration. Never touches the source or any existing USB copy."""
    find_project(config, name)
    candidate = dict(config, projects=[p for p in config['projects'] if p['name'] != name])
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


def exclusion_path(project, path):
    """Accept an absolute path inside the source, or a source-relative one."""
    source = Path(project['source'])
    path = Path(path).expanduser()
    if path.is_absolute():
        # Resolve the parent only: a symlink inside the folder is excluded as itself.
        path = path.parent.resolve()/path.name
        if source not in path.parents:
            raise BackupError('Choose a file or subfolder inside the registered folder.')
        rel = path.relative_to(source).as_posix()
    else:
        rel = path.as_posix().strip('/')
    if any(c in rel for c in '*?[\n'):
        # rsync would read these as wildcard patterns and could hide more than intended.
        raise BackupError('Names containing * ? [ cannot be excluded safely; rename or skip.')
    relative_path(rel)
    return rel


def add_exclusion(config, name, path, reason):
    """Leave one file or subfolder out of a registered folder. Copies nothing."""
    project = find_project(config, name)
    rel = exclusion_path(project, path)
    reason = (reason or '').strip()
    if not reason:raise BackupError('Every exclusion needs a reason.')
    source = Path(project['source'])
    if not (source/rel).exists() and not (source/rel).is_symlink():
        raise BackupError(f'Nothing at {rel} inside {project["name"]}.')
    if any(rel == e['path'] for e in project.get('excludes', [])):
        raise BackupError(f'{rel} is already excluded.')
    if (source/'.git').exists():
        result = subprocess.run(['git', '-C', str(source), 'ls-files', '--', rel],
                                capture_output=True, text=True)
        if result.returncode or result.stdout:
            raise BackupError(f'{rel} is tracked by Git (or Git could not check it); it must stay in the backup.')
    updated = dict(project, excludes=[*project.get('excludes', []), {'path': rel, 'reason': reason}])
    candidate = dict(config, projects=[updated if p is project else p for p in config['projects']])
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


def remove_exclusion(config, name, path):
    """Put an excluded item back into the backup set."""
    project = find_project(config, name)
    rel = exclusion_path(project, path)
    if not any(rel == e['path'] for e in project.get('excludes', [])):
        raise BackupError(f'{rel} is not excluded from {name}.')
    updated = dict(project, excludes=[e for e in project['excludes'] if e['path'] != rel])
    candidate = dict(config, projects=[updated if p is project else p for p in config['projects']])
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


# Profiles: each one is its own list of folders. The backup USB is shared.

def switch_profile(config, name):
    config = with_profiles(config)
    if name not in config['profiles']:raise BackupError(f'No profile named {name!r}.')
    candidate = dict(config, profile=name, projects=list(config['profiles'][name]))
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


def new_profile(config, name, copy_current=False):
    """Create a profile and switch to it. Copies nothing on disk."""
    config = with_profiles(config)
    name = profile_name(name)
    if name.casefold() in (n.casefold() for n in config['profiles']):
        raise BackupError(f'A profile named {name!r} already exists.')
    projects = [dict(p) for p in config['projects']] if copy_current else []
    candidate = dict(config, profiles={**config['profiles'], name: projects}, profile=name, projects=projects)
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


def rename_profile(config, old, new):
    config = with_profiles(config)
    new = profile_name(new)
    if old not in config['profiles']:raise BackupError(f'No profile named {old!r}.')
    if new != old and new.casefold() in (n.casefold() for n in config['profiles']):
        raise BackupError(f'A profile named {new!r} already exists.')
    profiles = {(new if n == old else n): v for n, v in config['profiles'].items()}
    candidate = dict(config, profiles=profiles, profile=new if config['profile'] == old else config['profile'])
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate


def delete_profile(config, name):
    """Forget one profile's folder list. Sources and USB copies are untouched."""
    config = with_profiles(config)
    if name not in config['profiles']:raise BackupError(f'No profile named {name!r}.')
    if len(config['profiles']) == 1:raise BackupError('This is the only profile; there must always be one.')
    profiles = {n: v for n, v in config['profiles'].items() if n != name}
    active = config['profile'] if config['profile'] != name else next(iter(profiles))
    candidate = dict(config, profiles=profiles, profile=active, projects=list(profiles[active]))
    validate_config(candidate, allow_empty=True, canonical_sources=True)
    return candidate
