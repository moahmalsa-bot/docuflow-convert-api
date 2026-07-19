import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.utils.files import cleanup_old_directories, sanitize_filename


ORIGINAL_VERSION_ID = "v0000"


def document_store_root() -> Path:
    root = Path(os.getenv("DOCUFLOW_DOCUMENT_STORE", Path(tempfile.gettempdir()) / "docuflow-documents"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def cleanup_stale_documents() -> int:
    ttl_hours = int(os.getenv("DOCUFLOW_DOCUMENT_TTL_HOURS", "24"))
    return cleanup_old_directories(document_store_root(), ttl_hours * 3600)


def create_document_dir() -> tuple[str, Path]:
    cleanup_stale_documents()
    document_id = uuid.uuid4().hex
    document_dir = document_store_root() / document_id
    document_dir.mkdir(parents=True, exist_ok=False)
    (document_dir / "previews").mkdir()
    (document_dir / "versions").mkdir()
    return document_id, document_dir


def document_dir(document_id: str) -> Path:
    if not document_id or len(document_id) != 32 or any(char not in "0123456789abcdef" for char in document_id):
        raise HTTPException(status_code=400, detail="Invalid document_id")
    path = document_store_root() / document_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return path


def metadata_path(document_id: str) -> Path:
    return document_dir(document_id) / "metadata.json"


def load_metadata(document_id: str) -> dict[str, Any]:
    path = metadata_path(document_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document metadata not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save_metadata(document_id: str, metadata: dict[str, Any]) -> None:
    metadata["updated_at"] = int(time.time())
    metadata_path(document_id).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def initialize_document(
    document_id: str,
    source_pdf: Path,
    original_filename: str,
    analysis_payload: dict[str, Any],
) -> dict[str, Any]:
    directory = document_dir(document_id)
    version_file = directory / "versions" / "v0000-original.pdf"
    shutil.copy2(source_pdf, version_file)
    now = int(time.time())
    metadata = {
        **analysis_payload,
        "document_id": document_id,
        "original_file_name": sanitize_filename(original_filename),
        "stored_file_name": source_pdf.name,
        "current_version_id": ORIGINAL_VERSION_ID,
        "versions": [
            {
                "version_id": ORIGINAL_VERSION_ID,
                "file_name": version_file.name,
                "source": "original",
                "created_at": now,
                "operations": [],
            }
        ],
        "undone_versions": [],
        "created_at": now,
        "updated_at": now,
    }
    save_metadata(document_id, metadata)
    return metadata


def current_version(document_id: str) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    versions = metadata.get("versions") or []
    if not versions:
        raise HTTPException(status_code=404, detail="Document has no versions")
    return versions[-1]


def current_pdf_path(document_id: str) -> Path:
    version = current_version(document_id)
    path = document_dir(document_id) / "versions" / version["file_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Current PDF version not found")
    return path


def original_pdf_path(document_id: str) -> Path:
    path = document_dir(document_id) / "versions" / "v0000-original.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")
    return path


def add_version(
    document_id: str,
    source_pdf: Path,
    operations: list[dict[str, Any]],
    label: str = "edit",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    next_index = len(metadata.get("versions", []))
    version_id = f"v{next_index:04d}"
    stem = Path(metadata.get("original_file_name", "document.pdf")).stem
    file_name = f"{stem}-{label}-{version_id}.pdf"
    target = document_dir(document_id) / "versions" / file_name
    shutil.copy2(source_pdf, target)

    version = {
        "version_id": version_id,
        "file_name": file_name,
        "source": label,
        "created_at": int(time.time()),
        "operations": operations,
        "warnings": warnings or [],
    }
    metadata.setdefault("versions", []).append(version)
    metadata["undone_versions"] = []
    metadata["current_version_id"] = version_id
    save_metadata(document_id, metadata)
    return version


def undo(document_id: str) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    versions = metadata.get("versions", [])
    if len(versions) <= 1:
        raise HTTPException(status_code=409, detail="Nothing to undo")
    undone = versions.pop()
    metadata.setdefault("undone_versions", []).append(undone)
    metadata["current_version_id"] = versions[-1]["version_id"]
    save_metadata(document_id, metadata)
    return response_for_current_version(document_id)


def redo(document_id: str) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    undone_versions = metadata.get("undone_versions", [])
    if not undone_versions:
        raise HTTPException(status_code=409, detail="Nothing to redo")
    restored = undone_versions.pop()
    metadata.setdefault("versions", []).append(restored)
    metadata["current_version_id"] = restored["version_id"]
    save_metadata(document_id, metadata)
    return response_for_current_version(document_id)


def response_for_current_version(document_id: str) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    version = current_version(document_id)
    return {
        "fileName": version["file_name"],
        "downloadUrl": f"/pdf/download/{document_id}/{version['file_name']}",
        "document_id": document_id,
        "version_id": version["version_id"],
        "can_undo": len(metadata.get("versions", [])) > 1,
        "can_redo": bool(metadata.get("undone_versions")),
        "warnings": version.get("warnings", []),
    }


def download_pdf_path(document_id: str, file_name: str) -> Path:
    safe_name = sanitize_filename(file_name)
    if safe_name != file_name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    for version in load_metadata(document_id).get("versions", []):
        if version.get("file_name") == safe_name:
            path = document_dir(document_id) / "versions" / safe_name
            if path.exists():
                return path
    raise HTTPException(status_code=404, detail="PDF version not found")


def history(document_id: str) -> dict[str, Any]:
    metadata = load_metadata(document_id)
    return {
        "document_id": document_id,
        "current_version_id": metadata.get("current_version_id", ORIGINAL_VERSION_ID),
        "versions": metadata.get("versions", []),
        "undone_versions": metadata.get("undone_versions", []),
        "can_undo": len(metadata.get("versions", [])) > 1,
        "can_redo": bool(metadata.get("undone_versions")),
    }
