"""Remove the official raster lockup's white matte without redrawing it.

Requires Pillow, NumPy and SciPy; this is an asset maintenance tool only.
The white ink enclosed inside the colored icon remains opaque.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes, distance_transform_edt

BRAND = Path(__file__).resolve().parents[1] / 'folith/resources/brand'


def main():
    source = np.asarray(Image.open(BRAND / 'folith-lockup.png').convert('RGB'))
    rgb = source.astype(float)
    # The official 1187 x 328 lockup has an icon ending at x=272; the
    # wordmark begins at x=332. This boundary is specific to that asset.
    icon = np.zeros(source.shape[:2], dtype=bool)
    icon[:, :290] = binary_fill_holes(source[:, :290].min(axis=2) < 190)
    solid = (source.min(axis=2) < 150) | icon
    _, nearest = distance_transform_edt(~solid, return_indices=True)
    foreground = rgb[nearest[0], nearest[1]]
    # Undo white compositing at antialiased edges, using nearby original
    # foreground color. Keep all opaque interior pixels exactly as supplied.
    strength = np.maximum(255 - foreground, 1)
    alpha = np.clip(((255 - rgb) / strength).mean(axis=2), 0, 1)
    alpha[source.min(axis=2) >= 245] = 0
    alpha[solid] = 1
    restored = np.clip(255 - (255 - rgb) / np.maximum(alpha[..., None], .001), 0, 255)
    restored[solid] = rgb[solid]
    rgba = np.dstack((restored, alpha * 255)).round().astype('uint8')
    Image.fromarray(rgba).save(BRAND / 'folith-lockup-transparent.png')
    # Same original alpha silhouette and colored icon in every theme.
    # Only the wordmark and subtitles change ink color on the dark canvas.
    dark = rgba.copy()
    dark[:185, 290:, :3] = (230, 237, 245)
    dark[185:, 290:, :3] = (182, 195, 211)
    Image.fromarray(dark).save(BRAND / 'folith-lockup-transparent-dark.png')


if __name__ == '__main__':
    main()
