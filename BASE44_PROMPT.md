# Base44 Integration Prompt for DocuFlow API

Use this prompt inside Base44 when connecting a frontend to DocuFlow API.

## Goal

Build a document conversion UI that calls an external backend named DocuFlow API. The backend URL is stored in:

```text
DOCUFLOW_API_BASE_URL
```

Example:

```text
https://docuflow-api.onrender.com
```

Do not hardcode the URL. Read `DOCUFLOW_API_BASE_URL` from the Base44 environment/configuration system.

## How Requests Work

Every conversion request is a `multipart/form-data` POST request. The backend returns a binary blob, not JSON, when conversion succeeds. The frontend must download the returned blob using the filename from the `Content-Disposition` response header when available.

Health check:

```javascript
const res = await fetch(`${DOCUFLOW_API_BASE_URL}/health`);
const data = await res.json();
```

Generic blob download helper:

```javascript
async function postMultipartAndDownload(endpoint, formData, fallbackFilename) {
  const response = await fetch(`${DOCUFLOW_API_BASE_URL}${endpoint}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    let message = "Conversion failed";
    try {
      const error = await response.json();
      message = error.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const filename = match ? match[1] : fallbackFilename;

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
```

## Endpoint Examples

PDF to Word:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
formData.append("use_ocr", String(useOcr));
await postMultipartAndDownload("/convert/pdf-to-word", formData, "converted.docx");
```

PDF to PowerPoint:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
await postMultipartAndDownload("/convert/pdf-to-powerpoint", formData, "converted.pptx");
```

PDF to Excel:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
await postMultipartAndDownload("/convert/pdf-to-excel", formData, "converted.xlsx");
```

PDF to JPG ZIP:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
formData.append("dpi", "200");
await postMultipartAndDownload("/convert/pdf-to-jpg", formData, "pages-jpg.zip");
```

PDF to PNG ZIP:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
formData.append("dpi", "200");
await postMultipartAndDownload("/convert/pdf-to-png", formData, "pages-png.zip");
```

Office to PDF:

```javascript
const formData = new FormData();
formData.append("file", officeFile);
await postMultipartAndDownload("/convert/word-to-pdf", formData, "converted.pdf");
```

Use `/convert/powerpoint-to-pdf` for PPT/PPTX and `/convert/excel-to-pdf` for XLS/XLSX.

Image to PDF:

```javascript
const formData = new FormData();
for (const file of imageFiles) {
  formData.append("files", file);
}
await postMultipartAndDownload("/convert/image-to-pdf", formData, "images.pdf");
```

Merge PDFs:

```javascript
const formData = new FormData();
for (const file of pdfFiles) {
  formData.append("files", file);
}
await postMultipartAndDownload("/pdf/merge", formData, "merged.pdf");
```

Split PDF:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
await postMultipartAndDownload("/pdf/split", formData, "split-pages.zip");
```

Rotate PDF:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
formData.append("degrees", "90");
await postMultipartAndDownload("/pdf/rotate", formData, "rotated.pdf");
```

Compress PDF:

```javascript
const formData = new FormData();
formData.append("file", pdfFile);
await postMultipartAndDownload("/pdf/compress", formData, "compressed.pdf");
```

Visual PDF edit:

```javascript
const operations = [
  {
    type: "cover",
    page: 1,
    rect: [72, 72, 220, 110],
    fill: [1, 1, 1],
  },
  {
    type: "text",
    page: 1,
    rect: [72, 72, 300, 120],
    text: "Replacement text",
    font_size: 12,
    color: [0, 0, 0],
  },
];

const formData = new FormData();
formData.append("file", pdfFile);
formData.append("operations_json", JSON.stringify(operations));
await postMultipartAndDownload("/pdf/edit", formData, "edited.pdf");
```

Important: PDF editing is visual editing. The backend covers old visible content and places replacement content on top. It does not promise perfect internal PDF text replacement.

## UI Requirements

Show a clear loading state while a request is running. Disable the submit button during conversion. If the backend returns JSON with `detail`, show that message to the user. If the backend returns a blob, download it immediately.

Accepted upload fields:

- Single-file endpoints use field name `file`.
- Multi-file endpoints use field name `files`.
- OCR uses field name `use_ocr`.
- Image DPI uses field name `dpi`.
- PDF rotation uses field name `degrees`.
- PDF visual editing uses field name `operations_json`.

