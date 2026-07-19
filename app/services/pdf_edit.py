import base64
import hashlib
import io
import json
import logging
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
logger = logging.getLogger("docuflow.pdf_edit")


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

        page_number, rect, source_object = _operation_rect(operation, objects_by_id)
        clean: dict[str, Any] = {
            "operation": op_type,
            "page": page_number,
            "bounding_box": _bbox(rect),
        }
        if operation.get("object_id"):
            clean["object_id"] = str(operation["object_id"])
        if source_object:
            clean["original_text"] = source_object.get("text", "")
            clean["source_object_type"] = source_object.get("object_type")

        if op_type == "replace_text":
            clean["new_text"] = str(operation.get("new_text", operation.get("text", "")))
            clean["font_size"] = float(operation.get("font_size", source_object.get("font_size") if source_object else 12) or 12)
            clean["color"] = _color(operation.get("color"), _object_color(source_object) or (0, 0, 0))
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
    verification_targets: list[dict[str, Any]] = []
    warnings: list[str] = []

    with fitz.open(source_pdf) as document:
        for operation in validated:
            page = document[int(operation["page"]) - 1]
            rect = _as_rect(operation["bounding_box"])
            op_type = operation["operation"]
            extracted_text_before = page.get_text("text")
            before_page_digest = _render_page_digest(page)
            _log_edit_resolution(operation, source_pdf, scratch_pdf, extracted_text_before, rect)

            if op_type == "replace_text":
                _whiteout(page, rect)
                _insert_replacement_text(page, rect, operation)
                verification_targets.append(
                    {
                        "page": int(operation["page"]),
                        "original_text": operation.get("original_text", ""),
                        "new_text": operation.get("new_text", ""),
                        "replacement_rect": _bbox(rect),
                        "before_page_digest": before_page_digest,
                    }
                )
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

    warnings.extend(_verify_visual_replacements(scratch_pdf, verification_targets))
    document_history.add_version(document_id, scratch_pdf, validated, label="ai-edited", warnings=_dedupe(warnings))
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


def _operation_rect(
    operation: dict[str, Any],
    objects_by_id: dict[str, dict[str, Any]],
) -> tuple[int, fitz.Rect, dict[str, Any] | None]:
    object_id = operation.get("object_id")
    source = None
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
    return page_number, _as_rect(bbox), source


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


def _redact_area(page: fitz.Page, rect: fitz.Rect, fill: Color = (1, 1, 1), expand: bool = True) -> None:
    redact_rect = _expanded_rect(rect, page.rect, padding=2.0) if expand else rect
    page.add_redact_annot(redact_rect, fill=fill)
    page.apply_redactions()


def _insert_replacement_text(page: fitz.Page, rect: fitz.Rect, operation: dict[str, Any]) -> None:
    text = str(operation.get("new_text", ""))
    color = operation.get("color") or (0, 0, 0)
    starting_size = float(operation.get("font_size", 12))
    fit_mode = str(operation.get("fit_mode", "shrink_to_fit"))
    candidates = _insertion_rect_candidates(rect, page.rect)

    for candidate in candidates:
        sizes = _font_size_candidates(starting_size if fit_mode == "shrink_to_fit" else max(starting_size, 6))
        for font_size in sizes:
            leftover = page.insert_textbox(
                candidate,
                text,
                fontsize=font_size,
                color=color,
                overlay=True,
            )
            if leftover >= 0:
                logger.info(
                    json.dumps(
                        {
                            "event": "pdf_edit_inserted_replacement_text",
                            "replacement_text": text,
                            "font_size": font_size,
                            "insertion_rect": _bbox(candidate),
                            "textbox_leftover": leftover,
                        }
                    )
                )
                return

    raise HTTPException(
        status_code=422,
        detail="Replacement text does not fit inside the selected area. Try selecting a larger text box.",
    )


