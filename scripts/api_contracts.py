from __future__ import annotations

from collections import Counter

from app.main import app
from app.models.ai_models import AiEditApplyRequest, AiEditPlanRequest, UndoRedoRequest
from app.models.pdf_models import EditApplyResponse, HistoryResponse, PdfAnalyzeResponse


EXPECTED = {
    ("GET", "/health"),
    ("GET", "/privacy"),
    ("GET", "/privacy-policy"),
    ("DELETE", "/pdf/session/{document_id}"),
    ("DELETE", "/jobs/{job_id}"),
    ("POST", "/convert/pdf-to-word"),
    ("POST", "/convert/pdf-to-powerpoint"),
    ("POST", "/convert/pdf-to-excel"),
    ("POST", "/convert/pdf-to-jpg"),
    ("POST", "/convert/pdf-to-png"),
    ("POST", "/convert/word-to-pdf"),
    ("POST", "/convert/powerpoint-to-pdf"),
    ("POST", "/convert/excel-to-pdf"),
    ("GET", "/download/{job_id}/{file_name}"),
    ("POST", "/convert/image-to-pdf"),
    ("POST", "/pdf/merge"),
    ("POST", "/pdf/split"),
    ("POST", "/pdf/rotate"),
    ("POST", "/pdf/compress"),
    ("POST", "/pdf/edit"),
    ("POST", "/pdf/analyze"),
    ("GET", "/pdf/render/{document_id}/{page_number}"),
    ("POST", "/pdf/ai-edit/plan"),
    ("POST", "/pdf/ai-edit/apply"),
    ("POST", "/pdf/undo"),
    ("POST", "/pdf/redo"),
    ("GET", "/pdf/history/{document_id}"),
    ("GET", "/pdf/download/{document_id}/{file_name}"),
}

pairs: list[tuple[str, str]] = []
route_by_pair = {}
for route in app.routes:
    path = getattr(route, "path", "")
    for method in getattr(route, "methods", set()) or set():
        if method in {"HEAD", "OPTIONS"}:
            continue
        pair = (method, path)
        pairs.append(pair)
        route_by_pair[pair] = route

missing = sorted(EXPECTED.difference(pairs))
if missing:
    raise AssertionError(f"Missing API contracts: {missing}")

duplicates = [pair for pair, count in Counter(pairs).items() if count > 1 and pair in EXPECTED]
if duplicates:
    raise AssertionError(f"Duplicate API method/path contracts: {duplicates}")

expected_models = {
    ("POST", "/pdf/analyze"): PdfAnalyzeResponse,
    ("POST", "/pdf/ai-edit/apply"): EditApplyResponse,
    ("POST", "/pdf/undo"): EditApplyResponse,
    ("POST", "/pdf/redo"): EditApplyResponse,
    ("GET", "/pdf/history/{document_id}"): HistoryResponse,
}
for pair, model in expected_models.items():
    actual = getattr(route_by_pair[pair], "response_model", None)
    if actual is not model:
        raise AssertionError(f"{pair} response_model drifted: {actual!r} != {model.__name__}")

def fields(model):
    return set(model.model_fields)

required_fields = {
    AiEditPlanRequest: {"document_id", "instruction", "selected_object_ids", "scope"},
    AiEditApplyRequest: {"document_id", "operations"},
    UndoRedoRequest: {"document_id"},
    PdfAnalyzeResponse: {
        "document_id", "original_file_name", "page_count", "scanned_pages", "pages", "objects",
        "text_object_count", "image_object_count", "table_object_count", "current_version_id",
        "delete_token", "delete_url", "expires_at", "retention_hours", "training_use", "privacy",
    },
    EditApplyResponse: {
        "fileName", "downloadUrl", "document_id", "version_id", "can_undo", "can_redo", "warnings",
    },
    HistoryResponse: {
        "document_id", "current_version_id", "versions", "undone_versions", "can_undo", "can_redo",
    },
}
for model, expected in required_fields.items():
    missing_fields = expected - fields(model)
    if missing_fields:
        raise AssertionError(f"{model.__name__} lost contract fields: {sorted(missing_fields)}")

print("API contracts: PASS")
