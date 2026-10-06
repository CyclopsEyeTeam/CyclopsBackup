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
filesystems are supported, and the review estimate still uses size/mtime while
actual copying uses checksums. See the README before relying on the backup.
