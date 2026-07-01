# DocuFlow API

DocuFlow API is a production-ready FastAPI backend for document conversion and visual PDF editing. It uses LibreOffice headless, Ghostscript, OCRmyPDF, Tesseract, PyMuPDF, pypdf, pdf2docx, pdfplumber, openpyxl, python-pptx, and Pillow.

## Features

- PDF to Word, PowerPoint, Excel, JPG, and PNG
- Word, PowerPoint, and Excel to PDF through LibreOffice headless
- Multiple images to one PDF
- Merge, split, rotate, and compress PDFs
- Visual PDF editing with cover boxes, text boxes, highlights, rectangles, and freehand drawings
- Temporary job folders with automatic cleanup after responses
- Upload size control through `MAX_UPLOAD_MB`
- CORS enabled for browser clients

## Run Locally

```bash
docker compose up --build
```

The API runs at `http://localhost:8000`.

Health check:

```bash
curl http://localhost:8000/health
```

OpenAPI docs:

```text
http://localhost:8000/docs
```

## Environment

Copy `.env.example` to `.env` for local configuration.

```env
MAX_UPLOAD_MB=100
MAX_PDF_PAGES=200
MAX_EDIT_OPERATIONS=250
COMMAND_TIMEOUT_SECONDS=900
DOCUFLOW_DOCUMENT_STORE=/tmp/docuflow-documents
DOCUFLOW_DOCUMENT_TTL_HOURS=24
PDF_OCR_ENGINE=auto
OCR_LANGUAGE=en
OCR_RENDER_DPI=220
PDF_RENDER_DPI=144
AI_PROVIDER=
AI_API_KEY=
AI_MODEL=
AI_BASE_URL=
```

`MAX_UPLOAD_MB` defaults to `100` when unset.

AI planning uses `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`, and optional `AI_BASE_URL`. No model is hardcoded. If no AI provider is configured, `/pdf/ai-edit/plan` returns a conservative validated local plan for simple selected-object instructions such as highlight, redact, delete, remove, and replace quoted text.

`MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `MAX_EDIT_OPERATIONS`, and `COMMAND_TIMEOUT_SECONDS` protect the service from oversized jobs. `DOCUFLOW_DOCUMENT_TTL_HOURS` controls automatic cleanup for analyzed document state and rendered previews.

## Architecture

The backend is split by responsibility:

- `app/main.py`: app setup, structured request logging, error handlers, CORS, router registration
- `app/api/conversion_routes.py`: existing conversion endpoints
- `app/api/pdf_routes.py`: PDF analyze, render, edit, history, undo/redo, and download endpoints
- `app/services/pdf_analysis.py`: native PDF text/table/page analysis
- `app/services/image_detection.py`: embedded image detection with PyMuPDF
- `app/services/ocr.py`: OCR for scanned pages only, using PaddleOCR when available and Tesseract fallback
- `app/services/pdf_render.py`: high-resolution preview rendering
- `app/services/pdf_edit.py`: visual edit application and operation validation
- `app/services/ai_planner.py`: AI provider integration and local fallback planning
- `app/services/document_history.py`: versioned document storage, cleanup, undo, redo
- `app/models/*.py`: Pydantic request/response schemas
- `app/utils/*.py`: file handling, coordinates, and validation helpers

## Endpoints

All conversion endpoints accept `multipart/form-data` and return the converted file as a downloadable blob.

### `GET /health`

Returns service status.

### `POST /convert/pdf-to-word`

Fields:

- `file`: PDF
- `use_ocr`: optional boolean, default `false`

Uses `pdf2docx`. If `use_ocr=true` or no selectable PDF text is detected, DocuFlow runs OCRmyPDF first.

```bash
curl -X POST http://localhost:8000/convert/pdf-to-word \
  -F "file=@input.pdf" \
  -F "use_ocr=false" \
  -o output.docx
```

### `POST /convert/pdf-to-powerpoint`

Fields:

- `file`: PDF

Renders each PDF page as a high-resolution image and places it as a full-slide image.

### `POST /convert/pdf-to-excel`

Fields:

- `file`: PDF

Uses `pdfplumber` to extract tables. If a page has no tables, text lines are written into Excel.

### `POST /convert/pdf-to-jpg`

Fields:

- `file`: PDF
- `dpi`: optional integer, default `200`, allowed `50` to `600`

Returns a ZIP containing one JPG per page.

### `POST /convert/pdf-to-png`

Fields:

- `file`: PDF
- `dpi`: optional integer, default `200`, allowed `50` to `600`

Returns a ZIP containing one PNG per page.

### `POST /convert/word-to-pdf`

Fields:

- `file`: DOC or DOCX

### `POST /convert/powerpoint-to-pdf`

Fields:

- `file`: PPT or PPTX

### `POST /convert/excel-to-pdf`

Fields:

- `file`: XLS or XLSX

### `POST /convert/image-to-pdf`

Fields:

- `files`: multiple JPG, PNG, or WEBP images

Returns a single PDF.

### `POST /pdf/merge`

Fields:

- `files`: multiple PDFs, minimum 2

### `POST /pdf/split`

Fields:

- `file`: PDF

Returns a ZIP with each page as a separate PDF.

### `POST /pdf/rotate`

Fields:

- `file`: PDF
- `degrees`: `90`, `180`, or `270`

### `POST /pdf/compress`

Fields:

- `file`: PDF

Uses Ghostscript with `/ebook` compression settings.

### `POST /pdf/edit`

Fields:

- `file`: PDF
- `operations_json`: JSON array

This endpoint performs visual editing with PyMuPDF. It can cover old content and place replacement content, but it does not claim perfect internal PDF text replacement.

Supported operations:

```json
[
  {
    "type": "cover",
    "page": 1,
    "rect": [72, 72, 220, 110],
    "fill": [1, 1, 1]
  },
  {
    "type": "text",
    "page": 1,
    "rect": [72, 72, 300, 120],
    "text": "Replacement text",
    "font_size": 12,
    "color": [0, 0, 0]
  },
  {
    "type": "highlight",
    "page": 1,
    "rect": [70, 150, 300, 175],
    "color": [1, 1, 0],
    "opacity": 0.35
  },
  {
    "type": "rectangle",
    "page": 1,
    "rect": [70, 190, 300, 250],
    "color": [1, 0, 0],
    "width": 2
  },
  {
    "type": "draw",
    "page": 1,
    "points": [[70, 280], [120, 300], [180, 275]],
    "color": [0, 0, 1],
    "width": 3
  }
]
```

### `POST /pdf/analyze`

Fields:

- `file`: PDF

Stores the uploaded PDF as a document record, extracts native text blocks and image blocks with PyMuPDF, and runs OCR only on pages without selectable native text. PaddleOCR is used when installed and `PDF_OCR_ENGINE=auto` or `paddle`; otherwise the service falls back to the Tesseract CLI installed in Docker.

Response includes:

- `document_id`
- `page_count`
- `scanned_pages`
- `pages`, each with exact original PDF `page_width` and `page_height`, matching preview `render_width` and `render_height`, and per-page `objects`
- `objects`, a backward-compatible flat list of all page objects
- `text_object_count`, `image_object_count`, and `table_object_count`

Each object includes:

```json
{
  "object_id": "p1-text-1",
  "object_type": "text",
  "page_number": 1,
  "bounding_box": [72, 96, 240, 120],
  "normalized_box": {
    "left": 0.121008,
    "top": 0.114014,
    "width": 0.282353,
    "height": 0.028504
  },
  "text": "Example text",
  "font_size": 12,
  "confidence": 1.0
}
```

`bounding_box` uses original PDF page coordinates. `normalized_box` values are between `0` and `1`, so a frontend can place selection overlays by multiplying `left`, `top`, `width`, and `height` by the rendered page dimensions. Embedded PDF images are returned as `object_type: "image"` with unique object IDs and image bounding boxes. Tables are returned as `object_type: "table"` when PyMuPDF table detection is available. Headers and footers remain `object_type: "text"` for Base44 compatibility and are marked by `metadata.region` as `header`, `body`, or `footer`.

### `GET /pdf/render/{document_id}/{page_number}`

Returns a high-resolution PNG preview for a previously analyzed document page.

### `POST /pdf/ai-edit/plan`

JSON body:

```json
{
  "document_id": "document id from /pdf/analyze",
  "instruction": "Highlight the selected clause",
  "selected_object_ids": ["p1-text-2"],
  "scope": "selection"
}
```

Returns a validated JSON array of edit operations only. This endpoint does not modify the PDF.

Supported operation types:

- `replace_text`
- `delete_text`
- `highlight`
- `redact`
- `delete_image`
- `replace_image`

### `POST /pdf/ai-edit/apply`

JSON body:

```json
{
  "document_id": "document id from /pdf/analyze",
  "operations": [
    {
      "operation": "highlight",
      "object_id": "p1-text-2"
    }
  ]
}
```

Applies approved operations to a new PDF and never overwrites the original. `operation` is the canonical operation-name field. For backward compatibility, `type` is also accepted. `replace_text` uses `new_text`, plus optional `preserve_style` and `fit_mode`. `replace_image` operations require `replacement_image_base64`.

Base44-compatible replace text payload:

```json
{
  "document_id": "document id from /pdf/analyze",
  "operations": [
    {
      "operation": "replace_text",
      "object_id": "p1-text-2",
      "page": 1,
      "new_text": "ABB - BLOCKED - 60 FSA-2021-32",
      "preserve_style": true,
      "fit_mode": "shrink_to_fit"
    }
  ]
}
```

Response:

```json
{
  "fileName": "input-ai-edited-1234abcd.pdf",
  "downloadUrl": "/pdf/download/{document_id}/input-ai-edited-1234abcd.pdf"
}
```

### `POST /pdf/undo`

JSON body:

```json
{"document_id": "document id from /pdf/analyze"}
```

Moves the current document pointer back one version. The original file remains preserved.

### `POST /pdf/redo`

JSON body:

```json
{"document_id": "document id from /pdf/analyze"}
```

Restores the next undone version when available.

### `GET /pdf/history/{document_id}`

Returns version history, current version, undo availability, and redo availability.

## Tests

Run tests with:

```bash
python -m unittest discover -s tests
```

Pure coordinate and selection tests run without external PDF libraries. PDF detection and integration tests automatically skip when FastAPI or PyMuPDF are unavailable, and run in the Docker app environment where dependencies are installed.

### `GET /pdf/download/{document_id}/{file_name}`

Downloads an edited PDF returned by `/pdf/ai-edit/apply`.

Example:

```bash
curl -X POST http://localhost:8000/pdf/edit \
  -F "file=@input.pdf" \
  -F 'operations_json=[{"type":"cover","page":1,"rect":[72,72,220,110]},{"type":"text","page":1,"rect":[72,72,260,110],"text":"Updated"}]' \
  -o edited.pdf
```

## Error Format

Errors are returned as JSON:

```json
{"detail": "Clear explanation of what failed"}
```

Conversion command failures return HTTP `422`. Validation errors return HTTP `400` or `413`.

## Deploy on Render with Docker

1. Push this repository to GitHub.
2. In Render, create a new **Web Service**.
3. Select the GitHub repository.
4. Choose **Docker** as the runtime.
5. Set the port to `8000` or let Render use the exposed Docker port.
6. Add environment variable `MAX_UPLOAD_MB`, for example `100`.
7. Deploy.

Render will build the Docker image, install LibreOffice, Ghostscript, Tesseract, OCRmyPDF, poppler-utils, fonts, and Python dependencies, then run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Use the Render service URL as your frontend API base URL.

## Notes for Production

- Put this service behind HTTPS.
- Keep uploads private and do not log file contents.
- Tune `MAX_UPLOAD_MB` and Render instance size for your expected files.
- OCR and Office conversions are CPU-heavy. Use larger instances or background jobs for high-volume workloads.
- Temporary files are removed after each response is sent.
