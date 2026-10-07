# Showcase candidates

Candidates are the best-CER pages under the recommended config (`tessdata_best`, 350 DPI, PSM 6), **excluding pages whose full-page production parse is corrupted by the quote-parser bug**. All are real 1882 scan pages with the original aged paper and RTL layout.

Excluded for parser corruption: p5, p100, p140, p180.

## page 0020 — easy

- **CER 8.2%** · folded 7.4% · WER 37.5% · mean confidence 68.4 · missing lines 0

- GT : `مقصودي هذا لا يتم الا بطلبه للعلم فانه الكاشف للبصيرة والمنور`

- OCR: `متصودي هذا لا يتم ال بطلبه للعلم فانه الكاشف للبصيرة والمنور`

- Why: aged paper and RTL layout are visible, the passage is representative prose, and the output is readable without cherry-picking a single lucky line.

## page 0220 — normal

- **CER 12.0%** · folded 10.5% · WER 43.5% · mean confidence 65.6 · missing lines 0

- GT : `المنزل تحت نظرك وتلاحظي حركاتهم وسكناتهم واخلاقهم فاني`

- OCR: `المزل تحت نظرك وتلاحظي حركاتم وسكاءم وإخلاق, فاني`

- Why: aged paper and RTL layout are visible, the passage is representative prose, and the output is readable without cherry-picking a single lucky line.

## page 0169 — hard

- **CER 14.0%** · folded 12.7% · WER 50.0% · mean confidence 68.2 · missing lines 0

- GT : `برام الحجارة والصاد قدور الصفر والنحاس قال ابن جني`

- OCR: `٠‏ برام احجارة والصاد قدور الصفر وا تحاس قال ابن جنى`

- Why: aged paper and RTL layout are visible, the passage is representative prose, and the output is readable without cherry-picking a single lucky line.


## Not recommended for showcase

Excluded for Poor CER: p211 (26.2%), p7 (23.0%). p180 has the best region CER but its full-page production parse is corrupted by the quote-parser bug and must not be shown as-is.

