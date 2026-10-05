"""Estágio 05 — Tabelas.

Ordem de tentativa, da mais confiável para a menos:

1. **lines** — malha vetorial. Bordas desenhadas viram células diretamente.
2. **camelot / tabula** — só se instalados e só quando (1) ficou abaixo do
   limiar de confiança. São uma segunda opinião, não o motor.
3. **stream** — alinhamento por espaço, para tabela sem bordas, aplicado
   apenas fora das regiões já resolvidas.

Tabela abaixo do limiar de confiança **não é descartada**: entra no Markdown com
o conteúdo que temos e uma marcação de revisão. Perder o dado é pior do que
entregá-lo sinalizado.
"""

from __future__ import annotations

import logging

from app.extractors import tables_fallback, tables_lines, tables_stream
from app.extractors.tables_lines import TableExtraction
from app.model.blocks import TableBlock
from app.model.layout import PageLayout
from app.model.provenance import Provenance, Severity, Source
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s05")


def _to_block(
    extraction: TableExtraction, page: int, block_id: str, threshold: float
) -> TableBlock:
    source = Source.VECTOR if extraction.engine == "lines" else Source.HEURISTIC
    block = TableBlock(
        id=block_id,
        page=page,
        bbox=extraction.bbox,
        rows=extraction.rows,
        header_rows=extraction.header_rows,
        alignments=extraction.alignments,
        engine=extraction.engine,
        provenance=Provenance(
            source=source,
            engine=extraction.engine,
            confidence=extraction.confidence,
            notes=list(extraction.notes),
        ),
    )

    reasons: list[str] = []
    if extraction.confidence < threshold:
        reasons.append(f"confiança de apenas {extraction.confidence:.0%}")
    if extraction.has_merged:
        reasons.append("contém células mescladas, que o Markdown não representa")

    if reasons:
        # As notas do extrator já podem trazer o mesmo motivo; deduplicamos para
        # que o comentário no Markdown fique legível.
        seen = set()
        unique = [r for r in reasons + extraction.notes if not (r in seen or seen.add(r))]
        block.flag_review("tabela precisa de conferência — " + "; ".join(unique))
    return block


def _mark_consumed(layout: PageLayout, extraction: TableExtraction) -> None:
    for b in layout.blocks:
        if b.is_header or b.is_footer:
            continue
        if b.bbox.contained_in(extraction.bbox, tolerance=0.6):
            b.consumed = True


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    doc = ctx.doc
    settings = ctx.settings
    layouts: dict[int, PageLayout] = ctx.scratch.get("layouts", {})
    grids_by_page: dict[int, list] = ctx.scratch.get("grids", {})

    threshold = settings.table_min_confidence
    fallbacks = tables_fallback.describe_availability()
    ctx.scratch["table_fallbacks"] = fallbacks
    log.info("motores de reserva disponíveis: %s", fallbacks)

    produced: list[TableBlock] = []
    low_confidence: list[tuple[int, float]] = []
    total_pages = len(layouts) or 1

    for index, (number, layout) in enumerate(sorted(layouts.items()), start=1):
        ctx.progress(
            "tables",
            0.58 + 0.10 * (index / total_pages),
            f"Extraindo tabelas — página {number}",
        )

        page_tables: list[TableExtraction] = []

        # (1) motor de réguas
        for grid in grids_by_page.get(number, []):
            extraction = tables_lines.extract_from_grid(
                grid, layout, snap=settings.table_line_snap_tolerance
            )
            if extraction is None:
                continue
            if (
                len(extraction.rows) < settings.table_min_rows
                or max((len(r) for r in extraction.rows), default=0) < settings.table_min_cols
            ):
                continue

            # (2) segunda opinião apenas quando a confiança é baixa
            if extraction.confidence < threshold and (
                fallbacks["camelot"] or fallbacks["tabula"]
            ):
                alternatives = tables_fallback.try_camelot(
                    ctx.pdf_path, number, extraction.bbox
                ) + tables_fallback.try_tabula(ctx.pdf_path, number, extraction.bbox)
                for alt in alternatives:
                    if alt.confidence > extraction.confidence:
                        alt.bbox = extraction.bbox
                        alt.notes.append(
                            f"substituiu o motor 'lines' (confiança "
                            f"{extraction.confidence:.0%} → {alt.confidence:.0%})"
                        )
                        extraction = alt

            page_tables.append(extraction)

        # (3) motor de alinhamento, fora do que já foi resolvido
        stream_tables = tables_stream.detect(
            layout,
            exclude=[t.bbox for t in page_tables],
            min_cols=settings.table_min_cols,
            min_lines=max(3, settings.table_min_rows + 1),
        )
        page_tables.extend(stream_tables)

        for extraction in sorted(page_tables, key=lambda t: (t.bbox.y0, t.bbox.x0)):
            block = _to_block(extraction, number, doc.next_block_id("t"), threshold)
            doc.blocks.append(block)
            produced.append(block)
            _mark_consumed(layout, extraction)

            if extraction.confidence < threshold:
                low_confidence.append((number, extraction.confidence))

    ctx.scratch["tables_detected"] = len(produced)
    log.info(
        "tabelas: %s detectadas (%s abaixo do limiar de confiança)",
        len(produced),
        len(low_confidence),
    )

    if low_confidence:
        pages = sorted({p for p, _ in low_confidence})
        ctx.warn(
            "TABLE_LOW_CONFIDENCE",
            f"{len(low_confidence)} tabela(s) com confiança abaixo de "
            f"{threshold:.0%} e marcadas para revisão no Markdown. "
            f"Páginas: {', '.join(map(str, pages[:20]))}"
            + ("…" if len(pages) > 20 else ""),
            severity=Severity.WARNING,
            pages=pages,
            count=len(low_confidence),
        )

    merged = _merge_across_pages(ctx, produced) if ctx.options.merge_across_pages else 0
    if merged:
        log.info("tabelas unidas através de quebra de página: %s", merged)


