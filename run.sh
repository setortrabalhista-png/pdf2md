#!/usr/bin/env bash
# Sobe o pdf2md: cria o ambiente na primeira execução e inicia o servidor.
#
#   ./run.sh              interface web
#   ./run.sh a.pdf        conversão pela linha de comando
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "Criando o ambiente virtual..."
    (command -v python3.12 >/dev/null && python3.12 -m venv .venv) \
        || python3 -m venv .venv
    echo "Instalando as dependências..."
    "$PY" -m pip install --upgrade pip --quiet
    "$PY" -m pip install -r requirements.txt -r requirements-ocr.txt
fi

if [ ! -f tessdata/por.traineddata ]; then
    echo "Baixando o modelo de português para o OCR..."
    mkdir -p tessdata
    base="https://github.com/tesseract-ocr/tessdata_best/raw/main"
    curl -fsSL -o tessdata/por.traineddata "$base/por.traineddata"
    curl -fsSL -o tessdata/osd.traineddata "$base/osd.traineddata"
fi

if [ $# -gt 0 ]; then
    exec "$PY" -m app.cli "$@"
else
    echo "pdf2md em http://127.0.0.1:${PDF2MD_PORT:-8000}"
    exec "$PY" -m app.main
fi
