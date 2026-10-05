"""Estágio 06 — Imagens e figuras.

Grava os arquivos em `/assets` e cria os blocos de figura no IR. Também procura
a legenda: um texto curto imediatamente abaixo (ou acima) da figura, começando
por "Figura", "Gráfico", "Foto" e afins. Quando encontrada, ela vira a legenda
do bloco e o texto é consumido, para não aparecer duas vezes no Markdown.
"""

from __future__ import annotations

import logging
import re

from app.extractors import images as image_extractor
from app.model.blocks import FigureBlock
from app.model.geometry import BBox
from app.model.layout import PageLayout
from app.model.provenance import Provenance, Severity, Source
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s06")

CAPTION_PATTERN = re.compile(
    r"^\s*(figura|fig\.?|imagem|gráfico|grafico|foto|quadro|ilustração|ilustracao|"
    r"anexo|documento)\s*[\d ivxlc]*\s*[-–—:.)]",
    re.IGNORECASE,
)

CAPTION_MAX_CHARS = 320
CAPTION_MAX_DISTANCE = 46.0   # pontos


def _find_caption(layout: PageLayout, figure_box: BBox) -> tuple[str | None, object]:
    """Procura legenda abaixo e, em seguida, acima da figura."""
    best = None
    best_distance = CAPTION_MAX_DISTANCE

    for block in layout.blocks:
        if block.consumed or block.is_header or block.is_footer or block.is_inside_table:
            continue
        text = block.text.strip()
        if not text or len(text) > CAPTION_MAX_CHARS:
            continue
        if not CAPTION_PATTERN.match(text):
            continue

        # Precisa se sobrepor horizontalmente à figura.
        overlap = min(block.bbox.x1, figure_box.x1) - max(block.bbox.x0, figure_box.x0)
        if overlap <= 0:
            continue

        below = block.bbox.y0 - figure_box.y1
        above = figure_box.y0 - block.bbox.y1
        distance = below if below >= 0 else (above if above >= 0 else -1)
        if distance < 0 or distance > best_distance:
            continue

        best_distance = distance
        best = block

    if best is None:
        return None, None
    return " ".join(best.text.split()), best


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None and ctx.pdf is not None
    doc = ctx.doc
    settings = ctx.settings
    layouts: dict[int, PageLayout] = ctx.scratch.get("layouts", {})
    assets_dir = ctx.assets_dir

    total_pages = len(doc.pages) or 1
    extracted_count = 0
    vector_count = 0
    suppressed_scans = 0
    failures: list[int] = []

    for page_info in doc.pages:
        if page_info.excluded:
            continue

        number = page_info.number
        page = ctx.pdf[number - 1]
        layout = layouts.get(number)
        ctx.progress(
            "images",
            0.68 + 0.07 * (number / total_pages),
            f"Extraindo imagens — página {number}",
        )

        try:
            found = image_extractor.extract_page_images(
                ctx.pdf,
                page,
                number,
                assets_dir,
                min_width=settings.image_min_width,
                min_height=settings.image_min_height,
                min_area_px=settings.image_min_area_px,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("página %s: extração de imagens falhou (%s)", number, exc)
            failures.append(number)
            found = []

        # Página inteiramente digitalizada: a "imagem" é a própria página, e o
        # texto já veio pelo OCR. Reproduzi-la no Markdown só polui.
        page_is_scan = (
            page_info.route.value == "ocr"
            and len(found) == 1
            and found[0].bbox.area >= page_info.width * page_info.height * 0.85
        )
        if page_is_scan:
            found[0].path.unlink(missing_ok=True)
            found = []
            suppressed_scans += 1

        if settings.extract_vector_figures and ctx.options.extract_vector_figures:
            try:
                vectors = image_extractor.extract_vector_figures(
                    page,
                    number,
                    assets_dir,
                    exclude=[img.bbox for img in found],
                    dpi=settings.vector_figure_dpi,
                    min_drawings=settings.vector_figure_min_drawings,
                    start_index=1,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("página %s: figuras vetoriais falharam (%s)", number, exc)
                vectors = []
            found.extend(vectors)
            vector_count += len(vectors)

        for img in found:
            relative = f"{settings.md_assets_dir}/{img.path.name}"
            caption, caption_block = (None, None)
            if layout is not None:
                caption, caption_block = _find_caption(layout, img.bbox)

            block = FigureBlock(
                id=doc.next_block_id("f"),
                page=number,
                bbox=img.bbox,
                asset_path=relative,
                caption=caption,
                alt=caption or f"Imagem da página {number}",
                width_px=img.width,
                height_px=img.height,
                is_vector=img.is_vector,
                xref=img.xref,
                provenance=Provenance(
                    source=Source.VECTOR if img.is_vector else Source.NATIVE,
                    engine="pymupdf",
                    confidence=1.0,
                    notes=[img.note] if img.note else [],
                ),
            )
            doc.blocks.append(block)
            doc.assets.append(relative)
            extracted_count += 1

            if caption_block is not None:
                caption_block.consumed = True

    ctx.scratch["images_extracted"] = extracted_count
    ctx.scratch["vector_figures"] = vector_count
    log.info(
        "imagens extraídas: %s (%s figuras vetoriais)", extracted_count, vector_count
    )

    if failures:
        ctx.warn(
            "IMAGE_EXTRACTION_FAILED",
            f"Falha ao extrair imagens de {len(failures)} página(s): "
            f"{', '.join(map(str, failures[:20]))}",
            severity=Severity.ERROR,
            pages=failures,
        )

    # Confronto: quantas imagens o PDF declara versus quantas saíram.
    declared = sum(p.image_count for p in doc.pages if not p.excluded)
    ctx.scratch["images_declared"] = declared
    # A imagem de uma página inteiramente digitalizada é a própria página: o
    # texto já veio pelo OCR e reproduzi-la só polui o Markdown. Descontamos
    # para que a validação não a acuse como imagem perdida.
    ctx.scratch["page_scans_suppressed"] = suppressed_scans
