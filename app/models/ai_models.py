from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AiEditPlanRequest(BaseModel):
    document_id: str
    instruction: str
    selected_object_ids: list[str] = Field(default_factory=list)
    scope: str = "selection"


class AiEditPlanResponse(BaseModel):
    operations: list[dict[str, Any]]


class AiEditApplyRequest(BaseModel):
    document_id: str
    operations: list["EditOperation"]


class UndoRedoRequest(BaseModel):
    document_id: str


class EditOperation(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    operation: Literal["replace_text", "delete_text", "highlight", "redact", "delete_image", "replace_image"] | None = None
    type: Literal["replace_text", "delete_text", "highlight", "redact", "delete_image", "replace_image"] | None = None
    object_id: str | None = None
    page: int | None = None
    page_number: int | None = None
    bounding_box: list[float] | None = None
    bbox: list[float] | None = None
    new_text: str | None = None
    text: str | None = None
    font_size: float | None = None
    color: list[float] | None = None
    fill: list[float] | None = None
    opacity: float | None = None
    preserve_style: bool = True
    fit_mode: Literal["shrink_to_fit", "clip", "overflow"] = "shrink_to_fit"
    replacement_image_base64: str | None = None
