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
```

`MAX_UPLOAD_MB` defaults to `100` when unset.

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

