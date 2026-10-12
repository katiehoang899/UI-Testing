FROM python:3.12-slim

# Tesseract OCR with Vietnamese + English language data
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY uicheck uicheck
COPY web web

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "uicheck.server:app", "--host", "0.0.0.0", "--port", "8000"]
