"""Estágio 04 — Regiões da página.

Quatro perguntas são respondidas aqui, e todas afetam o que sobra para o corpo
do documento:

* Quais blocos são cabeçalho/rodapé recorrente? (carimbo do PJe, numeração,
  "documento assinado eletronicamente por…")
* Onde estão as malhas de tabela?
* Onde termina o corpo e começam as notas de rodapé?
* A página tem mais de uma coluna?

Nada é apagado: o que sai do corpo fica registrado no documento para auditoria.
"""

from __future__ import annotations

import logging
import re
import statistics

from rapidfuzz import fuzz

from app.extractors import rulings
from app.model.layout import PageLayout, RawBlock
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s04")

# Números de página, datas e códigos de validação variam a cada página; para
# medir recorrência precisamos ignorá-los.
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


def normalize_for_recurrence(text: str) -> str:
    t = _DIGITS.sub("#", text.lower())
    return _SPACES.sub(" ", t).strip()


# ── Cabeçalho e rodapé ────────────────────────────────────────────────────


def _band_blocks(layout: PageLayout, ratio: float, top: bool) -> list[RawBlock]:
    if layout.height <= 0:
        return []
    limit = layout.height * ratio
    out = []
    for b in layout.blocks:
        if b.consumed:
            continue
        if (top and b.bbox.y1 <= limit) or (not top and b.bbox.y0 >= layout.height - limit):
            out.append(b)
    return out


def _detect_recurrent(
    candidates: dict[int, list[RawBlock]],
    page_count: int,
    threshold: float,
    similarity: int,
) -> set[tuple[int, int]]:
    """Devolve o conjunto (página, número do bloco) considerado recorrente.

    A proporção é medida sobre as páginas que **poderiam** conter o carimbo —
    aquelas com algum bloco na faixa. Num processo com metade das folhas
    digitalizadas, medir sobre o total de páginas afundaria a taxa e o carimbo
    escaparia para o corpo do texto.
    """
    eligible = sum(1 for blocks in candidates.values() if blocks) or page_count
    # Agrupa textos normalizados por similaridade fuzzy.
    groups: list[dict] = []

    for page, blocks in candidates.items():
        for b in blocks:
            norm = normalize_for_recurrence(b.text)
            if len(norm) < 4:
                continue
            for g in groups:
                if fuzz.ratio(norm, g["key"]) >= similarity:
                    g["hits"].append((page, b.number))
                    g["pages"].add(page)
                    break
            else:
                groups.append({"key": norm, "hits": [(page, b.number)], "pages": {page}})

    recurrent: set[tuple[int, int]] = set()
    for g in groups:
        if len(g["pages"]) / max(1, eligible) >= threshold:
            recurrent.update(g["hits"])
    return recurrent


def detect_headers_footers(ctx: PipelineContext, layouts: dict[int, PageLayout]) -> None:
    settings = ctx.settings
    doc = ctx.doc
    assert doc is not None

    page_count = len(layouts)
    if page_count < settings.min_pages_for_repetition:
        log.debug("documento curto demais para inferir recorrência")
        return

    header_candidates = {
        n: _band_blocks(lay, settings.header_band_ratio, top=True)
        for n, lay in layouts.items()
    }
    footer_candidates = {
        n: _band_blocks(lay, settings.footer_band_ratio, top=False)
        for n, lay in layouts.items()
    }

    recurrent_headers = _detect_recurrent(
        header_candidates, page_count, settings.repetition_threshold, settings.repetition_similarity
    )
    recurrent_footers = _detect_recurrent(
        footer_candidates, page_count, settings.repetition_threshold, settings.repetition_similarity
    )

    seen_headers: set[str] = set()
    seen_footers: set[str] = set()

    for number, layout in layouts.items():
        page_info = doc.page(number)
        for b in layout.blocks:
            if (number, b.number) in recurrent_headers:
                b.is_header = True
                if page_info:
                    page_info.boilerplate_char_count += b.char_count
                if page_info and not page_info.header_text:
                    page_info.header_text = b.text
                key = normalize_for_recurrence(b.text)
                if key not in seen_headers:
                    seen_headers.add(key)
                    doc.discarded_headers.append(b.text)
            elif (number, b.number) in recurrent_footers:
                b.is_footer = True
                if page_info:
                    page_info.boilerplate_char_count += b.char_count
                if page_info and not page_info.footer_text:
                    page_info.footer_text = b.text
                key = normalize_for_recurrence(b.text)
                if key not in seen_footers:
                    seen_footers.add(key)
                    doc.discarded_footers.append(b.text)

    n_h = sum(1 for lay in layouts.values() for b in lay.blocks if b.is_header)
    n_f = sum(1 for lay in layouts.values() for b in lay.blocks if b.is_footer)
    log.info("cabeçalho/rodapé recorrentes removidos do corpo: %s / %s blocos", n_h, n_f)


