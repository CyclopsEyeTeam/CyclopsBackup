"""End of a backup: a clear popup, then the application closes."""
import os
import subprocess
import unittest
from unittest.mock import patch

from backup import gui
from backup.config import save_config
import test_controls


class FinishTests(unittest.TestCase):
    setUp = test_controls.ControlTests.setUp

    def run_from_panel(self, responses):
        save_config(self.registry, self.config)
        answers = iter(responses)
        seen = []
        def respond(*args):
            seen.append(args)
            code, out = next(answers)
            return subprocess.CompletedProcess([], code, out, '')
        with patch.dict(os.environ, {'DISPLAY': ':fixture'}), \
                patch('backup.gui.resolve_device', return_value=self.device), \
                patch('backup.core.resolve_device', return_value=self.device), \
                patch('backup.gui.progress_task', side_effect=lambda title, task: task(lambda s: None)), \
                patch('backup.gui.dialog', side_effect=respond):
            code = gui.launch(self.registry, self.reports)
        return code, seen

    def test_successful_backup_shows_popup_then_closes(self):
        # panel: Back up — full check -> review: Run Backup -> popup: Close
        code, seen = self.run_from_panel([(0, 'run\n'), (0, ''), (0, '')])
        self.assertEqual(code, 0)
        self.assertTrue((self.usb/'CyclopsBackup/Projects/Demo/README.md').exists())
        popup = seen[-1]
        self.assertIn('--info', popup)
        text = next(a for a in popup if a.startswith('--text='))
        self.assertIn('Backup successful', text)
        self.assertIn('2 folders from profile “Default”', text)
        self.assertIn('Full check: every file SHA-256 verified', text)
        self.assertRegex(text, r'Scanned \d+ · Unchanged 0 · Copied \d+ · Verified \d+ · Written ')
        self.assertIn('Safe to eject Test USB', text)
        self.assertIn('Cyclops Backup will now close', text)
        self.assertIn('--extra-button=View report', popup)
        self.assertEqual(len(seen), 3)   # nothing opens after the popup

    def test_view_report_button_shows_the_full_report(self):
        code, seen = self.run_from_panel([(0, 'run\n'), (0, ''), (1, 'View report\n'), (0, '')])
        self.assertEqual(code, 0)
        self.assertIn('--text-info', seen[-1])

    def test_failed_backup_says_so_and_not_safe_to_eject(self):
        result = {'safe_to_eject': False, 'projects': [], 'failures': ['Demo: rsync failed (23)']}
        with patch('backup.gui.dialog', return_value=subprocess.CompletedProcess([], 0, '', '')) as shown:
            gui.finished(result, self.config, self.device)
        args = shown.call_args.args
        self.assertIn('--error', args)
        text = next(a for a in args if a.startswith('--text='))
        self.assertIn('Backup did not complete', text)
        self.assertIn('rsync failed (23)', text)
        self.assertIn('not marked safe to eject', text)

    def test_result_window_uses_only_options_zenity_4_accepts(self):
        # Regression: --no-cancel made zenity 4 refuse to open the result/preview window at all.
        with patch('backup.gui.dialog', return_value=subprocess.CompletedProcess([], 0, '', '')) as shown:
            gui.text_dialog('report')
        self.assertNotIn('--no-cancel', shown.call_args.args)


if __name__ == '__main__':
    unittest.main()
