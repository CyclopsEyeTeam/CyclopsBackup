"""Profiles: each profile is its own folder list; the USB and the copy engine are shared."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backup import cli, gui
from backup.config import BackupError, load_config, run_view, save_config
from backup.core import preview
from backup.folders import FolderBook
from backup import setup

ROOT = Path(__file__).resolve().parent.parent


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops profiles ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = self.root/'Studio'; self.a.mkdir(); (self.a/'a.txt').write_text('a\n')
        self.b = self.root/'Zilo evidence'; self.b.mkdir(); (self.b/'b.bin').write_bytes(b'b')
        (self.b/'cache').mkdir(); (self.b/'cache/x').write_text('x')
        self.registry = self.root/'cfg/registry.json'

    def project(self, name, source):
        return {'name': name, 'source': str(source), 'destination': 'Projects/'+name, 'excludes': []}

    def run_cli(self, *args):
        return cli.main(['--config', str(self.registry), '--report-dir', str(self.root/'reports'), *args])

    def test_older_settings_file_becomes_default_profile(self):
        self.registry.parent.mkdir()
        self.registry.write_text(json.dumps({'version': 1, 'device': {'uuid': 'u'},
                                             'projects': [self.project('Studio', self.a)]}))
        config = load_config(self.registry)
        self.assertEqual(config['profile'], 'Default')
        self.assertEqual([p['name'] for p in config['profiles']['Default']], ['Studio'])
        save_config(self.registry, config)
        stored = json.loads(self.registry.read_text())
        self.assertEqual(stored['profiles']['Default'], stored['projects'])

    def test_profiles_keep_separate_folder_lists(self):
        config = setup.add_project(load_config(self.registry, allow_empty=True), 'Studio', self.a)
        save_config(self.registry, config)
        config = setup.new_profile(load_config(self.registry), 'Evidence')
        self.assertEqual(config['projects'], [])
        config = setup.add_project(config, 'Zilo', self.b, archive=True)
        save_config(self.registry, config)
        loaded = load_config(self.registry)
        self.assertEqual(loaded['profile'], 'Evidence')
        self.assertEqual([p['name'] for p in loaded['projects']], ['Zilo'])
        back = setup.switch_profile(loaded, 'Default')
        self.assertEqual([p['name'] for p in back['projects']], ['Studio'])
        save_config(self.registry, back)
        self.assertEqual([p['name'] for p in load_config(self.registry)['profiles']['Evidence']], ['Zilo'])

    def test_unsaved_folder_change_survives_a_profile_switch(self):
        config = load_config(self.registry, allow_empty=True)
        config = setup.add_project(config, 'Studio', self.a)        # not saved yet
        config = setup.new_profile(config, 'Other')
        config = setup.switch_profile(config, 'Default')
        self.assertEqual([p['name'] for p in config['projects']], ['Studio'])

    def test_same_folder_may_be_in_two_profiles_but_names_cannot_collide(self):
        config = setup.add_project(load_config(self.registry, allow_empty=True), 'Studio', self.a)
        config = setup.new_profile(config, 'Copy', copy_current=True)
        save_config(self.registry, config)                            # same source, same USB folder: fine
        clash = setup.new_profile(config, 'Clash')
        with self.assertRaisesRegex(BackupError, 'both use Projects/Studio'):
            setup.add_project(clash, 'Studio', self.b)

    def test_profile_name_rules(self):
        config = load_config(self.registry, allow_empty=True)
        for bad in ('', ' x', 'a/b', 'a|b', 'x'*41):
            with self.assertRaises(BackupError):setup.new_profile(config, bad)
        config = setup.new_profile(config, 'Weekly')
        with self.assertRaisesRegex(BackupError, 'already exists'):setup.new_profile(config, 'weekly')
        config = setup.rename_profile(config, 'Weekly', 'Weekly USB')
        self.assertEqual(config['profile'], 'Weekly USB')
        self.assertIn('Default', config['profiles'])

    def test_delete_profile(self):
        config = load_config(self.registry, allow_empty=True)
        with self.assertRaisesRegex(BackupError, 'only profile'):setup.delete_profile(config, 'Default')
        config = setup.add_project(config, 'Studio', self.a)
        config = setup.new_profile(config, 'Spare')
        config = setup.delete_profile(config, 'Spare')
        self.assertEqual(config['profile'], 'Default')
        self.assertEqual([p['name'] for p in config['projects']], ['Studio'])
        self.assertTrue((self.a/'a.txt').exists())

    def test_backup_run_sees_only_the_active_profile(self):
        config = setup.add_project(load_config(self.registry, allow_empty=True), 'Studio', self.a)
        config = setup.new_profile(config, 'Evidence')
        config = setup.add_project(config, 'Zilo', self.b, archive=True)
        view = run_view(config)
        self.assertNotIn('profiles', view)
        self.assertEqual(view['profile'], 'Evidence')
        report = preview(view)
        self.assertEqual([p['name'] for p in report['projects']], ['Zilo'])
        self.assertEqual(report['registry']['profile'], 'Evidence')

    def test_terminal_profile_commands(self):
        self.assertEqual(self.run_cli('project-add', '--name', 'Studio', '--source', str(self.a)), 0)
        self.assertEqual(self.run_cli('profile-new', '--name', 'Evidence'), 0)
        self.assertEqual(self.run_cli('project-add', '--name', 'Zilo', '--source', str(self.b), '--archive'), 0)
        self.assertEqual(self.run_cli('profile-use', '--name', 'Default'), 0)
        self.assertEqual([p['name'] for p in load_config(self.registry)['projects']], ['Studio'])
        with patch('sys.stdout') as out:
            self.assertEqual(self.run_cli('profiles'), 0)
        printed = ''.join(c.args[0] for c in out.write.call_args_list)
        self.assertIn('* Default (1 folder)', printed)
        self.assertIn('  Evidence (1 folder)', printed)
        self.assertEqual(self.run_cli('profile-rename', '--name', 'Projects weekly'), 0)
        self.assertEqual(self.run_cli('profile-delete', '--name', 'Evidence'), 0)
        self.assertEqual(list(load_config(self.registry)['profiles']), ['Projects weekly'])
        self.assertEqual(self.run_cli('profile-delete', '--name', 'Projects weekly'), 2)


class FolderBookTests(ProfileTests.__base__):
    def setUp(self):
        ProfileTests.setUp(self)

    def test_window_actions_save_straight_away(self):
        book = FolderBook(self.registry)
        self.assertIn('No folders', book.summary())
        book.add(str(self.b), 'Zilo', archive=True)
        book.leave_out('Zilo', str(self.b/'cache'), 'rebuildable cache')
        self.assertIn('1 folder · 1 item left out', book.summary())
        book.new_profile('Second')
        self.assertEqual(book.folders, [])
        book.switch('Default')
        again = FolderBook(self.registry)
        self.assertEqual(again.profile, 'Default')
        self.assertEqual(again.folders[0]['excludes'][0]['path'], 'cache')
        again.put_back('Zilo', 'cache')
        again.remove('Zilo')
        self.assertEqual(FolderBook(self.registry).folders, [])
        self.assertTrue((self.b/'cache/x').exists())

    def test_refused_change_leaves_settings_as_they_were(self):
        book = FolderBook(self.registry)
        book.add(str(self.a), 'Studio')
        before = self.registry.read_text()
        with self.assertRaises(BackupError):book.add(str(self.a/'missing'), 'Gone')
        with self.assertRaises(BackupError):book.add(str(self.b), 'Studio')
        self.assertEqual(self.registry.read_text(), before)


class NameTests(ProfileTests.__base__):
    def setUp(self):
        ProfileTests.setUp(self)

    def test_clear_name_errors(self):
        config = load_config(self.registry, allow_empty=True)
        with self.assertRaisesRegex(BackupError, 'Give the folder a backup name'):
            setup.add_project(config, '', self.a)
        with self.assertRaisesRegex(BackupError, 'can only use letters'):
            setup.add_project(config, 'my studio', self.a)
        config = setup.add_project(config, 'Studio', self.a)
        with self.assertRaisesRegex(BackupError, "'Studio' is already used in this profile"):
            setup.add_project(config, 'Studio', self.b)
        with self.assertRaisesRegex(BackupError, "already in this profile as 'Studio'"):
            setup.add_project(config, 'Other', self.a)

    def test_suggested_names(self):
        self.assertEqual(setup.clean_name('  play engine (early) zips! '), 'play-engine-early-zips')
        config = setup.add_project(load_config(self.registry, allow_empty=True), 'Shared', self.a)
        config = setup.new_profile(config, 'Two')
        self.assertEqual(setup.suggest_name(config, self.a), 'Shared')       # same folder: same USB copy
        clash = self.root/'other'/'Studio'; clash.mkdir(parents=True); (clash/'f').write_text('f')
        config = setup.add_project(config, 'Studio', clash)
        self.assertEqual(setup.suggest_name(config, self.root/'Studio x'), 'Studio-x')
        self.assertEqual(setup.suggest_name(config, self.a), 'Shared')
        same = self.root/'more'/'Studio'; same.mkdir(parents=True)
        self.assertEqual(setup.suggest_name(config, same), 'Studio-2')


class ZenityFallbackTests(ProfileTests.__base__):
    def setUp(self):
        ProfileTests.setUp(self)

    def test_profile_switch_and_create_without_gtk(self):
        config = setup.add_project(load_config(self.registry, allow_empty=True), 'Studio', self.a)
        save_config(self.registry, config)
        config = load_config(self.registry)
        answers = iter([(0, 'profile\n'), (0, 'new\n'), (0, 'Evidence\n'),
                        (0, 'profile\n'), (0, 'use:Default\n'), (1, '')])
        seen = []
        def respond(*args):
            seen.append(args); code, out = next(answers)
            return subprocess.CompletedProcess([], code, out, '')
        with patch('backup.gui.gtk_available', return_value=False), patch('backup.gui.dialog', side_effect=respond):
            gui.view_folders(self.registry, config)
        self.assertEqual(config['profile'], 'Default')
        self.assertEqual(list(load_config(self.registry)['profiles']), ['Default', 'Evidence'])
        self.assertIn('Profile: Default  ▾', seen[0])


@unittest.skipUnless(os.environ.get('DISPLAY'), 'needs a display')
class GtkWindowTests(ProfileTests.__base__):
    def setUp(self):
        ProfileTests.setUp(self)

    def test_window_shows_profile_dropdown_and_folders(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, Pango
        except (ImportError, ValueError):
            self.skipTest('GTK 3 not installed')
        from backup.folders import FoldersWindow
        book = FolderBook(self.registry)
        book.add(str(self.b), 'Zilo', archive=True)
        book.leave_out('Zilo', str(self.b/'cache'), 'cache')
        book.new_profile('Empty')
        book.switch('Default')
        win = FoldersWindow(Gtk, Pango, book)
        self.assertEqual(win.combo.get_active_id(), 'Default')
        self.assertEqual(len(win.store), 1)
        self.assertEqual(win.store[0][2], 'Archives/Zilo')
        self.assertEqual(win.store[0].iterchildren().__next__()[5], 'cache')
        self.assertFalse(win.buttons['remove'].get_sensitive())   # nothing selected yet
        win.combo.set_active_id('Empty')                           # dropdown switches and saves
        self.assertEqual(load_config(self.registry, allow_empty=True)['profile'], 'Empty')
        self.assertEqual(len(win.store), 0)
        win.destroy()

    def run_dialogs(self, win, fill):
        """Answer the window's dialogs like a person: fill them in, then press the OK button."""
        from gi.repository import Gtk
        def all_widgets(w):
            yield w
            if isinstance(w, Gtk.Container):
                for child in w.get_children():yield from all_widgets(child)
        def run(dialog):
            fill(list(all_widgets(dialog)))
            return Gtk.ResponseType.OK
        return patch.object(Gtk.Dialog, 'run', run)

    def gtk(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, Pango
        except (ImportError, ValueError):
            self.skipTest('GTK 3 not installed')
        from backup.folders import FoldersWindow
        return Gtk, (lambda book: FoldersWindow(Gtk, Pango, book))

    def test_add_dialog_keeps_what_was_typed(self):
        # Regression: the name was read after the dialog closed and arrived empty.
        Gtk, window = self.gtk()
        spaced = self.root/'play engine early zips'; spaced.mkdir(); (spaced/'a.zip').write_bytes(b'z')
        book = FolderBook(self.registry)
        win = window(book)
        def fill(widgets):
            chooser = next(w for w in widgets if isinstance(w, Gtk.FileChooserButton))
            chooser.set_filename(str(spaced))
            chooser.emit('file-set')
            name = next(w for w in widgets if isinstance(w, Gtk.Entry))
            self.assertEqual(name.get_text(), 'play-engine-early-zips')   # suggested from the folder
            label = next(w for w in widgets if isinstance(w, Gtk.Label) and 'Saved on the USB' in w.get_text())
            self.assertIn('Projects/play-engine-early-zips', label.get_text())
        with self.run_dialogs(win, fill), patch.object(Gtk.MessageDialog, 'run', side_effect=AssertionError):
            win.on_add()
        saved = load_config(self.registry)['projects']
        self.assertEqual([(p['name'], p['source']) for p in saved], [('play-engine-early-zips', str(spaced))])
        win.destroy()

    def test_typed_name_with_spaces_is_cleaned(self):
        Gtk, window = self.gtk()
        book = FolderBook(self.registry)
        win = window(book)
        def fill(widgets):
            next(w for w in widgets if isinstance(w, Gtk.FileChooserButton)).set_filename(str(self.a))
            next(w for w in widgets if isinstance(w, Gtk.Entry)).set_text('My Studio  (main)')
            next(w for w in widgets if isinstance(w, Gtk.RadioButton) and 'Evidence' in w.get_label()).set_active(True)
        with self.run_dialogs(win, fill):
            win.on_add()
        p = load_config(self.registry)['projects'][0]
        self.assertEqual((p['name'], p['destination']), ('My-Studio-main', 'Archives/My-Studio-main'))
        win.destroy()

    def test_new_and_rename_profile_dialogs_keep_typed_names(self):
        Gtk, window = self.gtk()
        win = window(FolderBook(self.registry))
        with self.run_dialogs(win, lambda ws: next(w for w in ws if isinstance(w, Gtk.Entry)).set_text('Weekly')):
            win.on_new()
        with self.run_dialogs(win, lambda ws: next(w for w in ws if isinstance(w, Gtk.Entry)).set_text('Weekly USB')):
            win.on_rename()
        config = load_config(self.registry, allow_empty=True)
        self.assertEqual((config['profile'], list(config['profiles'])), ('Weekly USB', ['Default', 'Weekly USB']))
        self.assertEqual(win.combo.get_active_id(), 'Weekly USB')
        win.destroy()

    def test_folders_command_reports_missing_gtk(self):
        code = subprocess.run([sys.executable, '-c', 'import sys; sys.modules["gi"]=None\n'
                               'from backup.folders import open_window; raise SystemExit(open_window("x"))'],
                              cwd=ROOT).returncode
        self.assertEqual(code, 3)


if __name__ == '__main__':
    unittest.main()
