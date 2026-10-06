"""Explicit project registration; no scanning for unrelated folders."""
from pathlib import Path
import subprocess

from .config import BackupError, relative_path, validate_config
from .core import inventory


def add_project(config, name, source, archive=False):
    source = Path(source).expanduser().absolute()
    if source.is_symlink():raise BackupError('Source is a symlink; register its actual location.')
    source = source.resolve()
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
