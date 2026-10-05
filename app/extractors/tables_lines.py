"""Motor primário de tabelas: reconstrói células a partir da malha vetorial.

Funciona igual para página nativa e página de OCR, porque consome os spans já
normalizados do `PageLayout` — não o PDF. Isso é o que evita um caminho especial
para tabela dentro de documento digitalizado.

Detecta também células mescladas: se a régua vertical que deveria separar duas
colunas não existe naquela faixa de linhas, as células estão fundidas. O
Markdown não representa mescla, então o renderizador é avisado e marca a tabela
para conferência — melhor do que entregar uma tabela silenciosamente errada.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from app.extractors.rulings import Grid, Segment
from app.model.blocks import Alignment, TableCell
from app.model.geometry import BBox
from app.model.layout import PageLayout, TextSpan

log = logging.getLogger("pdf2md.tables.lines")


@dataclass
class TableExtraction:
    rows: list[list[TableCell]]
    header_rows: int
    alignments: list[Alignment]
    confidence: float
    engine: str
    bbox: BBox
    has_merged: bool
    notes: list[str]


def _spans_in(layout: PageLayout, box: BBox) -> list[TextSpan]:
    """Spans cujo centro cai dentro da caixa."""
    out = []
    for block in layout.blocks:
        if block.is_header or block.is_footer:
            continue
        if not block.bbox.intersection_area(box):
            continue
        for line in block.lines:
            for span in line.spans:
                if not span.text.strip():
                    continue
                if box.x0 <= span.bbox.cx <= box.x1 and box.y0 <= span.bbox.cy <= box.y1:
                    out.append(span)
    return out


def _join_spans(spans: list[TextSpan]) -> str:
    """Reagrupa spans em linhas visuais antes de concatenar.

    Célula com texto em duas linhas deve virar uma frase, não duas metades
    coladas sem espaço.
    """
    if not spans:
        return ""

    ordered = sorted(spans, key=lambda s: (round(s.bbox.cy, 1), s.bbox.x0))
    lines: list[list[TextSpan]] = [[ordered[0]]]
    for s in ordered[1:]:
        prev = lines[-1][-1]
        if s.bbox.vertical_overlap(prev.bbox) >= 0.45:
            lines[-1].append(s)
        else:
            lines.append([s])

    parts = []
    for line in lines:
        line.sort(key=lambda s: s.bbox.x0)
        text = "".join(s.text for s in line).strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _has_vertical_border(v_segments: list[Segment], x: float, y0: float, y1: float, tol: float) -> bool:
    """Existe régua vertical em `x` cobrindo a faixa [y0, y1]?"""
    span = y1 - y0
    if span <= 0:
        return True
    for v in v_segments:
        if abs(v.pos - x) > tol:
            continue
        covered = min(v.end, y1) - max(v.start, y0)
        if covered / span >= 0.6:
            return True
    return False


def _has_horizontal_border(h_segments: list[Segment], y: float, x0: float, x1: float, tol: float) -> bool:
    span = x1 - x0
    if span <= 0:
        return True
    for h in h_segments:
        if abs(h.pos - y) > tol:
            continue
        covered = min(h.end, x1) - max(h.start, x0)
        if covered / span >= 0.6:
            return True
    return False


def _detect_alignment(spans: list[TextSpan], cell: BBox) -> Alignment | None:
    if not spans or cell.width <= 0:
        return None
    left_gap = min(s.bbox.x0 for s in spans) - cell.x0
    right_gap = cell.x1 - max(s.bbox.x1 for s in spans)
    total = left_gap + right_gap
    if total <= 0:
        return Alignment.LEFT
    ratio = left_gap / total
    if ratio > 0.62:
        return Alignment.RIGHT
    if 0.38 <= ratio <= 0.62 and min(left_gap, right_gap) > cell.width * 0.08:
        return Alignment.CENTER
    return Alignment.LEFT


def _looks_numeric(text: str) -> bool:
    stripped = text.strip().replace(".", "").replace(",", "").replace("%", "")
    stripped = stripped.replace("R$", "").replace("-", "").replace(" ", "")
    return bool(stripped) and stripped.isdigit()


def extract_from_grid(grid: Grid, layout: PageLayout, snap: float = 3.0) -> TableExtraction | None:
    rows_y = grid.rows
    cols_x = grid.cols
    n_rows, n_cols = grid.n_rows, grid.n_cols
    if n_rows < 1 or n_cols < 1:
        return None

    notes: list[str] = []
    has_merged = False

    cells: list[list[TableCell]] = []
    alignment_votes: list[list[Alignment]] = [[] for _ in range(n_cols)]
    numeric_votes: list[int] = [0] * n_cols
    filled_votes: list[int] = [0] * n_cols
    empty_cells = 0

    # Posições já cobertas por uma célula mesclada anterior. Sem isto, uma
    # célula com colspan 2 seria lida duas vezes e o texto apareceria duplicado.
    covered: set[tuple[int, int]] = set()

    for i in range(n_rows):
        y0, y1 = rows_y[i], rows_y[i + 1]
        row: list[TableCell] = []
        for j in range(n_cols):
            if (i, j) in covered:
                # Continuação de uma mescla: célula vazia, sem repetir conteúdo.
                row.append(TableCell(text="", rowspan=0, colspan=0))
                continue

            x0, x1 = cols_x[j], cols_x[j + 1]
            cell_box = BBox(x0=x0, y0=y0, x1=x1, y1=y1)

            colspan = 1
            # A régua direita da célula está ausente ⇒ fundida com a seguinte.
            k = j
            while k + 1 < n_cols and not _has_vertical_border(
                grid.v_segments, cols_x[k + 1], y0, y1, snap
            ):
                colspan += 1
                k += 1
            if colspan > 1:
                cell_box = BBox(x0=x0, y0=y0, x1=cols_x[k + 1], y1=y1)

            rowspan = 1
            m = i
            while m + 1 < n_rows and not _has_horizontal_border(
                grid.h_segments, rows_y[m + 1], x0, cell_box.x1, snap
            ):
                rowspan += 1
                m += 1
            if rowspan > 1:
                cell_box = BBox(x0=cell_box.x0, y0=y0, x1=cell_box.x1, y1=rows_y[m + 1])

            if colspan > 1 or rowspan > 1:
                has_merged = True
                for di in range(rowspan):
                    for dj in range(colspan):
                        if di or dj:
                            covered.add((i + di, j + dj))

            spans = _spans_in(layout, cell_box)
            text = _join_spans(spans)
            if not text:
                empty_cells += 1
            else:
                filled_votes[j] += 1
                if _looks_numeric(text):
                    numeric_votes[j] += 1
                align = _detect_alignment(spans, cell_box)
                if align:
                    alignment_votes[j].append(align)

            row.append(TableCell(text=text, rowspan=rowspan, colspan=colspan))
        cells.append(row)

    total_cells = max(1, n_rows * n_cols - len(covered))
    fill_ratio = 1.0 - (empty_cells / total_cells)

    header_rows = _guess_header_rows(cells, layout)
    alignments = _resolve_alignments(alignment_votes, numeric_votes, filled_votes, n_cols)

    confidence = _score(
        closed_ratio=grid.closed_ratio,
        fill_ratio=fill_ratio,
        n_rows=n_rows,
        n_cols=n_cols,
        has_merged=has_merged,
        layout=layout,
        bbox=grid.bbox,
        notes=notes,
    )

    return TableExtraction(
        rows=cells,
        header_rows=header_rows,
        alignments=alignments,
        confidence=confidence,
        engine="lines",
        bbox=grid.bbox,
        has_merged=has_merged,
        notes=notes,
    )


def _resolve_alignments(
    votes: list[list[Alignment]],
    numeric_votes: list[int],
    filled_votes: list[int],
    n_cols: int,
) -> list[Alignment]:
    out: list[Alignment] = []
    for j in range(n_cols):
        # Coluna majoritariamente numérica alinha à direita — convenção
        # contábil que o alinhamento visual nem sempre revela.
        if filled_votes[j] and numeric_votes[j] / filled_votes[j] >= 0.7:
            out.append(Alignment.RIGHT)
            continue
        column_votes = votes[j]
        if not column_votes:
            out.append(Alignment.LEFT)
            continue
        out.append(max(set(column_votes), key=column_votes.count))
    return out


def _guess_header_rows(cells: list[list[TableCell]], layout: PageLayout) -> int:
    """Primeira linha é cabeçalho se estiver em negrito ou destoar do corpo."""
    if len(cells) < 2:
        return 0

    first = cells[0]
    if not any(c.text.strip() for c in first):
        return 0

    # Sinal 1: nenhuma célula numérica na primeira linha, mas há nas seguintes.
    first_numeric = sum(1 for c in first if _looks_numeric(c.text))
    rest_numeric = sum(
        1 for row in cells[1:] for c in row if _looks_numeric(c.text)
    )
    if first_numeric == 0 and rest_numeric > 0:
        return 1

    # Sinal 2: negrito predominante na faixa vertical da primeira linha.
    bold_chars = 0
    total_chars = 0
    for block in layout.blocks:
        for line in block.lines:
            for span in line.spans:
                txt = span.text.strip()
                if not txt:
                    continue
                if any(txt in c.text for c in first):
                    total_chars += len(txt)
                    if span.bold:
                        bold_chars += len(txt)
    if total_chars and bold_chars / total_chars >= 0.6:
        return 1

    return 0


def _score(
    *,
    closed_ratio: float,
    fill_ratio: float,
    n_rows: int,
    n_cols: int,
    has_merged: bool,
    layout: PageLayout,
    bbox: BBox,
    notes: list[str],
) -> float:
    """Confiança 0-1 combinando fechamento da malha, preenchimento e OCR."""
    score = 0.55 * closed_ratio + 0.30 * fill_ratio

    # Uma tabela plausível tem pelo menos duas linhas de dados.
    if n_rows >= 2 and n_cols >= 2:
        score += 0.15
    else:
        notes.append("tabela com dimensões mínimas")

    if fill_ratio < 0.5:
        notes.append(f"mais da metade das células vazias ({(1-fill_ratio):.0%})")

    if closed_ratio < 0.6:
        notes.append("malha de bordas incompleta")

    if has_merged:
        # Markdown não representa mescla; o conteúdo é preservado, o formato não.
        notes.append("contém células mescladas, que o Markdown não representa")
        score *= 0.80

    # Confiança de OCR da região arrasta a confiança da tabela.
    ocr_confidences = [
        span.confidence
        for block in layout.blocks
        if block.is_ocr and block.bbox.intersection_area(bbox) > 0
        for line in block.lines
        for span in line.spans
        if span.text.strip()
    ]
    if ocr_confidences:
        mean_ocr = statistics.fmean(ocr_confidences)
        score *= 0.5 + 0.5 * mean_ocr
        if mean_ocr < 0.75:
            notes.append(f"texto vindo de OCR com confiança média de {mean_ocr:.0%}")

    return round(max(0.0, min(1.0, score)), 4)
