# Hardened Stage 3 Untrusted Document Parser Container
# Contains ONLY isolated parsing code and zero DB/API/Redis/Supabase credentials.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create dedicated unprivileged user/group (UID/GID 65532)
RUN groupadd --gid 65532 parser \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin parser \
    && mkdir -p /opt/ragbait_parser/app/modules/knowledge_base /sandbox/in /sandbox/out \
    && chown -R 65532:65532 /opt/ragbait_parser /sandbox

# Install offline Tesseract OCR engine strictly inside the hardened container sandbox
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Install pypdf, PyMuPDF, pdfplumber, and pytesseract strictly inside the container boundary
RUN pip install --no-cache-dir \
    "pypdf>=5.1.0" \
    "pymupdf>=1.25.0" \
    "pdfplumber>=0.11.4" \
    "pytesseract>=0.3.13"

# Minimal package markers (no app.core / DB / secrets)
RUN touch /opt/ragbait_parser/app/__init__.py \
    /opt/ragbait_parser/app/modules/__init__.py \
    /opt/ragbait_parser/app/modules/knowledge_base/__init__.py

# Copy ONLY parser modules from apps/api build context
COPY app/modules/knowledge_base/extractor_exceptions.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/pdf_extractor.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/txt_extractor.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/extraction_diagnostics.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/text_normalizer.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/ocr_processor.py /opt/ragbait_parser/app/modules/knowledge_base/
COPY app/modules/knowledge_base/parser_process.py /opt/ragbait_parser/app/modules/knowledge_base/

ENV PYTHONPATH=/opt/ragbait_parser
WORKDIR /sandbox
USER 65532:65532

ENTRYPOINT ["python", "-m", "app.modules.knowledge_base.parser_process"]