def _font_size_candidates(starting_size: float, minimum_size: float = 6.0) -> list[float]:
    size = max(starting_size, minimum_size)
    sizes: list[float] = []
    while size >= minimum_size:
        sizes.append(round(size, 2))
        size -= 0.5
    if minimum_size not in sizes:
        sizes.append(minimum_size)
    return sizes


def _insertion_rect_candidates(rect: fitz.Rect, page_rect: fitz.Rect) -> list[fitz.Rect]:
    candidates = [rect]
    downward = min(page_rect.y1, rect.y1 + max(12.0, rect.height * 0.75))
    if downward > rect.y1:
        candidates.append(fitz.Rect(rect.x0, rect.y0, rect.x1, downward))
    wider = fitz.Rect(max(page_rect.x0, rect.x0 - 1.0), rect.y0, min(page_rect.x1, rect.x1 + 1.0), downward)
    if wider != candidates[-1]:
        candidates.append(wider)
    return candidates


def _expanded_rect(rect: fitz.Rect, page_rect: fitz.Rect, padding: float = 2.0) -> fitz.Rect:
    expanded = fitz.Rect(rect.x0 - padding, rect.y0 - padding, rect.x1 + padding, rect.y1 + padding)
    return expanded & page_rect


def _object_color(source_object: dict[str, Any] | None) -> Color | None:
    if not source_object:
        return None
    color = source_object.get("metadata", {}).get("color")
    if isinstance(color, list) and len(color) == 3:
        return _color(color, None)
    return None


def _verify_visual_replacements(saved_pdf: Path, targets: list[dict[str, Any]]) -> list[str]:
    if not targets:
        return []
    warnings: list[str] = []
    with fitz.open(saved_pdf) as document:
        extracted_text = "\n".join(page.get_text("text") for page in document)
        after_digests = {
            index: _render_page_digest(document[int(target["page"]) - 1])
            for index, target in enumerate(targets)
        }
    logger.info(
        json.dumps(
            {
                "event": "pdf_edit_verified_text",
                "saved_output_file_path": str(saved_pdf),
                "edited_pdf_text": extracted_text,
            }
        )
    )
    normalized_extracted = _normalize_text(extracted_text)
    for index, target in enumerate(targets):
        original_text = _normalize_text(target.get("original_text", ""))
        new_text = _normalize_text(target.get("new_text", ""))
        visual_changed = bool(target.get("before_page_digest")) and target.get("before_page_digest") != after_digests.get(index)
        if not visual_changed:
            saved_pdf.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="Edited PDF verification failed: visible page did not change")
        if new_text and new_text not in normalized_extracted:
            warnings.append("Replacement text was rendered visually but was not found in PDF text extraction.")
        if original_text and original_text in normalized_extracted:
            warnings.append("Original text may remain in hidden PDF extraction layer, but visual text was replaced.")
    return _dedupe(warnings)


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _render_clip_digest(page: fitz.Page, rect: fitz.Rect) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    return hashlib.sha256(pixmap.samples).hexdigest()


def _render_page_digest(page: fitz.Page) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    return hashlib.sha256(pixmap.samples).hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def _log_edit_resolution(
    operation: dict[str, Any],
    source_pdf: Path,
    scratch_pdf: Path,
    extracted_text_before: str,
    padded_rect: fitz.Rect,
) -> None:
    logger.info(
        json.dumps(
            {
                "event": "pdf_edit_resolved_operation",
                "resolved_object_id": operation.get("object_id"),
                "original_text": operation.get("original_text"),
                "replacement_text": operation.get("new_text"),
                "resolved_page_number": operation.get("page"),
                "resolved_bounding_box": operation.get("bounding_box"),
                "visual_replacement_rect": _bbox(padded_rect),
                "source_input_file_path": str(source_pdf),
                "saved_output_file_path": str(scratch_pdf),
                "extracted_text_before_edit": extracted_text_before,
            }
        )
    )


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
