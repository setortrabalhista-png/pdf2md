"""Fixtures compartilhadas da suíte.

Os PDFs sintéticos são gerados na primeira execução, para que a suíte rode em
qualquer máquina sem depender de arquivos versionados.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
ARQUIVOS = ("juridico.pdf", "tabelas.pdf", "escaneado.pdf", "hibrido.pdf")


@pytest.fixture(scope="session", autouse=True)
def gerar_fixtures():
    if not all((FIXTURES / nome).exists() for nome in ARQUIVOS):
        from tests import make_fixtures

        make_fixtures.main()
    return FIXTURES


@pytest.fixture(scope="session")
def pdf_juridico(gerar_fixtures) -> Path:
    return gerar_fixtures / "juridico.pdf"


@pytest.fixture(scope="session")
def pdf_tabelas(gerar_fixtures) -> Path:
    return gerar_fixtures / "tabelas.pdf"


@pytest.fixture(scope="session")
def pdf_escaneado(gerar_fixtures) -> Path:
    return gerar_fixtures / "escaneado.pdf"


@pytest.fixture(scope="session")
def pdf_hibrido(gerar_fixtures) -> Path:
    return gerar_fixtures / "hibrido.pdf"


@pytest.fixture(scope="session")
def ocr_disponivel() -> bool:
    from app.config import get_settings
    from app.extractors import ocr_tesseract

    settings = get_settings()
    if not ocr_tesseract.is_available(settings.tesseract_cmd):
        return False
    idiomas = ocr_tesseract.available_languages(
        settings.tesseract_cmd, settings.tessdata_prefix
    )
    return "por" in idiomas


@pytest.fixture(scope="session")
def converter_juridico(pdf_juridico, tmp_path_factory):
    """Converte a peça uma única vez e compartilha o resultado com os testes."""
    from app.pipeline.runner import run_pipeline

    destino = tmp_path_factory.mktemp("juridico")
    doc = run_pipeline(pdf_juridico, destino)
    return doc, destino


@pytest.fixture(scope="session")
def converter_tabelas(pdf_tabelas, tmp_path_factory):
    from app.pipeline.runner import run_pipeline

    destino = tmp_path_factory.mktemp("tabelas")
    doc = run_pipeline(pdf_tabelas, destino)
    return doc, destino
