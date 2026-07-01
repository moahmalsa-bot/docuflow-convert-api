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


def plan_ai_edit(document_id: str, instruction: str, selected_object_ids: list[str], scope: str) -> Any:
    if not instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")
    metadata = load_metadata(document_id)
    selected_objects = _selected_objects(metadata, selected_object_ids)
    change_request = _parse_change_text_instruction(instruction)
    if change_request:
        return _plan_change_text(change_request[0], change_request[1], metadata, selected_objects)
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
                    "Use operation as the operation name field. "
                    "Allowed operation values are replace_text, delete_text, highlight, redact, delete_image, replace_image. "
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
            operations.append({"operation": "redact", "object_id": object_id})
        elif "highlight" in lower:
            operations.append({"operation": "highlight", "object_id": object_id})
        elif "delete" in lower or "remove" in lower:
            operations.append({"operation": "delete_image" if object_type == "image" else "delete_text", "object_id": object_id})
        elif "replace" in lower and object_type == "text":
            operations.append({"operation": "replace_text", "object_id": object_id, "new_text": replacement})
    return operations


def _plan_change_text(
    old_text: str,
    new_text: str,
    metadata: dict[str, Any],
    selected_objects: list[dict[str, Any]],
) -> Any:
    if selected_objects:
        operations = [
            {
                "operation": "replace_text",
                "object_id": item["object_id"],
                "page": item["page_number"],
                "new_text": new_text,
                "preserve_style": True,
                "fit_mode": "shrink_to_fit",
            }
            for item in selected_objects
            if item.get("object_type") == "text" and _text_matches(item.get("text", ""), old_text)
        ]
        if not operations:
            raise HTTPException(status_code=404, detail=f"Selected objects do not contain text matching: {old_text}")
        return validate_ai_edit_operations(operations, metadata, allow_image_payload=False)

    matches = _find_text_matches(metadata, old_text)
    if not matches:
        raise HTTPException(status_code=404, detail=f"Text not found in analyzed PDF objects: {old_text}")
    if len(matches) > 1:
        return {
            "requires_confirmation": True,
            "message": "Multiple text matches found. Select one object and apply the returned operation for that object.",
            "old_text": old_text,
            "new_text": new_text,
            "matches": [_match_confirmation_payload(item, new_text) for item in matches],
            "operations": [],
        }

    operations = [
        {
            "operation": "replace_text",
            "object_id": matches[0]["object_id"],
            "page": matches[0]["page_number"],
            "new_text": new_text,
            "preserve_style": True,
            "fit_mode": "shrink_to_fit",
        }
    ]
    return validate_ai_edit_operations(operations, metadata, allow_image_payload=False)


def _find_text_matches(metadata: dict[str, Any], old_text: str) -> list[dict[str, Any]]:
    return [
        item
        for item in metadata.get("objects", [])
        if item.get("object_type") == "text" and _text_matches(item.get("text", ""), old_text)
    ]


def _text_matches(candidate: str, needle: str) -> bool:
    return _normalize_for_search(needle) in _normalize_for_search(candidate)


def _match_confirmation_payload(item: dict[str, Any], new_text: str) -> dict[str, Any]:
    return {
        "object_id": item["object_id"],
        "object_type": item.get("object_type"),
        "page_number": item.get("page_number"),
        "text": item.get("text", ""),
        "bounding_box": item.get("bounding_box"),
        "normalized_box": item.get("normalized_box"),
        "confidence": item.get("confidence"),
        "source": item.get("metadata", {}).get("source"),
        "operation": {
            "operation": "replace_text",
            "object_id": item["object_id"],
            "page": item.get("page_number"),
            "new_text": new_text,
            "preserve_style": True,
            "fit_mode": "shrink_to_fit",
        },
    }


def _parse_change_text_instruction(instruction: str) -> tuple[str, str] | None:
    text = instruction.strip()
    quoted = re.search(
        r"^(?:change|replace)\s+['\"](.+?)['\"]\s+(?:to|with)\s+['\"](.+?)['\"]\s*\.?$",
        text,
        flags=re.IGNORECASE,
    )
    if quoted:
        return quoted.group(1).strip(), quoted.group(2).strip()

    unquoted = re.search(
        r"^(?:change|replace)\s+(.+?)\s+(?:to|with)\s+(.+?)\s*\.?$",
        text,
        flags=re.IGNORECASE,
    )
    if not unquoted:
        return None
    return _strip_instruction_text(unquoted.group(1)), _strip_instruction_text(unquoted.group(2))


def _strip_instruction_text(value: str) -> str:
    return value.strip().strip("'\"").strip()


def _normalize_for_search(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _extract_quoted_text(instruction: str) -> str:
    match = re.search(r"['\"]([^'\"]+)['\"]", instruction)
    return match.group(1) if match else ""
