# Remaining acceptance — 2026-10-23 closeout

[Windows native evidence](windows-native-20261023.md) and
[the closeout record](windows-closeout-20261023.md) supersede the earlier Windows
pending checklist. PR #68 and evidence PR #85 are merged; #44, #52 and #62 are
closed. The only open milestone item is #9, Windows support and publication.

| Required closeout | Current state |
| --- | --- |
| Final Windows candidate | Clean build at 2c5fb1d; native frozen DOCX/PDF, bundled OCR and artifact checks pass |
| IME, native Unicode file panel, sleep/wake | Passed with operator input and recorded Modern Standby events; draft survived |
| Installer Retry/Cancel | Operator verified uninstaller Retry remained blocked and Cancel exited; app/registration/user file preserved, cleanup passed; silent live-process guards and 0.7.0 upgrade already passed |
| Full release CI | Explicitly triggered on PR #86; Linux 3.10/3.11/3.12, complete Windows suite and both platform packages must finish |
| Downloadable v0.7.1 release | Local Windows installer and portable ZIP ready; publish after checks and manual feedback |

The 4,000-segment generated frozen recovery/final-delivery test and the
3,283-segment existing real-model task's checked working export are separate
pieces of evidence. No original user task was modified, no full long-document
model rerun or independent review is claimed, and real model output was not
replaced with stub targets to pass export.

Windows 10, Windows ARM64, an independent clean desktop VM, the original
performance VM and S3/hibernation remain untested scope limitations. Native
hardware acceptance is Windows 11 x64 build 26200. Controlled throughput retains
serialized costs and does not establish identical quality across concurrency
settings. macOS manual limitations remain documented separately.
