"""Launch opens the control panel straight away; backup and preview wait for a verified USB."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup import gui
from backup.config import BackupError, Device, load_config, save_config

MISSING = BackupError('Expected USB is not mounted. Plug it in and open it in Files, then retry.')


class FakeWindow:
    """Stands in for the zenity progress window: open until closed or 'Back' is pressed."""
    def __init__(self, back_after=None):
        self.back_after, self.polls, self.closed = back_after, 0, False
        self.stdin = self
    def poll(self):
        self.polls += 1
        if self.closed or (self.back_after is not None and self.polls > self.back_after):return 1
        return None
    def write(self, _):pass
    def flush(self):pass
    def close(self):self.closed = True
    def wait(self, timeout=None):return 0


class UsbGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops usb gate ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'My Project'
        self.source.mkdir()
        (self.source/'README.md').write_text('project\n')
        self.usb = self.root/'USB'
        self.usb.mkdir()
        self.device = Device(self.usb, 'test-uuid', 'Test USB', 'ext4')
        self.registry = self.root/'config/registry.json'
        self.reports = self.root/'reports'

    def registered(self, device=True):
        cfg = {'version': 1, 'device': {'uuid': 'test-uuid', 'label': 'Test USB'} if device else {},
               'projects': [{'name': 'Demo', 'source': str(self.source),
                             'destination': 'Projects/Demo', 'excludes': []}]}
        save_config(self.registry, cfg)

    def session(self, responses, usb_rows=(), resolve=MISSING, wait=False):
        answers = iter(responses)
        seen = []
        def respond(*args):
            seen.append(args)
            code, out = next(answers)
            return subprocess.CompletedProcess([], code, out, '')
        def resolve_device(config):
            if isinstance(resolve, Exception):raise resolve
            return resolve
        with patch.dict(os.environ, {'DISPLAY': ':fixture'}), \
                patch('backup.gui.devices', return_value=list(usb_rows)), \
                patch('backup.gui.resolve_device', side_effect=resolve_device), \
                patch('backup.core.resolve_device', side_effect=resolve_device), \
                patch('backup.gui.gtk_available', return_value=False), \
                patch('backup.gui.wait_for_usb', return_value=wait) as waited, \
                patch('backup.gui.progress_task', side_effect=lambda title, task: task(lambda s: None)), \
                patch('backup.gui.dialog', side_effect=respond):
            code = gui.launch(self.registry, self.reports)
        return code, seen, waited

    def test_first_launch_without_usb_opens_panel_not_folder_wizard(self):
        code, seen, _ = self.session([(1, '')])
        self.assertEqual(code, 1)
        self.assertEqual(len(seen), 1)
        self.assertIn('--list', seen[0])
        self.assertNotIn('--file-selection', seen[0])
        text = next(a for a in seen[0] if a.startswith('--text='))
        self.assertIn('No backup USB connected', text)
        self.assertIn('locked', text)
        self.assertFalse(self.registry.exists())

    def test_locked_rows_are_marked_and_connect_is_first(self):
        self.registered()
        _, seen, _ = self.session([(1, '')])
        rows = seen[0]
        self.assertIn('Review and back up  🔒', rows)
        self.assertIn('Preview what will be copied  🔒', rows)
        self.assertLess(rows.index('Connect backup USB'), rows.index('Review and back up  🔒'))
        self.assertIn('Use a different USB', rows)
        self.assertIn('View folders', rows)
        self.assertNotIn('Add a folder', rows)

    def test_verified_usb_unlocks_rows(self):
        self.registered()
        _, seen, _ = self.session([(1, '')], resolve=self.device)
        text = next(a for a in seen[0] if a.startswith('--text='))
        self.assertIn('USB verified: Test USB', text)
        self.assertIn('Review and back up', seen[0])
        self.assertNotIn('Review and back up  🔒', seen[0])

    def test_preview_while_locked_waits_and_back_returns_to_panel(self):
        self.registered()
        code, seen, waited = self.session([(0, 'preview\n'), (1, '')], wait=False)
        self.assertEqual(code, 1)
        waited.assert_called_once()
        self.assertFalse(list(self.reports.glob('*.changes.txt')) if self.reports.exists() else [])
        self.assertFalse((self.usb/'CyclopsBackup').exists())
        self.assertEqual(sum('--list' in a for a in seen), 2)   # panel shown again

    def test_run_while_locked_never_copies(self):
        self.registered()
        code, _, _ = self.session([(0, 'run\n'), (0, ''), (1, '')], wait=False)
        self.assertEqual(code, 1)
        self.assertFalse((self.usb/'CyclopsBackup').exists())

    def test_enter_on_locked_panel_means_connect_not_backup(self):
        self.registered()
        code, _, waited = self.session([(0, ''), (1, '')], wait=False)
        self.assertEqual(code, 1)
        waited.assert_called_once()
        self.assertFalse((self.usb/'CyclopsBackup').exists())

    def test_folders_can_be_set_up_without_usb(self):
        code, seen, _ = self.session([
            (0, 'folders\n'), (0, 'add\n'), (0, 'project\n'), (0, str(self.source)+'\n'), (0, 'Demo\n'),
            (1, ''), (1, '')])
        self.assertEqual(code, 1)
        saved = load_config(self.registry)
        self.assertEqual(saved['projects'][0]['source'], str(self.source))
        self.assertEqual(saved['device'], {})
        self.assertIn('Demo', next(a for a in seen[-1] if a.startswith('--text=')))

    def test_backup_without_folders_explains_and_stays_open(self):
        code, seen, waited = self.session([(0, 'run\n'), (0, ''), (1, '')], resolve=self.device)
        self.assertEqual(code, 1)
        self.assertTrue(any('--error' in a for a in seen))
        waited.assert_not_called()

    def test_unsupported_usb_is_named_and_stays_locked(self):
        rows = [{'uuid': 'AB-12', 'label': 'STICK', 'fstype': 'vfat', 'mountpoint': '/media/s', 'usb': True}]
        _, seen, _ = self.session([(1, '')], usb_rows=rows)
        text = next(a for a in seen[0] if a.startswith('--text='))
        self.assertIn('vfat', text)
        self.assertIn('Review and back up  🔒', seen[0])

    def test_plugged_in_usb_offered_for_choosing_then_preview_runs(self):
        self.registered(device=False)
        rows = [{'uuid': 'test-uuid', 'label': 'Test USB', 'fstype': 'ext4', 'mountpoint': str(self.usb), 'usb': True}]
        code, seen, _ = self.session([
            (0, 'preview\n'), (0, 'test-uuid\n'), (0, 'quick\n'), (0, ''), (1, '')],
            usb_rows=rows, resolve=self.device)
        self.assertEqual(code, 1)
        self.assertIn('Choose backup USB', seen[0])
        self.assertEqual(load_config(self.registry)['device']['uuid'], 'test-uuid')
        self.assertTrue(list(self.reports.glob('*.changes.txt')))
        self.assertFalse((self.usb/'CyclopsBackup').exists())


class WaitWindowTests(unittest.TestCase):
    def test_window_closes_itself_once_usb_is_verified(self):
        window = FakeWindow()
        checks = iter([(None, 'missing', ''), (None, 'missing', ''), ('dev', 'verified', '')])
        with patch('backup.gui.subprocess.Popen', return_value=window), \
                patch('backup.gui.usb_check', side_effect=lambda c: next(checks)), \
                patch('backup.gui.time.sleep'):
            self.assertTrue(gui.wait_for_usb({'device': {'uuid': 'u'}}))
        self.assertTrue(window.closed)

    def test_back_button_returns_false(self):
        window = FakeWindow(back_after=2)
        with patch('backup.gui.subprocess.Popen', return_value=window), \
                patch('backup.gui.usb_check', return_value=(None, 'missing', '')), \
                patch('backup.gui.time.sleep'):
            self.assertFalse(gui.wait_for_usb({'device': {'uuid': 'u'}}))

    def test_already_connected_needs_no_window(self):
        with patch('backup.gui.subprocess.Popen') as popen, \
                patch('backup.gui.usb_check', return_value=('dev', 'verified', '')):
            self.assertTrue(gui.wait_for_usb({'device': {'uuid': 'u'}}))
        popen.assert_not_called()

    def test_any_usb_mode_waits_for_a_supported_filesystem(self):
        window = FakeWindow()
        seq = iter([[{'fstype': 'vfat', 'usb': True}], [{'fstype': 'ext4', 'usb': True}]])
        with patch('backup.gui.subprocess.Popen', return_value=window), \
                patch('backup.gui.usb_rows', side_effect=lambda: next(seq)), \
                patch('backup.gui.time.sleep'):
            self.assertTrue(gui.wait_for_usb({'device': {}}, any_usb=True))


if __name__ == '__main__':
    unittest.main()
