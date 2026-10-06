"""Folder/USB controls and copy preview. Real rsync/Git on tiny fixtures."""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup import cli, gui
from backup.config import BackupError, Device, load_config, save_config
from backup.core import change_preview, parse_itemized, run_backup
from backup.report import changes_text
from backup.setup import add_exclusion, add_project, remove_exclusion, remove_project


def tree(path):
    """Every path and its bytes below a folder, to prove nothing was written."""
    path = Path(path)
    if not path.exists():return None
    return sorted((str(p.relative_to(path)), p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                  for p in path.rglob('*'))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops control tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'My Project'
        (self.source/'cache').mkdir(parents=True)
        (self.source/'README.md').write_text('project\n')
        (self.source/'cache/big.tmp').write_bytes(b'x'*100)
        (self.source/'notes with spaces.txt').write_text('notes\n')
        self.other = self.root/'Evidence'
        self.other.mkdir()
        (self.other/'capture.bin').write_bytes(b'\x00evidence')
        self.usb = self.root/'USB'
        self.usb.mkdir()
        self.device = Device(self.usb, 'test-uuid', 'Test USB', 'ext4')
        cfg = {'version': 1, 'device': {'uuid': 'test-uuid', 'label': 'Test USB'}, 'projects': []}
        cfg = add_project(cfg, 'Demo', self.source)
        self.config = add_project(cfg, 'Evidence', self.other, archive=True)
        self.registry = self.root/'settings/registry.json'
        self.reports = self.root/'reports'

    def backup(self):
        with patch('backup.core.resolve_device', return_value=self.device):
            result = run_backup(self.config, self.device, self.reports)
        self.assertTrue(result['safe_to_eject'], result['failures'])

    def changes(self, exact=False):
        with patch('backup.core.resolve_device', return_value=self.device):
            report = change_preview(self.config, self.device, exact=exact)
        self.assertTrue(report['ok'], report['failures'])
        return {r['name']: {(c['action'], c['path']) for c in r['changes']} for r in report['projects']}, report

    # Folder controls

    def test_remove_folder_keeps_source_and_existing_usb_copy(self):
        self.backup()
        before_usb = tree(self.usb/'CyclopsBackup/Archives/Evidence')
        cfg = remove_project(self.config, 'Evidence')
        self.assertEqual([p['name'] for p in cfg['projects']], ['Demo'])
        self.assertEqual((self.other/'capture.bin').read_bytes(), b'\x00evidence')
        self.assertEqual(tree(self.usb/'CyclopsBackup/Archives/Evidence'), before_usb)
        with self.assertRaisesRegex(BackupError, 'No registered folder'):
            remove_project(cfg, 'Evidence')

    def test_removing_last_folder_saves_an_empty_set(self):
        cfg = remove_project(remove_project(self.config, 'Demo'), 'Evidence')
        save_config(self.registry, cfg)
        self.assertEqual(load_config(self.registry, allow_empty=True)['projects'], [])

    # Leave-out / put-back controls

    def test_exclude_by_absolute_or_relative_path_and_put_back(self):
        cfg = add_exclusion(self.config, 'Demo', self.source/'cache', 'rebuildable cache')
        self.assertEqual(cfg['projects'][0]['excludes'], [{'path': 'cache', 'reason': 'rebuildable cache'}])
        cfg = add_exclusion(cfg, 'Demo', 'notes with spaces.txt', 'copy kept elsewhere')
        self.assertEqual(len(cfg['projects'][0]['excludes']), 2)
        cfg = remove_exclusion(cfg, 'Demo', 'cache')
        self.assertEqual([e['path'] for e in cfg['projects'][0]['excludes']], ['notes with spaces.txt'])
        self.assertEqual(self.config['projects'][0]['excludes'], [])  # original not mutated

    def test_unsafe_exclusions_are_refused(self):
        (self.source/'odd[1].txt').write_text('x')
        cases = [(self.source/'cache', ''),                 # no reason
                 (self.other/'capture.bin', 'outside'),     # different folder
                 (self.source, 'whole folder'),             # the root itself
                 ('missing.txt', 'nothing there'),
                 ('../Evidence', 'escape'),
                 ('odd[1].txt', 'wildcard name'),
                 ('.git', 'metadata')]
        for path, reason in cases:
            with self.subTest(path=path), self.assertRaises(BackupError):
                add_exclusion(self.config, 'Demo', path, reason)
        cfg = add_exclusion(self.config, 'Demo', 'cache', 'cache')
        with self.assertRaisesRegex(BackupError, 'already'):
            add_exclusion(cfg, 'Demo', 'cache', 'again')
        with self.assertRaisesRegex(BackupError, 'not excluded'):
            remove_exclusion(cfg, 'Demo', 'README.md')

    def test_git_tracked_items_cannot_be_left_out(self):
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        subprocess.run(['git', '-C', str(self.source), 'add', 'README.md'], check=True)
        with self.assertRaisesRegex(BackupError, 'tracked'):
            add_exclusion(self.config, 'Demo', 'README.md', 'try')
        add_exclusion(self.config, 'Demo', 'cache', 'untracked cache is fine')

    def test_symlink_inside_folder_is_excluded_as_itself(self):
        (self.source/'link').symlink_to(self.other)
        cfg = add_exclusion(self.config, 'Demo', self.source/'link', 'points elsewhere')
        self.assertEqual(cfg['projects'][0]['excludes'][-1]['path'], 'link')

    # Copy preview

    def test_preview_on_fresh_usb_lists_everything_as_new_and_writes_nothing(self):
        before_usb, before_src = tree(self.usb), tree(self.source)
        changes, report = self.changes()
        self.assertIn(('new', 'README.md'), changes['Demo'])
        self.assertIn(('new', 'cache/big.tmp'), changes['Demo'])
        self.assertIn(('new', 'capture.bin'), changes['Evidence'])
        self.assertEqual(tree(self.usb), before_usb)
        self.assertEqual(tree(self.source), before_src)
        self.assertFalse((self.usb/'CyclopsBackup').exists())
        size = next(c for c in report['projects'][0]['changes'] if c['path'] == 'cache/big.tmp')['size']
        self.assertEqual(size, 100)

    def test_preview_after_backup_shows_only_new_and_changed(self):
        self.backup()
        changes, report = self.changes()
        self.assertEqual(changes, {'Demo': set(), 'Evidence': set()})
        self.assertIn('Already up to date', changes_text(report))
        (self.source/'README.md').write_text('project, edited\n')
        (self.source/'fresh.txt').write_text('new\n')
        before_usb = tree(self.usb)
        changes, report = self.changes()
        self.assertEqual(changes['Demo'], {('changed', 'README.md'), ('new', 'fresh.txt')})
        self.assertEqual(changes['Evidence'], set())
        self.assertEqual(tree(self.usb), before_usb)
        text = changes_text(report)
        self.assertIn('CHANGED README.md', text)
        self.assertIn('NEW     fresh.txt', text)

    def test_excluded_items_do_not_appear_in_preview(self):
        self.config = add_exclusion(self.config, 'Demo', 'cache', 'rebuildable')
        changes, report = self.changes()
        self.assertFalse(any(p.startswith('cache') for _, p in changes['Demo']))
        self.assertIn('Left out: cache — rebuildable', changes_text(report))

    def test_exact_preview_catches_same_size_and_time_corruption(self):
        self.backup()
        dest = self.usb/'CyclopsBackup/Projects/Demo/README.md'
        st = dest.stat()
        dest.write_text('corrupt\n')
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))
        quick, _ = self.changes()
        exact, _ = self.changes(exact=True)
        self.assertNotIn(('changed', 'README.md'), quick['Demo'])
        self.assertIn(('changed', 'README.md'), exact['Demo'])
        self.assertEqual(dest.read_text(), 'corrupt\n')  # preview repaired nothing

    def test_preview_blocks_on_the_same_problems_as_review(self):
        (self.other/'capture.bin').unlink()
        with patch('backup.core.resolve_device', return_value=self.device):
            report = change_preview(self.config, self.device)
        self.assertFalse(report['ok'])
        self.assertIn('PREVIEW BLOCKED', changes_text(report))

    def test_text_limit_and_itemize_parsing(self):
        out = ('cd+++++++++ ./\n>f+++++++++ a b.txt\n>f.st...... c\n.f...p..... d\n'
               'cL+++++++++ lnk\nhf+++++++++ hard\ncd+++++++++ sub/\n\nNumber of files: 6\n')
        parsed = parse_itemized(out, {'a b.txt': 3})
        self.assertEqual([(c['action'], c['kind'], c['path']) for c in parsed], [
            ('new', 'file', 'a b.txt'), ('changed', 'file', 'c'), ('metadata', 'file', 'd'),
            ('new', 'link', 'lnk'), ('new', 'file', 'hard'), ('new', 'folder', 'sub')])
        self.assertEqual(parsed[0]['size'], 3)
        _, report = self.changes()
        self.assertIn('more (full list saved', changes_text(report, limit=1))

    # Terminal and desktop controls

    def test_cli_projects_remove_exclude_include_and_file_preview(self):
        save_config(self.registry, self.config)
        def run(*args):
            buffer = io.StringIO()
            with redirect_stdout(buffer), patch('backup.cli.resolve_device', return_value=self.device), \
                    patch('backup.core.resolve_device', return_value=self.device):
                code = cli.main(['--config', str(self.registry), '--report-dir', str(self.reports), *args])
            return code, buffer.getvalue()
        self.assertEqual(run('exclude', '--name', 'Demo', '--path', 'cache', '--reason', 'cache')[0], 0)
        code, listing = run('projects')
        self.assertIn('left out: cache - cache', listing)
        code, out = run('preview', '--files')
        self.assertEqual(code, 0)
        self.assertIn('NEW     README.md', out)
        self.assertNotIn('big.tmp', out)
        self.assertTrue(list(self.reports.glob('*.changes.txt')))
        self.assertFalse((self.usb/'CyclopsBackup').exists())
        self.assertEqual(run('include', '--name', 'Demo', '--path', 'cache')[0], 0)
        self.assertEqual(run('project-remove', '--name', 'Evidence')[0], 0)
        self.assertEqual([p['name'] for p in load_config(self.registry)['projects']], ['Demo'])
        self.assertEqual(load_config(self.registry)['projects'][0]['excludes'], [])

    def gui_session(self, responses):
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
                patch('backup.gui.gtk_available', return_value=False), \
                patch('backup.gui.progress_task', side_effect=lambda title, task: task(lambda s: None)), \
                patch('backup.gui.dialog', side_effect=respond):
            code = gui.launch(self.registry, self.reports)
        return code, seen

    def test_gui_remove_folder_then_quit(self):
        code, _ = self.gui_session([(0, 'folders\n'), (0, 'remove\n'), (0, 'Evidence\n'), (0, ''), (1, ''), (1, '')])
        self.assertEqual(code, 1)
        self.assertEqual([p['name'] for p in load_config(self.registry)['projects']], ['Demo'])
        self.assertEqual((self.other/'capture.bin').read_bytes(), b'\x00evidence')

    def test_gui_leave_out_subfolder_then_put_it_back(self):
        code, seen = self.gui_session([
            (0, 'folders\n'),
            (0, 'exclude\n'), (0, 'Demo\n'), (0, 'folder\n'), (0, str(self.source/'cache')+'\n'),
            (0, 'downloads, rebuildable\n'),
            (0, 'include\n'), (0, 'Demo\tcache\tdownloads, rebuildable\n'),
            (1, ''), (1, '')])
        self.assertEqual(code, 1)
        self.assertIn('--directory', seen[4])
        self.assertEqual(load_config(self.registry)['projects'][0]['excludes'], [])

    def test_gui_preview_shows_changes_and_copies_nothing(self):
        code, seen = self.gui_session([(0, 'preview\n'), (0, 'quick\n'), (0, ''), (1, '')])
        self.assertEqual(code, 1)
        self.assertFalse((self.usb/'CyclopsBackup').exists())
        shown = next(a for a in seen if any(str(x).startswith('--filename=') for x in a))
        self.assertTrue(list(self.reports.glob('*.changes.txt')))
        self.assertIn('--text-info', shown)

    def test_gui_cancelled_choice_does_nothing_and_errors_keep_the_panel_open(self):
        # Esc on the preview mode list, then an invalid leave-out, then quit.
        code, seen = self.gui_session([
            (0, 'preview\n'), (1, ''),
            (0, 'folders\n'),
            (0, 'exclude\n'), (0, 'Demo\n'), (0, 'file\n'), (0, str(self.other/'capture.bin')+'\n'), (0, 'x\n'),
            (0, ''),  # error dialog acknowledged
            (1, ''), (1, '')])
        self.assertEqual(code, 1)
        self.assertTrue(any('--error' in a for a in seen))
        self.assertFalse((self.usb/'CyclopsBackup').exists())
        self.assertEqual(load_config(self.registry)['projects'][0]['excludes'], [])


if __name__ == '__main__':
    unittest.main()
