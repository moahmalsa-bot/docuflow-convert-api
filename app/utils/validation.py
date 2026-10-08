import os
from pathlib import Path

import fitz
from fastapi import HTTPException


MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "200"))
MAX_MERGED_PDF_PAGES = int(os.getenv("MAX_MERGED_PDF_PAGES", "400"))
MAX_EDIT_OPERATIONS = int(os.getenv("MAX_EDIT_OPERATIONS", "250"))


def validate_pdf_page_count(pdf_path: Path, max_pages: int = MAX_PDF_PAGES) -> int:
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise HTTPException(status_code=400, detail="Invalid or unreadable PDF")

    try:
        with pdf_path.open("rb") as file_obj:
            header = file_obj.read(1024)
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Invalid or unreadable PDF") from exc

    if b"%PDF-" not in header:
        raise HTTPException(status_code=400, detail="Invalid or corrupted PDF")

    try:
        with fitz.open(pdf_path) as document:
            if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
                raise HTTPException(status_code=400, detail="Encrypted/password-protected PDFs are not supported")

            page_count = document.page_count
            if page_count == 0:
                raise HTTPException(status_code=400, detail="PDF has no pages")
            if page_count > max_pages:
                raise HTTPException(status_code=413, detail=f"PDF exceeds MAX_PDF_PAGES={max_pages}")

            # Force every page object to load so damaged xref/page trees fail before
            # converters, OCR or renderers spend significant CPU on the document.
            for page_index in range(page_count):
                page = document.load_page(page_index)
                rect = page.rect
                if rect.width <= 0 or rect.height <= 0:
                    raise HTTPException(status_code=400, detail="Invalid or corrupted PDF")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid or corrupted PDF") from exc

    return page_count


def validate_pdf_batch_page_count(page_counts: list[int], max_pages: int = MAX_MERGED_PDF_PAGES) -> int:
    total = sum(page_counts)
    if total > max_pages:
        raise HTTPException(status_code=413, detail=f"Merged PDF exceeds MAX_MERGED_PDF_PAGES={max_pages}")
    return total


def validate_operation_count(operations: list[dict]) -> None:
    if len(operations) > MAX_EDIT_OPERATIONS:
        raise HTTPException(status_code=413, detail=f"Too many edit operations; max is {MAX_EDIT_OPERATIONS}")
