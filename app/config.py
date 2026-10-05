"""Configuração central. Todos os limiares do pipeline vivem aqui — nenhum
número mágico espalhado pelos estágios.

Sobrescreva qualquer valor por variável de ambiente ou arquivo .env, com o
prefixo PDF2MD_ (ex.: PDF2MD_OCR_DPI=400).
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_tesseract() -> str:
    """Encontra o tesseract mesmo quando ele não está no PATH (caso do Windows)."""
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ):
        if Path(candidate).exists():
            return candidate
    return "tesseract"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PDF2MD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Servidor ───────────────────────────────────────────────────────
    host: str = "127.0.0.1"          # local por padrão; mudar exige intenção
    port: int = 8000
    max_upload_mb: int = 200
    # Lote: teto de arquivos e de volume total. O limite de quantidade
    # protege a fila; o de volume protege o disco temporário.
    max_batch_files: int = 100
    max_batch_mb: int = 1000

    # ── Privacidade / ciclo de vida dos arquivos ───────────────────────
    workspace_root: Path = Field(
        default_factory=lambda: Path(os.environ.get("TEMP", "/tmp")) / "pdf2md"
    )
    job_ttl_minutes: int = 60         # purga automática do workspace
    sweep_interval_seconds: int = 300
    keep_source_pdf: bool = False     # o PDF de entrada é apagado após a conversão

    # ── Triagem / rota de página (estágio 01) ──────────────────────────
    min_chars_per_page: int = 60      # abaixo disso a página é candidata a OCR
    min_text_area_ratio: float = 0.012
    image_dominance_ratio: float = 0.55   # imagem cobrindo > 55% ⇒ provável scan

    # ── OCR (estágio 03) ───────────────────────────────────────────────
    ocr_enabled: bool = True
    ocr_lang: str = "por"
    ocr_dpi: int = 300                # 300 é o ponto de equilíbrio para A4
    ocr_psm: int = 3                  # 3 = segmentação automática de página
    ocr_oem: int = 1                  # 1 = LSTM
    ocr_min_word_confidence: float = 40.0
    ocr_low_confidence_page: float = 70.0   # abaixo disso a página vira warning
    ocr_deskew: bool = True
    ocr_binarize: bool = True
    tesseract_cmd: str = Field(default_factory=_default_tesseract)
    tessdata_prefix: Path = PROJECT_ROOT / "tessdata"

    # ── Cabeçalho / rodapé (estágio 04) ────────────────────────────────
    header_band_ratio: float = 0.11   # faixa superior considerada cabeçalho
    footer_band_ratio: float = 0.11
    repetition_threshold: float = 0.6  # presente em ≥60% das páginas ⇒ recorrente
    repetition_similarity: int = 88    # similaridade fuzzy mínima (0-100)
    min_pages_for_repetition: int = 2

    # ── Tabelas (estágio 05) ───────────────────────────────────────────
    table_min_confidence: float = 0.65        # abaixo disso: marcar para revisão
    table_min_rows: int = 2
    table_min_cols: int = 2
    table_line_snap_tolerance: float = 3.0    # tolerância p/ alinhar réguas
    table_engines: list[str] = ["lines", "plumber", "camelot", "tabula"]

    # ── Imagens (estágio 06) ───────────────────────────────────────────
    image_min_width: int = 24         # descarta ícones/artefatos de assinatura
    image_min_height: int = 24
    image_min_area_px: int = 2500
    vector_figure_dpi: int = 300      # rasterização de figuras vetoriais
    vector_figure_min_drawings: int = 8
    extract_vector_figures: bool = True

    # ── Semântica (estágio 07) ─────────────────────────────────────────
    heading_max_words: int = 28
    heading_size_delta: float = 0.75  # pt acima da mediana p/ ser candidato
    max_heading_levels: int = 6

    # ── IA (estágio 09) — desligada por padrão ─────────────────────────
    ai_enabled: bool = False
    ai_provider: str = "null"         # null | claude_cli | anthropic | ollama
    ai_claude_cli_path: str = ""      # resolvido dinamicamente se vazio
    ai_model: str = "claude-sonnet-5"
    ai_timeout_seconds: int = 180
    ai_max_blocks_per_call: int = 120
    ai_require_consent: bool = True   # a UI precisa confirmar a saída de dados

    # ── Render (estágio 10) ────────────────────────────────────────────
    md_page_markers: bool = True      # <!-- página N --> no Markdown
    md_review_markers: bool = True    # <!-- REVISAR: ... -->
    md_footnote_style: str = "inline"  # inline | end
    md_assets_dir: str = "assets"
    # O .md leva o nome do PDF de origem ("peticao.pdf" -> "peticao.md").
    # Desligue para voltar ao nome fixo "documento.md".
    md_use_source_name: bool = True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_batch_bytes(self) -> int:
        return self.max_batch_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
