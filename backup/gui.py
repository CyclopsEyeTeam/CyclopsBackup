"""Small Zenity wizard; engine and CLI remain usable without a desktop."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile
import re

from .config import BackupError, devices, load_config, resolve_device, save_config
from .core import preview, run_backup
from .report import readable, write_reports
from .setup import add_project


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
        device=resolve_device(config) if config.get('device',{}).get('uuid') or config.get('device',{}).get('label') else choose_device(config_path,config)
        if device is None:return 1
        report=progress_task('Reviewing projects and estimating the transfer',lambda p:preview(config,device,p))
        write_reports(report,report_dir)
        if not report['ok']:
            text_dialog(readable(report));return 2
        if not text_dialog(readable(report),confirm=True):return 1
        result=progress_task('Backing up and verifying — please keep USB plugged in',
                             lambda p:run_backup(config,device,report_dir,p))
        text_dialog(readable(result));return 0 if result['safe_to_eject'] else 2
    except (BackupError,OSError,ValueError) as exc:
        dialog('--error','--text='+str(exc),'--width=560');return 2
    except KeyboardInterrupt:
        dialog('--warning','--text=Backup cancelled. No completed backup result.','--width=560');return 130
