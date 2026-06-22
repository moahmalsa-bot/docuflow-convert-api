from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.models.ai_models import AiEditApplyRequest, AiEditPlanRequest, UndoRedoRequest
from app.models.pdf_models import EditApplyResponse, HistoryResponse, PdfAnalyzeResponse
from app.services.ai_planner import plan_ai_edit
from app.services.document_history import create_document_dir, download_pdf_path, history, redo, undo
from app.services.pdf_analysis import analyze_pdf_document
from app.services.pdf_edit import apply_ai_edit_operations, apply_pdf_edits
from app.services.pdf_render import render_document_page
from app.utils.files import cleanup_path, parse_operations_json, response_for_file, save_upload
from app.utils.validation import validate_pdf_page_count


router = APIRouter()


@router.post("/pdf/edit")
async def edit_pdf_endpoint(file: UploadFile = File(...), operations_json: str = Form(...)):
    job_dir = create_document_dir()[1]
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        operations = parse_operations_json(operations_json)
        output_pdf = job_dir / f"{input_pdf.stem}-edited.pdf"
        apply_pdf_edits(input_pdf, output_pdf, operations)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/pdf/analyze", response_model=PdfAnalyzeResponse)
async def analyze_pdf_endpoint(file: UploadFile = File(...)):
    document_id, document_dir = create_document_dir()
    try:
        input_pdf = await save_upload(file, document_dir, [".pdf"], "PDF")
        return analyze_pdf_document(document_id, document_dir, input_pdf, file.filename or input_pdf.name)
    except Exception:
        cleanup_path(document_dir)
        raise


@router.get("/pdf/render/{document_id}/{page_number}")
async def render_pdf_page_endpoint(document_id: str, page_number: int):
    png_path = render_document_page(document_id, page_number)
    return FileResponse(path=png_path, media_type="image/png", filename=png_path.name)


@router.post("/pdf/ai-edit/plan")
async def plan_ai_pdf_edit_endpoint(payload: AiEditPlanRequest):
    return plan_ai_edit(
        document_id=payload.document_id,
        instruction=payload.instruction,
        selected_object_ids=payload.selected_object_ids,
        scope=payload.scope,
    )


@router.post("/pdf/ai-edit/apply", response_model=EditApplyResponse)
async def apply_ai_pdf_edit_endpoint(payload: AiEditApplyRequest):
    return apply_ai_edit_operations(payload.document_id, payload.operations)


@router.post("/pdf/undo", response_model=EditApplyResponse)
async def undo_pdf_edit_endpoint(payload: UndoRedoRequest):
    return undo(payload.document_id)


@router.post("/pdf/redo", response_model=EditApplyResponse)
async def redo_pdf_edit_endpoint(payload: UndoRedoRequest):
    return redo(payload.document_id)


@router.get("/pdf/history/{document_id}", response_model=HistoryResponse)
async def pdf_history_endpoint(document_id: str):
    return history(document_id)


@router.get("/pdf/download/{document_id}/{file_name}")
async def download_ai_edited_pdf_endpoint(document_id: str, file_name: str):
    pdf_path = download_pdf_path(document_id, file_name)
    return FileResponse(path=pdf_path, media_type="application/pdf", filename=pdf_path.name)
