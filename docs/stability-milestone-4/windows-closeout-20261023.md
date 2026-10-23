# Windows closeout — 2026-10-23

This extends [the native acceptance record](windows-native-20261023.md).
PR #68 and the evidence PR #85 are merged. Issues #44, #52 and #62 are closed;
the Windows release umbrella #9 remains open until release acceptance completes.

## Human IME and sleep/wake

The operator entered `你好，睡眠唤醒验收。` using a real Chinese input method in
an isolated frozen `selftest.docx` task, clicked outside the editor, and manually
slept and woke the Windows 11 machine. The operator confirmed completion.
The same test process remained responsive, the Chinese draft remained visible
and persisted in `translation_drafts.json` (388 bytes,
SHA-256 `58225a34c9f3c08fb3962465de316754abe28de86c9a34850e6d0431cf758cb4`).

Windows Kernel-Power events corroborate Modern Standby entry/exit: event 506 at
04:42:51.328 UTC and 507 at 04:42:52.246 UTC, then 506 at 04:43:00.697 UTC and
507 at 04:43:05.467 UTC. This verifies Modern Standby, not S3 or hibernation.
The external observer's largest sampling gap was 5.154 seconds; that is not a
Qt heartbeat measurement.

After wake, Ctrl+O opened the native Windows file panel. Selecting a generated
TXT file from a path containing Chinese characters and spaces successfully
opened its preparation page. This check used the earlier `2603643` frozen
candidate; the later code change concerns completeness sentence counting only.

## Real model task export and completeness fix

A private copy of an existing model-translated EPUB task contained 3,283 source
segments and 3,283 nonempty targets. Ordinary checked working export initially
blocked one complete collection-credit translation: paired English initials
sharing a surname were incorrectly counted as sentence endings. PR #86 strips
only those paired-name abbreviation periods before counting sentences. It does
not bypass export checks or weaken the separate short-translation gate.

After the fix, ordinary working export produced all seven assets. Every target
was found in DOCX source order; the source/target digest was unchanged. No
provider calls or edits to the original user task were made. This is a working
export, not an assertion that all existing segments passed independent review.
The final `2c5fb1d` frozen executable also opened another private copy and
exported through its visible delivery page and the native Windows directory
panel. All seven assets were written, all 3,283 targets were found in DOCX source
order, and the source/target snapshot digest remained unchanged. The existing
stale uncommitted draft was retained and was not saved over the committed target.

The generated 4,000-segment frozen forced-termination/recovery/final-delivery
check remains separately documented in the earlier record.

The new regression suite plus Arabic RTL cases passed (54 cases), and the full
completeness red-team file passed (18 cases). Synthetic names appear in tests;
private book text, credentials, paths and translations are not published.

## Final local candidate

A fresh detached checkout at `2c5fb1d00108e4a43950120547dc16cb3032f06e`
built the portable ZIP and NSIS installer. The artifact checker passed with no
source drift. Native frozen DOCX/PDF main flows passed, including PDF source
rendering/highlights, editing, review, delivery and reopening. Bundled OCR
passed English, Simplified Chinese and Arabic, with the missing-language-pack
negative check also passing.

| Asset | SHA-256 |
| --- | --- |
| Folith-Qt-0.7.1-windows-x64.zip | `39b1d1873f86926af0a9e0e0e496ff26a88def2e37f33152060f3c9d3aac3802` |
| Folith-Setup-0.7.1-windows-x64.exe | `80ac353ea343ff6c8860b6a017e800c7a4bc609a8fc1745f3c75487deb68977b` |

## Interactive uninstaller Retry/Cancel

The operator manually opened the isolated installer test's uninstaller while a
Windows file handle prevented replacement of its executable. Retry remained
blocked, and Cancel exited. The backend then verified that the app, registration
and user marker were still present. After releasing the handle, ordinary silent
cleanup succeeded. There was no pre-existing registered install or Start Menu
shortcut folder at this test's start. This directly verifies uninstaller
Retry/Cancel; install-side Retry/Cancel was not separately clicked. Both silent
install and uninstall live-process refusal were verified in the earlier record.

Full release CI was explicitly requested with `ci:release` on PR #86; its
completion and publication are still pending
at this point in the record. Local raw reports remain in `.qa-artifacts/milestone4/`.

The final local installer also passed the complete backend round trip: install
and uninstall while locked both returned exit code 2, upgrade removed stale
runtime files, user files remained, and fresh uninstall removed its own folder.

## Scope limitations

Native hardware acceptance is Windows 11 x64, build 26200. Windows 10, ARM64,
S3/hibernation and a separate clean Windows desktop VM were not manually tested.
The original performance VM was not rerun. Controlled concurrency still shows
serialized commit costs; higher concurrency does not promise equal wording or
linear speedup. Packages are unsigned. These are documented limits, not passed
tests.
