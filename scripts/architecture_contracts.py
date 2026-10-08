from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

REQUIRED = [
    "app/main.py",
    "app/api/conversion_routes.py",
    "app/api/pdf_routes.py",
    "app/models/ai_models.py",
    "app/models/pdf_models.py",
    "app/services/ai_planner.py",
    "app/services/document_history.py",
    "app/services/pdf_analysis.py",
    "app/services/ocr.py",
    "app/services/layout_analysis.py",
    "app/services/pdf_edit.py",
    "app/services/pdf_render.py",
    "app/utils/files.py",
    "app/utils/validation.py",
    "Dockerfile",
    "requirements.txt",
    "tests/test_usage_protection.py",
    "tests/test_ocr_layout.py",
]


def fail(message: str) -> None:
    raise AssertionError(message)


for relative in REQUIRED:
    if not (ROOT / relative).exists():
        fail(f"Required architecture file missing: {relative}")

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
if main.count("app.include_router(conversion_router)") != 1:
    fail("conversion_router must be registered exactly once")
if main.count("app.include_router(pdf_router)") != 1:
    fail("pdf_router must be registered exactly once")
if "allow_credentials=False" not in main:
    fail("CORS must not enable credentials while wildcard origins are used")

# Dependency direction: lower layers must never import HTTP/router layers.
for path in (APP / "services").glob("*.py"):
    text = path.read_text(encoding="utf-8")
    if "from app.api" in text or "import app.api" in text or "from app.main" in text:
        fail(f"Service imports HTTP layer: {path.relative_to(ROOT)}")

for path in (APP / "models").glob("*.py"):
    text = path.read_text(encoding="utf-8")
    if any(token in text for token in ("from app.api", "from app.services", "from app.main")):
        fail(f"Model imports a higher layer: {path.relative_to(ROOT)}")

# Parse every Python file and reject duplicated top-level imports.
for path in list(APP.rglob("*.py")) + list((ROOT / "tests").glob("*.py")):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            key = ast.dump(node, include_attributes=False)
            if key in seen:
                fail(f"Duplicate top-level import in {path.relative_to(ROOT)}")
            seen.add(key)

# Security/build invariants.
for path in APP.rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    if "os.system(" in text:
        fail(f"os.system is forbidden: {path.relative_to(ROOT)}")
    if "shell=True" in text:
        fail(f"shell=True is forbidden: {path.relative_to(ROOT)}")

print("Architecture contracts: PASS")


# Upload/conversion protections are release contracts, not optional UI behavior.
files_text = (ROOT / "app/utils/files.py").read_text(encoding="utf-8")
validation_text = (ROOT / "app/utils/validation.py").read_text(encoding="utf-8")
converters_text = (ROOT / "app/converters.py").read_text(encoding="utf-8")

for token in (
    "MAX_UPLOAD_BYTES",
    "MAX_BATCH_UPLOAD_BYTES",
    "MAX_UPLOAD_FILES",
    "validate_upload_filename",
    "cleanup_stale_temp_jobs",
    "COMMAND_TIMEOUT_SECONDS",
):
    if token not in files_text:
        fail(f"Usage protection missing from files.py: {token}")

for token in (
    "MAX_PDF_PAGES",
    "MAX_MERGED_PDF_PAGES",
    "Encrypted/password-protected PDFs are not supported",
    "Invalid or corrupted PDF",
):
    if token not in validation_text:
        fail(f"PDF protection missing from validation.py: {token}")

if "timeout=900" in converters_text or "timeout=600" in converters_text:
    fail("Converters must use the centralized bounded command timeout")

print("Usage protection contracts: PASS")


# OCR quality contracts: Arabic+English, scanned pages, OCR table layout and image metadata.
ocr_text = (ROOT / "app/services/ocr.py").read_text(encoding="utf-8")
analysis_text = (ROOT / "app/services/pdf_analysis.py").read_text(encoding="utf-8")
layout_text = (ROOT / "app/services/layout_analysis.py").read_text(encoding="utf-8")
image_text = (ROOT / "app/services/image_detection.py").read_text(encoding="utf-8")
docker_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

for token in (
    'DEFAULT_OCR_LANGUAGES = "ara+eng"',
    "OCR_RENDER_DPI",
    "preserve_interword_spaces=1",
    "script_profile",
    "page_image_coverage",
):
    if token not in ocr_text:
        fail(f"OCR contract missing: {token}")

for token in ("infer_ocr_tables", "merge_ocr_text", "detected_language", "ocr_languages"):
    if token not in analysis_text:
        fail(f"PDF analysis OCR integration missing: {token}")

for token in ("ocr_layout", "column_count", "row_count"):
    if token not in layout_text:
        fail(f"OCR table-layout contract missing: {token}")

for token in ("page_coverage", "is_page_scan_candidate"):
    if token not in image_text:
        fail(f"Image-analysis contract missing: {token}")

if "tesseract-ocr-ara" not in docker_text or "OCR_LANGUAGES=ara+eng" not in docker_text:
    fail("Docker image must ship Arabic and English Tesseract data")

print("OCR Arabic/layout contracts: PASS")
