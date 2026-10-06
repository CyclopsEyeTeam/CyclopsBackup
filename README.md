# Cyclops Backup

An independent Linux utility for weekly physical backups of project folders,
Git repositories and evidence to a USB drive. Choose folders, review the copy
estimate, back up, verify, then safely eject. Studio, Operator and other Cyclops
applications are not required. There is no background service or cloud account.

**v0.1.0 is a public preview.** Automated tests exercise real rsync and Git on
small fixtures. A complete physical USB backup of a large real project set has
not yet been validated. Start by reviewing and backing up a small folder.

## Install on Zorin / Ubuntu

Requires Linux, Python 3.10+, rsync, Git and util-linux (`lsblk`, `findmnt`, `sync`).
The desktop wizard also needs Zenity. On Zorin or Ubuntu:

```sh
sudo apt install python3 rsync git util-linux zenity desktop-file-utils
```

Download and extract a release from
[GitHub Releases](https://github.com/CyclopsEyeTeam/CyclopsBackup/releases),
or clone the repository:

```sh
git clone https://github.com/CyclopsEyeTeam/CyclopsBackup.git
cd CyclopsBackup
python3 install.py
```

Run the installer from the extracted folder if you downloaded a ZIP. Keep the
folder in its final location: the launcher points to it. Installation creates
**Cyclops Backup** in the application menu and on the desktop. If prompted,
right-click the desktop shortcut and select **Allow Launching**. A different
existing launcher is left untouched; the installer reports the conflict.

## First setup and weekly use

1. Connect a mounted USB formatted as **ext4, xfs or btrfs**. The utility never
   formats, mounts or automatically ejects a drive. FAT/exFAT/NTFS are refused
   because this version requires Linux permissions and symlinks.
2. Launch **Cyclops Backup**. On first use, select the actual project/evidence
   folders and give each a unique backup name. Choose the USB by its displayed
   label and UUID. Setup copies nothing.
3. Close applications and recording tools that write to the selected folders.
4. Review every source, destination, exclusion, estimate and available space.
   Tick the confirmation checkbox and choose **Run Backup**.
5. Leave the USB connected while copying and SHA-256 verification finish.
6. Only after **SAFE TO EJECT USB**, use Files → Eject, then unplug it.

Later launches open the **control panel**: review and back up, preview what will
be copied, choose or change the backup USB, add or remove folders, leave out a
file or subfolder, or put a left-out item back. Every change is saved straight
away and copies nothing. Later launches reuse the explicit registry and pinned filesystem UUID. No
projects are discovered automatically. Missing/moved/empty folders, a missing
USB, insufficient space, changed source data or failed verification block success.
Reformatting a USB changes its UUID and requires configuration again.

Settings are private to the current user:

- Registry: `$XDG_CONFIG_HOME/cyclops-backup/registry.json`, normally
  `~/.config/cyclops-backup/registry.json`.
- Reports: `$XDG_STATE_HOME/cyclops-backup/reports`, normally
  `~/.local/state/cyclops-backup/reports`.

The release contains no configured project folders, USB identities or personal
backup reports. Settings and reports can contain private paths; keep them out of
public repositories. Registry files are written with mode 0600. Backups themselves
are ordinary files, without encryption supplied by this utility.

## Add or remove folders, files and the USB

Everything below is in the desktop control panel. The same controls work from a
terminal without a desktop:

```sh
python3 cyclops-backup project-add --name MyProject --source "/absolute/path/My Project"
python3 cyclops-backup project-add --name Evidence --source "/absolute/path/Evidence" --archive
python3 cyclops-backup devices
python3 cyclops-backup configure --uuid YOUR_FILESYSTEM_UUID
python3 cyclops-backup projects                     # what is registered
python3 cyclops-backup project-remove --name Evidence
python3 cyclops-backup exclude --name MyProject --path "/absolute/path/My Project/cache" --reason "rebuildable cache"
python3 cyclops-backup include --name MyProject --path cache
python3 cyclops-backup preview
python3 cyclops-backup preview --files               # list every item the next run copies
python3 cyclops-backup preview --files --exact       # compare contents like the real copy
python3 cyclops-backup run
```

**Removing a folder** only takes it out of the backup set. Its source folder and
any copy already on the USB are left exactly as they are.

**Leaving out a file or subfolder** needs a reason and is refused for Git-tracked
files, `.git` itself, anything outside the folder, and names containing `*`, `?`
or `[` (rsync would read those as wildcards). Single files outside a registered
folder cannot be added on their own; register the folder that holds them.

**Copy preview** is read-only. It runs the normal review first, then lists each
folder's items as NEW (not on the USB yet), CHANGED (will be updated; the old USB
copy goes to PreviousVersions) or PERMS (only permissions/time). Quick mode
compares size and time; **Exact** compares contents with checksums exactly like
the real copy, so it reads every file on both sides and is slow on large folders.
The full list is saved as `RUN.changes.txt` with the local reports.

