import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException, UploadFile
from starlette.background import BackgroundTask
from starlette.responses import FileResponse


MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_BATCH_UPLOAD_MB = int(os.getenv("MAX_BATCH_UPLOAD_MB", "200"))
MAX_BATCH_UPLOAD_BYTES = MAX_BATCH_UPLOAD_MB * 1024 * 1024
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "20"))
MAX_FILENAME_CHARS = int(os.getenv("MAX_FILENAME_CHARS", "180"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "300"))
TEMP_JOB_TTL_HOURS = int(os.getenv("TEMP_JOB_TTL_HOURS", "2"))

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".zip": "application/zip",
}


class ConversionError(RuntimeError):
    """Raised when a document conversion command fails."""


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def validate_upload_filename(filename: str, label: str = "File") -> str:
    if not filename:
        raise HTTPException(status_code=400, detail=f"{label} filename is required")
    if len(filename) > MAX_FILENAME_CHARS:
        raise HTTPException(status_code=400, detail=f"{label} filename is too long")
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail=f"{label} filename contains a path")
    if re.search(r"[\x00-\x1f\x7f]", filename):
        raise HTTPException(status_code=400, detail=f"{label} filename contains control characters")

    name = filename.strip()
    if name != filename or name in {"", ".", ".."} or name.startswith(".") or name.endswith("."):
        raise HTTPException(status_code=400, detail=f"{label} filename is not allowed")
    return name


def sanitize_filename(filename: str, fallback: str = "upload") -> str:
    name = Path(filename or fallback).name.strip().replace("\x00", "")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:MAX_FILENAME_CHARS].rstrip(" .")
    return name or fallback


def unique_path(directory: Path, filename: str) -> Path:
    safe_name = sanitize_filename(filename)
    candidate = directory / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    return directory / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def ensure_ext(path: Path, extensions: Iterable[str], label: str) -> None:
    allowed = {ext.lower() for ext in extensions}
    if path.suffix.lower() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be one of: {', '.join(sorted(allowed))}",
        )


def cleanup_stale_temp_jobs(
    max_age_seconds: int | None = None,
    temp_root: Path | None = None,
) -> int:
    root = temp_root or Path(tempfile.gettempdir())
    max_age = max_age_seconds if max_age_seconds is not None else TEMP_JOB_TTL_HOURS * 3600
    if not root.exists():
        return 0

    now = time.time()
    removed = 0
    for child in root.glob("docuflow-*"):
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            if now - child.stat().st_mtime <= max_age:
                continue
            cleanup_path(child)
            removed += 1
        except OSError:
            continue
    return removed


def create_job_dir() -> Path:
    cleanup_stale_temp_jobs()
    return Path(tempfile.mkdtemp(prefix="docuflow-"))


def cleanup_path(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def cleanup_old_directories(root: Path, max_age_seconds: int) -> int:
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for child in root.iterdir():
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            if now - child.stat().st_mtime > max_age_seconds:
                cleanup_path(child)
                removed += 1
        except OSError:
            continue
    return removed


async def save_upload(upload: UploadFile, directory: Path, allowed_exts: Iterable[str], label: str) -> Path:
    filename = validate_upload_filename(upload.filename or "", label)
    declared_size = getattr(upload, "size", None)
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        await upload.close()
        raise HTTPException(status_code=413, detail=f"Upload exceeds MAX_UPLOAD_MB={MAX_UPLOAD_MB}")

    output_path = unique_path(directory, filename)
    ensure_ext(output_path, allowed_exts, label)

    total = 0
    try:
        with output_path.open("wb") as file_obj:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds MAX_UPLOAD_MB={MAX_UPLOAD_MB}",
                    )
                file_obj.write(chunk)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if total == 0:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{label} is empty")
    return output_path


async def save_uploads(
    uploads: list[UploadFile],
    directory: Path,
    allowed_exts: Iterable[str],
    label: str,
    min_count: int = 1,
) -> list[Path]:
    if len(uploads) < min_count:
        raise HTTPException(status_code=400, detail=f"At least {min_count} {label} file(s) required")
    if len(uploads) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"Too many files; max is {MAX_UPLOAD_FILES}")

    declared_total = sum(int(size) for size in (getattr(upload, "size", None) for upload in uploads) if size is not None)
    if declared_total > MAX_BATCH_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Batch exceeds MAX_BATCH_UPLOAD_MB={MAX_BATCH_UPLOAD_MB}")

    paths: list[Path] = []
    actual_total = 0
    for upload in uploads:
        path = await save_upload(upload, directory, allowed_exts, f"{label} file")
        paths.append(path)
        actual_total += path.stat().st_size
        if actual_total > MAX_BATCH_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Batch exceeds MAX_BATCH_UPLOAD_MB={MAX_BATCH_UPLOAD_MB}")
    return paths


def parse_operations_json(raw_json: str | None) -> list[dict]:
    if not raw_json:
        raise HTTPException(status_code=400, detail="operations_json is required")
    try:
        operations = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"operations_json is invalid JSON: {exc.msg}") from exc
    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail="operations_json must be a JSON array")
    return operations


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_command(command: list[str], timeout: int | float | None = None) -> subprocess.CompletedProcess[str]:
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT_SECONDS
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=effective_timeout,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"Required command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Command timed out after {effective_timeout}s: {' '.join(command)}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown command failure"
        raise ConversionError(stderr[-2000:])
    return result


def first_available_binary(candidates: Iterable[str], label: str) -> str:
    for candidate in candidates:
        if command_exists(candidate):
            return candidate
    raise ConversionError(f"{label} is not installed or not on PATH")


def libreoffice_binary() -> str:
    return first_available_binary(("libreoffice", "soffice"), "LibreOffice")


def ghostscript_binary() -> str:
    return first_available_binary(("gs", "gswin64c", "gswin32c"), "Ghostscript")


def zip_directory(files: list[Path], output_zip: Path) -> Path:
    with ZipFile(output_zip, "w", ZIP_DEFLATED) as archive:
        for file_path in files:
            archive.write(file_path, arcname=file_path.name)
    return output_zip


def response_for_file(path: Path, job_dir: Path, download_name: str | None = None) -> FileResponse:
    media_type = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=download_name or path.name,
        background=BackgroundTask(cleanup_path, job_dir),
    )
