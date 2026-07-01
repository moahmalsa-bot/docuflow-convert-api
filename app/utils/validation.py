import os
from pathlib import Path

import fitz
from fastapi import HTTPException


MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "200"))
MAX_EDIT_OPERATIONS = int(os.getenv("MAX_EDIT_OPERATIONS", "250"))


def validate_pdf_page_count(pdf_path: Path, max_pages: int = MAX_PDF_PAGES) -> int:
    try:
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid or unreadable PDF") from exc

    if page_count == 0:
        raise HTTPException(status_code=400, detail="PDF has no pages")
    if page_count > max_pages:
        raise HTTPException(status_code=413, detail=f"PDF exceeds MAX_PDF_PAGES={max_pages}")
    return page_count


def validate_operation_count(operations: list[dict]) -> None:
    if len(operations) > MAX_EDIT_OPERATIONS:
        raise HTTPException(status_code=413, detail=f"Too many edit operations; max is {MAX_EDIT_OPERATIONS}")

