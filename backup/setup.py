"""Explicit project registration; no scanning for unrelated folders."""
from pathlib import Path

from .config import BackupError, validate_config
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
