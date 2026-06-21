import base64
import io
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import fitz
from fastapi import HTTPException
from PIL import Image

from app.utils import ConversionError, run_command, sanitize_filename


def _as_rect(value: Any) -> fitz.Rect:
    if not isinstance(value, list) or len(value) != 4:
        raise HTTPException(status_code=400, detail="Operation rect must be [x0, y0, x1, y1]")
    try:
        return fitz.Rect(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Operation rect values must be numbers") from exc


Color = tuple[float, float, float]


def _color(value: Any, default: Color | None) -> Color | None:
    if value is None:
        return default
    if not isinstance(value, list) or len(value) != 3:
        raise HTTPException(status_code=400, detail="Color must be [r, g, b] values from 0 to 1")
    try:
        color = tuple(float(channel) for channel in value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Color channels must be numbers") from exc
    if any(channel < 0 or channel > 1 for channel in color):
        raise HTTPException(status_code=400, detail="Color channels must be between 0 and 1")
    return color  # type: ignore[return-value]


def _page(document: fitz.Document, operation: dict) -> fitz.Page:
    page_number = int(operation.get("page", 1))
    if page_number < 1 or page_number > document.page_count:
        raise HTTPException(status_code=400, detail=f"Invalid page number: {page_number}")
    return document[page_number - 1]


def _draw_freehand(page: fitz.Page, operation: dict) -> None:
    points = operation.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise HTTPException(status_code=400, detail="Draw operation requires at least two points")
    parsed_points = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise HTTPException(status_code=400, detail="Draw points must be [x, y]")
        parsed_points.append(fitz.Point(float(point[0]), float(point[1])))

    shape = page.new_shape()
    for start, end in zip(parsed_points, parsed_points[1:]):
        shape.draw_line(start, end)
    shape.finish(
        color=_color(operation.get("color"), (1, 0, 0)),
        width=float(operation.get("width", 2)),
    )
    shape.commit()


def apply_pdf_edits(input_pdf: Path, output_pdf: Path, operations: list[dict]) -> Path:
    with fitz.open(input_pdf) as document:
        for operation in operations:
            if not isinstance(operation, dict):
                raise HTTPException(status_code=400, detail="Each operation must be an object")

            op_type = str(operation.get("type", "")).lower()
            page = _page(document, operation)

            if op_type in {"cover", "redact"}:
                rect = _as_rect(operation.get("rect"))
                page.draw_rect(
                    rect,
                    color=_color(operation.get("border_color"), (1, 1, 1)),
                    fill=_color(operation.get("fill"), (1, 1, 1)),
                    width=float(operation.get("width", 0)),
                    overlay=True,
                )
            elif op_type == "text":
                rect = _as_rect(operation.get("rect"))
                page.insert_textbox(
                    rect,
                    str(operation.get("text", "")),
                    fontsize=float(operation.get("font_size", 12)),
                    fontname=str(operation.get("font", "helv")),
                    color=_color(operation.get("color"), (0, 0, 0)),
                    align=int(operation.get("align", 0)),
                    overlay=True,
                )
            elif op_type == "highlight":
                rect = _as_rect(operation.get("rect"))
                page.draw_rect(
                    rect,
                    color=None,
                    fill=_color(operation.get("color"), (1, 1, 0)),
                    fill_opacity=float(operation.get("opacity", 0.35)),
                    overlay=True,
                )
            elif op_type == "rectangle":
                rect = _as_rect(operation.get("rect"))
                page.draw_rect(
                    rect,
                    color=_color(operation.get("color"), (1, 0, 0)),
                    fill=_color(operation.get("fill"), None) if operation.get("fill") is not None else None,
                    width=float(operation.get("width", 1)),
                    overlay=True,
                )
            elif op_type in {"draw", "freehand"}:
                _draw_freehand(page, operation)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported operation type: {operation.get('type')}",
                )

        document.save(output_pdf, garbage=4, deflate=True)
    return output_pdf


def _document_store_root() -> Path:
    root = Path(os.getenv("DOCUFLOW_DOCUMENT_STORE", Path(tempfile.gettempdir()) / "docuflow-documents"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_document_dir() -> tuple[str, Path]:
    document_id = uuid.uuid4().hex
    document_dir = _document_store_root() / document_id
    document_dir.mkdir(parents=True, exist_ok=False)
    (document_dir / "previews").mkdir()
    (document_dir / "edits").mkdir()
    return document_id, document_dir


def _document_dir(document_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", document_id or ""):
        raise HTTPException(status_code=400, detail="Invalid document_id")
    path = _document_store_root() / document_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return path


def _metadata_path(document_id: str) -> Path:
    return _document_dir(document_id) / "metadata.json"


def _load_metadata(document_id: str) -> dict:
    path = _metadata_path(document_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document metadata not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_metadata(document_dir: Path, metadata: dict) -> None:
    (document_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _original_pdf_path(document_id: str) -> Path:
    metadata = _load_metadata(document_id)
    path = _document_dir(document_id) / metadata["stored_file_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")
    return path


def _bbox(rect: fitz.Rect) -> list[float]:
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


def _object(
    page_number: int,
    object_id: str,
    object_type: str,
    text: str,
    bbox: list[float],
    font_size: float | None,
    confidence: float | None,
) -> dict:
    return {
        "page_number": page_number,
        "object_id": object_id,
        "object_type": object_type,
        "text": text,
        "bounding_box": bbox,
        "font_size": round(font_size, 2) if font_size is not None else None,
        "confidence": round(confidence, 4) if confidence is not None else None,
    }


def _native_page_objects(page: fitz.Page, page_number: int) -> tuple[list[dict], bool]:
    objects: list[dict] = []
    text_chars = 0
    page_dict = page.get_text("dict")

    for block_index, block in enumerate(page_dict.get("blocks", []), start=1):
        block_type = block.get("type")
        rect = fitz.Rect(block.get("bbox", [0, 0, 0, 0]))

        if block_type == 0:
            lines = block.get("lines", [])
            text_parts: list[str] = []
            sizes: list[float] = []
            for line in lines:
                line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                if line_text:
                    text_parts.append(line_text)
                for span in line.get("spans", []):
                    if span.get("size"):
                        sizes.append(float(span["size"]))

            text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
            text_chars += len(text)
            if text:
                font_size = sum(sizes) / len(sizes) if sizes else None
                objects.append(
                    _object(page_number, f"p{page_number}-text-{block_index}", "text", text, _bbox(rect), font_size, 1.0)
                )
        elif block_type == 1:
            objects.append(
                _object(page_number, f"p{page_number}-image-{block_index}", "image", "", _bbox(rect), None, 1.0)
            )

    return objects, text_chars > 0


def _render_page_to_png(page: fitz.Page, output_path: Path, dpi: int = 220) -> Path:
    zoom = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pixmap.save(output_path)
    return output_path


def _ocr_with_paddle(image_path: Path, page: fitz.Page, page_number: int, scale: float) -> list[dict] | None:
    engine = os.getenv("PDF_OCR_ENGINE", "auto").lower()
    if engine not in {"auto", "paddle"}:
        return None
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return None

    reader = PaddleOCR(use_angle_cls=True, lang=os.getenv("OCR_LANGUAGE", "en"), show_log=False)
    result = reader.ocr(str(image_path), cls=True)
    objects: list[dict] = []
    for index, item in enumerate(result[0] if result else [], start=1):
        box, value = item
        text, confidence = value
        xs = [point[0] / scale for point in box]
        ys = [point[1] / scale for point in box]
        bbox = [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
        font_size = max(ys) - min(ys)
        objects.append(_object(page_number, f"p{page_number}-ocr-{index}", "ocr_text", text, bbox, font_size, confidence))
    return objects


def _ocr_with_tesseract(image_path: Path, page: fitz.Page, page_number: int, scale: float) -> list[dict]:
    result = run_command(["tesseract", str(image_path), "stdout", "--psm", "6", "tsv"], timeout=180)
    lines = result.stdout.splitlines()
    if len(lines) <= 1:
        return []

    headers = lines[0].split("\t")
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
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

    objects: list[dict] = []
    for index, words in enumerate(grouped.values(), start=1):
        left = min(float(word["left"]) for word in words) / scale
        top = min(float(word["top"]) for word in words) / scale
        right = max(float(word["left"]) + float(word["width"]) for word in words) / scale
        bottom = max(float(word["top"]) + float(word["height"]) for word in words) / scale
        text = " ".join(word["text"].strip() for word in words if word["text"].strip())
        confidence = sum(float(word["conf"]) for word in words) / (100 * len(words))
        objects.append(
            _object(
                page_number,
                f"p{page_number}-ocr-{index}",
                "ocr_text",
                text,
                [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                bottom - top,
                confidence,
            )
        )
    return objects


def _ocr_page(page: fitz.Page, page_number: int, document_dir: Path) -> list[dict]:
    dpi = int(os.getenv("OCR_RENDER_DPI", "220"))
    scale = dpi / 72
    image_path = document_dir / f"ocr-page-{page_number}.png"
    _render_page_to_png(page, image_path, dpi=dpi)
    paddle_objects = _ocr_with_paddle(image_path, page, page_number, scale)
    if paddle_objects is not None:
        return paddle_objects
    return _ocr_with_tesseract(image_path, page, page_number, scale)


def analyze_pdf_document(document_id: str, document_dir: Path, pdf_path: Path, original_filename: str) -> dict:
    objects: list[dict] = []
    scanned_pages: list[int] = []

    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            page_objects, has_native_text = _native_page_objects(page, page_index)
            objects.extend(page_objects)
            if not has_native_text:
                scanned_pages.append(page_index)
                objects.extend(_ocr_page(page, page_index, document_dir))

        metadata = {
            "document_id": document_id,
            "original_file_name": sanitize_filename(original_filename),
            "stored_file_name": pdf_path.name,
            "page_count": document.page_count,
            "scanned_pages": scanned_pages,
            "objects": objects,
            "edits": [],
        }

    _save_metadata(document_dir, metadata)
    return metadata


def render_document_page(document_id: str, page_number: int, dpi: int = 220) -> Path:
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be 1 or greater")

    document_dir = _document_dir(document_id)
    output_path = document_dir / "previews" / f"page-{page_number}.png"
    if output_path.exists():
        return output_path

    with fitz.open(_original_pdf_path(document_id)) as document:
        if page_number > document.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        _render_page_to_png(document[page_number - 1], output_path, dpi=dpi)
    return output_path


def _object_index(metadata: dict) -> dict[str, dict]:
    return {item["object_id"]: item for item in metadata.get("objects", [])}


def _operation_rect(operation: dict, objects_by_id: dict[str, dict]) -> tuple[int, fitz.Rect]:
    object_id = operation.get("object_id")
    if object_id:
        source = objects_by_id.get(object_id)
        if not source:
            raise HTTPException(status_code=400, detail=f"Unknown object_id: {object_id}")
        page_number = int(source["page_number"])
        bbox = source["bounding_box"]
    else:
        page_number = int(operation.get("page_number", operation.get("page", 0)))
        bbox = operation.get("bounding_box") or operation.get("bbox")

    if page_number < 1:
        raise HTTPException(status_code=400, detail="Operation page_number must be 1 or greater")
    return page_number, _as_rect(bbox)


def validate_ai_edit_operations(
    operations: Any,
    metadata: dict,
    allow_image_payload: bool = False,
) -> list[dict]:
    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail="AI edit operations must be a JSON array")

    allowed = {"replace_text", "delete_text", "highlight", "redact", "delete_image", "replace_image"}
    objects_by_id = _object_index(metadata)
    validated: list[dict] = []

    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise HTTPException(status_code=400, detail=f"Operation {index} must be an object")

        op_type = str(operation.get("type", "")).lower().strip()
        if op_type not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported AI edit operation type: {operation.get('type')}")

        page_number, rect = _operation_rect(operation, objects_by_id)
        clean = {
            "type": op_type,
            "page_number": page_number,
            "bounding_box": _bbox(rect),
        }
        if operation.get("object_id"):
            clean["object_id"] = str(operation["object_id"])

        if op_type == "replace_text":
            clean["text"] = str(operation.get("text", ""))
            clean["font_size"] = float(operation.get("font_size", 12))
            clean["color"] = _color(operation.get("color"), (0, 0, 0))
        elif op_type == "highlight":
            clean["color"] = _color(operation.get("color"), (1, 1, 0))
            clean["opacity"] = float(operation.get("opacity", 0.35))
        elif op_type == "redact":
            clean["fill"] = _color(operation.get("fill"), (0, 0, 0))
        elif op_type == "replace_image":
            if allow_image_payload:
                image_payload = operation.get("replacement_image_base64")
                if not image_payload:
                    raise HTTPException(status_code=400, detail="replace_image requires replacement_image_base64")
                clean["replacement_image_base64"] = str(image_payload)
            else:
                clean["requires_replacement_image_base64"] = True

        validated.append(clean)

    return validated


def _selected_objects(metadata: dict, selected_object_ids: list[str]) -> list[dict]:
    objects_by_id = _object_index(metadata)
    if not selected_object_ids:
        return []
    missing = [object_id for object_id in selected_object_ids if object_id not in objects_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown selected object IDs: {', '.join(missing)}")
    return [objects_by_id[object_id] for object_id in selected_object_ids]


def _extract_quoted_text(instruction: str) -> str:
    match = re.search(r"['\"]([^'\"]+)['\"]", instruction)
    return match.group(1) if match else ""


def _local_edit_plan(instruction: str, selected_objects: list[dict]) -> list[dict]:
    lower = instruction.lower()
    operations: list[dict] = []
    replacement = _extract_quoted_text(instruction)

    for item in selected_objects:
        object_id = item["object_id"]
        object_type = item["object_type"]
        if "redact" in lower:
            operations.append({"type": "redact", "object_id": object_id})
        elif "highlight" in lower:
            operations.append({"type": "highlight", "object_id": object_id})
        elif "delete" in lower or "remove" in lower:
            op_type = "delete_image" if object_type == "image" else "delete_text"
            operations.append({"type": op_type, "object_id": object_id})
        elif "replace" in lower and object_type in {"text", "ocr_text"}:
            operations.append({"type": "replace_text", "object_id": object_id, "text": replacement})

    return operations


def _ai_request_payload(instruction: str, selected_objects: list[dict], scope: str) -> dict:
    model = os.getenv("AI_MODEL")
    if not model:
        raise HTTPException(status_code=503, detail="AI_MODEL is required when AI planning is enabled")

    system = (
        "Return only a JSON array of PDF edit operations. "
        "Allowed operation types are replace_text, delete_text, highlight, redact, delete_image, replace_image. "
        "Prefer object_id-based operations. Do not include prose or markdown."
    )
    user = {
        "instruction": instruction,
        "scope": scope,
        "selected_objects": selected_objects,
        "schema": {
            "type": "replace_text|delete_text|highlight|redact|delete_image|replace_image",
            "object_id": "object ID from selected_objects when applicable",
            "text": "required for replace_text",
        },
    }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)},
        ],
        "temperature": 0,
    }


def _call_ai_provider(instruction: str, selected_objects: list[dict], scope: str) -> list[dict]:
    provider = os.getenv("AI_PROVIDER", "").lower().strip()
    api_key = os.getenv("AI_API_KEY")
    if not provider or not api_key:
        return _local_edit_plan(instruction, selected_objects)

    base_url = os.getenv("AI_BASE_URL")
    if provider == "openai":
        url = base_url or "https://api.openai.com/v1/chat/completions"
    elif base_url:
        url = base_url.rstrip("/") + "/chat/completions"
    else:
        raise HTTPException(status_code=503, detail="AI_BASE_URL is required for non-openai AI_PROVIDER")

    request = urllib.request.Request(
        url,
        data=json.dumps(_ai_request_payload(instruction, selected_objects, scope)).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ConversionError(f"AI provider request failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ConversionError(f"AI provider request failed: {exc.reason}") from exc

    content = payload["choices"][0]["message"]["content"]
    try:
        operations = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="AI provider did not return valid JSON operations") from exc
    return operations


def plan_ai_edit(document_id: str, instruction: str, selected_object_ids: list[str], scope: str) -> list[dict]:
    if not instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")
    metadata = _load_metadata(document_id)
    selected_objects = _selected_objects(metadata, selected_object_ids)
    operations = _call_ai_provider(instruction, selected_objects, scope)
    return validate_ai_edit_operations(operations, metadata, allow_image_payload=False)


def _whiteout(page: fitz.Page, rect: fitz.Rect) -> None:
    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), width=0, overlay=True)


def _image_payload_to_png_stream(payload: str) -> bytes:
    if "," in payload and payload.split(",", 1)[0].startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="replacement_image_base64 is invalid") from exc
    with Image.open(io.BytesIO(raw)) as image:
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()


def apply_ai_edit_operations(document_id: str, operations: list[dict]) -> dict:
    metadata = _load_metadata(document_id)
    validated = validate_ai_edit_operations(operations, metadata, allow_image_payload=True)
    source_pdf = _original_pdf_path(document_id)
    edits_dir = _document_dir(document_id) / "edits"
    file_name = f"{Path(metadata['original_file_name']).stem}-ai-edited-{uuid.uuid4().hex[:8]}.pdf"
    output_pdf = edits_dir / file_name

    with fitz.open(source_pdf) as document:
        for operation in validated:
            page = document[int(operation["page_number"]) - 1]
            rect = _as_rect(operation["bounding_box"])
            op_type = operation["type"]

            if op_type == "replace_text":
                _whiteout(page, rect)
                page.insert_textbox(
                    rect,
                    operation.get("text", ""),
                    fontsize=float(operation.get("font_size", 12)),
                    color=operation.get("color") or (0, 0, 0),
                    overlay=True,
                )
            elif op_type == "delete_text":
                _whiteout(page, rect)
            elif op_type == "highlight":
                page.draw_rect(
                    rect,
                    color=None,
                    fill=operation.get("color") or (1, 1, 0),
                    fill_opacity=float(operation.get("opacity", 0.35)),
                    overlay=True,
                )
            elif op_type == "redact":
                page.add_redact_annot(rect, fill=operation.get("fill") or (0, 0, 0))
                page.apply_redactions()
            elif op_type == "delete_image":
                _whiteout(page, rect)
            elif op_type == "replace_image":
                _whiteout(page, rect)
                image_stream = _image_payload_to_png_stream(operation["replacement_image_base64"])
                page.insert_image(rect, stream=image_stream, keep_proportion=True, overlay=True)

        document.save(output_pdf, garbage=4, deflate=True)

    metadata.setdefault("edits", []).append({"file_name": file_name, "operations": validated})
    _save_metadata(_document_dir(document_id), metadata)
    return {
        "fileName": file_name,
        "downloadUrl": f"/pdf/download/{document_id}/{file_name}",
    }


def download_pdf_path(document_id: str, file_name: str) -> Path:
    safe_name = sanitize_filename(file_name)
    if safe_name != file_name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    path = _document_dir(document_id) / "edits" / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Edited PDF not found")
    return path


def remove_document(document_id: str) -> None:
    shutil.rmtree(_document_dir(document_id), ignore_errors=True)
