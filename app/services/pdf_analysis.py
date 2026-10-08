import os
from pathlib import Path
from typing import Any

import fitz

from app.services.document_history import initialize_document
from app.services.image_detection import bbox, detect_embedded_images
from app.services.layout_analysis import infer_ocr_tables, merge_ocr_text, table_overlaps_existing
from app.services.ocr import normalize_ocr_languages, ocr_page, page_needs_ocr, script_profile
from app.utils.coordinates import normalized_box, page_region, render_dimensions
from app.utils.validation import validate_pdf_page_count


PDF_RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "144"))


def analyze_pdf_document(document_id: str, document_dir: Path, pdf_path: Path, original_filename: str) -> dict[str, Any]:
    validate_pdf_page_count(pdf_path)
    pages: list[dict[str, Any]] = []
    all_objects: list[dict[str, Any]] = []
    scanned_pages: list[int] = []
    counts = {"text": 0, "image": 0, "table": 0}

    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            page_payload, scanned = analyze_page(page, page_index, document_dir)
            if scanned:
                scanned_pages.append(page_index)
            pages.append(page_payload)
            all_objects.extend(page_payload["objects"])
            counts["text"] += sum(1 for item in page_payload["objects"] if item["object_type"] in {"text", "header", "footer"})
            counts["image"] += sum(1 for item in page_payload["objects"] if item["object_type"] == "image")
            counts["table"] += sum(1 for item in page_payload["objects"] if item["object_type"] == "table")

        languages = sorted({
            page.get("detected_language", "unknown")
            for page in pages
            if page.get("detected_language") not in {"", "unknown"}
        })
        analysis_payload = {
            "page_count": document.page_count,
            "scanned_pages": scanned_pages,
            "detected_languages": languages,
            "ocr_languages": normalize_ocr_languages().split("+"),
            "pages": pages,
            "objects": all_objects,
            "text_object_count": counts["text"],
            "image_object_count": counts["image"],
            "table_object_count": counts["table"],
        }

    return initialize_document(document_id, pdf_path, original_filename, analysis_payload)


def analyze_page(page: fitz.Page, page_number: int, document_dir: Path) -> tuple[dict[str, Any], bool]:
    page_width = round(float(page.rect.width), 2)
    page_height = round(float(page.rect.height), 2)
    render_width, render_height = render_dimensions(page_width, page_height, PDF_RENDER_DPI)

    native_text = detect_text_blocks(page, page_number)
    native_tables = detect_tables(page, page_number)
    images = detect_embedded_images(page, page_number)
    objects = [*native_text, *native_tables, *images]

    scanned = page_needs_ocr(page)
    ocr_objects: list[dict[str, Any]] = []
    if scanned:
        ocr_objects = ocr_page(page, page_number, document_dir)
        unique_ocr = merge_ocr_text(native_text, ocr_objects)
        objects.extend(unique_ocr)

        inferred_tables = infer_ocr_tables(ocr_objects, page_number, page_width, page_height)
        objects.extend(table for table in inferred_tables if not table_overlaps_existing(table, objects))

    combined_text = "\n".join(
        str(item.get("text", ""))
        for item in objects
        if item.get("object_type") == "text" and item.get("text")
    )
    profile = script_profile(combined_text)

    return (
        {
            "page_number": page_number,
            "page_width": page_width,
            "page_height": page_height,
            "render_width": render_width,
            "render_height": render_height,
            "ocr_used": scanned,
            "ocr_languages": normalize_ocr_languages().split("+") if scanned else [],
            "detected_language": profile["language"],
            "text_direction": profile["direction"],
            "objects": objects,
        },
        scanned,
    )


def detect_text_blocks(page: fitz.Page, page_number: int) -> list[dict[str, Any]]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    objects: list[dict[str, Any]] = []
    text_index = 0

    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        rect = fitz.Rect(block.get("bbox", [0, 0, 0, 0]))
        text_parts: list[str] = []
        sizes: list[float] = []
        colors: list[list[float]] = []
        for line in block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if line_text.strip():
                text_parts.append(line_text.strip())
            for span in line.get("spans", []):
                if span.get("size"):
                    sizes.append(float(span["size"]))
                if span.get("color") is not None:
                    colors.append(_int_color_to_rgb(float(span["color"])))

        text = "\n".join(text_parts).strip()
        if not text:
            continue

        text_index += 1
        box = bbox(rect)
        region = page_region(box, page_height)
        objects.append(
            {
                "object_id": f"p{page_number}-text-{text_index}",
                "object_type": "text",
                "page_number": page_number,
                "bounding_box": box,
                "normalized_box": normalized_box(box, page_width, page_height),
                "text": text,
                "font_size": round(sum(sizes) / len(sizes), 2) if sizes else None,
                "confidence": 1.0,
                "metadata": {"source": "native", "region": region, "color": colors[0] if colors else [0, 0, 0]},
            }
        )
    return objects


def detect_tables(page: fitz.Page, page_number: int) -> list[dict[str, Any]]:
    if not hasattr(page, "find_tables"):
        return []
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    objects: list[dict[str, Any]] = []
    try:
        tables = page.find_tables()
    except Exception:
        return []

    for index, table in enumerate(getattr(tables, "tables", []) or [], start=1):
        rect = fitz.Rect(table.bbox)
        box = bbox(rect)
        try:
            rows = table.extract() or []
        except Exception:
            rows = []
        clean_rows = [
            [str(cell or "").strip() for cell in row]
            for row in rows
            if row
        ]
        table_text = "\n".join(" | ".join(row) for row in clean_rows)
        column_count = max((len(row) for row in clean_rows), default=0)
        objects.append(
            {
                "object_id": f"p{page_number}-table-{index}",
                "object_type": "table",
                "page_number": page_number,
                "bounding_box": box,
                "normalized_box": normalized_box(box, page_width, page_height),
                "text": table_text,
                "font_size": None,
                "confidence": 0.9,
                "metadata": {
                    "source": "native_table",
                    "rows": clean_rows,
                    "row_count": len(clean_rows),
                    "column_count": column_count,
                },
            }
        )
    return objects


def _int_color_to_rgb(color_value: float) -> list[float]:
    value = int(color_value)
    red = ((value >> 16) & 255) / 255
    green = ((value >> 8) & 255) / 255
    blue = (value & 255) / 255
    return [round(red, 4), round(green, 4), round(blue, 4)]
