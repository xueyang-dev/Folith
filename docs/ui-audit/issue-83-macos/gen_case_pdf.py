"""Generate a 49-page English PDF equivalent to the Windows #83 evidence task."""
import sys
import pymupdf

OUT = sys.argv[1]

doc = pymupdf.open()
paras_total = 0
titles = [
    "General Description", "System Overview", "Accident Analysis", "Instrumentation",
    "Reactor Coolant System", "Engineered Safety Features", "Electrical Systems",
    "Containment Systems", "Radiological Considerations",
]
for page_index in range(49):
    page = doc.new_page(width=595, height=842)
    y = 72.0
    chapter = titles[page_index % len(titles)]
    page.insert_text(pymupdf.Point(72, y), f"Chapter {page_index % 9 + 1} {chapter}", fontsize=14)
    y += 28
    if page_index % 7 == 2:
        page.insert_text(pymupdf.Point(72, y), f"2.2 Accident Sequence of unit {page_index}", fontsize=12)
        y += 24
    for para in range(6):
        paras_total += 1
        if page_index % 5 == para % 5:
            text = (f"8.5 The value 8.5 governs subsection 8.5.{paras_total} of paragraph "
                    f"{paras_total} on page {page_index + 1}; margin 8.5 applies.")
        else:
            text = (f"Paragraph {paras_total} describes the functional requirement of the "
                    f"{chapter.lower()} subsystem with reference to page {page_index + 1}.")
        rect = pymupdf.Rect(72, y, 523, y + 52)
        page.insert_textbox(rect, text, fontsize=10.5, align=0)
        y += 62
    page.insert_text(pymupdf.Point(72, 812), f"Case study draft - page {page_index + 1} of 49", fontsize=8)

doc.save(OUT)
doc.close()
print("pages:", 49, "paragraphs:", paras_total)
