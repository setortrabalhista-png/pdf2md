"""Opções de conversão expostas ao usuário na interface."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class OcrMode(str, Enum):
    AUTO = "auto"      # OCR apenas nas páginas sem camada de texto útil
    FORCE = "force"    # OCR em todas as páginas (PDF com camada de texto ruim)
    NEVER = "never"    # nunca — útil para conferir a extração nativa isolada


class ConversionOptions(BaseModel):
    ocr_mode: OcrMode = OcrMode.AUTO
    ocr_lang: str = "por"

    extract_images: bool = True
    extract_vector_figures: bool = True
    extract_tables: bool = True

    detect_headers_footers: bool = True
    detect_footnotes: bool = True
    merge_across_pages: bool = True    # une parágrafos/tabelas partidos pela página

    page_markers: bool = True
    review_markers: bool = True

    # Camada de IA — exige consentimento explícito porque envia trechos do
    # documento para fora da máquina.
    ai_enabled: bool = False
    ai_consent: bool = False
    ai_provider: str | None = None

    # Restrição de páginas (1-indexed, inclusive). Vazio = documento inteiro.
    page_range: list[int] = Field(default_factory=list)
