"""Edge-safe placement of small anchored popovers (coach marks) (C2 / #520).

A first-use tip must stay inside both the screen and the window that owns it.
At minimum window width the Windows acceptance run (2026-11-18) saw the
「冻结交付」 tip clipped at the window's left edge.  ``Popover.open_at`` already
clamps to the *screen*; this helper adds the *window* bound and a simple flip
above the anchor when there is not enough room below, without changing any
native window behaviour.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect


def place_within(anchor_rect: QRect, size, window_rect: QRect,
                 screen_rect: QRect, *, gap: int = 6) -> QPoint:
    """Return a top-left point for a popover of ``size`` below ``anchor_rect``.

    Order of constraints: window first (so the card never leaves its own app),
    then screen (so it never lands off-display).  When it cannot fit below the
    anchor inside the window, it flips above.  Pure function: testable on any
    platform without opening a window.
    """
    w = max(1, int(size.width()))
    h = max(1, int(size.height()))
    # Screen and window bounds intersect: the usable area for the card.
    bounds = window_rect.intersected(screen_rect)
    if bounds.isEmpty():
        bounds = screen_rect
    ax, ay, aw, ah = (anchor_rect.x(), anchor_rect.y(),
                      anchor_rect.width(), anchor_rect.height())
    x = ax + aw // 2 - w // 2              # centred under the anchor
    y = ay + ah + gap
    if y + h > bounds.bottom():
        y = ay - h - gap                    # flip above
    x = max(bounds.left(), min(x, bounds.right() - w))
    y = max(bounds.top(), min(y, bounds.bottom() - h))
    return QPoint(x, y)


def clipped(rect: QRect, bounds: QRect) -> bool:
    """Whether ``rect`` pokes outside ``bounds`` (tests + diagnostics)."""
    return (rect.left() < bounds.left() or rect.top() < bounds.top()
            or rect.right() > bounds.right() or rect.bottom() > bounds.bottom())
