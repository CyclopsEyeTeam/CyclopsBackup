"""The Cyclops look stays private to Cyclops Backup, and the launcher picks up its icon."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import install  # noqa: E402
from backup import __version__, gui, look  # noqa: E402


class LookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops look ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'config/gtk-3.0').mkdir(parents=True)
        (self.root/'config/gtk-3.0/bookmarks').write_text('file:///home/me/Projects\n')
        self.env = patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.root/'cache'),
                                           'XDG_CONFIG_HOME': str(self.root/'config')})
        self.env.start();self.addCleanup(self.env.stop)

    def test_dialogs_get_a_private_stylesheet_and_keep_bookmarks(self):
        env = look.dialog_env()
        private = Path(env['XDG_CONFIG_HOME'])
        self.assertEqual(private, self.root/'cache/cyclops-backup/look')
        self.assertIn(look.EYE, (private/'gtk-4.0/gtk.css').read_text())
        self.assertIn(look.EYE, (private/'gtk-3.0/gtk.css').read_text())
        self.assertEqual((private/'gtk-3.0/bookmarks').read_text(), 'file:///home/me/Projects\n')
        # the person's own GTK settings are never written to
        self.assertEqual(sorted(p.name for p in (self.root/'config/gtk-3.0').iterdir()), ['bookmarks'])

    def test_plain_mode_and_unwritable_cache_fall_back_to_desktop_theme(self):
        with patch.dict(os.environ, {'CYCLOPS_BACKUP_PLAIN': '1'}):
            self.assertEqual(look.dialog_env()['XDG_CONFIG_HOME'], str(self.root/'config'))
        with patch('backup.look.theme_dir', side_effect=PermissionError('read-only')):
            self.assertEqual(look.dialog_env()['XDG_CONFIG_HOME'], str(self.root/'config'))

    def test_every_zenity_call_uses_the_look(self):
        with patch('backup.gui.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            gui.dialog('--info', '--text=x')
        self.assertEqual(run.call_args.kwargs['env']['XDG_CONFIG_HOME'],
                         str(self.root/'cache/cyclops-backup/look'))

    def test_brand_line_and_icon(self):
        self.assertIn('v'+__version__, look.brand())
        self.assertTrue(look.ICON.is_file())
        self.assertIn('<svg', look.ICON.read_text())
        self.assertIn('foreground="'+look.GOOD, look.good('ok'))

    @unittest.skipUnless(os.environ.get('DISPLAY'), 'needs a display')
    def test_gtk3_stylesheet_parses(self):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
        except (ImportError, ValueError):
            self.skipTest('GTK 3 not installed')
        Gtk.CssProvider().load_from_data(look.GTK3_CSS.encode())   # raises on any parse error

    def test_version_is_1_0(self):
        self.assertEqual(__version__, '1.0.0')


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops launcher ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def menu(self):
        return self.home/'.local/share/applications/cyclops-backup.desktop'

    def test_launcher_uses_the_eye_icon(self):
        install.install(ROOT, self.home)
        self.assertIn('Icon='+str(ROOT/'assets/cyclops-backup.svg'), self.menu().read_text().splitlines())

    def test_older_launcher_for_this_utility_is_updated(self):
        install.install(ROOT, self.home)
        old = self.menu().read_text().replace('Icon='+str(ROOT/'assets/cyclops-backup.svg'), 'Icon=drive-removable-media')
        self.menu().write_text(old)
        install.install(ROOT, self.home)
        self.assertIn('cyclops-backup.svg', self.menu().read_text())

    def test_someone_elses_launcher_is_left_alone(self):
        self.menu().parent.mkdir(parents=True)
        self.menu().write_text('[Desktop Entry]\nName=Other\nExec=/usr/bin/other\n')
        with self.assertRaisesRegex(OSError, 'left untouched'):
            install.install(ROOT, self.home)
        self.assertIn('Exec=/usr/bin/other', self.menu().read_text())


if __name__ == '__main__':
    unittest.main()
