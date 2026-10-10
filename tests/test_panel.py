"""Control panel 1.1: quick-check row, Back up now after a preview, compact header, honest progress lines."""
import io
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from backup import cli, gui
from backup.config import save_config
import test_controls


class PanelTests(unittest.TestCase):
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

    def text_of(self, args):
        return next(a for a in args if a.startswith('--text='))

    def test_quick_check_row_runs_a_quick_check(self):
        self.backup()                                            # something on the USB already
        (self.source/'README.md').write_text('edited\n')
        code, seen = self.run_from_panel([(0, 'quick\n'), (0, ''), (0, '')])
        self.assertEqual(code, 0)
        review = Path(next(a for a in seen[1] if a.startswith('--filename='))[len('--filename='):])
        self.assertTrue(any('quick check' in a for a in seen[1]))
        popup = self.text_of(seen[-1])
        self.assertIn('Quick check:', popup)
        self.assertIn('Copied 1 ', popup)
        self.assertFalse(review.exists())                        # temp review file is cleaned up

    def test_preview_then_back_up_now_runs_a_reviewed_full_backup(self):
        code, seen = self.run_from_panel([
            (0, 'preview\n'), (0, 'quick\n'),                    # preview, quick comparison
            (1, 'Back up now\n'),                                # preview window: Back up now
            (0, ''),                                             # review: Run Backup (still asked)
            (0, '')])                                            # success popup: Close
        self.assertEqual(code, 0)
        self.assertIn('--extra-button=Back up now', seen[2])
        self.assertIn('--checkbox=I have reviewed these folders and want to back them up (full check)', seen[3])
        self.assertIn('Full check:', self.text_of(seen[-1]))
        self.assertTrue((self.usb/'CyclopsBackup/Projects/Demo/README.md').exists())

    def test_preview_close_returns_to_panel_without_copying(self):
        code, _ = self.run_from_panel([(0, 'preview\n'), (0, 'quick\n'), (0, ''), (1, '')])
        self.assertEqual(code, 1)
        self.assertFalse((self.usb/'CyclopsBackup/Projects').exists())

    def test_header_stays_short_with_many_folders(self):
        config = dict(self.config, projects=[dict(self.config['projects'][0], name=f'Folder{i:02}') for i in range(31)])
        summary = gui.folder_summary(config['projects'])
        self.assertEqual(summary, '31 folders — Folder00, Folder01, Folder02, Folder03 +27 more')
        with patch('backup.gui.usb_check', return_value=(self.device, 'verified', 'USB verified')), \
                patch('backup.gui.dialog', return_value=subprocess.CompletedProcess([], 1, '', '')) as shown:
            gui.main_menu(config)
        longest = max(len(line) for line in self.text_of(shown.call_args.args).split('\n'))
        self.assertLess(longest, 160)
        self.assertIn('--width=780', shown.call_args.args)

    def test_progress_window_keeps_stage_and_counter_lines(self):
        written = io.StringIO()
        class Window:
            stdin = written
            def poll(self):return None
            def wait(self, timeout=None):return 0
        written.close = lambda: None
        with patch('backup.gui.subprocess.Popen', return_value=Window()):
            gui.progress_task('x', lambda progress: progress('Copying Demo\nScanned 3 · Copied 1'))
        self.assertIn('#Copying Demo\\nScanned 3 · Copied 1\n', written.getvalue())

    def test_terminal_run_quick(self):
        save_config(self.registry, self.config)
        out = io.StringIO()
        with patch('backup.cli.resolve_device', return_value=self.device), \
                patch('backup.core.resolve_device', return_value=self.device), \
                patch('sys.stdout', out):
            code = cli.main(['--config', str(self.registry), '--report-dir', str(self.reports), 'run', '--yes', '--quick'])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('Quick check', out.getvalue())
        self.assertIn('Check: quick', out.getvalue())

    def backup(self):
        from backup.core import run_backup
        with patch('backup.core.resolve_device', return_value=self.device):
            self.assertTrue(run_backup(self.config, self.device, self.reports)['safe_to_eject'])


if __name__ == '__main__':
    unittest.main()
