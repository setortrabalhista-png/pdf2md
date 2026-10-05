"""Extração de imagens preservando a qualidade original.

Três casos precisam de tratamento distinto:

1. **Imagem embutida normal** — os bytes originais são copiados do PDF sem
   recompressão. Um JPEG sai JPEG, com o mesmo peso e a mesma qualidade.
2. **Imagem com máscara de transparência** — os bytes crus perderiam o alfa, e
   um logo com fundo transparente viraria um retângulo preto; nesse caso
   compomos um PNG.
3. **Scan fatiado** — muitos geradores cortam uma página digitalizada em dezenas
   de faixas horizontais. Extraí-las uma a uma produz um Markdown com 40
   imagens de 20 pixels de altura. Faixas contíguas são recompostas numa única
   imagem, na densidade original.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.model.geometry import BBox

log = logging.getLogger("pdf2md.images")


@dataclass
class ExtractedImage:
    path: Path
    bbox: BBox
    width: int
    height: int
    xref: int | None
    is_vector: bool = False
    recomposed_from: int = 0     # nº de faixas fundidas (0 = imagem única)
    note: str | None = None


def _placement_rects(page: pymupdf.Page, xref: int) -> list[BBox]:
    try:
        return [BBox.from_tuple(tuple(r)) for r in page.get_image_rects(xref)]
    except Exception:  # noqa: BLE001
        return []


def _save_original(pdf: pymupdf.Document, xref: int, dest_stem: Path) -> tuple[Path, int, int] | None:
    """Grava os bytes originais da imagem. Devolve (caminho, largura, altura)."""
    try:
        info = pdf.extract_image(xref)
    except Exception as exc:  # noqa: BLE001
        log.debug("xref %s: extract_image falhou (%s)", xref, exc)
        return None

    data = info.get("image")
    if not data:
        return None

    ext = (info.get("ext") or "png").lower()
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)

    # Máscara de transparência presente: os bytes crus não a carregam.
    if info.get("smask"):
        return _save_composited(pdf, xref, dest_stem)

    path = dest_stem.with_suffix(f".{ext}")
    path.write_bytes(data)
    return path, width, height


def _save_composited(pdf: pymupdf.Document, xref: int, dest_stem: Path) -> tuple[Path, int, int] | None:
    """Compõe imagem + máscara alfa num PNG."""
    try:
        pix = pymupdf.Pixmap(pdf, xref)
        if pix.colorspace and pix.colorspace.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        path = dest_stem.with_suffix(".png")
        pix.save(str(path))
        return path, pix.width, pix.height
    except Exception as exc:  # noqa: BLE001
        log.debug("xref %s: composição falhou (%s)", xref, exc)
        return None


def _group_slices(
    entries: list[tuple[int, BBox, int, int]], tol: float = 2.0
) -> list[list[tuple[int, BBox, int, int]]]:
    """Agrupa faixas horizontais contíguas de mesma largura.

    Cada entrada é (xref, caixa, largura_px, altura_px).
    """
    groups: list[list[tuple[int, BBox, int, int]]] = []
    remaining = sorted(entries, key=lambda e: (round(e[1].x0, 1), e[1].y0))

    for entry in remaining:
        placed = False
        for group in groups:
            last = group[-1]
            same_column = (
                abs(entry[1].x0 - last[1].x0) <= tol
                and abs(entry[1].x1 - last[1].x1) <= tol
            )
            contiguous = abs(entry[1].y0 - last[1].y1) <= tol + 1.0
            if same_column and contiguous:
                group.append(entry)
                placed = True
                break
        if not placed:
            groups.append([entry])

    return groups


def _render_region(
    page: pymupdf.Page, box: BBox, target_px_width: int, dest_stem: Path, max_dpi: int = 600
) -> tuple[Path, int, int] | None:
    """Rasteriza uma região da página na densidade original do conteúdo."""
    width_pt = max(1.0, box.width)
    dpi = min(max_dpi, max(150, round(target_px_width / (width_pt / 72.0))))
    try:
        pix = page.get_pixmap(
            clip=pymupdf.Rect(*box.as_tuple()),
            dpi=dpi,
            alpha=False,
        )
        path = dest_stem.with_suffix(".png")
        pix.save(str(path))
        return path, pix.width, pix.height
    except Exception as exc:  # noqa: BLE001
        log.debug("falha ao rasterizar região %s: %s", box.as_tuple(), exc)
        return None


def extract_page_images(
    pdf: pymupdf.Document,
    page: pymupdf.Page,
    page_number: int,
    assets_dir: Path,
    *,
    min_width: int,
    min_height: int,
    min_area_px: int,
) -> list[ExtractedImage]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    results: list[ExtractedImage] = []

    entries: list[tuple[int, BBox, int, int]] = []
    try:
        raw_images = page.get_images(full=True)
    except Exception:  # noqa: BLE001
        raw_images = []

    for item in raw_images:
        xref = item[0]
        px_w, px_h = int(item[2] or 0), int(item[3] or 0)
        for rect in _placement_rects(page, xref):
            if rect.width <= 1 or rect.height <= 1:
                continue
            entries.append((xref, rect, px_w, px_h))

    if not entries:
        return results

    counter = 0
    for group in _group_slices(entries):
        counter += 1
        stem = assets_dir / f"p{page_number:03d}_img{counter:03d}"

        if len(group) > 1:
            # Scan fatiado: recompõe a região inteira de uma vez.
            union = group[0][1]
            for _x, box, _w, _h in group[1:]:
                union = union.union(box)
            target_width = max(w for _x, _b, w, _h in group)
            rendered = _render_region(page, union, target_width, stem)
            if rendered is None:
                continue
            path, w, h = rendered
            if w < min_width or h < min_height or w * h < min_area_px:
                path.unlink(missing_ok=True)
                continue
            results.append(
                ExtractedImage(
                    path=path,
                    bbox=union,
                    width=w,
                    height=h,
                    xref=None,
                    recomposed_from=len(group),
                    note=f"recomposta a partir de {len(group)} faixas",
                )
            )
            continue

        xref, box, px_w, px_h = group[0]
        if px_w and px_h and (px_w < min_width or px_h < min_height or px_w * px_h < min_area_px):
            continue

        saved = _save_original(pdf, xref, stem)
        if saved is None:
            rendered = _render_region(page, box, px_w or int(box.width * 4), stem)
            if rendered is None:
                continue
            path, w, h = rendered
            results.append(
                ExtractedImage(
                    path=path, bbox=box, width=w, height=h, xref=xref,
                    note="rasterizada: os bytes originais não puderam ser lidos",
                )
            )
            continue

        path, w, h = saved
        if w and h and (w < min_width or h < min_height or w * h < min_area_px):
            path.unlink(missing_ok=True)
            continue

        results.append(ExtractedImage(path=path, bbox=box, width=w, height=h, xref=xref))

    return results


def extract_vector_figures(
    page: pymupdf.Page,
    page_number: int,
    assets_dir: Path,
    *,
    exclude: list[BBox],
    dpi: int,
    min_drawings: int,
    start_index: int = 1,
) -> list[ExtractedImage]:
    """Rasteriza aglomerados de desenho vetorial — gráficos, organogramas, brasões.

    Sem isto, um gráfico de laudo pericial simplesmente desaparece do Markdown:
    não é imagem embutida, é vetor.
    """
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return []

    boxes: list[BBox] = []
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        box = BBox.from_tuple((rect.x0, rect.y0, rect.x1, rect.y1))
        if box.width < 4 or box.height < 4:
            continue
        # Réguas de tabela e filetes não são figura.
        if box.width < 2 or box.height < 2:
            continue
        boxes.append(box)

    if len(boxes) < min_drawings:
        return []

    clusters: list[list[BBox]] = []
    for box in sorted(boxes, key=lambda b: (b.y0, b.x0)):
        for cluster in clusters:
            envelope = cluster[0]
            for c in cluster[1:]:
                envelope = envelope.union(c)
            grown = BBox(
                x0=envelope.x0 - 12, y0=envelope.y0 - 12,
                x1=envelope.x1 + 12, y1=envelope.y1 + 12,
            )
            if box.intersection_area(grown) > 0:
                cluster.append(box)
                break
        else:
            clusters.append([box])

    page_area = abs(page.rect.width * page.rect.height) or 1.0
    results: list[ExtractedImage] = []
    index = start_index

    for cluster in clusters:
        if len(cluster) < min_drawings:
            continue
        envelope = cluster[0]
        for c in cluster[1:]:
            envelope = envelope.union(c)

        if envelope.area / page_area > 0.9:
            continue  # moldura da página, não figura
        if envelope.width < 40 or envelope.height < 40:
            continue
        if any(envelope.contained_in(e, 0.8) for e in exclude):
            continue

        stem = assets_dir / f"p{page_number:03d}_fig{index:03d}"
        try:
            pix = page.get_pixmap(clip=pymupdf.Rect(*envelope.as_tuple()), dpi=dpi, alpha=False)
            path = stem.with_suffix(".png")
            pix.save(str(path))
        except Exception as exc:  # noqa: BLE001
            log.debug("figura vetorial não rasterizada: %s", exc)
            continue

        results.append(
            ExtractedImage(
                path=path,
                bbox=envelope,
                width=pix.width,
                height=pix.height,
                xref=None,
                is_vector=True,
                note=f"figura vetorial rasterizada a {dpi} dpi",
            )
        )
        index += 1

    return results
