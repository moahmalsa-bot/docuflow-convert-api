"""
Excel -> PDF using LibreOffice Calc headless (the real spreadsheet print
engine). Existing print settings are respected exactly; sane defaults are
applied ONLY to sheets that have none. Returns JSON with a real download URL.
"""

import hashlib
import os
import re
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from pypdf import PdfReader
from starlette.responses import FileResponse, JSONResponse

from app.utils.files import cleanup_old_directories, libreoffice_binary, run_command

OUTPUT_ROOT = Path(os.getenv("OUTPUT_DIR", "/tmp/docuflow-outputs"))
# Converted files are kept for at least ONE HOUR — never deleted sooner.
OUTPUT_MAX_AGE_SECONDS = max(int(os.getenv("OUTPUT_MAX_AGE_SECONDS", "86400")), 3600)
EXCEL_TIMEOUT_SECONDS = int(os.getenv("EXCEL_TIMEOUT_SECONDS", "300"))

DOWNLOAD_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

_active_digests: set[str] = set()
_active_lock = threading.Lock()


def _workbook_has_drawings(path: Path) -> bool:
    """Charts/images/drawings present -> openpyxl round-trip would drop them,
    so the workbook must never be rewritten."""
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.startswith(("xl/charts/", "xl/media/", "xl/drawings/")) for n in z.namelist())
    except zipfile.BadZipFile:
        return True


def _sheet_is_wide(ws) -> bool:
    total_width = 0.0
    for col in range(1, ws.max_column + 1):
        letter = ws.cell(row=1, column=col).column_letter
        dim = ws.column_dimensions.get(letter)
        total_width += dim.width if dim and dim.width else 8.43
    total_height = sum(
        (ws.row_dimensions[r].height if r in ws.row_dimensions and ws.row_dimensions[r].height else 15.0)
        for r in range(1, min(ws.max_row, 200) + 1)
    )
    return total_width * 7 > max(total_height * (4 / 3), 700)


def _apply_default_print_settings(path: Path) -> None:
    """Sheets that already have a print area / fit-to-page / scale are left
    untouched. Only unconfigured sheets get: used-range print area, A4,
    landscape when wide, fit one page wide (unlimited tall), centred."""
    if path.suffix.lower() not in {".xlsx", ".xlsm"} or _workbook_has_drawings(path):
        return
    import openpyxl

    wb = openpyxl.load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")
    changed = False
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        fit_cfg = bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage)
        has_config = bool(ws.print_area) or fit_cfg or bool(ws.page_setup.scale and ws.page_setup.scale != 100)
        if has_config or ws.max_row < 1 or ws.max_column < 1:
            continue
        ws.print_area = ws.calculate_dimension()  # real used range only
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.orientation = "landscape" if _sheet_is_wide(ws) else "portrait"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0  # allow multiple pages vertically
        ws.print_options.horizontalCentered = True
        changed = True
    if changed:
        wb.save(path)


def _count_visible_sheets(path: Path) -> int:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True)
        count = sum(1 for ws in wb.worksheets if ws.sheet_state == "visible")
        wb.close()
        return count
    import xlrd

    book = xlrd.open_workbook(str(path), on_demand=True)
    return sum(1 for i in range(book.nsheets) if book.sheet_visible(i) == 0) or book.nsheets


def _page_is_blank(page) -> bool:
    if (page.extract_text() or "").strip():
        return False
    resources = page.get("/Resources") or {}
    return "/XObject" not in resources  # no text and no drawn objects


def excel_to_pdf_job(input_file: Path, job_dir: Path, request: Request) -> dict:
    digest = hashlib.sha256(input_file.read_bytes()).hexdigest()
    with _active_lock:
        if digest in _active_digests:
            raise HTTPException(status_code=429, detail="This file is already being converted.")
        _active_digests.add(digest)
    try:
        sheet_count = _count_visible_sheets(input_file)
        if sheet_count == 0:
            raise HTTPException(status_code=400, detail="The workbook has no visible worksheets.")

        _apply_default_print_settings(input_file)

        run_command(
            [
                libreoffice_binary(),
                "--headless",
                "--calc",
                "--norestore",
                "--nologo",
                f"-env:UserInstallation=file://{job_dir}/lo-profile",
                "--convert-to",
                "pdf:calc_pdf_Export",
                "--outdir",
                str(job_dir),
                str(input_file),
            ],
            timeout=EXCEL_TIMEOUT_SECONDS,
        )
        pdf_path = job_dir / f"{input_file.stem}.pdf"
        if not pdf_path.exists():
            matches = list(job_dir.glob("*.pdf"))
            if not matches:
                raise HTTPException(status_code=500, detail="LibreOffice did not produce a PDF.")
            pdf_path = matches[0]

        # ---- output validation ----
        if pdf_path.stat().st_size <= 0:
            raise HTTPException(status_code=500, detail="Conversion produced an empty PDF.")
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
        if page_count == 0:
            raise HTTPException(status_code=500, detail="Conversion produced a PDF with no pages.")
        if page_count < sheet_count:
            raise HTTPException(
                status_code=500,
                detail=f"Only {page_count} page(s) exported for {sheet_count} visible worksheet(s).",
            )
        if _page_is_blank(reader.pages[0]) or _page_is_blank(reader.pages[-1]):
            raise HTTPException(status_code=500, detail="The exported PDF has a blank first or last page.")

        # ---- persist under a unique job id (kept >= 1 hour) ----
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        cleanup_old_directories(OUTPUT_ROOT, OUTPUT_MAX_AGE_SECONDS)
        job_id = uuid.uuid4().hex
        file_name = f"{input_file.stem}.pdf"
        final_dir = OUTPUT_ROOT / job_id
        final_dir.mkdir(parents=True)
        final_path = final_dir / file_name
        pdf_path.replace(final_path)

        base = str(request.base_url).rstrip("/")
        if base.startswith("http://") and request.headers.get("x-forwarded-proto") == "https":
            base = "https://" + base[len("http://"):]
        return {
            "success": True,
            "fileName": file_name,
            # file name is URL-encoded so spaces/special characters survive
            "downloadUrl": f"{base}/download/{job_id}/{quote(file_name)}",
            "sheetCount": sheet_count,
            "pageCount": page_count,
            "fileSize": final_path.stat().st_size,
        }
    finally:
        with _active_lock:
            _active_digests.discard(digest)


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"success": False, "error": "Converted file was not found or has expired."},
    )


def excel_download_response(job_id: str, file_name: str):
    """Serves the REAL file bytes as a forced attachment download.
    Never returns HTML, redirects, 204, or an empty body."""
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or "/" in file_name or "\\" in file_name or ".." in file_name:
        return _not_found()
    path = OUTPUT_ROOT / job_id / file_name
    if not path.is_file() or path.stat().st_size <= 0:
        return _not_found()

    media_type = DOWNLOAD_MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    ascii_name = file_name.encode("ascii", "replace").decode()
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name)}"
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=file_name,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(path.stat().st_size),
            "Cache-Control": "no-store",
        },
    )
