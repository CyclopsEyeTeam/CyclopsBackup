"""Zenity control panel; engine and CLI remain usable without a desktop."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re

from .config import BackupError, devices, load_config, resolve_device, run_view, save_config
from .core import change_preview, preview, run_backup
from . import look
from .report import changes_text, readable, size_text, write_change_list, write_reports
from .setup import (add_exclusion, add_project, clean_name, delete_profile, suggest_name, new_profile, remove_exclusion,
                    remove_project, switch_profile)


def dialog(*args):
    return subprocess.run(['zenity','--title=Cyclops Backup',*args],capture_output=True,text=True,env=look.dialog_env())


def text_dialog(text, confirm=False):
    with tempfile.NamedTemporaryFile(mode='w',prefix='cyclops-review-',suffix='.txt') as file:
        file.write(text);file.flush()
        args=['--text-info','--filename='+file.name,'--width=820','--height=650']
        if confirm:args.extend(['--checkbox=I have reviewed these projects and want to back them up','--ok-label=Run Backup','--cancel-label=Cancel'])
        else:args.append('--ok-label=Close')   # zenity 4 refuses --no-cancel here
        return dialog(*args).returncode==0


def progress_task(title, task):
    process=subprocess.Popen(['zenity','--title=Cyclops Backup','--progress','--pulsate',
                              '--text='+look.brand()+'\n\n'+markup(title),'--auto-close','--no-cancel','--width=540'],
                             stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True,
                             env=look.dialog_env())
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


SUPPORTED_FS = ('ext4', 'xfs', 'btrfs')


def usb_rows():
    """Mounted USB filesystems. A failing lsblk counts as 'nothing found', never a crash."""
    try:return [r for r in devices() if r['usb']]
    except (BackupError, OSError, ValueError):return []


def choose_device(config_path, config):
    rows=[r for r in usb_rows() if r['fstype'] in SUPPORTED_FS]
    if not rows:
        # Nothing usable plugged in yet: wait for one rather than refusing.
        if not wait_for_usb(config,any_usb=True):return None
        rows=[r for r in usb_rows() if r['fstype'] in SUPPORTED_FS]
        if not rows:return None
    args=['--list','--text=Select your backup USB. Nothing is copied during setup.',
          '--column=UUID','--column=Label','--column=Mounted at','--width=860','--height=300','--print-column=1']
    for r in rows:args.extend([r['uuid'],r['label'],r['mountpoint']])
    result=dialog(*args)
    if result.returncode or not result.stdout.strip():return None
    candidate=dict(config,device={'uuid':result.stdout.strip().split('|')[0]})
    device=resolve_device(candidate)
    candidate['device']['label']=device.label
    save_config(config_path,candidate)
    config.update(candidate)
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


LOCKED = ('run', 'preview')   # need a connected, verified USB


def markup(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def usb_check(config):
    """(device, state, message). Only state 'verified' unlocks backup and copy preview.

    States: verified, missing (chosen USB not plugged in), unchosen (a usable USB is
    plugged in but not picked yet), none (nothing usable plugged in), problem.
    """
    chosen = config.get('device') or {}
    if not (chosen.get('uuid') or chosen.get('label')):
        rows = usb_rows()
        usable = [r for r in rows if r['fstype'] in SUPPORTED_FS]
        if usable:
            return None, 'unchosen', 'USB detected — choose it below to unlock backup and preview.'
        if rows:
            kinds = ', '.join(sorted({(r['fstype'] or 'unknown') for r in rows}))
            return None, 'none', (f'A USB is plugged in, but it is {kinds}. Cyclops Backup needs an '
                                  'ext4, xfs or btrfs USB. Nothing was changed on it.')
        return None, 'none', 'No backup USB connected — plug one in to unlock backup and preview.'
    name = chosen.get('label') or chosen.get('uuid')
    try:
        device = resolve_device(config)
    except BackupError as exc:
        if 'not mounted' in str(exc):
            return None, 'missing', (f'Backup USB "{name}" is not connected — plug it in and open it in '
                                     'Files to unlock backup and preview.')
        return None, 'problem', f'Backup USB "{name}": {exc}'
    except (OSError, ValueError) as exc:
        return None, 'problem', f'Backup USB "{name}" could not be checked: {exc}'
    return device, 'verified', f'USB verified: {device.label or device.uuid} at {device.mountpoint}'


def usb_status(config):
    device, _, message = usb_check(config)
    return device, 'USB: ' + message


def folders_row(config):
    n = len(config['projects'])
    return ('folders', 'View folders',
            f"Profile “{config.get('profile') or 'Default'}” — {n} folder{'s' if n != 1 else ''}. "
            'Switch profile, add, remove, leave out')


def menu_rows(config, state):
    if state == 'verified':
        return [('run', 'Review and back up', 'Check everything, confirm, copy, verify'),
                ('preview', 'Preview what will be copied', 'List new and changed items — copies nothing'),
                folders_row(config),
                ('change', 'Change backup USB', 'Pick a different USB drive')]
    lock = '🔒 Locked — plug in and verify your backup USB first'
    connect = {'unchosen': ('usb', 'Choose backup USB', 'Pick the USB that is plugged in, then verify it'),
               'missing': ('usb', 'Connect backup USB', 'Wait for your chosen USB, then verify it'),
               'problem': ('usb', 'Check backup USB again', 'Retry verification after fixing the problem'),
               }.get(state, ('usb', 'Connect backup USB', 'Wait for a USB to be plugged in, then choose it'))
    rows = [connect,
            ('run', 'Review and back up  🔒', lock),
            ('preview', 'Preview what will be copied  🔒', lock),
            folders_row(config)]
    if state in ('missing', 'problem'):
        rows.append(('change', 'Use a different USB', 'Pick another plugged-in USB instead'))
    return rows


def main_menu(config):
    _, state, message = usb_check(config)
    verified = state == 'verified'
    banner = (look.good(f'◉ {markup(message)}') if verified else
              look.warn(f'⚠ {markup(message)}') + '\n' +
              look.dim('Backup and copy preview stay locked until the USB is verified. You can still set up folders.'))
    folders = ', '.join(p['name'] for p in config['projects']) or 'none yet — open View folders to add some'
    profile = config.get('profile') or 'Default'
    args = ['--list', f'--text={look.brand()}\n\n{banner}\n'
            f'Profile: {look.span(markup(profile), look.EYE, bold=True)} · Folders: {markup(folders)}', '--column=key', '--column=Action',
            '--column=What it does', '--hide-column=1', '--print-column=1',
            '--width=780', '--height=440', '--ok-label=Open', '--cancel-label=Quit']
    for row in menu_rows(config, state):args.extend(row)
    result = dialog(*args)
    if result.returncode:return None
    # OK with nothing highlighted means the first row: back up when verified, connect USB when not.
    return result.stdout.strip().split('|')[0] or ('run' if verified else 'usb')


def wait_for_usb(config, interval=1.0, any_usb=False):
    """Pulsing 'plug in your USB' window that closes itself once a USB is usable.

    With a USB already chosen, waits until that exact USB passes verification.
    With none chosen (or any_usb), waits for any ext4/xfs/btrfs USB to appear.
    Returns True when ready, False if the person presses Cancel (back to the panel).
    """
    chosen = {} if any_usb else (config.get('device') or {})
    name = chosen.get('label') or chosen.get('uuid')
    def ready():
        if name:return usb_check(config)[1] == 'verified'
        return any(r['fstype'] in SUPPORTED_FS for r in usb_rows())
    if ready():return True
    text = look.brand() + '\n\n' + (
        look.warn(f'Waiting for backup USB "{markup(name)}"…') + '\n\nPlug it in and open it in Files.' if name else
        look.warn('Waiting for a backup USB…') + '\n\nPlug in an ext4, xfs or btrfs USB and open it in Files.')
    process = subprocess.Popen(['zenity', '--title=Cyclops Backup', '--progress', '--pulsate',
                                '--text='+text+'\nThis continues by itself once the USB is verified.',
                                '--width=540'],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                               env=look.dialog_env())
    try:
        while process.poll() is None:
            if ready():return True
            time.sleep(interval)
        return False
    finally:
        if process.poll() is None:
            try:process.stdin.write('100\n');process.stdin.flush();process.stdin.close()
            except (BrokenPipeError, OSError):pass
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.terminate();process.wait()


def verified_device(config_path, config):
    """The gate for backup and copy preview: a connected, verified USB or nothing."""
    device, state, _ = usb_check(config)
    if state == 'verified':return device
    if state == 'problem':
        raise BackupError(usb_check(config)[2] + '\nFix this or choose a different USB first.')
    if state in ('none', 'unchosen'):
        return choose_device(config_path, config)
    if not wait_for_usb(config):return None
    device, state, message = usb_check(config)
    if state != 'verified':raise BackupError(message)
    return device


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
    name = dialog('--entry', '--text=Backup name for this folder (letters, numbers, . _ -; spaces become -).',
                  '--entry-text='+suggest_name(config, source))
    if name.returncode:return False
    candidate = add_project(config, clean_name(name.stdout), source, archive)
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


LAUNCHER = Path(__file__).resolve().parent.parent/'cyclops-backup'


def gtk_available():
    return importlib.util.find_spec('gi') is not None


def reload(config_path, config):
    fresh = load_config(config_path, allow_empty=True)
    config.clear();config.update(fresh)


def view_folders(config_path, config):
    """The folders window (GTK); plain Zenity lists if GTK cannot open."""
    from .folders import GTK_MISSING
    save_config(config_path, config)   # the window reads the settings file
    if gtk_available():
        code = subprocess.run([sys.executable, str(LAUNCHER), '--config', str(config_path), 'folders']).returncode
        if code != GTK_MISSING:
            reload(config_path, config);return True
    return zenity_folders(config_path, config)


def zenity_folders(config_path, config):
    while True:
        profile = config.get('profile') or 'Default'
        n = len(config['projects'])
        listing = '\n'.join(f"  • {markup(p['name'])} — {markup(p['source'])}"
                            + (f" ({len(p['excludes'])} left out)" if p.get('excludes') else '')
                            for p in config['projects']) or '  (no folders yet)'
        args = ['--list', f'--text={look.brand("Folders")}\n\nProfile: {look.span(markup(profile), look.EYE, bold=True)}'
                f' — {n} folder{"s" if n != 1 else ""}\n{listing}',
                '--column=key', '--column=Action', '--column=What it does', '--hide-column=1', '--print-column=1',
                '--width=780', '--height=460', '--ok-label=Open', '--cancel-label=Back',
                'profile', f'Profile: {profile}  ▾', 'Switch to another profile, or create / delete one',
                'add', 'Add a folder', 'Register another project or evidence folder',
                'remove', 'Remove a folder', 'Stop backing it up — source and USB copy untouched',
                'exclude', 'Leave out a file or subfolder', 'Skip one item inside a registered folder',
                'include', 'Put an item back', 'Undo a leave-out']
        result = dialog(*args)
        if result.returncode:return True
        choice = result.stdout.strip().split('|')[0]
        try:
            {'profile': lambda: pick_profile(config_path, config), 'add': lambda: add_folder(config_path, config),
             'remove': lambda: remove_folder(config_path, config), 'exclude': lambda: exclude_item(config_path, config),
             'include': lambda: include_item(config_path, config)}.get(choice, lambda: None)()
        except (BackupError, OSError, ValueError) as exc:
            dialog('--error', '--text='+markup(exc), '--width=560')


def pick_profile(config_path, config):
    names = list(config.get('profiles') or [config.get('profile') or 'Default'])
    options = [('use:'+n, ('✔ ' if n == config.get('profile') else '    ')+n) for n in names]
    options.append(('new', '＋ New profile…'))
    if len(names) > 1:options.append(('delete', f'Delete profile “{config.get("profile")}”'))
    choice = choose('Choose a profile. Each profile has its own list of folders; the USB is shared.', options)
    if choice is None:return False
    if choice.startswith('use:'):
        candidate = switch_profile(config, choice[4:])
    elif choice == 'new':
        name = dialog('--entry', '--text=Name for the new profile (it starts with no folders).')
        if name.returncode:return False
        candidate = new_profile(config, name.stdout.strip())
    else:
        if dialog('--question', f'--text=Delete profile “{markup(config.get("profile"))}”?\n\nOnly its list of '
                  'folders is forgotten. Folders on this computer and copies on the USB are not touched.',
                  '--ok-label=Delete profile', '--cancel-label=Keep').returncode:
            return False
        candidate = delete_profile(config, config.get('profile'))
    save_config(config_path, candidate);reload(config_path, config)
    return True


def show_change_preview(config, report_dir, device=None):
    if device is None:
        device, status = usb_status(config)
        if device is None:raise BackupError(status.replace('USB: ', 'Backup USB: ') + '\nChoose or connect the USB first.')
    mode = choose('How thorough should the preview be?', [
        ('quick', 'Quick — compares size and date (fast)'),
        ('exact', 'Exact — compares contents like the real backup (reads every file, slow)')])
    if mode is None:return False
    exact = mode == 'exact'
    report = progress_task('Working out what would be copied — nothing is being copied',
                           lambda p:change_preview(run_view(config), device, p, exact=exact))
    write_reports(report, report_dir)
    saved = write_change_list(report, report_dir)
    text_dialog(changes_text(report, limit=2000) + f'\nFull list saved: {saved}\n')
    return report['ok']


def review_and_run(config, device, report_dir):
    config=run_view(config)
    report=progress_task('Reviewing projects and estimating the transfer',lambda p:preview(config,device,p))
    write_reports(report,report_dir)
    if not report['ok']:
        text_dialog(readable(report));return 2
    if not text_dialog(readable(report),confirm=True):return 1
    result=progress_task('Backing up and verifying — please keep USB plugged in',
                         lambda p:run_backup(config,device,report_dir,p))
    finished(result,config,device)
    return 0 if result['safe_to_eject'] else 2


def finished(result, config, device):
    """Clear end-of-backup popup before the application closes; full report on request."""
    rows = result.get('projects') or []
    profile = markup(config.get('profile') or 'Default')
    if result.get('safe_to_eject'):
        copied = sum(r.get('transfer_bytes') or 0 for r in rows)
        text = (look.brand() + '\n\n' + look.good('✔ Backup successful') + '\n\n'
                f"{len(rows)} folder{'s' if len(rows) != 1 else ''} from profile “{profile}” copied and verified "
                f"with SHA-256 ({size_text(result.get('total_bytes') or 0)} checked, {size_text(copied)} transferred).\n\n"
                + look.good(f'Safe to eject {markup(device.label or "the USB")}') + ' — use Files → Eject, then unplug it.\n\n'
                'Cyclops Backup will now close.')
        kind = '--info'
    else:
        problems = '\n'.join('• '+markup(f) for f in (result.get('failures') or ['Unknown problem.'])[:4])
        text = (look.brand() + '\n\n' + look.bad('✖ Backup did not complete') + '\n\n'+problems+'\n\n'
                + look.warn('Do not unplug the USB yet') + ' unless you need to — it is not marked safe to eject. '
                'Copies that finished are kept and the next run continues from them.\n\n'
                'Cyclops Backup will now close.')
        kind = '--error'
    answer = dialog(kind, '--text='+text, '--width=600', '--ok-label=Close', '--extra-button=View report',
                    '--icon='+str(look.ICON))
    if answer.returncode and answer.stdout.strip() == 'View report':
        text_dialog(readable(result))


def launch(config_path,report_dir):
    """Open straight into the control panel. Nothing is asked before it appears."""
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise BackupError('No desktop display. Use preview or run from a terminal.')
    try:
        config=load_config(config_path,allow_empty=True)
        actions={'usb':lambda:verified_device(config_path,config),
                 'change':lambda:choose_device(config_path,config),
                 'folders':lambda:view_folders(config_path,config)}
        while True:
            choice=main_menu(config)
            if choice is None:return 1
            try:
                if choice in LOCKED:
                    if not config['projects']:
                        raise BackupError(f"Profile “{config.get('profile') or 'Default'}” has no folders yet. "
                                          'Open View folders to add some.')
                    device=verified_device(config_path,config)
                    if device is None:continue   # USB not ready or person went back
                    if choice=='run':return review_and_run(config,device,report_dir)
                    show_change_preview(config,report_dir,device)
                elif choice in actions:
                    actions[choice]()
            except (BackupError,OSError,ValueError) as exc:
                dialog('--error','--text='+markup(exc),'--width=560')
    except (BackupError,OSError,ValueError) as exc:
        dialog('--error','--text='+markup(exc),'--width=560');return 2
    except KeyboardInterrupt:
        dialog('--warning','--text=Backup cancelled. No completed backup result.','--width=560');return 130