# ── Malhas de tabela ──────────────────────────────────────────────────────


def detect_table_grids(ctx: PipelineContext, layouts: dict[int, PageLayout]) -> None:
    settings = ctx.settings
    grids_by_page: dict[int, list] = {}

    for number, layout in layouts.items():
        grids = rulings.find_grids(
            layout.ruling_lines_h,
            layout.ruling_lines_v,
            snap=settings.table_line_snap_tolerance,
            min_rows=settings.table_min_rows + 1,   # N linhas exigem N+1 réguas
            min_cols=settings.table_min_cols + 1,
        )

        # Uma malha que cobre quase a página inteira costuma ser moldura de
        # papel timbrado, não tabela.
        page_area = layout.width * layout.height or 1.0
        grids = [g for g in grids if g.bbox.area / page_area < 0.92]

        if grids:
            grids_by_page[number] = grids
            for b in layout.blocks:
                if any(b.bbox.contained_in(g.bbox, tolerance=0.7) for g in grids):
                    b.is_inside_table = True

    ctx.scratch["grids"] = grids_by_page
    total = sum(len(g) for g in grids_by_page.values())
    log.info("malhas de tabela detectadas: %s", total)


# ── Zona de notas de rodapé ───────────────────────────────────────────────


def detect_footnote_zones(ctx: PipelineContext, layouts: dict[int, PageLayout]) -> None:
    """Marca blocos abaixo do filete separador ou com corpo visivelmente menor."""
    body_sizes = [
        b.size
        for lay in layouts.values()
        for b in lay.blocks
        if b.size > 0 and not (b.is_header or b.is_footer)
    ]
    if not body_sizes:
        return
    median_size = statistics.median(body_sizes)

    for layout in layouts.values():
        separator = rulings.find_footnote_separator(
            layout.ruling_lines_h, layout.width, layout.height
        )
        for b in layout.blocks:
            if b.is_header or b.is_footer or b.is_inside_table:
                continue
            below_separator = separator is not None and b.bbox.y0 >= separator - 1.0
            small_and_low = (
                b.size > 0
                and b.size <= median_size * 0.86
                and b.bbox.y0 >= layout.height * 0.72
            )
            if below_separator or small_and_low:
                b.is_footnote_zone = True


# ── Colunas ───────────────────────────────────────────────────────────────


def detect_columns(ctx: PipelineContext, layouts: dict[int, PageLayout]) -> None:
    """Detecta duas colunas por calha vertical vazia.

    Deliberadamente conservador: em peça jurídica a coluna única é a regra, e um
    falso positivo aqui embaralha o documento inteiro.
    """
    for layout in layouts.values():
        body = [
            b
            for b in layout.blocks
            if not (b.is_header or b.is_footer or b.is_inside_table) and b.char_count > 0
        ]
        if len(body) < 6:
            continue

        left_edge = min(b.bbox.x0 for b in body)
        right_edge = max(b.bbox.x1 for b in body)
        span = right_edge - left_edge
        if span <= 0:
            continue

        # Procura uma calha central que nenhum bloco atravessa.
        best_gutter = None
        for frac in (0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56):
            x = left_edge + span * frac
            crossing = sum(1 for b in body if b.bbox.x0 < x < b.bbox.x1)
            if crossing == 0:
                left = [b for b in body if b.bbox.x1 <= x]
                right = [b for b in body if b.bbox.x0 >= x]
                if len(left) >= 3 and len(right) >= 3:
                    balance = min(len(left), len(right)) / max(len(left), len(right))
                    if balance >= 0.35:
                        best_gutter = x
                        break

        if best_gutter is None:
            continue

        for b in layout.blocks:
            b.column = 0 if b.bbox.cx < best_gutter else 1
        layout.column_bounds = [(left_edge, best_gutter), (best_gutter, right_edge)]
        log.debug("página %s: duas colunas (calha em x=%.1f)", layout.page, best_gutter)


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    layouts: dict[int, PageLayout] = ctx.scratch.get("layouts", {})
    if not layouts:
        return

    ctx.progress("regions", 0.52, "Detectando cabeçalhos e rodapés recorrentes")
    if ctx.options.detect_headers_footers:
        detect_headers_footers(ctx, layouts)

    ctx.progress("regions", 0.55, "Localizando tabelas")
    if ctx.options.extract_tables:
        detect_table_grids(ctx, layouts)

    ctx.progress("regions", 0.57, "Separando notas de rodapé")
    if ctx.options.detect_footnotes:
        detect_footnote_zones(ctx, layouts)

    detect_columns(ctx, layouts)
