#!/usr/bin/env python3
"""Install only two launchers. The independent utility stays in this folder."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def desktop_quote(value):
    # Freedesktop Exec field quoting, not shell quoting; % must be doubled.
    value=str(value).replace('%','%%')
    for char in ('\\','"','`','$'):value=value.replace(char,'\\'+char)
    return '"'+value+'"'


def install(source,home):
    source=Path(source).resolve();home=Path(home)
    executable=source/'cyclops-backup'
    if not executable.is_file():raise OSError('Utility executable missing.')
    executable.chmod(executable.stat().st_mode|0o111)
    icon=source/'assets/cyclops-backup.svg'
    body=('''[Desktop Entry]
Version=1.0
Type=Application
Name=Cyclops Backup
Comment=Review, back up and verify project folders on your USB
Icon={icon}
Terminal=false
Categories=Utility;Archiving;
StartupNotify=true
'''.format(icon=icon if icon.is_file() else 'drive-removable-media')+f'Exec=/usr/bin/python3 {desktop_quote(executable)} gui\n')
    exec_line=body.splitlines()[-1]
    paths=[home/'.local/share/applications/cyclops-backup.desktop']
    desktop=home/'Desktop'
    if home==Path.home() and shutil.which('xdg-user-dir'):
        result=subprocess.run(['xdg-user-dir','DESKTOP'],capture_output=True,text=True)
        if result.returncode==0 and result.stdout.strip():desktop=Path(result.stdout.strip())
    paths.append(desktop/'Cyclops Backup.desktop')
    for path in paths:
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_text()!=body:
            # Our own earlier launcher for this same utility is updated; anything else is left alone.
            if exec_line not in path.read_text().splitlines():
                raise OSError(f'Existing different launcher left untouched: {path}')
        path.write_text(body);path.chmod(0o755)
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate',str(path)],check=True)
    if home==Path.home() and shutil.which('gio'):
        subprocess.run(['gio','set',str(paths[1]),'metadata::trusted','true'],capture_output=True)
    if home==Path.home() and shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database',str(paths[0].parent)],capture_output=True)
    return paths


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Install Cyclops Backup desktop and application menu launchers')
    parser.add_argument('--home',type=Path,default=Path.home())
    args=parser.parse_args()
    for path in install(Path(__file__).parent,args.home):print(path)
