"""Primitivas geométricas compartilhadas por todo o pipeline."""

from __future__ import annotations

from pydantic import BaseModel


class BBox(BaseModel):
    """Retângulo em coordenadas de página do PDF (origem no topo-esquerda)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def union(self, other: BBox) -> BBox:
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def intersection_area(self, other: BBox) -> float:
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        if dx <= 0 or dy <= 0:
            return 0.0
        return dx * dy

    def iou(self, other: BBox) -> float:
        inter = self.intersection_area(other)
        if inter <= 0:
            return 0.0
        return inter / (self.area + other.area - inter)

    def contained_in(self, other: BBox, tolerance: float = 0.85) -> bool:
        """Fração da própria área que cai dentro de `other`."""
        if self.area <= 0:
            return False
        return self.intersection_area(other) / self.area >= tolerance

    def vertical_overlap(self, other: BBox) -> float:
        """Fração de sobreposição vertical — usado para agrupar linhas."""
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        if dy <= 0:
            return 0.0
        return dy / min(self.height or 1.0, other.height or 1.0)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @classmethod
    def from_tuple(cls, t) -> BBox:
        return cls(x0=float(t[0]), y0=float(t[1]), x1=float(t[2]), y1=float(t[3]))
