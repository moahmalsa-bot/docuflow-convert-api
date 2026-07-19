import os
from pathlib import Path

import fitz
from fastapi import HTTPException

from app.services.document_history import document_dir, current_pdf_path, original_pdf_path


PDF_RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "144"))


def render_document_page(document_id: str, page_number: int, version: str = "original", dpi: int = PDF_RENDER_DPI) -> Path:
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be 1 or greater")

    source_pdf = original_pdf_path(document_id) if version == "original" else current_pdf_path(document_id)
    output_path = document_dir(document_id) / "previews" / f"{version}-page-{page_number}-{dpi}.png"
    if output_path.exists():
        return output_path

    with fitz.open(source_pdf) as document:
        if page_number > document.page_count:
            raise HTTPException(status_code=404, detail="Page not found")
        scale = dpi / 72
        pixmap = document[page_number - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.save(output_path)
    return output_path

