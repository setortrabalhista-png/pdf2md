"""Extração de layout nativo via PyMuPDF.

Entrega spans com fonte, tamanho e flags — a matéria-prima da detecção de
títulos — mais as réguas vetoriais que o detector de tabelas consome.
"""

from __future__ import annotations

import logging

import pymupdf

from app.model.geometry import BBox
from app.model.layout import PageLayout, RawBlock, TextLine, TextSpan, Word
from app.model.provenance import Provenance, Source

log = logging.getLogger("pdf2md.extract.pymupdf")

# Preserva ligaduras e espaços em branco significativos; descarta imagens
# (tratadas no estágio 06).
TEXT_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE
    | pymupdf.TEXT_PRESERVE_LIGATURES
    | pymupdf.TEXT_MEDIABOX_CLIP
)


def _collect_rulings(
    page: pymupdf.Page, snap: float
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Segmentos horizontais e verticais desenhados na página.

    Horizontais como (y, x0, x1); verticais como (x, y0, y1). Retângulos finos
    contam como régua — é assim que a maioria dos geradores desenha bordas de
    tabela.
    """
    horizontals: list[tuple[float, float, float]] = []
    verticals: list[tuple[float, float, float]] = []

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return horizontals, verticals

    for d in drawings:
        for item in d.get("items", []):
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= snap and abs(p1.x - p2.x) > snap:
                    horizontals.append(((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x)))
                elif abs(p1.x - p2.x) <= snap and abs(p1.y - p2.y) > snap:
                    verticals.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif op == "re":
                r = item[1]
                if r.height <= snap and r.width > snap:
                    horizontals.append((r.y0 + r.height / 2, r.x0, r.x1))
                elif r.width <= snap and r.height > snap:
                    verticals.append((r.x0 + r.width / 2, r.y0, r.y1))
                elif r.width > snap and r.height > snap:
                    # Retângulo cheio: as quatro bordas são réguas candidatas.
                    horizontals.append((r.y0, r.x0, r.x1))
                    horizontals.append((r.y1, r.x0, r.x1))
                    verticals.append((r.x0, r.y0, r.y1))
                    verticals.append((r.x1, r.y0, r.y1))

    return horizontals, verticals


def _collect_image_rects(page: pymupdf.Page) -> list[BBox]:
    rects: list[BBox] = []
    seen: set[tuple] = set()
    try:
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
                if key not in seen:
                    seen.add(key)
                    rects.append(BBox.from_tuple((r.x0, r.y0, r.x1, r.y1)))
    except Exception:  # noqa: BLE001
        pass
    return rects



def _collect_words(page: pymupdf.Page) -> list[Word]:
    """Palavras isoladas com caixa própria.

    O detector de tabela sem bordas precisa medir os vãos entre palavras; spans
    do `get_text("dict")` podem abranger a linha inteira e esconder esses vãos.
    """
    out: list[Word] = []
    try:
        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            if not text.strip():
                continue
            out.append(Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)))
    except Exception:  # noqa: BLE001
        pass
    return out


def extract_page(page: pymupdf.Page, number: int, snap: float = 3.0) -> PageLayout:
    layout = PageLayout(
        page=number,
        width=float(page.rect.width),
        height=float(page.rect.height),
    )

    try:
        data = page.get_text("dict", flags=TEXT_FLAGS)
    except Exception as exc:  # noqa: BLE001
        log.warning("página %s: falha ao ler o texto (%s)", number, exc)
        data = {"blocks": []}

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue

        lines: list[TextLine] = []
        for raw_line in block.get("lines", []):
            spans = [
                TextSpan(
                    text=span.get("text", ""),
                    bbox=BBox.from_tuple(span.get("bbox", (0, 0, 0, 0))),
                    font=span.get("font", ""),
                    size=float(span.get("size", 0.0)),
                    flags=int(span.get("flags", 0)),
                    color=int(span.get("color", 0)),
                )
                for span in raw_line.get("spans", [])
                if span.get("text")
            ]
            if not spans or not "".join(s.text for s in spans).strip():
                continue
            lines.append(
                TextLine(spans=spans, bbox=BBox.from_tuple(raw_line.get("bbox", (0, 0, 0, 0))))
            )

        if not lines:
            continue

        layout.blocks.append(
            RawBlock(
                page=number,
                number=int(block.get("number", len(layout.blocks))),
                bbox=BBox.from_tuple(block.get("bbox", (0, 0, 0, 0))),
                lines=lines,
                provenance=Provenance(source=Source.NATIVE, engine="pymupdf", confidence=1.0),
            )
        )

    layout.words = _collect_words(page)
    layout.ruling_lines_h, layout.ruling_lines_v = _collect_rulings(page, snap)
    layout.image_rects = _collect_image_rects(page)
    return layout
