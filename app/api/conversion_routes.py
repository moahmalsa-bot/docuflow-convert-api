from fastapi import APIRouter, File, Form, Request, UploadFile

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
from app.services.excel_pdf import excel_download_response, excel_to_pdf_job
from app.utils.files import cleanup_path, create_job_dir, response_for_file, save_upload, save_uploads
from app.utils.validation import validate_pdf_page_count


router = APIRouter()


@router.post("/convert/pdf-to-word")
async def convert_pdf_to_word(file: UploadFile = File(...), use_ocr: bool = Form(False)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_docx = job_dir / f"{input_pdf.stem}.docx"
        pdf_to_word(input_pdf, output_docx, use_ocr=use_ocr)
        return response_for_file(output_docx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/convert/pdf-to-powerpoint")
async def convert_pdf_to_powerpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_pptx = job_dir / f"{input_pdf.stem}.pptx"
        pdf_to_powerpoint(input_pdf, output_pptx)
        return response_for_file(output_pptx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/convert/pdf-to-excel")
async def convert_pdf_to_excel(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_xlsx = job_dir / f"{input_pdf.stem}.xlsx"
        pdf_to_excel(input_pdf, output_xlsx)
        return response_for_file(output_xlsx, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/convert/pdf-to-jpg")
async def convert_pdf_to_jpg(file: UploadFile = File(...), dpi: int = Form(200)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_zip = job_dir / f"{input_pdf.stem}-jpg.zip"
        pdf_to_images_zip(input_pdf, output_zip, "jpg", dpi=dpi)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/convert/pdf-to-png")
async def convert_pdf_to_png(file: UploadFile = File(...), dpi: int = Form(200)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_zip = job_dir / f"{input_pdf.stem}-png.zip"
        pdf_to_images_zip(input_pdf, output_zip, "png", dpi=dpi)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/convert/word-to-pdf")
async def convert_word_to_pdf(file: UploadFile = File(...)):
    return await _office_endpoint(file, [".doc", ".docx"], "Word document")


@router.post("/convert/powerpoint-to-pdf")
async def convert_powerpoint_to_pdf(file: UploadFile = File(...)):
    return await _office_endpoint(file, [".ppt", ".pptx"], "PowerPoint document")


@router.post("/convert/excel-to-pdf")
async def convert_excel_to_pdf(request: Request, file: UploadFile = File(...)):
    """LibreOffice Calc headless with print-setting preservation, output
    validation, and a JSON response carrying a real HTTPS download URL."""
    job_dir = create_job_dir()
    try:
        input_file = await save_upload(file, job_dir, [".xls", ".xlsx", ".xlsm"], "Excel workbook")
        return excel_to_pdf_job(input_file, job_dir, request)
    finally:
        cleanup_path(job_dir)


@router.get("/download/{job_id}/{file_name}")
def download_converted(job_id: str, file_name: str):
    return excel_download_response(job_id, file_name)


@router.post("/convert/image-to-pdf")
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


@router.post("/pdf/merge")
async def merge_pdf_endpoint(files: list[UploadFile] = File(...)):
    job_dir = create_job_dir()
    try:
        pdfs = await save_uploads(files, job_dir, [".pdf"], "PDF", min_count=2)
        for pdf in pdfs:
            validate_pdf_page_count(pdf)
        output_pdf = job_dir / "merged.pdf"
        merge_pdfs(pdfs, output_pdf)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/pdf/split")
async def split_pdf_endpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_zip = job_dir / f"{input_pdf.stem}-split.zip"
        split_pdf(input_pdf, output_zip)
        return response_for_file(output_zip, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/pdf/rotate")
async def rotate_pdf_endpoint(file: UploadFile = File(...), degrees: int = Form(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_pdf = job_dir / f"{input_pdf.stem}-rotated.pdf"
        rotate_pdf(input_pdf, output_pdf, degrees)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


@router.post("/pdf/compress")
async def compress_pdf_endpoint(file: UploadFile = File(...)):
    job_dir = create_job_dir()
    try:
        input_pdf = await save_upload(file, job_dir, [".pdf"], "PDF")
        validate_pdf_page_count(input_pdf)
        output_pdf = job_dir / f"{input_pdf.stem}-compressed.pdf"
        compress_pdf(input_pdf, output_pdf)
        return response_for_file(output_pdf, job_dir)
    except Exception:
        cleanup_path(job_dir)
        raise


async def _office_endpoint(file: UploadFile, extensions: list[str], label: str):
    job_dir = create_job_dir()
    try:
        input_file = await save_upload(file, job_dir, extensions, label)
        output_pdf = office_to_pdf(input_file, job_dir)
        return response_for_file(output_pdf, job_dir, download_name=f"{input_file.stem}.pdf")
    except Exception:
        cleanup_path(job_dir)
        raise
