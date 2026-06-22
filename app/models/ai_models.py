from typing import Any, Literal

from pydantic import BaseModel, Field


class AiEditPlanRequest(BaseModel):
    document_id: str
    instruction: str
    selected_object_ids: list[str] = Field(default_factory=list)
    scope: str = "selection"


class AiEditPlanResponse(BaseModel):
    operations: list[dict[str, Any]]


class AiEditApplyRequest(BaseModel):
    document_id: str
    operations: list[dict[str, Any]]


class UndoRedoRequest(BaseModel):
    document_id: str


class EditOperation(BaseModel):
    type: Literal["replace_text", "delete_text", "highlight", "redact", "delete_image", "replace_image"]
    page_number: int | None = None
    object_id: str | None = None
    bounding_box: list[float] | None = None
    text: str | None = None
    font_size: float | None = None
    color: list[float] | None = None
    fill: list[float] | None = None
    opacity: float | None = None
    replacement_image_base64: str | None = None

