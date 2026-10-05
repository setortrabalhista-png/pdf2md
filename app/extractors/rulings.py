"""Análise das réguas vetoriais da página.

É o motor primário de detecção de tabela. A ideia: as bordas de uma tabela são
literalmente desenhadas no PDF como linhas ou retângulos finos. Se as
recuperarmos e descobrirmos onde formam uma malha fechada, temos a tabela e as
suas células sem depender de Ghostscript, OpenCV ou Java.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.model.geometry import BBox


@dataclass
class Segment:
    """Segmento já normalizado: `pos` é y (horizontal) ou x (vertical)."""

    pos: float
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    def overlaps(self, other: Segment, tol: float = 0.0) -> bool:
        return not (self.end < other.start - tol or other.end < self.start - tol)


def merge_segments(
    raw: list[tuple[float, float, float]],
    snap: float,
    min_length: float,
) -> list[Segment]:
    """Agrupa segmentos colineares e funde os que se tocam.

    Uma borda de tabela costuma vir picotada em vários traços — sem esta fusão,
    a malha nunca fecha.
    """
    segments = [
        Segment(pos=p, start=min(a, b), end=max(a, b))
        for p, a, b in raw
        if abs(b - a) >= min_length
    ]
    if not segments:
        return []

    segments.sort(key=lambda s: (s.pos, s.start))

    merged: list[Segment] = []
    bucket: list[Segment] = [segments[0]]

    def flush(group: list[Segment]) -> None:
        group.sort(key=lambda s: s.start)
        current = Segment(
            pos=sum(s.pos for s in group) / len(group),
            start=group[0].start,
            end=group[0].end,
        )
        for s in group[1:]:
            if s.start <= current.end + snap:
                current.end = max(current.end, s.end)
            else:
                merged.append(current)
                current = Segment(pos=current.pos, start=s.start, end=s.end)
        merged.append(current)

    for s in segments[1:]:
        if abs(s.pos - bucket[-1].pos) <= snap:
            bucket.append(s)
        else:
            flush(bucket)
            bucket = [s]
    flush(bucket)

    return [s for s in merged if s.length >= min_length]


def _intersects(h: Segment, v: Segment, tol: float) -> bool:
    """A horizontal `h` cruza a vertical `v`?"""
    return (
        h.start - tol <= v.pos <= h.end + tol
        and v.start - tol <= h.pos <= v.end + tol
    )


@dataclass
class Grid:
    """Malha fechada de réguas — candidata a tabela."""

    bbox: BBox
    rows: list[float] = field(default_factory=list)   # coordenadas y das bordas
    cols: list[float] = field(default_factory=list)   # coordenadas x das bordas
    h_segments: list[Segment] = field(default_factory=list)
    v_segments: list[Segment] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return max(0, len(self.rows) - 1)

    @property
    def n_cols(self) -> int:
        return max(0, len(self.cols) - 1)

    @property
    def closed_ratio(self) -> float:
        """Quão completa é a malha — usado como base da confiança da tabela.

        Uma tabela totalmente riscada tem todas as intersecções presentes; uma
        com apenas a borda externa tem poucas.
        """
        expected = len(self.rows) * len(self.cols)
        if expected == 0:
            return 0.0
        found = sum(
            1
            for h in self.h_segments
            for v in self.v_segments
            if _intersects(h, v, tol=2.0)
        )
        return min(1.0, found / expected)


def find_grids(
    horizontals: list[tuple[float, float, float]],
    verticals: list[tuple[float, float, float]],
    *,
    snap: float = 3.0,
    min_length: float = 12.0,
    min_rows: int = 2,
    min_cols: int = 2,
) -> list[Grid]:
    """Encontra malhas fechadas: componentes conexos de réguas que se cruzam."""
    h_segs = merge_segments(horizontals, snap, min_length)
    v_segs = merge_segments(verticals, snap, min_length)
    if len(h_segs) < min_rows or len(v_segs) < min_cols:
        return []

    # Grafo bipartido horizontal <-> vertical, ligado por intersecção.
    n_h = len(h_segs)
    parent = list(range(n_h + len(v_segs)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, h in enumerate(h_segs):
        for j, v in enumerate(v_segs):
            if _intersects(h, v, snap):
                union(i, n_h + j)

    components: dict[int, tuple[list[Segment], list[Segment]]] = {}
    for i, h in enumerate(h_segs):
        components.setdefault(find(i), ([], []))[0].append(h)
    for j, v in enumerate(v_segs):
        components.setdefault(find(n_h + j), ([], []))[1].append(v)

    grids: list[Grid] = []
    for hs, vs in components.values():
        if len(hs) < min_rows or len(vs) < min_cols:
            continue

        rows = _cluster(sorted(h.pos for h in hs), snap)
        cols = _cluster(sorted(v.pos for v in vs), snap)
        if len(rows) < min_rows or len(cols) < min_cols:
            continue

        bbox = BBox(x0=min(cols), y0=min(rows), x1=max(cols), y1=max(rows))
        if bbox.width < min_length or bbox.height < min_length:
            continue

        grids.append(Grid(bbox=bbox, rows=rows, cols=cols, h_segments=hs, v_segments=vs))

    grids.sort(key=lambda g: (g.bbox.y0, g.bbox.x0))
    return grids


def _cluster(values: list[float], snap: float) -> list[float]:
    """Colapsa coordenadas próximas numa só — réguas duplas viram uma borda."""
    if not values:
        return []
    out = [values[0]]
    bucket = [values[0]]
    for v in values[1:]:
        if v - bucket[-1] <= snap:
            bucket.append(v)
            out[-1] = sum(bucket) / len(bucket)
        else:
            bucket = [v]
            out.append(v)
    return out


def find_footnote_separator(
    horizontals: list[tuple[float, float, float]],
    page_width: float,
    page_height: float,
    *,
    min_y_ratio: float = 0.62,
    max_width_ratio: float = 0.55,
) -> float | None:
    """Localiza o filete curto que separa as notas de rodapé do corpo.

    Convenção tipográfica antiga e ainda dominante em peça jurídica: um traço
    de cerca de um terço da largura, na metade inferior da página.
    """
    best: float | None = None
    for y, x0, x1 in horizontals:
        width = abs(x1 - x0)
        if y < page_height * min_y_ratio:
            continue
        if width > page_width * max_width_ratio:
            continue
        if width < page_width * 0.12:
            continue
        if best is None or y < best:
            best = y
    return best
