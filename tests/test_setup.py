"""Public setup must never start a backup or bundle a machine's settings."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup import cli, gui
from backup.config import BackupError, Device, load_config
from backup.core import preview, run_backup, verify_manifest
from backup.setup import add_project


class PublicSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cyclops-public-setup-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'My project with spaces'
        self.source.mkdir()
        (self.source/'data.bin').write_bytes(b'important evidence')
        self.config = self.root/'settings/registry.json'

    def command(self, *args):
        return subprocess.run(['python3', 'cyclops-backup', '--config', str(self.config),
                               *args], capture_output=True, text=True)

    def test_add_project_creates_private_settings_with_no_exclusions(self):
        result = self.command('project-add', '--source', str(self.source), '--name', 'MyProject')
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = load_config(self.config)
        self.assertEqual(cfg['device'], {})
        self.assertEqual(cfg['projects'][0]['source'], str(self.source))
        self.assertEqual(cfg['projects'][0]['destination'], 'Projects/MyProject')
        self.assertEqual(cfg['projects'][0]['excludes'], [])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.source.iterdir()), [self.source/'data.bin'])

    def test_evidence_registration_and_duplicate_do_not_overwrite_settings(self):
        result = self.command('project-add', '--source', str(self.source), '--name', 'Evidence', '--archive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load_config(self.config)['projects'][0]['destination'], 'Archives/Evidence')
        before = self.config.read_bytes()
        duplicate = self.command('project-add', '--source', str(self.source), '--name', 'Evidence')
        self.assertEqual(duplicate.returncode, 2)
        self.assertEqual(self.config.read_bytes(), before)

    def test_missing_empty_and_symlink_sources_do_not_save_configuration(self):
        empty = self.root/'empty'; empty.mkdir()
        link = self.root/'link'; link.symlink_to(self.source)
        for path in (self.root/'missing', empty, link):
            with self.subTest(path=path):
                result = self.command('project-add', '--source', str(path), '--name', 'Unsafe')
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.config.exists())

    def test_default_setup_uses_user_config_location_without_writing_package(self):
        before = sorted(Path('.').glob('registry*.json'))
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config')}, clear=False):
            path = cli.default_config_path()
        self.assertEqual(path, self.root/'config/cyclops-backup/registry.json')
        self.assertEqual(sorted(Path('.').glob('registry*.json')), before)

    def test_unconfigured_preview_refuses_and_does_not_create_settings(self):
        result = self.command('--report-dir', str(self.root/'reports'), 'preview')
        self.assertEqual(result.returncode, 2)
        self.assertIn('project', result.stderr.lower())
        self.assertFalse(self.config.exists())
        self.assertNotIn('SAFE TO EJECT', result.stdout)

    def test_gui_first_run_cancellation_does_not_create_settings(self):
        # Only the external display and human response are replaced.
        with patch.dict(os.environ, {'DISPLAY': ':fixture'}), patch(
                'backup.gui.dialog', return_value=subprocess.CompletedProcess([], 1, '', '')):
            self.assertEqual(gui.launch(self.config, self.root/'reports'), 1)
        self.assertFalse(self.config.exists())

    def test_gui_project_choice_saves_the_real_selected_folder(self):
        cfg = {'version': 1, 'device': {}, 'projects': []}
        responses = iter([str(self.source)+'\n', 'MyProject\n'])
        def choose(*args):
            return subprocess.CompletedProcess([], 0, next(responses), '')
        with patch('backup.gui.dialog', side_effect=choose):
            self.assertTrue(gui.choose_project(self.config, cfg))
        saved = load_config(self.config)
        self.assertEqual(saved['projects'][0]['source'], str(self.source))
        self.assertEqual(saved['projects'][0]['destination'], 'Projects/MyProject')
        self.assertEqual(saved['device'], {})
        self.assertEqual((self.source/'data.bin').read_bytes(), b'important evidence')

    def test_parent_alias_cannot_register_the_same_source_twice(self):
        first = self.command('project-add', '--source', str(self.source), '--name', 'First')
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.config.read_bytes()
        alias = self.root/'parent alias'; alias.symlink_to(self.root, target_is_directory=True)
        duplicate = self.command('project-add', '--source', str(alias/self.source.name), '--name', 'Second')
        self.assertEqual(duplicate.returncode, 2, duplicate.stderr)
        self.assertEqual(self.config.read_bytes(), before)

    def test_alias_into_backup_usb_cannot_bypass_overlap_refusal(self):
        usb = self.root/'USB'; usb.mkdir()
        project = usb/'Evidence'; project.mkdir(); (project/'data').write_text('evidence')
        alias = self.root/'USB alias'; alias.symlink_to(usb, target_is_directory=True)
        cfg = add_project({'version': 1, 'device': {}, 'projects': []}, 'Unsafe', alias/'Evidence')
        result = preview(cfg, Device(usb, 'fixture-uuid', 'Fixture USB', 'ext4'))
        self.assertFalse(result['ok'])
        self.assertIn('overlap', ' '.join(result['failures']).lower())
        self.assertFalse((usb/'CyclopsBackup').exists())

    def test_hand_edited_alias_source_is_refused(self):
        alias = self.root/'parent alias'; alias.symlink_to(self.root, target_is_directory=True)
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({'version': 1, 'device': {}, 'projects': [
            {'name': 'Alias', 'source': str(alias/self.source.name), 'destination': 'Projects/Alias', 'excludes': []}]}))
        with self.assertRaisesRegex(BackupError, 'canonical'):
            load_config(self.config)

    def test_saved_manifest_does_not_depend_on_original_source_path(self):
        usb = self.root/'Fixture USB'; usb.mkdir()
        device = Device(usb, 'fixture-uuid', 'Fixture USB', 'ext4')
        cfg = add_project({'version': 1, 'device': {'uuid': 'fixture-uuid'}, 'projects': []},
                          'Demo', self.source)
        with patch('backup.core.resolve_device', return_value=device):
            result = run_backup(cfg, device, self.root/'reports')
        self.assertTrue(result['safe_to_eject'], result['failures'])
        moved = self.root/'Moved original'
        self.source.rename(moved)
        self.source.symlink_to(moved, target_is_directory=True)
        checked = verify_manifest(Path(result['local_json']), usb/'CyclopsBackup')
        self.assertTrue(checked['ok'], checked['failures'])


if __name__ == '__main__':
    unittest.main()
