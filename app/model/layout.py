"""Modelo de layout de baixo nível — spans, linhas e blocos brutos.

Fica entre o PDF e o IR semântico. Os estágios 02 (nativo) e 03 (OCR) produzem
estas estruturas; o estágio 07 as converte em blocos tipados. Manter as duas
rotas produzindo o mesmo formato é o que permite tratar PDF híbrido sem
código duplicado.
"""

from __future__ import annotations

import re
import statistics

from pydantic import BaseModel, Field

from app.model.geometry import BBox
from app.model.provenance import Provenance, Source

# Bits de `flags` do PyMuPDF
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_SERIF = 4
FLAG_MONO = 8
FLAG_BOLD = 16


class TextSpan(BaseModel):
    text: str
    bbox: BBox
    font: str = ""
    size: float = 0.0
    flags: int = 0
    color: int = 0
    confidence: float = 1.0     # < 1.0 apenas em spans vindos de OCR

    @property
    def bold(self) -> bool:
        # Muitos PDFs de tribunal não setam o bit; o nome da fonte entrega.
        return bool(self.flags & FLAG_BOLD) or bool(
            re.search(r"(bold|black|heavy|semibold|demi)", self.font, re.I)
        )

    @property
    def italic(self) -> bool:
        return bool(self.flags & FLAG_ITALIC) or bool(
            re.search(r"(italic|oblique)", self.font, re.I)
        )

    @property
    def mono(self) -> bool:
        return bool(self.flags & FLAG_MONO)

    @property
    def superscript(self) -> bool:
        return bool(self.flags & FLAG_SUPERSCRIPT)


class TextLine(BaseModel):
    spans: list[TextSpan] = Field(default_factory=list)
    bbox: BBox

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def stripped(self) -> str:
        return self.text.strip()

    @property
    def size(self) -> float:
        """Tamanho de fonte dominante, ponderado por quantidade de caracteres."""
        weighted = [(s.size, len(s.text.strip())) for s in self.spans if s.text.strip()]
        if not weighted:
            return 0.0
        total = sum(w for _, w in weighted)
        if total == 0:
            return weighted[0][0]
        return sum(sz * w for sz, w in weighted) / total

    @property
    def font(self) -> str:
        best, best_len = "", -1
        for s in self.spans:
            n = len(s.text.strip())
            if n > best_len:
                best, best_len = s.font, n
        return best

    @property
    def bold_ratio(self) -> float:
        total = sum(len(s.text.strip()) for s in self.spans)
        if not total:
            return 0.0
        return sum(len(s.text.strip()) for s in self.spans if s.bold) / total

    @property
    def is_bold(self) -> bool:
        return self.bold_ratio >= 0.7

    @property
    def is_italic(self) -> bool:
        total = sum(len(s.text.strip()) for s in self.spans)
        if not total:
            return False
        return sum(len(s.text.strip()) for s in self.spans if s.italic) / total >= 0.7

    @property
    def mean_confidence(self) -> float:
        vals = [s.confidence for s in self.spans if s.text.strip()]
        return statistics.fmean(vals) if vals else 1.0


class RawBlock(BaseModel):
    """Bloco geométrico bruto, antes de qualquer decisão semântica."""

    page: int
    number: int                       # ordem original dada pelo extrator
    bbox: BBox
    lines: list[TextLine] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # Marcações atribuídas pelo estágio 04
    is_header: bool = False
    is_footer: bool = False
    is_inside_table: bool = False
    is_footnote_zone: bool = False
    column: int = 0
    consumed: bool = False            # já virou tabela/figura; ignorar no 07

    @property
    def text(self) -> str:
        return "\n".join(line.stripped for line in self.lines if line.stripped)

    @property
    def char_count(self) -> int:
        return sum(len(line.stripped) for line in self.lines)

    @property
    def size(self) -> float:
        sizes = [(line.size, len(line.stripped)) for line in self.lines if line.stripped]
        if not sizes:
            return 0.0
        total = sum(w for _, w in sizes)
        return sum(s * w for s, w in sizes) / total if total else sizes[0][0]

    @property
    def is_ocr(self) -> bool:
        return self.provenance.source == Source.OCR

    @property
    def mean_confidence(self) -> float:
        vals = [line.mean_confidence for line in self.lines if line.stripped]
        return statistics.fmean(vals) if vals else 1.0


class Word(BaseModel):
    """Palavra isolada com sua caixa — base do detector de tabela sem bordas."""

    text: str
    bbox: BBox
    confidence: float = 1.0
    from_ocr: bool = False


class PageLayout(BaseModel):
    page: int
    width: float
    height: float
    blocks: list[RawBlock] = Field(default_factory=list)
    words: list[Word] = Field(default_factory=list)
    ruling_lines_h: list[tuple[float, float, float]] = Field(default_factory=list)
    ruling_lines_v: list[tuple[float, float, float]] = Field(default_factory=list)
    image_rects: list[BBox] = Field(default_factory=list)
    column_bounds: list[tuple[float, float]] = Field(default_factory=list)

    @property
    def body_blocks(self) -> list[RawBlock]:
        return [
            b
            for b in self.blocks
            if not (b.is_header or b.is_footer or b.consumed)
        ]

    def all_lines(self) -> list[TextLine]:
        return [ln for b in self.blocks for ln in b.lines if ln.stripped]
