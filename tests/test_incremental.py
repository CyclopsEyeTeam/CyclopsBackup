"""Incremental runs: write only what changed, count honestly, retry a moving folder once.

Real rsync, hashing and inventories on tiny fixtures; only the USB identity lookup is replaced.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backup.config import Device
from backup.core import parse_itemized, run_backup, verify_manifest
from backup.report import readable


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Cyclops incremental ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'My Project'
        (self.source/'docs').mkdir(parents=True)
        (self.source/'README.md').write_text('project\n')
        (self.source/'.hidden').write_text('dotfile\n')
        (self.source/'docs/big.bin').write_bytes(b'x' * 50000)
        (self.source/'docs/name\nwith newline.txt').write_text('odd name\n')
        (self.source/'docs/café.txt').write_text('accent\n')
        self.second = self.root/'Second'
        self.second.mkdir()
        (self.second/'two.txt').write_text('two\n')
        self.usb = self.root/'USB'
        self.usb.mkdir()
        self.device = Device(self.usb, 'test-uuid', 'Test USB', 'ext4')
        self.config = {'version': 1, 'device': {'uuid': 'test-uuid'}, 'projects': [
            {'name': 'Demo', 'source': str(self.source), 'destination': 'Projects/Demo', 'excludes': []},
            {'name': 'Two', 'source': str(self.second), 'destination': 'Projects/Two', 'excludes': []}]}
        self.reports = self.root/'reports'

    def backup(self, **kwargs):
        with patch('backup.core.resolve_device', return_value=self.device):
            result = run_backup(self.config, self.device, self.reports, **kwargs)
        return result

    def dest(self, *parts):
        return self.usb.joinpath('CyclopsBackup', 'Projects', *parts)

    # ---- incremental behaviour -------------------------------------------------

    def test_unchanged_second_run_writes_nothing_but_still_full_checks(self):
        first = self.backup()
        self.assertTrue(first['safe_to_eject'], first['failures'])
        files = first['counters']['scanned']
        self.assertEqual(first['counters']['copied'], files)
        seen = []
        original = subprocess.run
        def spy(args, *a, **kw):
            if args[0] == 'rsync' and '--dry-run' not in args:seen.append(args)
            return original(args, *a, **kw)
        with patch('backup.core.subprocess.run', side_effect=spy):
            again = self.backup()
        self.assertTrue(again['safe_to_eject'], again['failures'])
        self.assertEqual(again['counters'], {'scanned': files, 'unchanged': files, 'copied': 0,
                                             'verified': files, 'size_checked': 0, 'bytes_written': 0})
        self.assertTrue(seen)
        self.assertFalse(any('--checksum' in a for a in seen))       # unchanged files are not read to compare
        self.assertEqual(again['check'], 'full')

    def test_only_the_changed_file_is_written_and_old_version_kept(self):
        self.backup()
        (self.source/'README.md').write_text('project, edited\n')
        result = self.backup()
        self.assertTrue(result['safe_to_eject'], result['failures'])
        c = result['counters']
        self.assertEqual((c['copied'], c['bytes_written']), (1, len('project, edited\n')))
        self.assertEqual(c['unchanged'], c['scanned'] - 1)
        self.assertEqual(result['projects'][0]['copied_files'], ['README.md'])
        self.assertEqual(self.dest('Demo/README.md').read_text(), 'project, edited\n')
        kept = self.usb/'CyclopsBackup/Archives/PreviousVersions'/result['run_id']/'Demo/README.md'
        self.assertEqual(kept.read_text(), 'project\n')

    def test_hidden_files_odd_names_and_times_are_kept(self):
        self.backup()
        self.assertEqual(self.dest('Demo/.hidden').read_text(), 'dotfile\n')
        self.assertEqual(self.dest('Demo/docs/name\nwith newline.txt').read_text(), 'odd name\n')
        self.assertEqual(self.dest('Demo/docs/café.txt').read_text(), 'accent\n')
        self.assertEqual(self.dest('Demo/docs/big.bin').stat().st_mtime_ns,
                         (self.source/'docs/big.bin').stat().st_mtime_ns)

    # ---- quick (lighter) check ---------------------------------------------------

    def test_quick_check_hashes_only_new_and_changed_files(self):
        self.backup()
        (self.source/'docs/café.txt').write_text('accent, edited\n')
        result = self.backup(quick=True)
        self.assertTrue(result['safe_to_eject'], result['failures'])
        self.assertEqual(result['check'], 'quick')
        demo = result['projects'][0]
        self.assertEqual(demo['copied_files'], ['docs/café.txt'])            # decoded from rsync's \#ooo
        self.assertEqual(demo['verification']['scope'], 'quick')
        self.assertEqual(demo['verification']['hashed_files'], 1)
        self.assertEqual(demo['verification']['size_checked_files'], demo['file_count'] - 1)
        self.assertEqual(result['counters']['verified'], 1)
        self.assertEqual(result['counters']['size_checked'], result['counters']['scanned'] - 1)
        checked = verify_manifest(Path(result['local_json']), self.usb/'CyclopsBackup')
        self.assertTrue(checked['ok'], checked)
        self.assertIn('quick', checked['scope'])

    def test_quick_first_run_hashes_everything_it_copied(self):
        result = self.backup(quick=True)
        self.assertTrue(result['safe_to_eject'], result['failures'])
        self.assertEqual(result['counters']['verified'], result['counters']['scanned'])
        self.assertIn('docs/name\nwith newline.txt', result['projects'][0]['copied_files'])

    def test_quick_manifest_still_catches_damage(self):
        self.backup()
        (self.source/'README.md').write_text('changed\n')
        result = self.backup(quick=True)
        self.dest('Demo/docs/big.bin').write_bytes(b'short')            # unchanged file, size differs
        self.assertFalse(verify_manifest(Path(result['local_json']), self.usb/'CyclopsBackup')['ok'])
        self.dest('Demo/docs/big.bin').write_bytes(b'x' * 50000)
        self.dest('Demo/README.md').write_text('CHANGED\n')                # hashed file, same size
        self.assertFalse(verify_manifest(Path(result['local_json']), self.usb/'CyclopsBackup')['ok'])

    def test_full_check_after_quick_finds_silent_damage_and_repairs_it(self):
        self.backup()
        target = self.dest('Demo/docs/big.bin')
        before = target.stat()
        target.write_bytes(b'y' * 50000)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        quick = self.backup(quick=True)
        self.assertTrue(quick['safe_to_eject'])                             # the lighter check's known limit
        full = self.backup()
        self.assertTrue(full['safe_to_eject'], full['failures'])
        self.assertEqual(target.read_bytes(), b'x' * 50000)
        self.assertEqual(full['projects'][0]['retries'], ['SHA-256 mismatch: docs/big.bin'])

    # ---- folders that change while backing up ------------------------------------

    def test_folder_changing_once_gets_one_fresh_retry(self):
        done = []
        def touch_once(message):
            if message.startswith('Verifying Demo') and not done:
                done.append(1)
                (self.source/'new.txt').write_text('arrived mid-backup\n')
        result = self.backup(progress=touch_once)
        self.assertTrue(result['safe_to_eject'], result['failures'])
        self.assertEqual(self.dest('Demo/new.txt').read_text(), 'arrived mid-backup\n')
        self.assertEqual(len(result['projects'][0]['retries']), 1)
        self.assertEqual(result['counters']['verified'], result['counters']['scanned'])   # no double counting

    def test_folder_that_keeps_changing_fails_clearly(self):
        def keep_touching(message):
            if message.startswith('Verifying Demo'):
                (self.source/'live-cache.tmp').write_text(str(os.urandom(8)))
        result = self.backup(progress=keep_touching)
        self.assertFalse(result['safe_to_eject'])
        self.assertTrue(any(f.startswith('Demo:') for f in result['failures']))
        self.assertEqual(len(result['projects'][0]['retries']), 1)

    def test_final_check_retries_a_folder_changed_while_another_copied(self):
        done = []
        def touch_demo_during_two(message):
            if message.startswith('Copying Two') and not done:
                done.append(1)
                (self.source/'README.md').write_text('edited during Two\n')
        result = self.backup(progress=touch_demo_during_two)
        self.assertTrue(result['safe_to_eject'], result['failures'])
        self.assertEqual(self.dest('Demo/README.md').read_text(), 'edited during Two\n')
        self.assertEqual(result['projects'][0]['retries'], ['Source changed before final verification.'])

    def test_final_check_gives_up_after_one_retry_with_advice(self):
        def keep_editing(message):
            if message.startswith('Copying Two') or message.startswith('Final check'):
                (self.source/'README.md').write_text(os.urandom(4).hex())
        result = self.backup(progress=keep_editing)
        self.assertFalse(result['safe_to_eject'])
        self.assertIn('again after a fresh retry', ' '.join(result['failures']))

    # ---- honest reporting --------------------------------------------------------

    def test_progress_shows_phases_and_counters(self):
        seen = []
        self.backup(progress=seen.append)
        joined = '\n'.join(seen)
        for phase in ('Scanning', 'Copying Demo', 'Verifying Demo', 'Final check'):
            self.assertIn(phase, joined)
        self.assertRegex(joined, r'Scanned \d+ · Unchanged \d+ · Copied \d+ · Verified \d+ · Written ')

    def test_report_keeps_counters_and_text_shows_them(self):
        self.backup()
        result = self.backup()
        saved = json.loads(Path(result['local_json']).read_text())
        self.assertEqual(saved['counters']['copied'], 0)
        text = readable(result)
        self.assertIn('Scanned', text)
        self.assertIn('Written 0.0 B', text)
        self.assertIn('Check: full', text)

    def test_rsync_escaped_names_are_decoded(self):
        out = '>f+++++++++ docs/name\\#012with newline.txt\n>f.st...... docs/caf\\#303\\#251.txt\n'
        paths = [c['path'] for c in parse_itemized(out, {})]
        self.assertEqual(paths, ['docs/name\nwith newline.txt', 'docs/café.txt'])


if __name__ == '__main__':
    unittest.main()
