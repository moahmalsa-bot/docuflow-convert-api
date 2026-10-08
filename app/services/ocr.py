import os
import re
from pathlib import Path
from statistics import mean
from typing import Any

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.utils.coordinates import normalized_box
from app.utils.files import ConversionError, run_command


ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
LATIN_RE = re.compile(r"[A-Za-z]")
DEFAULT_OCR_LANGUAGES = "ara+eng"


def normalize_ocr_languages(value: str | None = None) -> str:
    raw = (value or os.getenv("OCR_LANGUAGES", DEFAULT_OCR_LANGUAGES)).replace(",", "+")
    requested = []
    for item in raw.split("+"):
        lang = re.sub(r"[^A-Za-z0-9_]", "", item.strip())
        if lang and lang not in requested:
            requested.append(lang)
    if not requested:
        requested = ["ara", "eng"]
    return "+".join(requested)


def script_profile(text: str) -> dict[str, Any]:
    arabic = len(ARABIC_RE.findall(text or ""))
    latin = len(LATIN_RE.findall(text or ""))
    if arabic and latin:
        language = "mixed"
        direction = "mixed"
    elif arabic:
        language = "ar"
        direction = "rtl"
    elif latin:
        language = "en"
        direction = "ltr"
    else:
        language = "unknown"
        direction = "ltr"
    return {
        "language": language,
        "direction": direction,
        "arabic_characters": arabic,
        "latin_characters": latin,
    }


def page_image_coverage(page: fitz.Page) -> float:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    total = 0.0
    seen: set[tuple[int, tuple[float, float, float, float]]] = set()
    for image_info in page.get_images(full=True):
        xref = int(image_info[0])
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            key = (xref, tuple(round(float(v), 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1)))
            if key in seen:
                continue
            seen.add(key)
            clipped = rect & page.rect
            if clipped.is_empty:
                continue
            total += max(0.0, float(clipped.width * clipped.height))
    return round(min(1.0, total / page_area), 4)


def page_ocr_reason(page: fitz.Page, min_chars: int = 40) -> str:
    text = page.get_text("text").strip()
    useful = sum(1 for char in text if char.isalnum())
    if useful < min_chars:
        return "no_or_sparse_native_text"

    replacement_count = text.count("\ufffd")
    if replacement_count and replacement_count / max(len(text), 1) > 0.02:
        return "low_quality_native_text"

    # A common scanned-PDF pattern is a full-page image plus a tiny native footer,
    # stamp or broken OCR layer. OCR the page, then deduplicate against native text.
    coverage = page_image_coverage(page)
    if coverage >= 0.55 and useful < 180:
        return "image_dominant_page_with_sparse_text"
    return ""


def page_needs_ocr(page: fitz.Page, min_chars: int = 40) -> bool:
    return bool(page_ocr_reason(page, min_chars=min_chars))


def render_for_ocr(page: fitz.Page, output_path: Path, dpi: int) -> Path:
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pixmap.save(output_path)
    return output_path


def preprocess_ocr_image(source_path: Path, output_path: Path) -> Path:
    with Image.open(source_path) as source:
        gray = ImageOps.grayscale(source)
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = ImageEnhance.Contrast(gray).enhance(1.18)
        gray = gray.filter(ImageFilter.SHARPEN)
        gray.save(output_path, format="PNG", optimize=True)
    return output_path


def ocr_page(page: fitz.Page, page_number: int, document_dir: Path) -> list[dict[str, Any]]:
    dpi = int(os.getenv("OCR_RENDER_DPI", "300"))
    scale = dpi / 72
    raw_image = document_dir / f"ocr-page-{page_number}-raw.png"
    image_path = document_dir / f"ocr-page-{page_number}.png"
    render_for_ocr(page, raw_image, dpi)
    preprocess_ocr_image(raw_image, image_path)
    raw_image.unlink(missing_ok=True)

    engine = os.getenv("PDF_OCR_ENGINE", "tesseract").lower()
    if engine == "paddle":
        paddle_objects = _ocr_with_paddle(image_path, page, page_number, scale)
        if paddle_objects is not None:
            return paddle_objects
    return _ocr_with_tesseract(image_path, page, page_number, scale)


