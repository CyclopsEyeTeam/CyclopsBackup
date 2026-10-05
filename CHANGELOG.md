# Release notes

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
