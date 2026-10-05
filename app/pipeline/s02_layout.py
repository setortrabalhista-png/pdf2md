"""Estágio 02 — Layout nativo.

Lê a camada de texto de cada página que não depende exclusivamente de OCR e
guarda o resultado em `ctx.scratch["layouts"]`, indexado por número de página.
Nada aqui decide semântica: só geometria, fonte e réguas.
"""

from __future__ import annotations

import logging

from app.extractors import text_pymupdf
from app.model.document import PageRoute
from app.model.layout import PageLayout
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s02")

# Páginas nessas rotas têm camada de texto aproveitável.
NATIVE_ROUTES = {PageRoute.NATIVE, PageRoute.HYBRID}


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None and ctx.pdf is not None

    layouts: dict[int, PageLayout] = {}
    ctx.scratch["layouts"] = layouts

    total = len(ctx.doc.pages) or 1
    snap = ctx.settings.table_line_snap_tolerance

    for page_info in ctx.doc.pages:
        number = page_info.number

        if page_info.excluded:
            # Sem layout: sem tabela, sem imagem e sem texto adiante.
            continue

        page = ctx.pdf[number - 1]

        if page_info.route in NATIVE_ROUTES:
            layout = text_pymupdf.extract_page(page, number, snap=snap)
        else:
            # Página que irá para OCR: ainda coletamos réguas e retângulos de
            # imagem, porque servem à detecção de tabela mesmo sem texto nativo.
            layout = PageLayout(
                page=number,
                width=float(page.rect.width),
                height=float(page.rect.height),
            )
            layout.ruling_lines_h, layout.ruling_lines_v = text_pymupdf._collect_rulings(
                page, snap
            )
            layout.image_rects = text_pymupdf._collect_image_rects(page)

        layouts[number] = layout
        page_info.vector_line_count = len(layout.ruling_lines_h) + len(layout.ruling_lines_v)

        ctx.progress(
            "layout",
            0.05 + 0.15 * (number / total),
            f"Lendo estrutura — página {number} de {total}",
        )

    blocks_total = sum(len(lay.blocks) for lay in layouts.values())
    log.info("layout nativo: %s blocos em %s páginas", blocks_total, len(layouts))
    ctx.scratch["native_block_count"] = blocks_total