def _merge_across_pages(ctx: PipelineContext, tables: list[TableBlock]) -> int:
    """Une tabelas partidas pela quebra de página.

    Critério: mesma quantidade de colunas, a primeira termina rente ao pé da
    página, a segunda começa rente ao topo da seguinte, e o cabeçalho ou se
    repete ou está ausente na continuação.

    A união encadeia. Demonstrativo de verbas e cartão de ponto costumam ocupar
    três ou quatro páginas seguidas, então a tabela já unida volta para a lista
    da página seguinte como candidata — é o que permite juntar a corrente
    inteira, e não só o primeiro par.
    """
    assert ctx.doc is not None
    doc = ctx.doc
    merged_count = 0

    by_page: dict[int, list[TableBlock]] = {}
    for t in tables:
        by_page.setdefault(t.page, []).append(t)

    for page_number in sorted(by_page):
        next_page = page_number + 1

        # `.get(...) or []` e não `in by_page`: uma união anterior pode ter
        # esvaziado a lista desta página sem remover a chave.
        atual = by_page.get(page_number) or []
        seguinte = by_page.get(next_page) or []
        if not atual or not seguinte:
            continue

        page_info = doc.page(page_number)
        next_info = doc.page(next_page)
        if not page_info or not next_info:
            continue

        last = max(atual, key=lambda t: t.bbox.y1)
        first = min(seguinte, key=lambda t: t.bbox.y0)

        if last is first:
            continue

        if last.n_cols != first.n_cols or last.n_cols == 0:
            continue
        if last.bbox.y1 < page_info.height * 0.78:
            continue
        if first.bbox.y0 > next_info.height * 0.28:
            continue

        rows = list(first.rows)
        # Cabeçalho repetido na continuação: descarta a repetição.
        if (
            last.header_rows
            and first.header_rows
            and rows
            and [c.text.strip() for c in rows[0]]
            == [c.text.strip() for c in last.rows[0]]
        ):
            rows = rows[1:]

        last.rows.extend(rows)
        last.bbox = last.bbox.union(first.bbox)
        last.provenance.notes.append(
            f"unida com a continuação da página {next_page}"
        )
        first.continues_previous = True
        doc.blocks = [b for b in doc.blocks if b is not first]

        # A tabela unida passa a "terminar" na página seguinte — sua caixa foi
        # estendida até lá. Colocá-la nessa lista é o que deixa a corrente
        # continuar na página seguinte à seguinte.
        by_page[next_page] = [t for t in seguinte if t is not first]
        by_page[next_page].append(last)

        merged_count += 1

    return merged_count
