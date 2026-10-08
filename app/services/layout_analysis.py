from __future__ import annotations

from statistics import median
from typing import Any

from app.utils.coordinates import normalized_box


def infer_ocr_tables(
    ocr_objects: list[dict[str, Any]],
    page_number: int,
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    table_rows: list[dict[str, Any]] = []
    for line in ocr_objects:
        words = [w for w in line.get("metadata", {}).get("words", []) if w.get("text")]
        if len(words) < 2:
            continue
        words = sorted(words, key=lambda w: float(w["bounding_box"][0]))
        heights = [max(1.0, float(w["bounding_box"][3]) - float(w["bounding_box"][1])) for w in words]
        threshold = max(18.0, median(heights) * 1.8)
        cells: list[list[dict[str, Any]]] = [[words[0]]]
        for word in words[1:]:
            previous = cells[-1][-1]
            gap = float(word["bounding_box"][0]) - float(previous["bounding_box"][2])
            if gap >= threshold:
                cells.append([word])
            else:
                cells[-1].append(word)

        if len(cells) < 2:
            continue
        table_rows.append(
            {
                "top": min(float(w["bounding_box"][1]) for w in words),
                "bottom": max(float(w["bounding_box"][3]) for w in words),
                "cells": [_cell_payload(cell) for cell in cells],
            }
        )

    if not table_rows:
        return []

    table_rows.sort(key=lambda row: row["top"])
    groups: list[list[dict[str, Any]]] = []
    for row in table_rows:
        if not groups:
            groups.append([row])
            continue
        previous = groups[-1][-1]
        prev_height = max(1.0, previous["bottom"] - previous["top"])
        close_vertically = row["top"] - previous["bottom"] <= max(22.0, prev_height * 2.5)
        similar_columns = abs(len(row["cells"]) - len(previous["cells"])) <= 1
        if close_vertically and similar_columns:
            groups[-1].append(row)
        else:
            groups.append([row])

    tables: list[dict[str, Any]] = []
    for group in groups:
        max_columns = max(len(row["cells"]) for row in group)
        # Two-row tables need at least 3 visible columns; two-column layouts need
        # three rows. This avoids classifying ordinary spaced prose as a table.
        if len(group) < 2 or (len(group) == 2 and max_columns < 3):
            continue
        if not _columns_align(group, page_width):
            continue

        cells = [cell for row in group for cell in row["cells"]]
        box = [
            round(min(cell["bounding_box"][0] for cell in cells), 2),
            round(min(cell["bounding_box"][1] for cell in cells), 2),
            round(max(cell["bounding_box"][2] for cell in cells), 2),
            round(max(cell["bounding_box"][3] for cell in cells), 2),
        ]
        index = len(tables) + 1
        tables.append(
            {
                "object_id": f"p{page_number}-table-ocr-{index}",
                "object_type": "table",
                "page_number": page_number,
                "bounding_box": box,
                "normalized_box": normalized_box(box, page_width, page_height),
                "text": "\n".join(" | ".join(cell["text"] for cell in row["cells"]) for row in group),
                "font_size": None,
                "confidence": 0.72,
                "metadata": {
                    "source": "ocr_layout",
                    "row_count": len(group),
                    "column_count": max_columns,
                    "rows": [row["cells"] for row in group],
                },
            }
        )
    return tables


def _cell_payload(words: list[dict[str, Any]]) -> dict[str, Any]:
    box = [
        round(min(float(w["bounding_box"][0]) for w in words), 2),
        round(min(float(w["bounding_box"][1]) for w in words), 2),
        round(max(float(w["bounding_box"][2]) for w in words), 2),
        round(max(float(w["bounding_box"][3]) for w in words), 2),
    ]
    return {
        "text": " ".join(str(w.get("text", "")).strip() for w in words if str(w.get("text", "")).strip()),
        "bounding_box": box,
        "confidence": round(sum(float(w.get("confidence", 0)) for w in words) / max(len(words), 1), 4),
    }


def _columns_align(rows: list[dict[str, Any]], page_width: float) -> bool:
    if len(rows) < 2:
        return False
    tolerance = max(28.0, page_width * 0.06)
    reference = max(rows, key=lambda row: len(row["cells"]))
    ref_centers = [_center(cell["bounding_box"]) for cell in reference["cells"]]
    aligned_rows = 0
    for row in rows:
        centers = [_center(cell["bounding_box"]) for cell in row["cells"]]
        matches = sum(1 for center in centers if any(abs(center - ref) <= tolerance for ref in ref_centers))
        if matches >= min(2, len(centers)):
            aligned_rows += 1
    return aligned_rows >= max(2, len(rows) - 1)


def _center(box: list[float]) -> float:
    return (float(box[0]) + float(box[2])) / 2


def boxes_overlap_ratio(first: list[float], second: list[float]) -> float:
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[2]), float(second[2]))
    bottom = min(float(first[3]), float(second[3]))
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    first_area = max(1.0, (float(first[2]) - float(first[0])) * (float(first[3]) - float(first[1])))
    return intersection / first_area


def merge_ocr_text(native_objects: list[dict[str, Any]], ocr_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    native_text = [obj for obj in native_objects if obj.get("object_type") == "text"]
    native_normalized = {_normalize(obj.get("text", "")) for obj in native_text if obj.get("text")}
    merged: list[dict[str, Any]] = []
    for ocr in ocr_objects:
        normalized = _normalize(ocr.get("text", ""))
        if normalized and normalized in native_normalized:
            continue
        duplicate = False
        for native in native_text:
            if boxes_overlap_ratio(ocr["bounding_box"], native["bounding_box"]) >= 0.75:
                native_value = _normalize(native.get("text", ""))
                if normalized == native_value or normalized in native_value or native_value in normalized:
                    duplicate = True
                    break
        if not duplicate:
            merged.append(ocr)
    return merged


def table_overlaps_existing(table: dict[str, Any], existing: list[dict[str, Any]]) -> bool:
    return any(
        item.get("object_type") == "table"
        and (
            boxes_overlap_ratio(table["bounding_box"], item["bounding_box"]) >= 0.6
            or boxes_overlap_ratio(item["bounding_box"], table["bounding_box"]) >= 0.6
        )
        for item in existing
    )


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).casefold()