def _ocr_object(
    page: fitz.Page,
    page_number: int,
    index: int,
    text: str,
    box: list[float],
    confidence: float,
    words: list[dict[str, Any]] | None = None,
    engine: str = "tesseract",
) -> dict[str, Any]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    profile = script_profile(text)
    return {
        "object_id": f"p{page_number}-ocr-{index}",
        "object_type": "text",
        "page_number": page_number,
        "bounding_box": box,
        "normalized_box": normalized_box(box, page_width, page_height),
        "text": text,
        "font_size": round(max(1.0, box[3] - box[1]), 2),
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "metadata": {
            "source": "ocr",
            "engine": engine,
            "region": _region(box, page_height),
            "language": profile["language"],
            "direction": profile["direction"],
            "words": words or [],
        },
    }


def _ocr_with_paddle(
    image_path: Path,
    page: fitz.Page,
    page_number: int,
    scale: float,
) -> list[dict[str, Any]] | None:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return None

    # Paddle is optional. For Arabic installations use the Arabic multilingual model.
    reader = PaddleOCR(
        use_angle_cls=True,
        lang=os.getenv("OCR_PADDLE_LANGUAGE", "ar"),
        show_log=False,
    )
    result = reader.ocr(str(image_path), cls=True)
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(result[0] if result else [], start=1):
        box_points, value = item
        text, confidence = value
        xs = [point[0] / scale for point in box_points]
        ys = [point[1] / scale for point in box_points]
        box = [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
        word = {"text": text, "bounding_box": box, "confidence": round(float(confidence), 4)}
        objects.append(_ocr_object(page, page_number, index, text, box, float(confidence), [word], "paddle"))
    return objects


def _installed_tesseract_languages() -> set[str]:
    try:
        result = run_command(["tesseract", "--list-langs"], timeout=10)
    except ConversionError:
        return set()
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines()]
    return {line for line in lines if re.fullmatch(r"[A-Za-z0-9_]+", line)}


def effective_tesseract_languages() -> str:
    requested = normalize_ocr_languages().split("+")
    installed = _installed_tesseract_languages()
    if not installed:
        return "+".join(requested)
    usable = [lang for lang in requested if lang in installed]
    if usable:
        return "+".join(usable)
    if "eng" in installed:
        return "eng"
    return sorted(installed)[0]


def _ocr_with_tesseract(
    image_path: Path,
    page: fitz.Page,
    page_number: int,
    scale: float,
) -> list[dict[str, Any]]:
    languages = effective_tesseract_languages()
    psm = str(int(os.getenv("OCR_PSM", "3")))
    timeout = int(os.getenv("OCR_TESSERACT_TIMEOUT_SECONDS", "180"))
    result = run_command(
        [
            "tesseract",
            str(image_path),
            "stdout",
            "-l",
            languages,
            "--oem",
            "1",
            "--psm",
            psm,
            "-c",
            "preserve_interword_spaces=1",
            "tsv",
        ],
        timeout=timeout,
    )
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
        parsed_words: list[dict[str, Any]] = []
        for word in words:
            left = float(word["left"]) / scale
            top = float(word["top"]) / scale
            right = (float(word["left"]) + float(word["width"])) / scale
            bottom = (float(word["top"]) + float(word["height"])) / scale
            parsed_words.append(
                {
                    "text": word["text"].strip(),
                    "bounding_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                    "confidence": round(max(0.0, min(1.0, float(word["conf"]) / 100)), 4),
                }
            )

        left = min(word["bounding_box"][0] for word in parsed_words)
        top = min(word["bounding_box"][1] for word in parsed_words)
        right = max(word["bounding_box"][2] for word in parsed_words)
        bottom = max(word["bounding_box"][3] for word in parsed_words)
        text = _join_words_for_script(parsed_words)
        confidence = mean(word["confidence"] for word in parsed_words)
        objects.append(
            _ocr_object(
                page,
                page_number,
                index,
                text,
                [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                confidence,
                parsed_words,
                "tesseract",
            )
        )
    return objects


def _join_words_for_script(words: list[dict[str, Any]]) -> str:
    if not words:
        return ""
    text = " ".join(str(word.get("text", "")).strip() for word in words if str(word.get("text", "")).strip())
    # Tesseract already returns logical reading order for Arabic when ara is loaded.
    # Keep that order instead of reversing words, which breaks Arabic+English mixed lines.
    return re.sub(r"\s+", " ", text).strip()


def _region(box: list[float], page_height: float) -> str:
    mid_y = (box[1] + box[3]) / 2
    if mid_y <= page_height * 0.12:
        return "header"
    if mid_y >= page_height * 0.88:
        return "footer"
    return "body"
