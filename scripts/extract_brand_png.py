"""Derive the opaque and night lockups from the transparent day lockup.

``folith-lockup-transparent.png`` is the maintained lockup master: the
user-provided wordmark with its icon replaced by the standard 2026-10-16 mark
(see ``docs/brand.md``).  This tool derives, without redrawing anything:

    folith-lockup.png                   the master composited on white (README hero)
    folith-lockup-transparent-dark.png  same silhouette, wordmark inks recoloured

Requires Pillow and NumPy; this is an asset maintenance tool only.
"""
from pathlib import Path

import numpy as np
from PIL import Image

BRAND = Path(__file__).resolve().parents[1] / 'folith/resources/brand'
# The 1187 x 328 lockup has an icon ending at x=272; the wordmark begins at
# x=332.  This boundary is specific to that asset.
WORDMARK_X = 290


def main():
    master = Image.open(BRAND / 'folith-lockup-transparent.png').convert('RGBA')
    white = Image.new('RGBA', master.size, (255, 255, 255, 255))
    Image.alpha_composite(white, master).convert('RGB').save(BRAND / 'folith-lockup.png')
    dark = np.asarray(master).copy()
    dark[:185, WORDMARK_X:, :3] = (230, 237, 245)
    dark[185:, WORDMARK_X:, :3] = (182, 195, 211)
    Image.fromarray(dark).save(BRAND / 'folith-lockup-transparent-dark.png')


if __name__ == '__main__':
    main()
