from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.converters import (
    compress_pdf,
    images_to_pdf,
    merge_pdfs,
    office_to_pdf,
    pdf_to_excel,
    pdf_to_images_zip,
    pdf_to_powerpoint,
    pdf_to_word,
    rotate_pdf,
    split_pdf,
)
from app.pdf_edit import (
    analyze_pdf_document,
    apply_ai_edit_operations,
    apply_pdf_edits,
    create_document_dir,
    download_pdf_path,
    plan_ai_edit,
    render_document_page,
)
from app.utils import (
    ConversionError,
    create_job_dir,
    cleanup_path,
    parse_operations_json,
    response_for_file,
    save_upload,
    save_uploads,
)


app = FastAPI(
    title="DocuFlow API",
    version="1.0.0",
    description="Production-ready document conversion and PDF editing API.",
)


class AiEditPlanRequest(BaseModel):
    document_id: str
    instruction: str
    selected_object_ids: list[str] = Field(default_factory=list)
    scope: str = "selection"


class AiEditApplyRequest(BaseModel):
    document_id: str
    operations: list[dict]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ConversionError)
async def conversion_error_handler(_: Request, exc: ConversionError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "DocuFlow API"}


@app.post("/convert/pdf-to-word")
async def convert_pdf_to_word(file: UploadFile = File(...), use_ocr: bool = Form(False)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_docx = job_dir / f"{input_pdf.stem}.docx"
        pdf_to_word(input_pdf, output_docx, use_ocr=use_ocr)
        return response_for_file(output_docx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/pdf-to-powerpoint")
async def convert_pdf_to_powerpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_pptx = job_dir / f"{input_pdf.stem}.pptx"
        pdf_to_powerpoint(input_pdf, output_pptx)
        return response_for_file(output_pptx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/pdf-to-excel")
async def convert_pdf_to_excel(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_xlsx = job_dir / f"{input_pdf.stem}.xlsx"
        pdf_to_excel(input_pdf, output_xlsx)
        return response_for_file(output_xlsx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/pdf-to-jpg")
async def convert_pdf_to_jpg(file: UploadFile = File(...), dpi: int = Form(200)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_zip = job_dir / f"{input_pdf.stem}-jpg.zip"
        pdf_to_images_zip(input_pdf, output_zip, "jpg", dpi=dpi)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/pdf-to-png")
async def convert_pdf_to_png(file: UploadFile = File(...), dpi: int = Form(200)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_zip = job_dir / f"{input_pdf.stem}-png.zip"
        pdf_to_images_zip(input_pdf, output_zip, "png", dpi=dpi)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/word-to-pdf")
async def convert_word_to_pdf(file: UploadFile = File(...)):
    return await _office_endpoint(file, [".doc", ".docx"], "Word document")


@app.post("/convert/powerpoint-to-pdf")
async def convert_powerpoint_to_pdf(file: UploadFile = File(...)):
    return await _office_endpoint(file, [".ppt", ".pptx"], "PowerPoint document")


@app.post("/convert/excel-to-pdf")
async def convert_excel_to_pdf(file: UploadFile = File(...)):
    return await _office_endpoint(file, [".xls", ".xlsx"], "Excel workbook")


async def _office_endpoint(file: UploadFile, extensions: list[str], label: str):
    job_dir = create_job_dir()
    try:
        input_file = await save_upload(file, job_dir, extensions, label)
        output_pdf = office_to_pdf(input_file, job_dir)
        return response_for_file(output_pdf, job_dir, download_name=f"{input_file.stem}.pdf")
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/convert/image-to-pdf")
async def convert_image_to_pdf(files: list[UploadFile] = File(...)):
    job_dir = create_job_dir()
    try:
        image_paths = await save_uploads(files, job_dir, [".jpg", ".jpeg", ".png", ".webp"], "image")
        output_pdf = job_dir / "images.pdf"
        images_to_pdf(image_paths, output_pdf)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/merge")
async def merge_pdf_endpoint(files: list[UploadFile] = File(...)):
    job_dir = create_job_dir()
    try:
        pdfs = await save_uploads(files, job_dir, [".pdf"], "PDF", min_count=2)
        output_pdf = job_dir / "merged.pdf"
        merge_pdfs(pdfs, output_pdf)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/split")
async def split_pdf_endpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_zip = job_dir / f"{input_pdf.stem}-split.zip"
        split_pdf(input_pdf, output_zip)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/rotate")
async def rotate_pdf_endpoint(file: UploadFile = File(...), degrees: int = Form(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_pdf = job_dir / f"{input_pdf.stem}-rotated.pdf"
        rotate_pdf(input_pdf, output_pdf, degrees)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/compress")
async def compress_pdf_endpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        output_pdf = job_dir / f"{input_pdf.stem}-compressed.pdf"
        compress_pdf(input_pdf, output_pdf)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/edit")
async def edit_pdf_endpoint(file: UploadFile = File(...), operations_json: str = Form(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        operations = parse_operations_json(operations_json)
        output_pdf = job_dir / f"{input_pdf.stem}-edited.pdf"
        apply_pdf_edits(input_pdf, output_pdf, operations)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@app.post("/pdf/analyze")
async def analyze_pdf_endpoint(file: UploadFile = File(...)):
    document_id, document_dir = create_document_dir()
    try:
        input_pdf = await save_upload(file, document_dir, [".pdf"], "PDF")
        return analyze_pdf_document(document_id, document_dir, input_pdf, file.filename or input_pdf.name)
    except Exception:
        cleanup_path(document_dir)
        raise


@app.get("/pdf/render/{document_id}/{page_number}")
async def render_pdf_page_endpoint(document_id: str, page_number: int):
    png_path = render_document_page(document_id, page_number)
    return FileResponse(path=png_path, media_type="image/png", filename=png_path.name)


@app.post("/pdf/ai-edit/plan")
async def plan_ai_pdf_edit_endpoint(payload: AiEditPlanRequest):
    return plan_ai_edit(
        document_id=payload.document_id,
        instruction=payload.instruction,
        selected_object_ids=payload.selected_object_ids,
        scope=payload.scope,
    )


@app.post("/pdf/ai-edit/apply")
async def apply_ai_pdf_edit_endpoint(payload: AiEditApplyRequest):
    return apply_ai_edit_operations(payload.document_id, payload.operations)


@app.get("/pdf/download/{document_id}/{file_name}")
async def download_ai_edited_pdf_endpoint(document_id: str, file_name: str):
    pdf_path = download_pdf_path(document_id, file_name)
    return FileResponse(path=pdf_path, media_type="application/pdf", filename=pdf_path.name)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
