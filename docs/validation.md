# v1.1.0 release validation

Validated on Linux on 2026-10-10 with Python 3.11, 3.12 and 3.13, rsync 3.2.7,
Zenity 4.0.1 and GTK 3: **127 tests** pass (one skipped where it needs to run as
an ordinary user). Syntax is checked against Python 3.10.

Investigation that led to this release (read-only, on the author's own reports):
two real runs of 26.8 GiB / 234,031 files wrote only 37.5 MiB on the second run,
but read every file about four times (rsync `--checksum` on both sides, then
SHA-256 on both sides), taking 40–60 minutes; both runs then failed their final
snapshot because a live folder changed during that window.

Changed and covered by tests:

- Copies compare size and nanosecond time (`--modify-window=-1`), so unchanged
  files are not read to decide; an unchanged second run writes nothing, a
  one-file change writes exactly that file and keeps its old version.
- Full check still SHA-256 verifies every file; same-size/same-time damage to a
  USB copy is detected and repaired by a single checksum retry of that folder.
- Quick check: only copied files (and any with a differing USB time) are hashed;
  manifests record `scope: quick`, and `verify` still checks every file's size
  and mode and every recorded hash. Full-check manifests are verified exactly as
  before.
- One fresh retry for a folder that changes during copy, verification or before
  the final snapshot; a folder that keeps changing fails with advice.
- rsync's escaped names (`\#ooo`, e.g. newlines and accents) are decoded, so
  copied-file lists match the inventory.
- Honest counters in progress, popup, reports and terminal; compact panel header;
  quick-check row; **Back up now** after a preview, still through review.

Fixture timing (400 × 1 MiB, page-cached, second run after one change):
1.0.0 3.8 s; 1.1.0 full check 3.6 s; quick check 0.3 s. On a real USB the full
check saves a whole read pass of both copies; actual times depend on the drive.

# v1.0.0 release validation

Validated on Linux on 2026-10-06 with Python 3.11, 3.12 and 3.13, rsync 3.2.7,
Zenity 4.0.1 and GTK 3: **107 tests** pass (one skipped where it needs to run
as an ordinary user: unreadable-file denial). Syntax is checked against Python
3.10 for Ubuntu 22.04. The copy, verification and manifest engine (`core.py`,
`report.py`) is byte-identical to 0.2.0.

Added since 0.2.0 and covered by tests:

- USB gate: backup and preview stay locked until the chosen USB is connected and
  verified; the waiting window closes itself once it is.
- Profiles: separate folder lists, switching, copying, renaming and deleting;
  older settings load as "Default"; runs and manifests record only the active
  profile; two profiles cannot share a USB destination for different folders.
- Folders window (GTK 3): real GTK dialog tests fill in the dialogs and press OK,
  covering the 0.3.0 bug where typed names were read after the dialog closed.
- End-of-backup popup, and the Zenity 4 `--no-cancel` refusal that kept result
  and preview windows from opening.
- The Cyclops look: private stylesheet, both GTK stylesheets parse, the person's
  own GTK settings are never written, and plain mode falls back cleanly.
- Installer: eye icon, updating its own older launcher, leaving others alone.

Every panel and window was also rendered on a virtual display and checked by
eye. The author confirmed a real backup and the new panels on Zorin OS before
release; a full physical backup of a large project set remains the person's
own acceptance step.

# v0.2.0 additions

Validated on Linux with Python 3.12 and rsync 3.2.7 on 2026-10-06: **62 tests**
pass (45 original + 17 control/preview). The 0.1.0 copy, verification and
manifest functions are byte-identical; preview and registry controls are new
functions. Preview tests prove no USB or source bytes change, exact mode detects
same-size/time corruption that quick mode cannot, and exclusions/removals never
alter sources or existing USB copies. Desktop panel tests replace only Zenity
responses. Native Zenity rendering was not exercised on a physical desktop.

# v0.1.0 release validation

Validated on Linux with Python 3.12, system rsync and Git on 2026-10-05.
The full unittest suite passed: **45 tests**. Tiny synthetic source and destination
folders were created in a dedicated HDD scratch run. No live project was copied,
and no physical USB backup was started. Temporary fixtures were cleaned by their
own test lifecycle; old scratch runs were not removed.

The original 34 engine checks cover dry-run behavior, missing/ambiguous/internal
drives, filesystem restrictions, missing/moved/empty projects, spaces and unusual
file names, Git/non-Git evidence, linked worktrees, hard links and modes, exclusions,
zero-transfer incremental updates, retained deletions and previous versions,
same-size/time corruption repair, changing sources, destination symlinks, device
disappearance/mount replacement, cancellation, space/sync/report failures and
independent manifest verification. GUI approve/cancel paths use synthetic desktop
responses while exercising the real fixture engine.

Eleven public-setup/recovery checks cover XDG settings location, source registration with
spaces and no exclusions, evidence destinations, duplicate refusal without
overwriting settings, invalid-source refusal, unconfigured preview refusal,
first-run cancellation, selected-folder registration, duplicate parent aliases,
USB aliases, refusal of hand-edited noncanonical sources, and independent verification
after the original source path changes to a symlink. Registry files are
private (0600). Public copying and verification code is byte-identical to the
original fixture-validated engine.

Public source was checked for syntax and for personal source paths, filesystem
UUIDs, private report references and common credential patterns. The repository
starts with fresh public history, without the original configured registry or
its earlier commits. GitHub Actions runs fixture tests on Ubuntu 22.04 and 24.04.

## Limits of this evidence

Fixtures replace the external block-device and human-response boundaries. They
exercise real files, Git, rsync, SHA-256 and filesystem flushing, but do not prove
a specific USB's reliability or a full large dataset backup. Native first-run
desktop interaction is not part of the automated suite. Only Linux-native USB
filesystems are supported. See the README before relying on the backup.
