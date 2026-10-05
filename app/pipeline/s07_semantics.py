"""Estágio 07 — Semântica: o que é título, o que é lista, o que é parágrafo.

O ponto central é a hierarquia de títulos. Não usamos tamanho absoluto ("maior
que 14pt é H1"): isso quebra no primeiro documento com corpo 13. Em vez disso,
agrupamos os estilos tipográficos efetivamente presentes no documento e os
ordenamos entre si. Um documento inteiro em corpo 10 com títulos em 11 negrito
funciona tão bem quanto um com títulos em 20.

Sinais combinados para promover uma linha a título: corpo de fonte acima da
mediana, negrito, caixa alta, centralização, numeração estrutural e brevidade.
Nenhum deles sozinho decide.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass

from app.model.blocks import (
    FootnoteBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    ParagraphStyle,
)
from app.model.layout import PageLayout, RawBlock, TextLine
from app.model.provenance import Provenance, Source
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s07")


# ── Padrões ───────────────────────────────────────────────────────────────

BULLET = re.compile(r"^\s*([-–—•▪◦·*])\s+(?=\S)")
ORDERED_ALPHA = re.compile(r"^\s*\(?([a-zA-Z])[.)]\s+(?=\S)")
ORDERED_NUM = re.compile(r"^\s*\(?(\d{1,3})[.)]\s+(?=\S)")
ORDERED_ROMAN = re.compile(r"^\s*\(?((?:x{0,3})(?:ix|iv|v?i{0,3}))[.)]\s+(?=\S)", re.I)

NUMBERING = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,4})[.)]?\s+(?=\S)")
ROMAN_HEADING = re.compile(r"^\s*([IVXLCDM]{1,7})\s*[-–—.)]\s+(?=\S)")
STRUCTURAL = re.compile(
    r"^\s*(CAP[ÍI]TULO|T[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|PARTE|ANEXO|"
    r"CL[ÁA]USULA|LIVRO)\b",
    re.IGNORECASE,
)
FOOTNOTE_MARKER = re.compile(r"^\s*(\[?\d{1,3}\]?|[*†‡§]{1,3})[.)\]]?\s+(?=\S)")

SENTENCE_END = re.compile(r"[.;:!?]\s*$")
HYPHEN_END = re.compile(r"(\w)-\s*$")


# ── Estilos tipográficos ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Style:
    size: float          # arredondado a 0,5pt
    bold: bool

    def __lt__(self, other: Style) -> bool:
        return (self.size, self.bold) < (other.size, other.bold)


def _style_of(line: TextLine) -> Style:
    return Style(size=round(line.size * 2) / 2, bold=line.is_bold)


def _body_style(lines: list[TextLine]) -> Style:
    """Estilo dominante do corpo, ponderado por quantidade de caracteres."""
    counter: Counter[Style] = Counter()
    for line in lines:
        counter[_style_of(line)] += len(line.stripped)
    if not counter:
        return Style(size=10.0, bold=False)
    return counter.most_common(1)[0][0]


def build_heading_levels(
    lines: list[TextLine], body: Style, max_levels: int, size_delta: float
) -> dict[Style, int]:
    """Mapeia estilos de título para níveis 1..N, do maior para o menor."""
    counter: Counter[Style] = Counter()
    for line in lines:
        style = _style_of(line)
        if style == body:
            continue
        bigger = style.size >= body.size + size_delta
        bold_same_size = style.bold and not body.bold and style.size >= body.size - 0.5
        if bigger or bold_same_size:
            counter[style] += 1

    # Estilo que aparece uma vez só costuma ser ruído tipográfico, não nível.
    candidates = [s for s, n in counter.items() if n >= 1]
    candidates.sort(key=lambda s: (s.size, s.bold), reverse=True)

    levels: dict[Style, int] = {}
    for index, style in enumerate(candidates[:max_levels]):
        levels[style] = index + 1
    return levels


# ── Hifenização ───────────────────────────────────────────────────────────


def document_uses_hyphenation(all_lines: list[TextLine], threshold: float = 0.02) -> bool:
    """O documento quebra palavras no fim da linha?

    Decidido no documento inteiro, não linha a linha: remover o hífen de
    "sócio-administrador" porque a linha terminou ali seria um erro de conteúdo,
    e conteúdo é o que não podemos alterar.
    """
    if not all_lines:
        return False
    hyphenated = sum(1 for ln in all_lines if HYPHEN_END.search(ln.stripped))
    return hyphenated / len(all_lines) >= threshold


def join_lines(lines: list[str], dehyphenate: bool) -> str:
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        match = HYPHEN_END.search(out)
        if match and dehyphenate and nxt[:1].islower():
            out = out[: match.end(1)] + nxt        # remove o hífen
        elif match:
            out = out.rstrip() + nxt.lstrip()      # mantém o hífen do composto
        else:
            out = out.rstrip() + " " + nxt.lstrip()
    return out.strip()


# ── Segmentação de blocos ─────────────────────────────────────────────────


def _median_line_gap(lines: list[TextLine]) -> float:
    gaps = [
        b.bbox.y0 - a.bbox.y1
        for a, b in zip(lines, lines[1:], strict=False)
        if b.bbox.y0 - a.bbox.y1 >= 0
    ]
    return statistics.median(gaps) if gaps else 0.0


def split_into_paragraphs(block: RawBlock) -> list[list[TextLine]]:
    """Quebra um bloco em parágrafos por recuo de primeira linha ou vão maior."""
    lines = [ln for ln in block.lines if ln.stripped]
    if len(lines) <= 1:
        return [lines] if lines else []

    gap = _median_line_gap(lines)
    left_edges = [ln.bbox.x0 for ln in lines]
    base_left = min(left_edges)

    groups: list[list[TextLine]] = [[lines[0]]]
    for previous, line in zip(lines, lines[1:], strict=False):
        vertical = line.bbox.y0 - previous.bbox.y1
        indented = line.bbox.x0 - base_left > 8.0
        short_previous = previous.bbox.x1 < block.bbox.x1 - (block.bbox.width * 0.18)

        # Título encostado no parágrafo anterior sai no mesmo bloco geométrico;
        # a troca de corpo ou de peso é o que revela a fronteira.
        style_changed = (
            abs(line.size - previous.size) >= 0.6
            or line.is_bold != previous.is_bold
        )

        starts_new = False
        if (gap > 0 and vertical > gap * 1.6) or (style_changed and short_previous) or (indented and short_previous) or BULLET.match(line.stripped) or ORDERED_NUM.match(line.stripped):
            starts_new = True

        if starts_new:
            groups.append([line])
        else:
            groups[-1].append(line)

    return groups


def detect_list_marker(text: str) -> tuple[str, bool, str] | None:
    """Devolve (marcador, é_ordenada, texto_sem_marcador) ou None."""
    m = BULLET.match(text)
    if m:
        return m.group(1), False, text[m.end():]

    m = ORDERED_NUM.match(text)
    if m:
        return m.group(0).strip(), True, text[m.end():]

    m = ORDERED_ALPHA.match(text)
    if m:
        return m.group(0).strip(), True, text[m.end():]

    m = ORDERED_ROMAN.match(text)
    if m and m.group(1):
        return m.group(0).strip(), True, text[m.end():]

    return None


def _is_heading(
    text: str,
    style: Style,
    body: Style,
    levels: dict[Style, int],
    line: TextLine,
    layout: PageLayout,
    max_words: int,
    isolated: bool = False,
) -> bool:
    if not text or len(text) < 2:
        return False

    words = text.split()
    if len(words) > max_words:
        return False

    # Um "título" que termina como frase quase sempre é frase.
    if SENTENCE_END.search(text) and not STRUCTURAL.match(text) and len(words) > 6:
        return False

    # Marcador de lista descarta a hipótese de título — EXCETO numeração, que é
    # justamente como uma peça jurídica enumera as suas seções ("1. DOS FATOS").
    # Os demais sinais (negrito, caixa alta, brevidade) separam um do outro.
    if BULLET.match(text) or ORDERED_ALPHA.match(text):
        return False

    score = 0
    if style in levels:
        score += 2
    if style.size >= body.size + 0.5:
        score += 1
    if style.bold and not body.bold:
        score += 1
    if text.isupper() and len(words) <= max_words:
        score += 1

    # Em página de OCR não há metadado de fonte: o corpo é estimado pela altura
    # da caixa da palavra, o que comprime as diferenças de tamanho e faz o teste
    # de "+0,5pt" acima falhar. A linha isolada, curta e maior que o corpo é o
    # sinal que sobrevive a essa perda de resolução.
    if isolated and style.size > body.size and len(words) <= max_words:
        score += 1
    if STRUCTURAL.match(text):
        score += 2
    if NUMBERING.match(text) or ROMAN_HEADING.match(text):
        score += 1

    # Centralização.
    if layout.width:
        left = line.bbox.x0
        right = layout.width - line.bbox.x1
        if abs(left - right) < layout.width * 0.06 and left > layout.width * 0.14:
            score += 1

    return score >= 3


def _heading_level(
    text: str, style: Style, levels: dict[Style, int], max_levels: int
) -> tuple[int, str | None]:
    base = levels.get(style, 2)

    m = NUMBERING.match(text)
    if m:
        depth = m.group(1).count(".") + 1
        return min(max_levels, max(base, depth)), m.group(1)

    m = ROMAN_HEADING.match(text)
    if m:
        return min(max_levels, base), m.group(1)

    if STRUCTURAL.match(text):
        return min(max_levels, base), None

    return min(max_levels, base), None


# ── Estágio ───────────────────────────────────────────────────────────────


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    doc = ctx.doc
    settings = ctx.settings
    layouts: dict[int, PageLayout] = ctx.scratch.get("layouts", {})
    if not layouts:
        return

    body_lines: list[TextLine] = []
    for layout in layouts.values():
        for block in layout.blocks:
            if block.is_header or block.is_footer or block.consumed or block.is_inside_table:
                continue
            body_lines.extend(ln for ln in block.lines if ln.stripped)

    if not body_lines:
        log.warning("nenhuma linha de corpo — o documento pode estar vazio")
        return

    body = _body_style(body_lines)
    levels = build_heading_levels(
        body_lines, body, settings.max_heading_levels, settings.heading_size_delta
    )
    dehyphenate = document_uses_hyphenation(body_lines)

    ctx.scratch["body_style"] = {"size": body.size, "bold": body.bold}
    ctx.scratch["heading_styles"] = {
        f"{s.size}{'B' if s.bold else ''}": lvl for s, lvl in levels.items()
    }
    ctx.scratch["dehyphenate"] = dehyphenate
    log.info(
        "corpo %.1fpt%s | %s nível(is) de título | dehifenização: %s",
        body.size,
        " negrito" if body.bold else "",
        len(levels),
        dehyphenate,
    )

    counts = Counter()

    for number in sorted(layouts):
        layout = layouts[number]
        for block in sorted(layout.blocks, key=lambda b: (b.column, b.bbox.y0, b.bbox.x0)):
            if block.is_header or block.is_footer or block.consumed or block.is_inside_table:
                continue

            if block.is_footnote_zone:
                _emit_footnotes(doc, block, dehyphenate)
                counts["footnote"] += 1
                continue

            for group in split_into_paragraphs(block):
                if not group:
                    continue
                produced = _emit_group(
                    doc, group, block, layout, body, levels, settings, dehyphenate
                )
                counts[produced] += 1

    ctx.scratch["semantic_counts"] = dict(counts)
    log.info("blocos semânticos: %s", dict(counts))


def _provenance_of(block: RawBlock, inferred: bool = True) -> Provenance:
    if block.provenance.source == Source.OCR:
        return block.provenance.model_copy(deep=True)
    return Provenance(
        source=Source.HEURISTIC if inferred else Source.NATIVE,
        engine=block.provenance.engine,
        confidence=block.provenance.confidence,
    )


def _emit_group(
    doc,
    group: list[TextLine],
    block: RawBlock,
    layout: PageLayout,
    body: Style,
    levels: dict[Style, int],
    settings,
    dehyphenate: bool,
) -> str:
    first = group[0]
    raw_lines = [ln.stripped for ln in group if ln.stripped]
    text = join_lines(raw_lines, dehyphenate)
    if not text:
        return "empty"

    bbox = group[0].bbox
    for ln in group[1:]:
        bbox = bbox.union(ln.bbox)

    style = _style_of(first)

    # Título. "Isolado" = o bloco geométrico contém só esta linha, sem parágrafo
    # colado — a assinatura tipográfica de um título de seção.
    isolated = len(group) == 1 and sum(1 for ln in block.lines if ln.stripped) == 1

    if _is_heading(
        text, style, body, levels, first, layout, settings.heading_max_words, isolated
    ):
        level, numbering = _heading_level(text, style, levels, settings.max_heading_levels)
        clean = text
        if numbering:
            clean = re.sub(r"^\s*" + re.escape(numbering) + r"[.)]?\s*", "", text).strip()
        doc.blocks.append(
            HeadingBlock(
                id=doc.next_block_id("h"),
                page=block.page,
                bbox=bbox,
                column=block.column,
                level=level,
                text=clean or text,
                numbering=numbering,
                provenance=_provenance_of(block),
            )
        )
        return "heading"

    # Item de lista
    marker_info = detect_list_marker(text)
    if marker_info:
        marker, ordered, rest = marker_info
        indent_level = 0
        if layout.width:
            linhas = layout.all_lines() or [first]
            relative = first.bbox.x0 - min(ln.bbox.x0 for ln in linhas)
            indent_level = max(0, min(4, int(relative // 18)))
        doc.blocks.append(
            ListItemBlock(
                id=doc.next_block_id("l"),
                page=block.page,
                bbox=bbox,
                column=block.column,
                text=rest.strip(),
                ordered=ordered,
                marker=marker,
                level=indent_level,
                provenance=_provenance_of(block),
            )
        )
        return "list_item"

    # Parágrafo, possivelmente citação recuada
    style_kind = ParagraphStyle.BODY
    if layout.width:
        left_margin = first.bbox.x0
        page_left = min(
            (ln.bbox.x0 for ln in layout.all_lines()), default=left_margin
        )
        indented = left_margin - page_left > layout.width * 0.06
        narrow = bbox.width < layout.width * 0.74
        smaller = style.size < body.size - 0.4
        if indented and (narrow or smaller):
            style_kind = ParagraphStyle.QUOTE

    doc.blocks.append(
        ParagraphBlock(
            id=doc.next_block_id("p"),
            page=block.page,
            bbox=bbox,
            column=block.column,
            text=text,
            style=style_kind,
            provenance=_provenance_of(block, inferred=False),
        )
    )
    return "paragraph"


def _emit_footnotes(doc, block: RawBlock, dehyphenate: bool) -> None:
    """Uma nota por marcador encontrado; sem marcador, o bloco inteiro vira nota."""
    current_marker: str | None = None
    current: list[str] = []
    emitted = 0

    def flush() -> None:
        nonlocal current, current_marker, emitted
        if not current:
            return
        doc.blocks.append(
            FootnoteBlock(
                id=doc.next_block_id("n"),
                page=block.page,
                bbox=block.bbox,
                column=block.column,
                marker=current_marker or "*",
                text=join_lines(current, dehyphenate),
                provenance=_provenance_of(block),
            )
        )
        emitted += 1
        current = []
        current_marker = None

    for line in block.lines:
        text = line.stripped
        if not text:
            continue
        m = FOOTNOTE_MARKER.match(text)
        if m:
            flush()
            current_marker = m.group(1).strip("[].)")
            current = [text[m.end():].strip()]
        else:
            current.append(text)

    flush()

    if emitted == 0 and block.text.strip():
        doc.blocks.append(
            FootnoteBlock(
                id=doc.next_block_id("n"),
                page=block.page,
                bbox=block.bbox,
                column=block.column,
                marker="*",
                text=join_lines([ln.stripped for ln in block.lines if ln.stripped], dehyphenate),
                provenance=_provenance_of(block),
            )
        )
