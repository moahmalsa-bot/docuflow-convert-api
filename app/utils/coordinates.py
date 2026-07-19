def normalized_box(bounding_box: list[float], page_width: float, page_height: float) -> dict[str, float]:
    x0, y0, x1, y1 = bounding_box
    if page_width <= 0 or page_height <= 0:
        raise ValueError("page_width and page_height must be greater than zero")

    left = _clamp(x0 / page_width)
    top = _clamp(y0 / page_height)
    right = _clamp(x1 / page_width)
    bottom = _clamp(y1 / page_height)
    return {
        "left": round(left, 6),
        "top": round(top, 6),
        "width": round(max(0.0, right - left), 6),
        "height": round(max(0.0, bottom - top), 6),
    }


def denormalize_box(box: dict[str, float], render_width: int, render_height: int) -> list[float]:
    left = box["left"] * render_width
    top = box["top"] * render_height
    right = left + box["width"] * render_width
    bottom = top + box["height"] * render_height
    return [left, top, right, bottom]


def render_dimensions(page_width: float, page_height: float, dpi: int) -> tuple[int, int]:
    scale = dpi / 72
    return round(page_width * scale), round(page_height * scale)


def page_region(bounding_box: list[float], page_height: float) -> str:
    _, y0, _, y1 = bounding_box
    mid_y = (y0 + y1) / 2
    if mid_y <= page_height * 0.12:
        return "header"
    if mid_y >= page_height * 0.88:
        return "footer"
    return "body"


def selection_box_from_normalized(
    normalized: dict[str, float],
    page_width: float,
    page_height: float,
) -> list[float]:
    left = normalized["left"] * page_width
    top = normalized["top"] * page_height
    right = left + normalized["width"] * page_width
    bottom = top + normalized["height"] * page_height
    return [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))

