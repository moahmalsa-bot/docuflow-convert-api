import base64
import io
import uuid
from pathlib import Path
from typing import Any

import fitz
from fastapi import HTTPException
from PIL import Image

from app.services import document_history
from app.utils.coordinates import normalized_box
from app.utils.validation import validate_operation_count


Color = tuple[float, float, float]


def apply_pdf_edits(input_pdf: Path, output_pdf: Path, operations: list[dict[str, Any]]) -> Path:
    with fitz.open(input_pdf) as document:
        for operation in operations:
            if not isinstance(operation, dict):
                raise HTTPException(status_code=400, detail="Each operation must be an object")
            op_type = str(operation.get("type", "")).lower()
            page = _page(document, operation)
            rect = _as_rect(operation.get("rect"))

            if op_type in {"cover", "redact"}:
                page.draw_rect(
                    rect,
                    color=_color(operation.get("border_color"), (1, 1, 1)),
                    fill=_color(operation.get("fill"), (1, 1, 1)),
                    width=float(operation.get("width", 0)),
                    overlay=True,
                )
            elif op_type == "text":
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
                page.draw_rect(
                    rect,
                    color=None,
                    fill=_color(operation.get("color"), (1, 1, 0)),
                    fill_opacity=float(operation.get("opacity", 0.35)),
                    overlay=True,
                )
            elif op_type == "rectangle":
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
                raise HTTPException(status_code=400, detail=f"Unsupported operation type: {operation.get('type')}")

        document.save(output_pdf, garbage=4, deflate=True)
    return output_pdf


def validate_ai_edit_operations(
    operations: Any,
    metadata: dict[str, Any],
    allow_image_payload: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail="AI edit operations must be a JSON array")
    validate_operation_count(operations)

    allowed = {"replace_text", "delete_text", "highlight", "redact", "delete_image", "replace_image"}
    objects_by_id = {item["object_id"]: item for item in metadata.get("objects", [])}
    validated: list[dict[str, Any]] = []

    for index, operation in enumerate(operations, start=1):
        operation = _operation_data(operation)
        if not isinstance(operation, dict):
            raise HTTPException(status_code=400, detail=f"Operation {index} must be an object")

        op_type = _operation_type(operation)
        if op_type not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported AI edit operation type: {op_type or None}")

        page_number, rect = _operation_rect(operation, objects_by_id)
        clean: dict[str, Any] = {
            "operation": op_type,
            "page": page_number,
            "bounding_box": _bbox(rect),
        }
        if operation.get("object_id"):
            clean["object_id"] = str(operation["object_id"])

        if op_type == "replace_text":
            clean["new_text"] = str(operation.get("new_text", operation.get("text", "")))
            clean["font_size"] = float(operation.get("font_size", 12))
            clean["color"] = _color(operation.get("color"), (0, 0, 0))
            clean["preserve_style"] = bool(operation.get("preserve_style", True))
            clean["fit_mode"] = str(operation.get("fit_mode", "shrink_to_fit"))
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


