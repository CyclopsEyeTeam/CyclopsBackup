# Release notes

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
