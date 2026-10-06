import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from .config import BackupError, devices, load_config, resolve_device, run_view, save_config
from . import __version__
from .core import preview, run_backup, verify_manifest
from .report import readable, write_reports

def default_config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'cyclops-backup/registry.json'


def default_report_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'cyclops-backup/reports'


def config_exclusion(config,name):
    return next(p for p in config['projects'] if p['name']==name)['excludes'][-1]['path']


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
    sub.add_parser('projects',help='List registered folders, exclusions and the pinned USB')
    rm=sub.add_parser('project-remove',help='Stop backing up one folder; source and USB copy are untouched')
    rm.add_argument('--name',required=True)
    ex=sub.add_parser('exclude',help='Leave one file or subfolder out of a registered folder')
    ex.add_argument('--name',required=True,help='Registered folder name')
    ex.add_argument('--path',required=True,help='Item inside the folder (absolute or relative)')
    ex.add_argument('--reason',required=True,help='Why it is safe to leave out')
    inc=sub.add_parser('include',help='Put a previously excluded item back into the backup')
    inc.add_argument('--name',required=True)
    inc.add_argument('--path',required=True)
    pv=sub.add_parser('preview',help='Read-only dry-run; saves only a local report')
    pv.add_argument('--files',action='store_true',help='List every item the next run would copy')
    pv.add_argument('--exact',action='store_true',help='With --files: compare checksums like the real copy (reads all data)')
    pv.add_argument('--limit',type=int,default=200,help='With --files: lines shown per folder (0 = all; full list is always saved)')
    run=sub.add_parser('run',help='Review and confirm, then copy and verify')
    run.add_argument('--yes',action='store_true',help='Deliberately approve the entire configured backup')
    sub.add_parser('gui',help='Open the desktop control panel')
    sub.add_parser('folders',help='Open the folders window (profile dropdown, add/remove/leave out)')
    sub.add_parser('profiles',help='List profiles; * marks the active one')
    pu=sub.add_parser('profile-use',help='Make a profile active (its folders are what gets backed up)')
    pu.add_argument('--name',required=True)
    pn=sub.add_parser('profile-new',help='Create a profile and make it active')
    pn.add_argument('--name',required=True)
    pn.add_argument('--copy',action='store_true',help="Start with a copy of the active profile's folders")
    pr=sub.add_parser('profile-rename',help='Rename the active profile')
    pr.add_argument('--name',required=True,help='New name')
    pd=sub.add_parser('profile-delete',help='Forget one profile; folders and USB copies untouched')
    pd.add_argument('--name',required=True)
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
        if args.action=='folders':
            from .folders import open_window
            return open_window(args.config)
        config=load_config(args.config,allow_empty=True)
        if args.action=='profiles':
            for name,projects in config['profiles'].items():
                mark='*' if name==config['profile'] else ' '
                print(f"{mark} {name} ({len(projects)} folder{'s' if len(projects)!=1 else ''})")
            return 0
        if args.action.startswith('profile-'):
            from .setup import delete_profile, new_profile, rename_profile, switch_profile
            if args.action=='profile-use':config=switch_profile(config,args.name)
            elif args.action=='profile-new':config=new_profile(config,args.name,args.copy)
            elif args.action=='profile-rename':config=rename_profile(config,config['profile'],args.name)
            else:config=delete_profile(config,args.name)
            save_config(args.config,config)
            print(f"Active profile: {config['profile']}. No data copied.");return 0
        if args.action=='project-add':
            from .setup import add_project
            config=add_project(config,args.name,args.source,args.archive)
            save_config(args.config,config)
            print(f'Registered {args.name}. Settings: {args.config}. No data copied.');return 0
        if args.action=='projects':
            device=config.get('device') or {}
            print(f"Profile: {config['profile']}")
            print(f"USB: {device.get('label') or '-'} ({device.get('uuid') or 'not configured'})")
            if not config['projects']:print('No folders registered.')
            for p in config['projects']:
                print(f"{p['name']}: {p['source']} -> {p['destination']}")
                for e in p.get('excludes',[]):print(f"    left out: {e['path']} - {e['reason']}")
            return 0
        if args.action in ('project-remove','exclude','include'):
            from .setup import add_exclusion, remove_exclusion, remove_project
            if args.action=='project-remove':
                config=remove_project(config,args.name)
                message=f'Removed {args.name} from the backup set. Its source and any USB copy were not touched.'
            elif args.action=='exclude':
                config=add_exclusion(config,args.name,args.path,args.reason)
                message=f'{args.name}: now leaving out {config_exclusion(config,args.name)}. Review again before backing up.'
            else:
                config=remove_exclusion(config,args.name,args.path)
                message=f'{args.name}: item is back in the backup set.'
            save_config(args.config,config);print(message);return 0
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
        if args.action=='preview' and args.files:
            if device is None:raise BackupError(problem)
            from .core import change_preview
            from .report import changes_text, write_change_list
            report=change_preview(run_view(config),device,exact=args.exact)
            write_reports(report,args.report_dir)
            saved=write_change_list(report,args.report_dir)
            print(changes_text(report,limit=args.limit or None))
            print(f'Full list: {saved}')
            return 0 if report['ok'] else 2
        config=run_view(config)
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
