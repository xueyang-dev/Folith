# Workbench theme foreground refresh (#119)

Windows, 2026-10-24. Baseline: `29285f5`. Fixed source: this change.

## Observed behavior

Changing Light to Dark while a translated task is open changed the background
and editor but left unselected target cells using the Light foreground. The
normal 90-switch probe captured this transient failure. Pausing the 1000 ms
poll after initial loading makes the missing immediate refresh deterministic:
`#131c2e` remained on Dark until `_render_segments()` ran again.

The fix explicitly repaints row brushes after applying the theme, including
accent-only changes. It uses the existing visible-first/chunked painting path
without reloading task state or remeasuring row heights.

## Native Windows evidence

Both images use Qt's real `windows` platform and synthetic showcase text. The
polling timer was paused after task loading. Settings followed the sequence
Light → Dark → Light → Dark. The fixed screenshot was taken before any explicit
row refresh. Foregrounds now follow each choice immediately:

| Appearance | Baseline target RGB | Fixed target RGB |
| --- | --- | --- |
| Light | `#131c2e` | `#131c2e` |
| Dark | `#131c2e` (wrong) | `#ffffff` (92% alpha) |

Before:

![Target cells retain dark text](before.png)

After:

![Target cells repaint immediately](after.png)

## Automated coverage

The settings-path regression first failed against the baseline with the stale
foreground. It stops polling and checks colors without pumping the event loop,
covering normal translations, untranslated placeholders, drafts, and accent
colors. It also preserves unsaved text, cursor/selection, undo availability,
selected segment, dirty state and scroll offsets. Separate cases exercise live
follow-system changes and tables large enough to use chunked painting.

The theme suite is included in the shared Windows/macOS CI workflow. Its system
appearance signals are simulated; this is not real Cocoa or actual OS
light/dark-switch acceptance. Native macOS and actual OS appearance changes
remain to be verified. This evidence concerns #119, not the distinct white
inspector shown in an earlier user screenshot.