`project-add` checks the source, records its canonical location, and copies nothing.
Direct symlink roots are refused; parent-directory aliases are resolved to prevent
duplicate registrations and source/USB overlap. Hand-edited sources must use their
canonical absolute paths. Use `--archive`
to place an evidence root under `Archives` instead of `Projects`. To remove a
folder from the backup set, remove only its object from the registry's `projects`
array. This neither modifies its source nor deletes any existing USB copy.
Review again after edits. Every remaining entry is required.

[`registry.example.json`](registry.example.json) explains the structure. Use
`--config /absolute/path/registry.json` before the command for another registry.
`required_markers` can list files whose disappearance should block backup.
An optional `source_uuid` pins a source on a separate mounted filesystem.

There are **no default exclusions**: `.git`, untracked/ignored files, evidence,
archives and data are included. Add an exclusion only when its contents are
proven reconstructible; each needs a reason. Git-tracked exclusions are refused
when the registered source is a Git repository. Avoid broad exclusions inside
folders containing nested repositories or unique evidence.

## Copy/update and verification behavior

- Reuses the same destination folders; it does not make a full snapshot per run.
- Actual copying uses `rsync --checksum`. Matching file contents are not copied;
  only new/changed files transfer. Changed local files are copied in full, while
  metadata may be updated even if contents match.
- Never uses `--delete`. Source-deleted files remain on USB. Replaced files are
  retained under dated `Archives/PreviousVersions` folders. History is not pruned
  automatically.
- Git folders use the same copying rule, including `.git` and included untracked
  data. Branch/HEAD information is reported, not used to decide copying.
- SHA-256 verification reads both copies of **every included regular file**,
  including unchanged ones. Modes and symlink targets are checked too. Zero
  bytes copied still entails substantial disk reads.
- The review shows total included data and **estimated** transfer separately.
  Its fast size/mtime check can differ from the actual checksum decision: touched
  identical files can overstate transfer; same-size/mtime corruption can understate
  it. Use the copy preview for a per-item list. Transfer counts are
  logical file-content bytes, not physical USB writes or hashing reads.

Linux modes, symlinks and hard links are preserved. Owner/group identities,
ACLs and extended attributes are not preserved. External symlink targets are
not followed. Git worktrees require their common repository to be registered;
external Git object stores and symlinked Git metadata are refused.

## USB layout and reports

```text
CyclopsBackup/
  Projects/                  current registered projects
  Archives/                  registered evidence roots
    PreviousVersions/RUN/    replaced files from that run
  Manifests/RUN.json         inventory, hashes, Git identity and results
  Manifests/RUN.registry.json
  Logs/RUN.txt               readable result
  Logs/RUN.PROJECT.rsync.txt  copy statistics
```

Saved reports record failures or data verification. **Eject authorization appears
only on screen after the final USB flush and device checks.** A saved report cannot
authorize ejecting a current/later run; its `safe_to_eject` field remains false.
Interrupted or failed runs retain successful copies for a later incremental retry.

## Recovery

Keep the original USB untouched. Copy into a new local folder; substitute your
mountpoint and project name:

```sh
rsync -aH -- "/path/to/mounted/USB/CyclopsBackup/Projects/MyProject/" "/path/to/Recovered MyProject/"
python3 cyclops-backup verify "/path/to/mounted/USB/CyclopsBackup/Manifests/RUN.json" --root "/path/to/mounted/USB/CyclopsBackup"
```

Use a recent complete/verified manifest. Verification also works against a
recovered layout containing `Projects`/`Archives`, without the original machine.
It detects changes, not repairs them. Older manifests can fail after later updates;
recover replaced files from `PreviousVersions` into a separate recovery folder.
Retained source-deleted files are extra to the current manifest and are not part
of that run's hashing scope. Manifests are not signed against malicious editing.

Check `git status`/`git log` after restoring a repository. Git bundles can be cloned
with `git clone file.bundle new-folder`. For linked worktrees, restore the common
repository too and run `git worktree repair NEW_WORKTREE_PATH`; repair absolute
links explicitly. Rebuild any deliberately excluded dependencies separately.

## Development

```sh
TMPDIR=/path/to/development/scratch python3 -m unittest discover -s tests -v
```

Fixtures exercise real copying/hashing/sync on tiny synthetic folders; no live
project or physical USB is used. See [validation](docs/validation.md) and
[release notes](CHANGELOG.md). Contributions and bug reports are welcome through
GitHub. Reports containing personal paths should be redacted before sharing.

Licensed under the [MIT licence](LICENSE). The external system tools retain their
own licences; they are not bundled in this source release.
