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

## Analyze and Select Objects

Use `/pdf/analyze` before rendering editor overlays. The response contains both a backward-compatible flat `objects` array and a richer `pages` array. Prefer `pages` because each page includes exact PDF dimensions and rendered preview dimensions.

```javascript
const formData = new FormData();
formData.append("file", pdfFile);

const response = await fetch(`${DOCUFLOW_API_BASE_URL}/pdf/analyze`, {
  method: "POST",
  body: formData,
});

const analysis = await response.json();
```

Each page has:

```javascript
{
  page_number: 1,
  page_width: 595,
  page_height: 842,
  render_width: 1190,
  render_height: 1684,
  objects: []
}
```

Each object has `bounding_box` in original PDF coordinates and `normalized_box` for frontend overlay placement:

```javascript
{
  object_id: "p1-text-1",
  object_type: "text",
  page_number: 1,
  bounding_box: [72, 96, 240, 120],
  normalized_box: {
    left: 0.121008,
    top: 0.114014,
    width: 0.282353,
    height: 0.028504,
  },
  text: "Example",
  font_size: 12,
  confidence: 1.0,
}
```

To position an overlay on a rendered page element:

```javascript
function overlayStyle(object, renderedPageWidth, renderedPageHeight) {
  const box = object.normalized_box;
  return {
    left: `${box.left * renderedPageWidth}px`,
    top: `${box.top * renderedPageHeight}px`,
    width: `${box.width * renderedPageWidth}px`,
    height: `${box.height * renderedPageHeight}px`,
  };
}
```

Embedded images are returned as `object_type: "image"`, tables as `object_type: "table"`, and selectable text as `object_type: "text"`. Headers and footers are still text objects for compatibility, with `metadata.region` set to `header` or `footer`.

Render a preview:

```javascript
const previewUrl = `${DOCUFLOW_API_BASE_URL}/pdf/render/${analysis.document_id}/1`;
```

Undo and redo are available:

```javascript
await fetch(`${DOCUFLOW_API_BASE_URL}/pdf/undo`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ document_id: analysis.document_id }),
});
```

For AI edit apply operations, use `operation` as the operation-name field. The backend still accepts `type` for older clients. The response contains a `downloadUrl` for the newly edited PDF version and may include `warnings`; warnings mean the visible edit was applied but the PDF may retain hidden extraction-layer text.

```javascript
const editResponse = await fetch(`${DOCUFLOW_API_BASE_URL}/pdf/ai-edit/apply`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    document_id: analysis.document_id,
    operations: [
      {
        operation: "replace_text",
        object_id: "p1-text-2",
        page: 1,
        new_text: "ABB - BLOCKED - 60 FSA-2021-32",
        preserve_style: true,
        fit_mode: "shrink_to_fit",
      },
    ],
  }),
});

const edited = await editResponse.json();
const editedBlob = await fetch(`${DOCUFLOW_API_BASE_URL}${edited.downloadUrl}`).then((res) => res.blob());
if (edited.warnings?.length) {
  console.warn("DocuFlow edit warnings", edited.warnings);
}
```

## UI Requirements

Show a clear loading state while a request is running. Disable the submit button during conversion. If the backend returns JSON with `detail`, show that message to the user. If the backend returns a blob, download it immediately.

Accepted upload fields:

- Single-file endpoints use field name `file`.
- Multi-file endpoints use field name `files`.
- OCR uses field name `use_ocr`.
- Image DPI uses field name `dpi`.
- PDF rotation uses field name `degrees`.
- PDF visual editing uses field name `operations_json`.
