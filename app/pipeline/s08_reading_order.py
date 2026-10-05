"""Estágio 08 — Ordem de leitura e junção através da quebra de página.

Até aqui os blocos foram criados por estágios diferentes e em ordens diferentes:
tabelas no 05, figuras no 06, texto no 07. Este estágio impõe a ordem única em
que o documento deve ser lido — por página, por coluna, de cima para baixo — e
costura o parágrafo que a quebra de página partiu no meio.
"""

from __future__ import annotations

import logging
import re

from app.model.blocks import BlockKind, PageBreakBlock, ParagraphStyle
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s08")

# Fim de parágrafo de verdade: pontuação final ou dois-pontos.
COMPLETE_END = re.compile(r"[.!?;:»”\"']\s*$")
# Continuação provável: minúscula, conjunção, ou abertura de citação.
CONTINUES = re.compile(r"^[a-zàáâãéêíóôõúç(\[“\"']")


def _sort_key(block) -> tuple:
    # Nota de rodapé vai para o fim da página, independentemente da geometria.
    is_footnote = 1 if block.kind == BlockKind.FOOTNOTE else 0
    return (block.page, block.column, is_footnote, round(block.bbox.y0, 1), round(block.bbox.x0, 1))


def _merge_paragraphs_across_pages(doc, dehyphenate: bool) -> int:
    """Une o parágrafo interrompido pela quebra de página."""
    merged = 0
    index = 0

    while index < len(doc.blocks) - 1:
        current = doc.blocks[index]
        nxt = doc.blocks[index + 1]
        index += 1

        if current.kind != BlockKind.PARAGRAPH or nxt.kind != BlockKind.PARAGRAPH:
            continue
        if nxt.page != current.page + 1:
            continue
        if current.style != nxt.style:
            continue

        left = current.text.rstrip()
        right = nxt.text.lstrip()
        if not left or not right:
            continue
        if COMPLETE_END.search(left):
            continue
        if not CONTINUES.match(right):
            continue

        if dehyphenate and left.endswith("-") and right[:1].islower():
            current.text = left[:-1] + right
        elif left.endswith("-"):
            current.text = left + right
        else:
            current.text = left + " " + right

        nxt_provenance_notes = nxt.provenance.notes
        current.provenance.notes.append(f"unido com a continuação da página {nxt.page}")
        current.provenance.notes.extend(nxt_provenance_notes)
        current.provenance.confidence = min(
            current.provenance.confidence, nxt.provenance.confidence
        )

        doc.blocks.remove(nxt)
        merged += 1
        index -= 1   # reavalia o mesmo bloco: pode continuar na página seguinte

    return merged


def _insert_page_breaks(doc) -> None:
    out = []
    previous_page = None
    for block in doc.blocks:
        if previous_page is not None and block.page != previous_page:
            out.append(
                PageBreakBlock(
                    id=f"pb{block.page:05d}",
                    page=block.page,
                    bbox=block.bbox,
                    label=f"página {block.page}",
                )
            )
        out.append(block)
        previous_page = block.page
    doc.blocks = out


def _promote_orphan_captions(doc) -> int:
    """Parágrafo curto logo abaixo de figura sem legenda vira legenda dela."""
    promoted = 0
    for index, block in enumerate(doc.blocks[:-1]):
        if block.kind != BlockKind.FIGURE or block.caption:
            continue
        nxt = doc.blocks[index + 1]
        if nxt.kind != BlockKind.PARAGRAPH or nxt.page != block.page:
            continue
        if len(nxt.text) > 200 or nxt.style != ParagraphStyle.BODY:
            continue
        distance = nxt.bbox.y0 - block.bbox.y1
        if not (0 <= distance <= 24):
            continue
        block.caption = nxt.text
        block.alt = block.alt or nxt.text
        nxt.style = ParagraphStyle.CAPTION
        promoted += 1
    return promoted


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    doc = ctx.doc

    doc.blocks.sort(key=_sort_key)

    merged = 0
    if ctx.options.merge_across_pages:
        merged = _merge_paragraphs_across_pages(doc, ctx.scratch.get("dehyphenate", False))

    promoted = _promote_orphan_captions(doc)

    if ctx.options.page_markers and ctx.settings.md_page_markers:
        _insert_page_breaks(doc)

    doc.resequence()

    ctx.scratch["paragraphs_merged"] = merged
    ctx.scratch["captions_promoted"] = promoted
    log.info(
        "ordem definida: %s blocos | %s parágrafos unidos entre páginas | "
        "%s legendas promovidas",
        len(doc.blocks),
        merged,
        promoted,
    )
