import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from .config import BackupError, devices, load_config, resolve_device, save_config
from . import __version__
from .core import preview, run_backup, verify_manifest
from .report import readable, write_reports

def default_config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'cyclops-backup/registry.json'


def default_report_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'cyclops-backup/reports'


def main(argv=None):
    parser=argparse.ArgumentParser(description='Cyclops Backup — independent weekly USB backups')
    parser.add_argument('--version',action='version',version='Cyclops Backup '+__version__)
    parser.add_argument('--config',type=Path,default=default_config_path())
    parser.add_argument('--report-dir',type=Path,default=default_report_dir())
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('devices',help='Show mounted USB filesystems and their stable identity')
    cfg=sub.add_parser('configure',help='Pin a mounted USB by filesystem UUID')
    cfg.add_argument('--uuid',required=True)
    add=sub.add_parser('project-add',help='Register one existing project or evidence folder; no copying')
    add.add_argument('--name',required=True,help='Unique simple backup folder name')
    add.add_argument('--source',required=True,type=Path,help='Actual source folder (spaces supported)')
    add.add_argument('--archive',action='store_true',help='Place evidence under Archives instead of Projects')
    sub.add_parser('preview',help='Read-only dry-run; saves only a local report')
    run=sub.add_parser('run',help='Review and confirm, then copy and verify')
    run.add_argument('--yes',action='store_true',help='Deliberately approve the entire configured backup')
    sub.add_parser('gui',help='Open the desktop wizard')
    verify=sub.add_parser('verify',help='Check saved SHA-256 manifest without the original computer')
    verify.add_argument('manifest',type=Path)
    verify.add_argument('--root',required=True,type=Path,help='CyclopsBackup folder or restored layout')
    args=parser.parse_args(argv)
    try:
        if args.action=='devices':
            print(json.dumps([d for d in devices() if d['usb']],indent=2));return 0
        if args.action=='verify':
            result=verify_manifest(args.manifest,args.root)
            print(json.dumps(result,indent=2));return 0 if result['ok'] else 2
        config=load_config(args.config,allow_empty=True)
        if args.action=='project-add':
            from .setup import add_project
            config=add_project(config,args.name,args.source,args.archive)
            save_config(args.config,config)
            print(f'Registered {args.name}. Settings: {args.config}. No data copied.');return 0
        for dep in ('rsync','git','lsblk','findmnt','sync'):
            if not shutil.which(dep):raise BackupError(f'Required system tool missing: {dep}')
        if args.action=='configure':
            candidate=dict(config,device={'uuid':args.uuid})
            device=resolve_device(candidate)
            candidate['device']['label']=device.label
            save_config(args.config,candidate)
            print(f'Configured {device.label} ({device.uuid}). No data copied.');return 0
        if args.action=='gui':
            from .gui import launch
            return launch(args.config,args.report_dir)
        problem=None
        try:device=resolve_device(config)
        except BackupError as exc:device=None;problem=str(exc)
        report=preview(config,device)
        if problem:report['failures'].append(problem);report['ok']=False
        write_reports(report,args.report_dir)
        print(readable(report))
        if not report['ok']:return 2
        if args.action=='preview':return 0
        if not args.yes:
            if not sys.stdin.isatty():raise BackupError('Confirmation required. Use the desktop launcher or deliberately pass run --yes.')
            if input('Type BACKUP to copy and verify these projects: ').strip()!='BACKUP':
                print('Cancelled. No data copied.');return 1
        result=run_backup(config,device,args.report_dir,progress=lambda s:print(s,flush=True))
        print(readable(result));return 0 if result['safe_to_eject'] else 2
    except (BackupError,OSError,ValueError,KeyError) as exc:
        print(f'Cyclops Backup stopped: {exc}',file=sys.stderr);return 2
    except KeyboardInterrupt:
        print('Cancelled. No completed backup result.',file=sys.stderr);return 130
