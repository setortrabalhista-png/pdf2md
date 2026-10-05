# Imagem completa: traz Tesseract com português, Ghostscript e Java, então os
# motores de reserva de tabela (Camelot e Tabula) ficam disponíveis — o que
# raramente acontece numa instalação local.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PDF2MD_HOST=0.0.0.0 \
    PDF2MD_PORT=8000 \
    PDF2MD_TESSERACT_CMD=/usr/bin/tesseract

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-por \
        tesseract-ocr-osd \
        ghostscript \
        default-jre-headless \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-ocr.txt requirements-tables.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt -r requirements-ocr.txt -r requirements-tables.txt

COPY app ./app
COPY pyproject.toml README.md ./

# Modelos de alta acurácia para português. Os pacotes do apt trazem os
# "fast", mais rápidos e menos precisos; aqui a prioridade é qualidade.
RUN mkdir -p /app/tessdata \
    && curl -fsSL -o /app/tessdata/por.traineddata \
        https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata \
    && curl -fsSL -o /app/tessdata/osd.traineddata \
        https://github.com/tesseract-ocr/tessdata_best/raw/main/osd.traineddata
ENV PDF2MD_TESSDATA_PREFIX=/app/tessdata

# Usuário sem privilégios: o container manipula documento de cliente.
RUN useradd --create-home --uid 10001 pdf2md \
    && mkdir -p /tmp/pdf2md && chown -R pdf2md:pdf2md /tmp/pdf2md /app
USER pdf2md

ENV PDF2MD_WORKSPACE_ROOT=/tmp/pdf2md

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
