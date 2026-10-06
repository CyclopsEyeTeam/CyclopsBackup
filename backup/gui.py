"""Zenity control panel; engine and CLI remain usable without a desktop."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile
import re

from .config import BackupError, devices, load_config, resolve_device, save_config
from .core import change_preview, preview, run_backup
from .report import changes_text, readable, write_change_list, write_reports
from .setup import add_exclusion, add_project, remove_exclusion, remove_project


def dialog(*args):
    return subprocess.run(['zenity','--title=Cyclops Backup',*args],capture_output=True,text=True)


def text_dialog(text, confirm=False):
    with tempfile.NamedTemporaryFile(mode='w',prefix='cyclops-review-',suffix='.txt') as file:
        file.write(text);file.flush()
        args=['--text-info','--filename='+file.name,'--width=820','--height=650']
        if confirm:args.extend(['--checkbox=I have reviewed these projects and want to back them up','--ok-label=Run Backup','--cancel-label=Cancel'])
        else:args.extend(['--ok-label=Close','--no-cancel'])
        return dialog(*args).returncode==0


def progress_task(title, task):
    process=subprocess.Popen(['zenity','--title=Cyclops Backup','--progress','--pulsate',
                              '--text='+title,'--auto-close','--no-cancel','--width=540'],
                             stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
    def progress(message):
        if process.poll() is not None:raise KeyboardInterrupt()
        try:process.stdin.write('#'+message.replace('\n',' ')+'\n');process.stdin.flush()
        except (BrokenPipeError,OSError):raise KeyboardInterrupt()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(task,progress)
            return future.result()
    finally:
        if process.poll() is None:
            try:process.stdin.write('100\n');process.stdin.flush();process.stdin.close()
            except (BrokenPipeError,OSError):pass
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.terminate();process.wait()


def choose_device(config_path, config):
    rows=[r for r in devices() if r['usb'] and r['fstype'] in ('ext4','xfs','btrfs')]
    if not rows:raise BackupError('Plug in an ext4 backup USB and open it in Files, then launch again.')
    args=['--list','--text=Select your backup USB. Nothing is copied during setup.',
          '--column=UUID','--column=Label','--column=Mounted at','--width=860','--height=300','--print-column=1']
    for r in rows:args.extend([r['uuid'],r['label'],r['mountpoint']])
    result=dialog(*args)
    if result.returncode:return None
    config['device']={'uuid':result.stdout.strip()}
    device=resolve_device(config)
    config['device']['label']=device.label
    save_config(config_path,config)
    return device


def choose_project(config_path, config):
    folder=dialog('--file-selection','--directory',
                  '--text=Choose a project or evidence folder. Setup does not copy anything.')
    if folder.returncode:return False
    source=Path(folder.stdout.rstrip('\n'))
    suggested=re.sub(r'[^A-Za-z0-9._-]+','-',source.name).strip('-._') or 'Project'
    name=dialog('--entry','--text=Choose a unique name for this folder in the backup.',
                '--entry-text='+suggested)
    if name.returncode:return False
    candidate=add_project(config,name.stdout.strip(),source)
    save_config(config_path,candidate)
    config.update(candidate)
    return True


MENU = [
    ('run', 'Review and back up', 'Check everything, confirm, copy, verify'),
    ('preview', 'Preview what will be copied', 'List new and changed items — copies nothing'),
    ('usb', 'Choose backup USB', 'Pick or change the USB drive'),
    ('add', 'Add a folder', 'Register another project or evidence folder'),
    ('remove', 'Remove a folder', 'Stop backing it up — source and USB copy untouched'),
    ('exclude', 'Leave out a file or subfolder', 'Skip one item inside a registered folder'),
    ('include', 'Put an item back', 'Undo a leave-out'),
]


def usb_status(config):
    if not (config.get('device', {}).get('uuid') or config.get('device', {}).get('label')):
        return None, 'USB: not chosen yet'
    try:
        device = resolve_device(config)
        return device, f'USB: {device.label or device.uuid} — connected at {device.mountpoint}'
    except BackupError as exc:
        return None, f"USB: {config['device'].get('label') or config['device'].get('uuid')} — {exc}"


def main_menu(config):
    _, status = usb_status(config)
    folders = ', '.join(p['name'] for p in config['projects']) or 'none'
    args = ['--list', f'--text={status}\nFolders: {folders}', '--column=key', '--column=Action',
            '--column=What it does', '--hide-column=1', '--print-column=1',
            '--width=760', '--height=420', '--ok-label=Open', '--cancel-label=Quit']
    for row in MENU:args.extend(row)
    result = dialog(*args)
    if result.returncode:return None
    # OK with nothing highlighted means the main action.
    return result.stdout.strip().split('|')[0] or 'run'


def choose(text, options):
    """Small pick-one list. Cancel/Esc returns None instead of a default answer."""
    args = ['--list', '--text='+text, '--column=key', '--column=Choice', '--hide-column=1',
            '--print-column=1', '--width=520', '--height=240']
    for key, label in options:args.extend([key, label])
    result = dialog(*args)
    if result.returncode or not result.stdout.strip():return None
    return result.stdout.strip().split('|')[0]


def pick_project(config, text):
    if not config['projects']:raise BackupError('No folders are registered yet. Add a folder first.')
    args = ['--list', '--text='+text, '--column=Name', '--column=Source', '--column=Backup location',
            '--print-column=1', '--width=860', '--height=360']
    for p in config['projects']:args.extend([p['name'], p['source'], p['destination']])
    result = dialog(*args)
    return result.stdout.strip().split('|')[0] if result.returncode == 0 and result.stdout.strip() else None


def add_folder(config_path, config):
    kind = choose('What kind of folder is it?', [('project', 'Project — stored under Projects on the USB'),
                                                 ('evidence', 'Evidence/archive — stored under Archives')])
    if kind is None:return False
    archive = kind == 'evidence'
    folder = dialog('--file-selection', '--directory', '--text=Choose a folder to add. Nothing is copied yet.')
    if folder.returncode:return False
    source = Path(folder.stdout.rstrip('\n'))
    suggested = re.sub(r'[^A-Za-z0-9._-]+', '-', source.name).strip('-._') or 'Project'
    name = dialog('--entry', '--text=Choose a unique name for this folder in the backup.', '--entry-text='+suggested)
    if name.returncode:return False
    candidate = add_project(config, name.stdout.strip(), source, archive)
    save_config(config_path, candidate);config.update(candidate)
    return True


def remove_folder(config_path, config):
    name = pick_project(config, 'Choose the folder to stop backing up.')
    if not name:return False
    if dialog('--question', f'--text=Stop backing up {name}?\n\nThe folder on your computer is not touched, '
              'and its existing copy on the USB is kept.', '--ok-label=Remove from backup',
              '--cancel-label=Keep').returncode:
        return False
    candidate = remove_project(config, name)
    save_config(config_path, candidate);config.update(candidate)
    return True


def exclude_item(config_path, config):
    name = pick_project(config, 'Which folder is the item inside?')
    if not name:return False
    project = next(p for p in config['projects'] if p['name'] == name)
    kind = choose('Leave out a single file or a whole subfolder?', [('file', 'A single file'), ('folder', 'A whole subfolder')])
    if kind is None:return False
    args = ['--file-selection', '--filename='+project['source'].rstrip('/')+'/',
            '--text=Choose the item to leave out of the backup.']
    if kind == 'folder':args.insert(1, '--directory')
    picked = dialog(*args)
    if picked.returncode:return False
    reason = dialog('--entry', '--text=Why is it safe to leave this out?\n'
                    '(Only skip things you can rebuild — e.g. caches, downloads, build output.)')
    if reason.returncode:return False
    candidate = add_exclusion(config, name, picked.stdout.rstrip('\n'), reason.stdout.strip())
    save_config(config_path, candidate);config.update(candidate)
    return True


def include_item(config_path, config):
    rows = [(p['name'], e['path'], e['reason']) for p in config['projects'] for e in p.get('excludes', [])]
    if not rows:raise BackupError('Nothing is currently left out.')
    args = ['--list', '--text=Choose an item to put back into the backup.', '--column=Folder',
            '--column=Item', '--column=Reason', '--print-column=ALL', '--separator=\t',
            '--width=860', '--height=360']
    for row in rows:args.extend(row)
    result = dialog(*args)
    if result.returncode or not result.stdout.strip():return False
    name, path = result.stdout.rstrip('\n').split('\t')[:2]
    candidate = remove_exclusion(config, name, path)
    save_config(config_path, candidate);config.update(candidate)
    return True


def show_change_preview(config, report_dir):
    device, status = usb_status(config)
    if device is None:raise BackupError(status.replace('USB: ', 'Backup USB: ') + '\nChoose or connect the USB first.')
    mode = choose('How thorough should the preview be?', [
        ('quick', 'Quick — compares size and date (fast)'),
        ('exact', 'Exact — compares contents like the real backup (reads every file, slow)')])
    if mode is None:return False
    exact = mode == 'exact'
    report = progress_task('Working out what would be copied — nothing is being copied',
                           lambda p:change_preview(config, device, p, exact=exact))
    write_reports(report, report_dir)
    saved = write_change_list(report, report_dir)
    text_dialog(changes_text(report, limit=2000) + f'\nFull list saved: {saved}\n')
    return report['ok']


def review_and_run(config, device, report_dir):
    report=progress_task('Reviewing projects and estimating the transfer',lambda p:preview(config,device,p))
    write_reports(report,report_dir)
    if not report['ok']:
        text_dialog(readable(report));return 2
    if not text_dialog(readable(report),confirm=True):return 1
    result=progress_task('Backing up and verifying — please keep USB plugged in',
                         lambda p:run_backup(config,device,report_dir,p))
    text_dialog(readable(result));return 0 if result['safe_to_eject'] else 2


def launch(config_path,report_dir):
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise BackupError('No desktop display. Use preview or run from a terminal.')
    try:
        config=load_config(config_path,allow_empty=True)
        if not config['projects']:
            while True:
                if not choose_project(config_path,config):return 1
                if dialog('--question','--text=Add another project or evidence folder?',
                          '--ok-label=Add Another','--cancel-label=Continue').returncode:break
        if not (config.get('device',{}).get('uuid') or config.get('device',{}).get('label')):
            if choose_device(config_path,config) is None:return 1
        actions={'usb':lambda:choose_device(config_path,config),'add':lambda:add_folder(config_path,config),
                 'remove':lambda:remove_folder(config_path,config),'exclude':lambda:exclude_item(config_path,config),
                 'include':lambda:include_item(config_path,config),'preview':lambda:show_change_preview(config,report_dir)}
        while True:
            choice=main_menu(config)
            if choice is None:return 1
            try:
                if choice=='run':
                    if not config['projects']:raise BackupError('No folders are registered. Add a folder first.')
                    return review_and_run(config,resolve_device(config),report_dir)
                actions[choice]()
            except (BackupError,OSError,ValueError) as exc:
                dialog('--error','--text='+str(exc),'--width=560')
    except (BackupError,OSError,ValueError) as exc:
        dialog('--error','--text='+str(exc),'--width=560');return 2
    except KeyboardInterrupt:
        dialog('--warning','--text=Backup cancelled. No completed backup result.','--width=560');return 130
