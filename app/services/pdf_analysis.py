import os
from pathlib import Path
from typing import Any

import fitz

from app.services.document_history import initialize_document
from app.services.image_detection import bbox, detect_embedded_images
from app.services.ocr import ocr_page, page_needs_ocr
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

        analysis_payload = {
            "page_count": document.page_count,
            "scanned_pages": scanned_pages,
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

    objects = detect_text_blocks(page, page_number)
    objects.extend(detect_tables(page, page_number))
    objects.extend(detect_embedded_images(page, page_number))

    scanned = page_needs_ocr(page)
    if scanned:
        objects.extend(ocr_page(page, page_number, document_dir))

    return (
        {
            "page_number": page_number,
            "page_width": page_width,
            "page_height": page_height,
            "render_width": render_width,
            "render_height": render_height,
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
        for line in block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if line_text.strip():
                text_parts.append(line_text.strip())
            for span in line.get("spans", []):
                if span.get("size"):
                    sizes.append(float(span["size"]))

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
                "metadata": {"source": "native", "region": region},
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
        objects.append(
            {
                "object_id": f"p{page_number}-table-{index}",
                "object_type": "table",
                "page_number": page_number,
                "bounding_box": box,
                "normalized_box": normalized_box(box, page_width, page_height),
                "text": "",
                "font_size": None,
                "confidence": 0.85,
                "metadata": {"source": "native"},
            }
        )
    return objects
