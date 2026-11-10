# Image du système de traitement : planificateur + pipeline (CSV et images via OCR)
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.scheduler"]
