"""Adaptadores opcionais: Camelot e Tabula.

Nenhum dos dois é necessário para o sistema funcionar — o motor de réguas cobre
o caso com bordas e o de alinhamento cobre o caso sem bordas. Estes entram
apenas como segunda opinião quando a confiança do motor primário fica baixa, e
somente se as dependências de sistema existirem (Ghostscript para o Camelot,
Java para o Tabula).

Todo import é tardio e toda falha é silenciosa por desenho: um fallback ausente
não pode derrubar a conversão.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.extractors.tables_lines import TableExtraction
from app.model.blocks import Alignment, TableCell
from app.model.geometry import BBox

log = logging.getLogger("pdf2md.tables.fallback")


def camelot_available() -> bool:
    try:
        import camelot  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    # Camelot no modo lattice depende do Ghostscript.
    return bool(
        shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    )


def tabula_available() -> bool:
    try:
        import tabula  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(shutil.which("java"))


def _rows_to_extraction(
    rows: list[list[str]],
    engine: str,
    bbox: BBox,
    accuracy: float | None,
) -> TableExtraction | None:
    cleaned = [[(c or "").strip() for c in row] for row in rows]
    cleaned = [row for row in cleaned if any(row)]
    if len(cleaned) < 2:
        return None

    width = max(len(r) for r in cleaned)
    if width < 2:
        return None
    for row in cleaned:
        row.extend([""] * (width - len(row)))

    filled = sum(1 for row in cleaned for c in row if c)
    fill_ratio = filled / (len(cleaned) * width)

    confidence = (accuracy / 100.0) if accuracy is not None else 0.7
    confidence = round(min(0.95, 0.6 * confidence + 0.4 * fill_ratio), 4)

    return TableExtraction(
        rows=[[TableCell(text=c) for c in row] for row in cleaned],
        header_rows=0,
        alignments=[Alignment.LEFT] * width,
        confidence=confidence,
        engine=engine,
        bbox=bbox,
        has_merged=False,
        notes=[f"extraída pelo motor de reserva {engine}"],
    )


def try_camelot(pdf_path: Path, page: int, region: BBox | None = None) -> list[TableExtraction]:
    if not camelot_available():
        return []
    try:
        import camelot

        kwargs = {"pages": str(page), "flavor": "lattice"}
        if region is not None:
            # Camelot usa origem no canto inferior esquerdo.
            kwargs["table_areas"] = [
                f"{region.x0},{region.y1},{region.x1},{region.y0}"
            ]
        tables = camelot.read_pdf(str(pdf_path), **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.debug("camelot indisponível ou falhou: %s", exc)
        return []

    out: list[TableExtraction] = []
    for t in tables:
        try:
            rows = t.df.values.tolist()
            accuracy = float(getattr(t, "accuracy", 0.0)) or None
            bbox = region or BBox(x0=0, y0=0, x1=0, y1=0)
            ext = _rows_to_extraction(rows, "camelot", bbox, accuracy)
            if ext:
                out.append(ext)
        except Exception:  # noqa: BLE001
            continue
    return out


def try_tabula(pdf_path: Path, page: int, region: BBox | None = None) -> list[TableExtraction]:
    if not tabula_available():
        return []
    try:
        import tabula

        kwargs = {"pages": page, "multiple_tables": True, "silent": True}
        if region is not None:
            kwargs["area"] = [region.y0, region.x0, region.y1, region.x1]
        frames = tabula.read_pdf(str(pdf_path), **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.debug("tabula indisponível ou falhou: %s", exc)
        return []

    out: list[TableExtraction] = []
    for df in frames or []:
        try:
            header = [str(c) for c in df.columns]
            rows = [header, *df.astype(str).values.tolist()]
            bbox = region or BBox(x0=0, y0=0, x1=0, y1=0)
            ext = _rows_to_extraction(rows, "tabula", bbox, None)
            if ext:
                ext.header_rows = 1
                out.append(ext)
        except Exception:  # noqa: BLE001
            continue
    return out


def describe_availability() -> dict[str, bool]:
    return {"camelot": camelot_available(), "tabula": tabula_available()}
