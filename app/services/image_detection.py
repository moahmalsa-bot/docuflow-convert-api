from typing import Any

import fitz

from app.utils.coordinates import normalized_box


def bbox(rect: fitz.Rect) -> list[float]:
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


def detect_embedded_images(page: fitz.Page, page_number: int) -> list[dict[str, Any]]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    objects: list[dict[str, Any]] = []
    image_index = 0
    seen: set[tuple[int, tuple[float, float, float, float]]] = set()

    for image_info in page.get_images(full=True):
        xref = int(image_info[0])
        for rect in page.get_image_rects(xref):
            rounded_rect = tuple(bbox(rect))
            key = (xref, rounded_rect)
            if key in seen:
                continue
            seen.add(key)
            image_index += 1
            box = list(rounded_rect)
            page_area = max(page_width * page_height, 1.0)
            image_area = max(0.0, float(rect.width * rect.height))
            coverage = round(min(1.0, image_area / page_area), 4)
            pixel_width = int(image_info[2]) if len(image_info) > 3 else 0
            pixel_height = int(image_info[3]) if len(image_info) > 3 else 0
            objects.append(
                {
                    "object_id": f"p{page_number}-image-{image_index}",
                    "object_type": "image",
                    "page_number": page_number,
                    "bounding_box": box,
                    "normalized_box": normalized_box(box, page_width, page_height),
                    "text": "",
                    "font_size": None,
                    "confidence": 1.0,
                    "metadata": {
                        "xref": xref,
                        "source": "embedded",
                        "pixel_width": pixel_width,
                        "pixel_height": pixel_height,
                        "page_coverage": coverage,
                        "is_page_scan_candidate": coverage >= 0.7,
                    },
                }
            )
    return objects

