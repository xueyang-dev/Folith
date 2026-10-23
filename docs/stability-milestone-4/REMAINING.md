# Acceptance ledger — 2026-10-23 closeout

[Windows native evidence](windows-native-20261023.md) and
[the closeout record](windows-closeout-20261023.md) supersede the earlier Windows
pending checklist. PR #68 and evidence PR #85 are merged; #44, #52 and #62 are
closed. The Windows support/release umbrella is [#9](https://github.com/xueyang-dev/Folith/issues/9);
its live state and milestone state are tracked on GitHub. Native acceptance and
full release validation are complete.

| Required closeout | Current state |
| --- | --- |
| Final Windows candidate | Clean build at 2c5fb1d; native frozen DOCX/PDF, bundled OCR and artifact checks pass |
| IME, native Unicode file panel, sleep/wake | Passed with operator input and recorded Modern Standby events; draft survived |
| Installer Retry/Cancel | Operator verified uninstaller Retry remained blocked and Cancel exited; app/registration/user file preserved, cleanup passed; silent live-process guards and 0.7.0 upgrade already passed |
| Full release CI | [Passed](https://github.com/xueyang-dev/Folith/actions/runs/39902584068): Linux 3.10/3.11/3.12, complete Windows suite and both platform packages |
| Downloadable v0.7.1 release | [Release location](https://github.com/xueyang-dev/Folith/releases/tag/v0.7.1): verified Windows installer/ZIP, CI macOS DMG and combined SHA-256 checksums |

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
