from typing import Any, Literal

from pydantic import BaseModel, Field


class NormalizedBox(BaseModel):
    left: float
    top: float
    width: float
    height: float


class PdfObject(BaseModel):
    object_id: str
    object_type: Literal["text", "image", "table"]
    page_number: int
    bounding_box: list[float]
    normalized_box: NormalizedBox
    text: str = ""
    font_size: float | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PdfPageAnalysis(BaseModel):
    page_number: int
    page_width: float
    page_height: float
    render_width: int
    render_height: int
    objects: list[PdfObject] = Field(default_factory=list)


class PdfAnalyzeResponse(BaseModel):
    document_id: str
    original_file_name: str
    page_count: int
    scanned_pages: list[int]
    pages: list[PdfPageAnalysis]
    objects: list[PdfObject]
    text_object_count: int
    image_object_count: int
    table_object_count: int
    current_version_id: str


class EditApplyResponse(BaseModel):
    fileName: str
    downloadUrl: str
    document_id: str
    version_id: str
    can_undo: bool
    can_redo: bool


class HistoryResponse(BaseModel):
    document_id: str
    current_version_id: str
    versions: list[dict]
    undone_versions: list[dict] = Field(default_factory=list)
    can_undo: bool
    can_redo: bool
