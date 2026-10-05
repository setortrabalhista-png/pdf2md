"""Rastreabilidade: de onde veio cada pedaço de conteúdo e o quanto confiamos nele.

Toda unidade do IR carrega proveniência. É isso que permite (a) gerar o relatório
de qualidade sem re-processar, (b) marcar no Markdown o que precisa de revisão
humana e (c) auditar qualquer alteração feita pela camada de IA.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Source(str, Enum):
    NATIVE = "native"        # camada de texto do próprio PDF
    OCR = "ocr"              # reconhecimento óptico
    VECTOR = "vector"        # derivado de desenho vetorial (réguas de tabela, figura)
    HEURISTIC = "heuristic"  # inferido por regra (nível de título, tipo de lista)
    AI = "ai"                # reorganizado pela camada de IA (nunca criado por ela)


class Provenance(BaseModel):
    source: Source = Source.NATIVE
    engine: str | None = None          # "pymupdf", "pdfplumber", "tesseract", ...
    confidence: float = 1.0            # 0.0 a 1.0
    notes: list[str] = Field(default_factory=list)

    def degrade(self, factor: float, note: str | None = None) -> Provenance:
        """Reduz a confiança preservando o histórico."""
        p = self.model_copy(deep=True)
        p.confidence = max(0.0, min(1.0, p.confidence * factor))
        if note:
            p.notes.append(note)
        return p


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Warning_(BaseModel):
    """Um achado do sistema de validação. Vai íntegro para o relatório JSON."""

    code: str                          # "PAGE_NO_TEXT", "TABLE_LOW_CONFIDENCE", ...
    severity: Severity = Severity.WARNING
    message: str
    page: int | None = None
    block_id: str | None = None
    detail: dict = Field(default_factory=dict)
