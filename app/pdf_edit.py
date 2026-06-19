from pathlib import Path
from typing import Any

import fitz
from fastapi import HTTPException


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
