import os
from pathlib import Path
from typing import Any

import fitz

from app.services.image_detection import bbox
from app.utils.coordinates import normalized_box
from app.utils.files import run_command


def page_needs_ocr(page: fitz.Page, min_chars: int = 20) -> bool:
    return len(page.get_text("text").strip()) < min_chars


def render_for_ocr(page: fitz.Page, output_path: Path, dpi: int) -> Path:
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pixmap.save(output_path)
    return output_path


def ocr_page(page: fitz.Page, page_number: int, document_dir: Path) -> list[dict[str, Any]]:
    dpi = int(os.getenv("OCR_RENDER_DPI", "220"))
    scale = dpi / 72
    image_path = document_dir / f"ocr-page-{page_number}.png"
    render_for_ocr(page, image_path, dpi)
    paddle_objects = _ocr_with_paddle(image_path, page, page_number, scale)
    if paddle_objects is not None:
        return paddle_objects
    return _ocr_with_tesseract(image_path, page, page_number, scale)


def _ocr_object(page: fitz.Page, page_number: int, index: int, text: str, box: list[float], confidence: float) -> dict[str, Any]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    return {
        "object_id": f"p{page_number}-ocr-{index}",
        "object_type": "text",
        "page_number": page_number,
        "bounding_box": box,
        "normalized_box": normalized_box(box, page_width, page_height),
        "text": text,
        "font_size": round(box[3] - box[1], 2),
        "confidence": round(confidence, 4),
        "metadata": {"source": "ocr", "region": _region(box, page_height)},
    }


def _ocr_with_paddle(image_path: Path, page: fitz.Page, page_number: int, scale: float) -> list[dict[str, Any]] | None:
    engine = os.getenv("PDF_OCR_ENGINE", "auto").lower()
    if engine not in {"auto", "paddle"}:
        return None
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return None

    reader = PaddleOCR(use_angle_cls=True, lang=os.getenv("OCR_LANGUAGE", "en"), show_log=False)
    result = reader.ocr(str(image_path), cls=True)
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(result[0] if result else [], start=1):
        box_points, value = item
        text, confidence = value
        xs = [point[0] / scale for point in box_points]
        ys = [point[1] / scale for point in box_points]
        box = [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
        objects.append(_ocr_object(page, page_number, index, text, box, float(confidence)))
    return objects


def _ocr_with_tesseract(image_path: Path, page: fitz.Page, page_number: int, scale: float) -> list[dict[str, Any]]:
    result = run_command(["tesseract", str(image_path), "stdout", "--psm", "6", "tsv"], timeout=180)
    lines = result.stdout.splitlines()
    if len(lines) <= 1:
        return []

    headers = lines[0].split("\t")
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in lines[1:]:
        values = row.split("\t")
        if len(values) != len(headers):
            continue
        data = dict(zip(headers, values))
        text = data.get("text", "").strip()
        if not text:
            continue
        try:
            confidence = float(data.get("conf", "-1"))
        except ValueError:
            confidence = -1
        if confidence < 0:
            continue
        key = (
            data.get("page_num", "1"),
            data.get("block_num", "0"),
            data.get("par_num", "0"),
            data.get("line_num", "0"),
        )
        grouped.setdefault(key, []).append(data)

    objects: list[dict[str, Any]] = []
    for index, words in enumerate(grouped.values(), start=1):
        left = min(float(word["left"]) for word in words) / scale
        top = min(float(word["top"]) for word in words) / scale
        right = max(float(word["left"]) + float(word["width"]) for word in words) / scale
        bottom = max(float(word["top"]) + float(word["height"]) for word in words) / scale
        text = " ".join(word["text"].strip() for word in words if word["text"].strip())
        confidence = sum(float(word["conf"]) for word in words) / (100 * len(words))
        objects.append(
            _ocr_object(
                page,
                page_number,
                index,
                text,
                [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                confidence,
            )
        )
    return objects


def _region(box: list[float], page_height: float) -> str:
    mid_y = (box[1] + box[3]) / 2
    if mid_y <= page_height * 0.12:
        return "header"
    if mid_y >= page_height * 0.88:
        return "footer"
    return "body"

