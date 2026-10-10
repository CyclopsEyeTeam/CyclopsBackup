import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup.config import Device, BackupError, load_config, resolve_device
from backup.core import preview, run_backup, verify_manifest


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops backup tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'Project with spaces'
        self.source.mkdir()
        (self.source / 'README.md').write_text('project\n')
        (self.source / 'evidence').mkdir()
        (self.source / 'evidence' / 'capture.bin').write_bytes(b'\x00evidence\xff')
        (self.source / 'evidence' / 'name\nwith newline.txt').write_text('names survive')
        (self.source / 'shortcut').symlink_to('README.md')
        self.usb = self.root / 'Test USB with spaces'
        self.usb.mkdir()
        self.device = Device(self.usb, 'test-uuid', 'Test USB', 'ext4')
        self.config = {'version': 1, 'device': {'uuid': 'test-uuid'}, 'projects': [
            {'name': 'Demo', 'source': str(self.source), 'destination': 'Projects/Demo',
             'required_markers': ['README.md'], 'excludes': []}]}
        self.reports = self.root / 'reports'

    def run_backup(self, **kwargs):
        # Only the external block-device boundary is replaced. Real rsync, Git,
        # inventories, hashing, reports and filesystem sync run on tiny fixtures.
        with patch('backup.core.resolve_device', return_value=self.device):
            return run_backup(self.config, self.device, self.reports, **kwargs)

    def test_missing_usb_refuses_without_creating_destination(self):
        with patch('backup.config.devices', return_value=[]):
            with self.assertRaisesRegex(BackupError, 'not mounted'):
                resolve_device(self.config)
        self.assertEqual(list(self.usb.iterdir()), [])

    def test_ambiguous_label_refuses(self):
        row = {'uuid': 'a', 'label': 'Backup', 'mountpoint': '/media/a', 'fstype': 'ext4', 'usb': True}
        with patch('backup.config.devices', return_value=[row, dict(row, uuid='b', mountpoint='/media/b')]):
            with self.assertRaisesRegex(BackupError, 'ambiguous'):
                resolve_device({'device': {'label': 'Backup'}})

    def test_dry_run_never_writes_usb_or_source(self):
        with patch('backup.core.resolve_device', return_value=self.device):
            result = preview(self.config, self.device)
        self.assertTrue(result['ok'])
        self.assertGreater(result['estimated_transfer_bytes'], 0)
        self.assertEqual(list(self.usb.iterdir()), [])
        self.assertFalse((self.source / '.git').exists())

    def test_missing_moved_and_empty_projects_block_before_copy(self):
        self.source.rename(self.root / 'Moved')
        result = self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('missing', ' '.join(result['failures']).lower())
        self.assertFalse((self.usb / 'CyclopsBackup').exists())
        self.source.mkdir()
        result = self.run_backup()
        self.assertFalse(result['safe_to_eject'])

    def test_spaces_git_untracked_evidence_and_sha_verification(self):
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        subprocess.run(['git', '-C', str(self.source), 'add', 'README.md'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        result = self.run_backup()
        self.assertTrue(result['safe_to_eject'], result['failures'])
        p = result['projects'][0]
        dest = self.usb / 'CyclopsBackup/Projects/Demo'
        self.assertTrue((dest / '.git/HEAD').is_file())
        self.assertEqual((dest / 'evidence/capture.bin').read_bytes(), b'\x00evidence\xff')
        self.assertTrue(p['git']['head'])
        self.assertEqual(p['verification']['hashed_files'], p['file_count'])
        self.assertEqual(next(f['sha256'] for f in p['files'] if f['path']=='evidence/capture.bin'),
                         hashlib.sha256(b'\x00evidence\xff').hexdigest())
        self.assertTrue(Path(result['local_json']).exists())
        self.assertTrue((self.usb / 'CyclopsBackup/Manifests' / (result['run_id']+'.json')).exists())
        checked = verify_manifest(Path(result['local_json']), self.usb / 'CyclopsBackup')
        self.assertTrue(checked['ok'], checked)

    def test_incremental_retains_overwritten_and_deleted_files(self):
        first = self.run_backup()
        self.assertTrue(first['safe_to_eject'])
        unchanged = self.run_backup()
        self.assertTrue(unchanged['safe_to_eject'])
        self.assertEqual(unchanged['projects'][0]['transfer_bytes'], 0)
        (self.source / 'README.md').write_text('updated project\n')
        (self.source / 'evidence/capture.bin').unlink()
        updated = self.run_backup()
        self.assertTrue(updated['safe_to_eject'], updated['failures'])
        dest = self.usb / 'CyclopsBackup/Projects/Demo'
        self.assertEqual((dest/'README.md').read_text(), 'updated project\n')
        self.assertTrue((dest/'evidence/capture.bin').exists())
        old = self.usb/'CyclopsBackup/Archives/PreviousVersions'/updated['run_id']/'Demo/README.md'
        self.assertEqual(old.read_text(), 'project\n')

    def test_saved_manifest_detects_corruption(self):
        result = self.run_backup()
        (self.usb/'CyclopsBackup/Projects/Demo/evidence/capture.bin').write_bytes(b'bad')
        checked = verify_manifest(Path(result['local_json']), self.usb/'CyclopsBackup')
        self.assertFalse(checked['ok'])
        self.assertTrue(checked['failures'])

    def test_partial_rsync_failure_never_safe(self):
        original = subprocess.run
        def fail_copy(args, *a, **kw):
            if args[0]=='rsync' and '--dry-run' not in args:
                return subprocess.CompletedProcess(args, 23, '', 'simulated I/O failure')
            return original(args, *a, **kw)
        with patch('backup.core.subprocess.run', side_effect=fail_copy):
            result = self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('rsync', ' '.join(result['failures']))
        self.assertTrue(Path(result['local_json']).exists())
        self.assertFalse(json.loads(Path(result['local_json']).read_text())['ok'])
        self.assertNotIn('SAFE TO EJECT', Path(result['local_text']).read_text())

    def test_destination_symlink_refuses(self):
        outside = self.root/'outside'; outside.mkdir()
        (self.usb/'CyclopsBackup').symlink_to(outside, target_is_directory=True)
        result = self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_destination_and_duplicate_rejected(self):
        path = self.root/'config.json'
        self.config['projects'][0]['destination']='../escape'
        path.write_text(json.dumps(self.config))
        with self.assertRaises(BackupError):load_config(path)
        self.config['projects'][0]['destination']='Projects/Demo'
        self.config['projects'].append(dict(self.config['projects'][0]))
        path.write_text(json.dumps(self.config))
        with self.assertRaises(BackupError):load_config(path)

    def test_device_disappears_before_copy(self):
        calls = 0
        def disconnected(config):
            nonlocal calls
            calls += 1
            if calls > 1:raise BackupError('Expected USB is not mounted')
            return self.device
        with patch('backup.core.resolve_device', side_effect=disconnected):
            result = run_backup(self.config,self.device,self.reports)
        self.assertFalse(result['safe_to_eject'])
        self.assertFalse((self.usb/'CyclopsBackup').exists())

    def test_mountpoint_replaced_cannot_write_to_empty_fallback(self):
        def replace_mount(message):
            if message.startswith('Copying'):
                self.usb.rename(self.root/'detached USB')
                (self.usb/'CyclopsBackup/Projects/Demo').mkdir(parents=True)
                (self.usb/'CyclopsBackup/Logs').mkdir()
        result=self.run_backup(progress=replace_mount)
        self.assertFalse(result['safe_to_eject'])
        self.assertEqual(list((self.usb/'CyclopsBackup/Projects/Demo').iterdir()), [])

    def test_changed_source_during_copy_fails(self):
        original = subprocess.run
        def change_source(args,*a,**kw):
            result=original(args,*a,**kw)
            if args[0]=='rsync' and '--dry-run' not in args:
                (self.source/'new-recording.bin').write_bytes(b'new recording')
            return result
        with patch('backup.core.subprocess.run',side_effect=change_source):
            result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('changed',' '.join(result['failures']))

    def test_sync_failure_has_failed_reports(self):
        with patch('backup.core.sync_device',side_effect=BackupError('simulated sync failure')):
            result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertFalse(json.loads(Path(result['local_json']).read_text())['safe_to_eject'])

    def test_report_failure_never_safe(self):
        self.reports.write_text('not a directory')
        result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('report',' '.join(result['failures']).lower())

    def test_exclusions_keep_ignored_evidence_and_can_not_hide_tracked_files(self):
        (self.source/'node_modules').mkdir()
        (self.source/'node_modules/junk').write_text('regenerable')
        (self.source/'.gitignore').write_text('evidence/\nnode_modules/\n')
        self.config['projects'][0]['excludes']=[{'path':'node_modules','reason':'fixture lockfile'}]
        result=self.run_backup()
        self.assertTrue(result['safe_to_eject'],result['failures'])
        dest=self.usb/'CyclopsBackup/Projects/Demo'
        self.assertFalse((dest/'node_modules').exists())
        self.assertTrue((dest/'evidence/capture.bin').exists())
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'add','-f','node_modules/junk'],check=True)
        result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])

    def test_corruption_with_same_size_and_time_is_repaired(self):
        self.assertTrue(self.run_backup()['safe_to_eject'])
        dest=self.usb/'CyclopsBackup/Projects/Demo/README.md'
        before=dest.stat()
        dest.write_text('corrupt\n')
        os.utime(dest,ns=(before.st_atime_ns,before.st_mtime_ns))
        result=self.run_backup()
        self.assertTrue(result['safe_to_eject'],result['failures'])
        self.assertEqual(dest.read_text(),'project\n')

    def test_cancellation_never_safe(self):
        def cancel(message):
            if message.startswith('Copying'):raise KeyboardInterrupt()
        result=self.run_backup(progress=cancel)
        self.assertFalse(result['safe_to_eject'])
        self.assertEqual(result['status'],'cancelled')

    def test_internal_disk_and_wrong_filesystem_refuse(self):
        row={'uuid':'test-uuid','label':'Test','mountpoint':str(self.usb),'fstype':'ext4','usb':False}
        with patch('backup.config.devices',return_value=[row]):
            with self.assertRaisesRegex(BackupError,'internal'):resolve_device(self.config)
        row.update(usb=True,fstype='exfat')
        with patch('backup.config.devices',return_value=[row]),patch('backup.config.os.path.ismount',return_value=True),patch('backup.config.mount_info',return_value={'uuid':'test-uuid','target':str(self.usb)}):
            with self.assertRaisesRegex(BackupError,'ext4'):resolve_device(self.config)

    def test_cli_missing_usb_dry_run_saves_local_report(self):
        path=self.root/'registry.json'
        self.config['device']['uuid']='absent-fixture-uuid'
        path.write_text(json.dumps(self.config))
        result=subprocess.run(['python3','cyclops-backup','--config',str(path),'--report-dir',str(self.reports),'preview'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertIn('not mounted',result.stdout)
        self.assertTrue(list(self.reports.glob('*.json')))
        self.assertNotIn('SAFE TO EJECT',result.stdout)

    def test_launcher_install_to_temp_home(self):
        import install
        home=self.root/'test home';home.mkdir()
        created=install.install(Path.cwd(),home)
        menu=home/'.local/share/applications/cyclops-backup.desktop'
        desktop=home/'Desktop/Cyclops Backup.desktop'
        self.assertTrue(menu.exists());self.assertTrue(desktop.exists())
        self.assertTrue(os.access(desktop,os.X_OK))
        result=subprocess.run(['desktop-file-validate',str(menu)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_worktree_preserves_common_git_when_main_is_registered(self):
        main=self.root/'main repo'
        subprocess.run(['git','init','-q',str(main)],check=True)
        (main/'README.md').write_text('main')
        subprocess.run(['git','-C',str(main),'add','.'],check=True)
        subprocess.run(['git','-C',str(main),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
        worktree=self.root/'linked worktree'
        subprocess.run(['git','-C',str(main),'worktree','add','-q','-b','test-branch',str(worktree)],check=True)
        self.config['projects']=[{'name':'Main','source':str(main),'destination':'Projects/Main','excludes':[]},
                                 {'name':'Linked','source':str(worktree),'destination':'Projects/Linked','excludes':[]}]
        result=self.run_backup()
        self.assertTrue(result['safe_to_eject'],result['failures'])
        self.assertEqual(result['projects'][1]['git']['branch'],'test-branch')
        self.assertTrue((self.usb/'CyclopsBackup/Projects/Main/.git/worktrees').is_dir())
        self.assertTrue((self.usb/'CyclopsBackup/Projects/Linked/.git').is_file())
        self.config['projects'].pop(0)
        result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])

    def test_gui_cancel_review_leaves_usb_untouched(self):
        from backup import gui
        cfg=self.root/'registry.json';cfg.write_text(json.dumps(self.config))
        # Desktop dialog response is the external human boundary. The actual
        # preview/engine runs, using a real temporary destination and rsync.
        with patch.dict(os.environ,{'DISPLAY':':fixture'}),patch('backup.gui.resolve_device',return_value=self.device),patch('backup.core.resolve_device',return_value=self.device),patch('backup.gui.progress_task',side_effect=lambda title,task:task(lambda s:None)),patch('backup.gui.dialog',return_value=subprocess.CompletedProcess([],1,'','')):
            self.assertEqual(gui.launch(cfg,self.reports),1)
        self.assertEqual(list(self.usb.iterdir()),[])

    def test_gui_approved_review_runs_real_fixture_backup(self):
        from backup import gui
        cfg=self.root/'registry.json';cfg.write_text(json.dumps(self.config))
        with patch.dict(os.environ,{'DISPLAY':':fixture'}),patch('backup.gui.resolve_device',return_value=self.device),patch('backup.core.resolve_device',return_value=self.device),patch('backup.gui.progress_task',side_effect=lambda title,task:task(lambda s:None)),patch('backup.gui.dialog',return_value=subprocess.CompletedProcess([],0,'','')):
            self.assertEqual(gui.launch(cfg,self.reports),0)
        self.assertEqual((self.usb/'CyclopsBackup/Projects/Demo/README.md').read_text(),'project\n')
        completed=[json.loads(p.read_text()) for p in self.reports.glob('*.json')]
        self.assertTrue(any(p['status']=='complete' and p['ok'] for p in completed))

    def test_preview_manifest_is_not_a_verified_backup(self):
        result=preview(self.config)
        path=self.root/'preview.json';path.write_text(json.dumps(result))
        checked=verify_manifest(path,self.usb/'CyclopsBackup')
        self.assertFalse(checked['ok'])

    def test_incomplete_manifest_cannot_pass_with_zero_hashes(self):
        result=self.run_backup()
        path=Path(result['local_json'])
        manifest=json.loads(path.read_text())
        manifest['projects'][0]['files']=[]
        path.write_text(json.dumps(manifest))
        self.assertFalse(verify_manifest(path,self.usb/'CyclopsBackup')['ok'])

    def test_manifest_missing_project_or_duplicate_paths_refuses(self):
        result=self.run_backup();path=Path(result['local_json'])
        manifest=json.loads(path.read_text())
        original=json.loads(path.read_text())
        manifest['projects']=[];path.write_text(json.dumps(manifest))
        self.assertFalse(verify_manifest(path,self.usb/'CyclopsBackup')['ok'])
        manifest=original
        manifest['projects'][0]['files'].append(dict(manifest['projects'][0]['files'][0]))
        path.write_text(json.dumps(manifest))
        self.assertFalse(verify_manifest(path,self.usb/'CyclopsBackup')['ok'])

    def test_non_git_nested_repository_records_branch_and_head(self):
        nested=self.source/'evidence/nested repo'
        subprocess.run(['git','init','-q',str(nested)],check=True)
        (nested/'file').write_text('nested')
        subprocess.run(['git','-C',str(nested),'add','.'],check=True)
        subprocess.run(['git','-C',str(nested),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
        result=self.run_backup()
        self.assertTrue(result['safe_to_eject'],result['failures'])
        repo=result['projects'][0]['git_repositories'][0]
        self.assertEqual(repo['path'],'evidence/nested repo')
        self.assertTrue(repo['head'])

    def test_hard_links_and_permissions_preserved(self):
        os.link(self.source/'README.md',self.source/'hardlink')
        (self.source/'README.md').chmod(0o750)
        result=self.run_backup()
        self.assertTrue(result['safe_to_eject'],result['failures'])
        dest=self.usb/'CyclopsBackup/Projects/Demo'
        self.assertEqual((dest/'README.md').stat().st_ino,(dest/'hardlink').stat().st_ino)
        self.assertEqual((dest/'README.md').stat().st_mode & 0o777,0o750)

    def test_external_git_object_symlink_cannot_omit_history(self):
        import shutil
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'add','README.md'],check=True)
        subprocess.run(['git','-C',str(self.source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
        outside=self.root/'external objects'
        shutil.move(self.source/'.git/objects',outside)
        (self.source/'.git/objects').symlink_to(outside,target_is_directory=True)
        result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('Git',' '.join(result['failures']))

    def test_same_size_mtime_source_replacement_is_caught_and_recopied(self):
        # A same-size, same-time swap after Demo was copied is caught by the final
        # snapshot; Demo gets one fresh checksum retry, so the USB holds the new
        # content and never a stale copy.
        second=self.root/'second project';second.mkdir();(second/'data').write_text('two')
        self.config['projects'].append({'name':'Two','source':str(second),'destination':'Projects/Two','excludes':[]})
        def replace_source(message):
            if message.startswith('Copying Two'):
                p=self.source/'README.md';before=p.stat()
                replacement=self.source/'replacement'
                replacement.write_text('evil!!!\n')
                os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns))
                replacement.replace(p)
        result=self.run_backup(progress=replace_source)
        self.assertTrue(result['safe_to_eject'], result['failures'])
        self.assertEqual((self.usb/'CyclopsBackup/Projects/Demo/README.md').read_text(), 'evil!!!\n')
        self.assertEqual(result['projects'][0]['retries'], ['Source changed before final verification.'])

    def test_no_durable_eject_claim_before_final_flush(self):
        with patch('backup.core.sync_device',side_effect=BackupError('simulated final flush failure')):
            result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        for path in (self.usb/'CyclopsBackup/Manifests').glob('*.json'):
            if path.name.endswith('.registry.json'):continue
            self.assertFalse(json.loads(path.read_text())['safe_to_eject'])
        self.assertTrue(self.run_backup()['safe_to_eject'])
        for path in (self.usb/'CyclopsBackup/Logs').glob('*.txt'):
            self.assertNotIn('SAFE TO EJECT',path.read_text())

    def test_real_rsync_unreadable_evidence_produces_failure(self):
        if os.getuid()==0:self.skipTest('File read denial needs an ordinary user')
        protected=self.source/'evidence/capture.bin'
        protected.chmod(0)
        try:result=self.run_backup()
        finally:protected.chmod(0o600)
        self.assertFalse(result['safe_to_eject'])
        self.assertEqual(result['projects'][0]['rsync_exit'],23)
        self.assertTrue(Path(result['local_json']).is_file())

    def test_insufficient_space_blocks_before_any_usb_write(self):
        import shutil
        usage=shutil.disk_usage(self.usb)
        with patch('backup.core.shutil.disk_usage',return_value=type(usage)(100,99,1)):
            result=self.run_backup()
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('space',' '.join(result['failures']))
        self.assertEqual(list(self.usb.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