def apply_ai_edit_operations(document_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = document_history.load_metadata(document_id)
    validated = validate_ai_edit_operations(operations, metadata, allow_image_payload=True)
    source_pdf = document_history.current_pdf_path(document_id)
    scratch_pdf = document_history.document_dir(document_id) / f"scratch-{uuid.uuid4().hex}.pdf"

    with fitz.open(source_pdf) as document:
        for operation in validated:
            page = document[int(operation["page"]) - 1]
            rect = _as_rect(operation["bounding_box"])
            op_type = operation["operation"]

            if op_type == "replace_text":
                _redact_area(page, rect, fill=(1, 1, 1))
                _insert_replacement_text(page, rect, operation)
            elif op_type == "delete_text":
                _redact_area(page, rect, fill=(1, 1, 1))
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
                page.insert_image(rect, stream=_image_payload_to_png_stream(operation["replacement_image_base64"]), keep_proportion=True, overlay=True)

        document.save(scratch_pdf, garbage=4, deflate=True)

    document_history.add_version(document_id, scratch_pdf, validated, label="ai-edited")
    scratch_pdf.unlink(missing_ok=True)
    return document_history.response_for_current_version(document_id)


def object_to_selection(object_payload: dict[str, Any], page_width: float, page_height: float) -> dict[str, Any]:
    box = object_payload["bounding_box"]
    return {
        "object_id": object_payload["object_id"],
        "page_number": object_payload["page_number"],
        "normalized_box": normalized_box(box, page_width, page_height),
    }


def _page(document: fitz.Document, operation: dict[str, Any]) -> fitz.Page:
    page_number = int(operation.get("page", operation.get("page_number", 1)))
    if page_number < 1 or page_number > document.page_count:
        raise HTTPException(status_code=400, detail=f"Invalid page number: {page_number}")
    return document[page_number - 1]


def _operation_rect(operation: dict[str, Any], objects_by_id: dict[str, dict[str, Any]]) -> tuple[int, fitz.Rect]:
    object_id = operation.get("object_id")
    if object_id:
        source = objects_by_id.get(object_id)
        if not source:
            raise HTTPException(status_code=400, detail=f"Unknown object_id: {object_id}")
        page_number = int(source["page_number"])
        bbox = source["bounding_box"]
    else:
        page_number = int(operation.get("page", operation.get("page_number", 0)))
        bbox = operation.get("bounding_box") or operation.get("bbox")
    if page_number < 1:
        raise HTTPException(status_code=400, detail="Operation page_number must be 1 or greater")
    return page_number, _as_rect(bbox)


def _operation_data(operation: Any) -> dict[str, Any]:
    if isinstance(operation, dict):
        return operation
    if hasattr(operation, "model_dump"):
        return operation.model_dump(exclude_none=True)
    if hasattr(operation, "dict"):
        return operation.dict(exclude_none=True)
    return operation


def _operation_type(operation: dict[str, Any]) -> str:
    return str(operation.get("operation") or operation.get("type") or "").lower().strip()


def _as_rect(value: Any) -> fitz.Rect:
    if not isinstance(value, list) or len(value) != 4:
        raise HTTPException(status_code=400, detail="Operation rect must be [x0, y0, x1, y1]")
    try:
        return fitz.Rect(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Operation rect values must be numbers") from exc


def _bbox(rect: fitz.Rect) -> list[float]:
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


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


def _draw_freehand(page: fitz.Page, operation: dict[str, Any]) -> None:
    points = operation.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise HTTPException(status_code=400, detail="Draw operation requires at least two points")
    parsed_points = [fitz.Point(float(point[0]), float(point[1])) for point in points]
    shape = page.new_shape()
    for start, end in zip(parsed_points, parsed_points[1:]):
        shape.draw_line(start, end)
    shape.finish(color=_color(operation.get("color"), (1, 0, 0)), width=float(operation.get("width", 2)))
    shape.commit()


def _whiteout(page: fitz.Page, rect: fitz.Rect) -> None:
    page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), width=0, overlay=True)


def _redact_area(page: fitz.Page, rect: fitz.Rect, fill: Color = (1, 1, 1)) -> None:
    page.add_redact_annot(rect, fill=fill)
    page.apply_redactions()


def _insert_replacement_text(page: fitz.Page, rect: fitz.Rect, operation: dict[str, Any]) -> None:
    text = operation.get("new_text", "")
    font_size = float(operation.get("font_size", 12))
    if operation.get("fit_mode") == "shrink_to_fit":
        font_size = _fit_font_size(page, rect, text, font_size)
    page.insert_textbox(
        rect,
        text,
        fontsize=font_size,
        color=operation.get("color") or (0, 0, 0),
        overlay=True,
    )


def _fit_font_size(page: fitz.Page, rect: fitz.Rect, text: str, starting_size: float) -> float:
    size = starting_size
    while size > 4:
        scratch = fitz.open()
        scratch_page = scratch.new_page(width=page.rect.width, height=page.rect.height)
        overflow = scratch_page.insert_textbox(rect, text, fontsize=size)
        scratch.close()
        if overflow >= 0:
            return size
        size -= 0.5
    return 4


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
