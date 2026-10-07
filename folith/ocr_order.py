"""Order geometric lines while retaining their logical Unicode strings."""
from typing import Any, Dict, List


def order_lines(lines: List[Dict[str, Any]], direction: str) -> List[Dict[str, Any]]:
    # Group fragments whose vertical centres fall on the same printed line.
    # Do not reverse characters, numerals or Latin spans within engine text.
    rows = []
    for line in sorted(lines, key=lambda x: (x["top"], x["left"])):
        centre = (line["top"] + line["bottom"]) / 2
        row = next((r for r in reversed(rows) if
                    abs(centre - r[0]) <= min(line["bottom"] - line["top"], r[1]) * .45), None)
        if row is None:
            rows.append([centre, line["bottom"] - line["top"], [line]])
        else:
            row[2].append(line)
    return [line for row in rows for line in sorted(
        row[2], key=lambda x: -x["right"] if direction == "rtl" else x["left"])]
