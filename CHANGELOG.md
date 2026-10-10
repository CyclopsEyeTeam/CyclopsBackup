# Release notes

## 1.1.0 — 2026-10-10

- **Faster, honest incremental runs.** Copying now decides by size and
  nanosecond time instead of reading every file with checksums, so unchanged
  files are not read just to be skipped. Only new and changed files are written
  (unchanged before too — now it is also shown).
- **Full check stays the default** and still SHA-256 verifies every file. A
  mismatch now triggers one checksum retry of that folder, which repairs a USB
  copy damaged without its size or time changing.
- **Quick check (optional):** *Back up — quick check* / `run --quick` SHA-256
  verifies only new and changed files and checks the rest by size, mode and
  time. Run a full check regularly.
- **One fresh retry** for a folder that changes while it is being backed up
  (during copy, verification or the final check) instead of failing the whole
  run; a folder that keeps changing still fails, with advice.
- **Truthful progress:** stages (Scanning · Copying · Verifying · Final check)
  with live counts — Scanned · Unchanged · Copied · Verified · Written — in the
  progress window, success/failure popup, reports and terminal.
- **Panel:** compact header (folder count instead of every name, so the window
  stays on screen), *Back up — quick check* row, and **Back up now** at the end
  of a preview (still goes through the normal review and confirmation).
- Copy previews and copied-file lists decode names rsync escapes (newlines,
  accented characters).
- 20 new tests (127 total).

## 1.0.0 — 2026-10-06

First stable release. Everything from 0.2.0–0.3.2 below, plus:

- **The Cyclops look.** Dark navy panels with a single cyan eye accent, green
  for verified/successful, amber for waiting, red for problems; an eye app
  icon on the launcher, folders window and result popups. Zenity dialogs are
  styled through a private settings folder, never the desktop theme
  (`CYCLOPS_BACKUP_PLAIN=1` turns it off).
- Re-running `install.py` updates the launcher it made earlier (new icon);
  any other launcher with the same name is still left untouched.
- 9 new tests (107 total). Copy and verification engine unchanged since 0.2.0.

## 0.3.2 — 2026-10-06

- After a backup, a popup now says how it went before the application closes:
  **Backup successful** (folders, profile, data checked and transferred, and
  that the USB is safe to eject) or **Backup did not complete** (the reasons,
  and that the USB is not marked safe to eject). **View report** opens the full
  report.
- Fix: result and copy-preview windows never opened on Zenity 4 (Zorin 17 /
  Ubuntu 24.04), because it refuses the `--no-cancel` option those windows
  used, so the app appeared to just close.
- 4 new tests (98 total), including a full fixture backup run from the panel.

## 0.3.1 — 2026-10-06

- Fix: Add folder, New profile, Rename and the leave-out reason lost what was
  typed, because the dialog was read after it had closed. Adding a folder
  always failed with a misleading "unique simple folder names" error.
- Fix: the folders window comes back to the front after every dialog instead
  of dropping behind other windows.
- Backup names: picking a folder suggests a name (reusing the name another
  profile already gives that same folder, otherwise numbered if taken); typed
  names are cleaned (spaces and symbols become -); the dialog shows live where
  the folder will be saved on the USB.
- Clear messages: missing name, characters not allowed, name already used in
  this profile, folder already in this profile (and under which name).
- 5 new tests (94 total), including real GTK dialog tests for this bug.

## 0.3.0 — 2026-10-06

- **Profiles.** Each profile is its own named list of folders; the active one
  is what review, preview and backup use. All profiles share the pinned USB.
  Create (empty or as a copy), switch, rename and delete. Settings from earlier
  versions load as profile "Default". Backup runs and USB manifests record only
  the active profile and its name.
- **One folders menu.** The control panel's Add / Remove / Leave out / Put back
  rows are collapsed into a single **View folders** row. It opens a window with
  a profile dropdown at the top, the profile's folder list (left-out items shown
  under their folder) and buttons for every folder action. GTK 3 via
  PyGObject; plain Zenity lists are used if GTK is unavailable.
- Two profiles may share a folder but never a USB destination for different
  folders.
- Terminal: `profiles`, `profile-new [--copy]`, `profile-use`, `profile-rename`,
  `profile-delete`, `folders`.
- 13 new tests (89 total). Copy and verification engine unchanged.

## 0.2.1 — 2026-10-06

- Launch opens straight into the control panel. The first-run folder wizard and
  forced USB picker are gone, so nothing is asked before the panel appears.
- The panel header shows the USB state: verified, not connected, plugged in but
  not chosen yet, or an unsupported filesystem (e.g. FAT/exFAT/NTFS, named).
- **Review and back up** and **Preview what will be copied** stay locked (🔒)
  until the backup USB is connected and passes verification. Choosing one
  while locked opens a "waiting for USB" window that continues by itself once
  the USB is verified; Cancel returns to the panel.
- Folders, leave-outs and put-backs can be set up with no USB connected.
- 14 new fixture tests (76 total). Copy and verification engine unchanged.

## 0.2.0 — 2026-10-06

- Desktop control panel after first setup: review and back up, copy preview,
  choose/change USB, add folder (project or evidence), remove folder, leave out
  a file or subfolder, put an item back. Cancel/Esc never picks a default.
- Copy preview: read-only per-item NEW / CHANGED / PERMS list, quick (size/time)
  or exact (checksum, matching the real copy). Full list saved locally.
- Terminal: `projects`, `project-remove`, `exclude`, `include`,
  `preview --files [--exact] [--limit N]`.
- Exclusions added through the controls refuse Git-tracked paths, `.git`,
  paths outside the folder and wildcard characters.
- 17 new fixture tests (62 total). Copy and verification engine unchanged:
  new code is appended alongside it.

## 0.1.0 — 2026-10-05 (public preview)

First public release of Cyclops Backup for Linux/Zorin/Ubuntu.

- Standalone Python CLI and Zenity desktop/application-menu launcher.
- Explicit project and evidence registration, private per-user settings, and
  first-run folder/USB selection. No automatic project discovery or exclusions.
- USB selection by stable filesystem UUID, safe refusals and reviewed copying.
- Checksum-based incremental file copying with retained deletions and previous
  versions; preservation of Git metadata and untracked project/evidence data.
- Full SHA-256 verification, dated readable/JSON reports and final eject gating.
- Fixture tests and GitHub Actions checks; MIT licence and recovery instructions.

The copy and verification engine is unchanged from the validated local utility.
This preview has not completed a full physical USB backup of a large project set.
Review estimates use size/mtime and can differ from actual checksum-based transfer.
Only ext4/xfs/btrfs USB filesystems are supported. ACLs, extended attributes and
owner/group identities are outside this release's preservation scope.
