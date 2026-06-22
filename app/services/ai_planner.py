import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from app.services.document_history import load_metadata
from app.services.pdf_edit import validate_ai_edit_operations
from app.utils.files import ConversionError


def plan_ai_edit(document_id: str, instruction: str, selected_object_ids: list[str], scope: str) -> list[dict[str, Any]]:
    if not instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")
    metadata = load_metadata(document_id)
    selected_objects = _selected_objects(metadata, selected_object_ids)
    operations = _call_ai_provider(instruction, selected_objects, scope)
    return validate_ai_edit_operations(operations, metadata, allow_image_payload=False)


def _selected_objects(metadata: dict[str, Any], selected_object_ids: list[str]) -> list[dict[str, Any]]:
    objects_by_id = {item["object_id"]: item for item in metadata.get("objects", [])}
    if not selected_object_ids:
        return []
    missing = [object_id for object_id in selected_object_ids if object_id not in objects_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown selected object IDs: {', '.join(missing)}")
    return [objects_by_id[object_id] for object_id in selected_object_ids]


def _call_ai_provider(instruction: str, selected_objects: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    provider = os.getenv("AI_PROVIDER", "").lower().strip()
    api_key = os.getenv("AI_API_KEY")
    if not provider or not api_key:
        return _local_edit_plan(instruction, selected_objects)

    base_url = os.getenv("AI_BASE_URL")
    if provider == "openai":
        url = base_url or "https://api.openai.com/v1/chat/completions"
    elif base_url:
        url = base_url.rstrip("/") + "/chat/completions"
    else:
        raise HTTPException(status_code=503, detail="AI_BASE_URL is required for non-openai AI_PROVIDER")

    request = urllib.request.Request(
        url,
        data=json.dumps(_ai_request_payload(instruction, selected_objects, scope)).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ConversionError(f"AI provider request failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ConversionError(f"AI provider request failed: {exc.reason}") from exc

    content = payload["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="AI provider did not return valid JSON operations") from exc


def _ai_request_payload(instruction: str, selected_objects: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    model = os.getenv("AI_MODEL")
    if not model:
        raise HTTPException(status_code=503, detail="AI_MODEL is required when AI planning is enabled")
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only a JSON array of PDF edit operations. "
                    "Allowed operation types are replace_text, delete_text, highlight, redact, delete_image, replace_image. "
                    "Prefer object_id-based operations. Do not include prose or markdown."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": instruction,
                        "scope": scope,
                        "selected_objects": selected_objects,
                    }
                ),
            },
        ],
        "temperature": 0,
    }


def _local_edit_plan(instruction: str, selected_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lower = instruction.lower()
    replacement = _extract_quoted_text(instruction)
    operations: list[dict[str, Any]] = []
    for item in selected_objects:
        object_id = item["object_id"]
        object_type = item["object_type"]
        if "redact" in lower:
            operations.append({"type": "redact", "object_id": object_id})
        elif "highlight" in lower:
            operations.append({"type": "highlight", "object_id": object_id})
        elif "delete" in lower or "remove" in lower:
            operations.append({"type": "delete_image" if object_type == "image" else "delete_text", "object_id": object_id})
        elif "replace" in lower and object_type == "text":
            operations.append({"type": "replace_text", "object_id": object_id, "text": replacement})
    return operations


def _extract_quoted_text(instruction: str) -> str:
    match = re.search(r"['\"]([^'\"]+)['\"]", instruction)
    return match.group(1) if match else ""
