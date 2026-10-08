FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    OCR_LANGUAGES=ara+eng \
    OCR_RENDER_DPI=300 \
    OCR_PSM=3 \
    PDF_OCR_ENGINE=tesseract

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto-core \
    fontconfig \
    ghostscript \
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    ocrmypdf \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ara \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN tesseract --list-langs | grep -qx ara && tesseract --list-langs | grep -qx eng

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

RUN mkdir -p /app/storage/outputs

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

