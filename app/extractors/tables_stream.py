"""Motor secundário: tabelas sem bordas desenhadas.

Tabela de verbas rescisórias, quadro de horários e demonstrativo de descontos
costumam vir sem uma única linha desenhada — só alinhamento por espaço. Este
detector reconstrói as colunas a partir dos vãos verticais.

O risco aqui é o falso positivo: parágrafo justificado também tem vãos largos.
A defesa é exigir **estabilidade**: um vão só vira coluna se aparecer
aproximadamente na mesma abscissa na maioria das linhas do grupo. Texto corrido
não passa nesse teste; tabela passa.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from app.extractors.tables_lines import TableExtraction, _looks_numeric
from app.model.blocks import Alignment, TableCell
from app.model.geometry import BBox
from app.model.layout import PageLayout, Word

log = logging.getLogger("pdf2md.tables.stream")


@dataclass
class VisualLine:
    words: list[Word]
    bbox: BBox

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_into_lines(words: list[Word], overlap: float = 0.5) -> list[VisualLine]:
    if not words:
        return []

    ordered = sorted(words, key=lambda w: (round(w.bbox.cy, 1), w.bbox.x0))
    lines: list[list[Word]] = [[ordered[0]]]

    for w in ordered[1:]:
        current = lines[-1]
        reference = current[-1]
        if w.bbox.vertical_overlap(reference.bbox) >= overlap:
            current.append(w)
        else:
            lines.append([w])

    out: list[VisualLine] = []
    for group in lines:
        group.sort(key=lambda w: w.bbox.x0)
        box = group[0].bbox
        for w in group[1:]:
            box = box.union(w.bbox)
        out.append(VisualLine(words=group, bbox=box))
    return out


def _median_space(lines: list[VisualLine]) -> float:
    gaps: list[float] = []
    for line in lines:
        for a, b in zip(line.words, line.words[1:], strict=False):
            gap = b.bbox.x0 - a.bbox.x1
            if 0 < gap < 40:
                gaps.append(gap)
    return statistics.median(gaps) if gaps else 2.0


def _line_gaps(line: VisualLine, threshold: float) -> list[float]:
    """Abscissas dos vãos significativos dentro da linha."""
    gaps = []
    for a, b in zip(line.words, line.words[1:], strict=False):
        gap = b.bbox.x0 - a.bbox.x1
        if gap >= threshold:
            gaps.append((a.bbox.x1 + b.bbox.x0) / 2)
    return gaps


def _cluster_positions(
    positions: list[float], tolerance: float
) -> list[tuple[float, int]]:
    """Agrupa abscissas próximas. Devolve (posição média, quantidade)."""
    if not positions:
        return []
    ordered = sorted(positions)
    clusters: list[list[float]] = [[ordered[0]]]
    for p in ordered[1:]:
        if p - clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(statistics.fmean(c), len(c)) for c in clusters]


def detect(
    layout: PageLayout,
    *,
    exclude: list[BBox] | None = None,
    min_lines: int = 3,
    min_cols: int = 2,
    gap_factor: float = 3.0,
    min_gap_points: float = 9.0,
    stability: float = 0.62,
) -> list[TableExtraction]:
    """Encontra tabelas sem bordas na página. Conservador por construção."""
    exclude = exclude or []

    words = [
        w
        for w in layout.words
        if w.text.strip() and not any(w.bbox.contained_in(e, 0.5) for e in exclude)
    ]
    if len(words) < min_lines * min_cols:
        return []

    # Descarta as faixas de cabeçalho e rodapé já identificadas.
    banned = [b.bbox for b in layout.blocks if b.is_header or b.is_footer]
    if banned:
        words = [w for w in words if not any(w.bbox.contained_in(b, 0.6) for b in banned)]

    lines = group_into_lines(words)
    if len(lines) < min_lines:
        return []

    median_space = _median_space(lines)
    threshold = max(min_gap_points, gap_factor * median_space)
    tolerance = max(6.0, median_space * 3)

    # Marca as linhas com vãos suficientes para serem candidatas.
    candidates: list[tuple[VisualLine, list[float]]] = []
    for line in lines:
        gaps = _line_gaps(line, threshold)
        candidates.append((line, gaps))

    # Agrupa linhas candidatas consecutivas.
    groups: list[list[tuple[VisualLine, list[float]]]] = []
    current: list[tuple[VisualLine, list[float]]] = []
    previous_bottom = None
    previous_height = None

    for line, gaps in candidates:
        is_candidate = len(gaps) >= (min_cols - 1)
        contiguous = True
        if previous_bottom is not None and previous_height:
            contiguous = (line.bbox.y0 - previous_bottom) <= previous_height * 2.2

        if is_candidate and contiguous:
            current.append((line, gaps))
        else:
            if len(current) >= min_lines:
                groups.append(current)
            current = [(line, gaps)] if is_candidate else []

        previous_bottom = line.bbox.y1
        previous_height = line.bbox.height or previous_height

    if len(current) >= min_lines:
        groups.append(current)

    results: list[TableExtraction] = []
    for group in groups:
        extraction = _build_table(group, tolerance, stability, min_cols, layout)
        if extraction is not None:
            results.append(extraction)

    return results


def _build_table(
    group: list[tuple[VisualLine, list[float]]],
    tolerance: float,
    stability: float,
    min_cols: int,
    layout: PageLayout,
) -> TableExtraction | None:
    n_lines = len(group)
    all_gaps = [g for _line, gaps in group for g in gaps]
    clusters = _cluster_positions(all_gaps, tolerance)

    # Só sobrevive o vão presente na maioria das linhas — o filtro que separa
    # tabela de parágrafo justificado.
    separators = [pos for pos, count in clusters if count / n_lines >= stability]
    separators.sort()

    if len(separators) < (min_cols - 1):
        return None

    n_cols = len(separators) + 1
    left = min(line.bbox.x0 for line, _ in group)
    right = max(line.bbox.x1 for line, _ in group)
    top = min(line.bbox.y0 for line, _ in group)
    bottom = max(line.bbox.y1 for line, _ in group)
    bounds = [left - 1.0, *separators, right + 1.0]

    rows: list[list[TableCell]] = []
    empty_cells = 0
    numeric_votes = [0] * n_cols
    filled_votes = [0] * n_cols
    misplaced = 0

    for line, _gaps in group:
        buckets: list[list[Word]] = [[] for _ in range(n_cols)]
        for w in line.words:
            placed = False
            for j in range(n_cols):
                if bounds[j] <= w.bbox.cx < bounds[j + 1]:
                    buckets[j].append(w)
                    placed = True
                    break
            if not placed:
                misplaced += 1
                buckets[-1].append(w)

        row: list[TableCell] = []
        for j, bucket in enumerate(buckets):
            bucket.sort(key=lambda w: w.bbox.x0)
            text = " ".join(w.text for w in bucket).strip()
            if text:
                filled_votes[j] += 1
                if _looks_numeric(text):
                    numeric_votes[j] += 1
            else:
                empty_cells += 1
            row.append(TableCell(text=text))
        rows.append(row)

    total_cells = n_lines * n_cols or 1
    fill_ratio = 1.0 - empty_cells / total_cells

    # Uma "tabela" quase vazia é quase sempre texto mal interpretado.
    if fill_ratio < 0.45:
        return None

    notes: list[str] = ["tabela sem bordas: colunas inferidas por alinhamento"]

    alignments: list[Alignment] = []
    for j in range(n_cols):
        if filled_votes[j] and numeric_votes[j] / filled_votes[j] >= 0.7:
            alignments.append(Alignment.RIGHT)
        else:
            alignments.append(Alignment.LEFT)

    header_rows = 1 if _first_row_looks_like_header(rows) else 0

    # Estabilidade média dos separadores é o principal componente da confiança.
    stabilities = [
        count / n_lines
        for pos, count in clusters
        if pos in separators or any(abs(pos - s) < 0.01 for s in separators)
    ]
    mean_stability = statistics.fmean(stabilities) if stabilities else 0.0

    confidence = 0.45 * mean_stability + 0.30 * fill_ratio + 0.10
    if n_lines >= 4:
        confidence += 0.05
    if misplaced:
        confidence -= min(0.15, misplaced * 0.02)
        notes.append(f"{misplaced} palavra(s) fora das colunas inferidas")

    ocr_words = [w for line, _ in group for w in line.words if w.from_ocr]
    if ocr_words:
        mean_ocr = statistics.fmean(w.confidence for w in ocr_words)
        confidence *= 0.5 + 0.5 * mean_ocr
        if mean_ocr < 0.75:
            notes.append(f"texto de OCR com confiança média de {mean_ocr:.0%}")

    # Teto deliberado: sem bordas desenhadas nunca temos certeza.
    confidence = round(max(0.0, min(0.88, confidence)), 4)

    return TableExtraction(
        rows=rows,
        header_rows=header_rows,
        alignments=alignments,
        confidence=confidence,
        engine="stream",
        bbox=BBox(x0=left, y0=top, x1=right, y1=bottom),
        has_merged=False,
        notes=notes,
    )


def _first_row_looks_like_header(rows: list[list[TableCell]]) -> bool:
    if len(rows) < 2:
        return False
    first_numeric = sum(1 for c in rows[0] if _looks_numeric(c.text))
    rest_numeric = sum(1 for row in rows[1:] for c in row if _looks_numeric(c.text))
    return first_numeric == 0 and rest_numeric > 0
