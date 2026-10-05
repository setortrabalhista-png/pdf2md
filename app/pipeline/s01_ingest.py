"""Estágio 01 — Ingestão e triagem.

Abre o PDF, coleta metadados e decide, **página a página**, qual rota de
extração usar. A decisão por página (e não por documento) é o que faz o sistema
funcionar em processo judicial: petição nativa do PJe seguida de anexos
escaneados é o caso comum, não a exceção.
"""

from __future__ import annotations

import hashlib
import logging

import pymupdf

from app.core.errors import EncryptedPdfError, InvalidPdfError
from app.model.document import DocumentMeta, DocumentModel, PageInfo, PageRoute
from app.model.provenance import Severity
from app.pipeline.options import OcrMode
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s01")


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_metrics(page: pymupdf.Page) -> dict:
    """Métricas baratas que decidem a rota da página."""
    page_area = abs(page.rect.width * page.rect.height) or 1.0

    text_area = 0.0
    image_area = 0.0
    image_count = 0
    char_count = 0

    try:
        info = page.get_text("dict")
    except Exception:  # noqa: BLE001 — página corrompida não derruba o documento
        info = {"blocks": []}

    for block in info.get("blocks", []):
        bbox = block.get("bbox") or (0, 0, 0, 0)
        area = abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        if block.get("type") == 0:
            block_chars = sum(
                len(span.get("text", ""))
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            )
            char_count += block_chars
            if block_chars:
                text_area += area
        elif block.get("type") == 1:
            image_count += 1
            image_area += area

    # Imagens desenhadas via XObject podem escapar do get_text("dict")
    try:
        xref_images = page.get_images(full=True)
        image_count = max(image_count, len(xref_images))
    except Exception:  # noqa: BLE001
        pass

    vector_lines = 0
    try:
        for drawing in page.get_drawings():
            for item in drawing.get("items", []):
                if item[0] in ("l", "re"):
                    vector_lines += 1
    except Exception:  # noqa: BLE001
        pass

    # Texto "visível": desconta espaços em branco, que inflam a contagem em
    # PDFs que carregam camada de texto vazia.
    visible_chars = len(page.get_text("text").strip())

    return {
        "native_char_count": visible_chars,
        "raw_char_count": char_count,
        "text_area_ratio": min(1.0, text_area / page_area),
        "image_count": image_count,
        "image_area_ratio": min(1.0, image_area / page_area),
        "vector_line_count": vector_lines,
    }


def _decide_route(m: dict, ctx: PipelineContext) -> tuple[PageRoute, bool]:
    """Devolve (rota, precisa_de_ocr)."""
    s = ctx.settings

    if ctx.options.ocr_mode == OcrMode.FORCE:
        return PageRoute.OCR, True

    has_text = (
        m["native_char_count"] >= s.min_chars_per_page
        and m["text_area_ratio"] >= s.min_text_area_ratio
    )
    image_dominant = m["image_area_ratio"] >= s.image_dominance_ratio
    # "Tinta na página": há algo desenhado, ainda que sem camada de texto.
    has_ink = m["image_count"] > 0 or m["vector_line_count"] > 4

    if ctx.options.ocr_mode == OcrMode.NEVER:
        if has_text:
            return PageRoute.NATIVE, False
        return PageRoute.EMPTY, has_ink

    if not has_text:
        # Sem texto útil: se há tinta na página, é scan; senão, está em branco.
        if has_ink:
            return PageRoute.OCR, True
        return PageRoute.EMPTY, False

    # Tem texto E uma imagem grande: provável anexo digitalizado colado dentro
    # de uma peça nativa. Extrai o nativo e roda OCR só na região da imagem.
    if image_dominant:
        return PageRoute.HYBRID, True

    return PageRoute.NATIVE, False


def run(ctx: PipelineContext) -> None:
    path = ctx.pdf_path
    if not path.exists():
        raise InvalidPdfError(f"arquivo não encontrado: {path}")

    try:
        pdf = pymupdf.open(path)
    except Exception as exc:
        raise InvalidPdfError(f"não foi possível abrir o PDF: {exc}") from exc

    if pdf.needs_pass:
        pdf.close()
        raise EncryptedPdfError(
            "PDF protegido por senha. Remova a proteção antes de converter."
        )

    if pdf.page_count == 0:
        pdf.close()
        raise InvalidPdfError("o PDF não contém páginas.")

    ctx.pdf = pdf
    md = pdf.metadata or {}

    doc = DocumentModel(
        meta=DocumentMeta(
            source_filename=path.name,
            source_sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            page_count=pdf.page_count,
            is_encrypted=bool(pdf.is_encrypted),
            pdf_title=(md.get("title") or None),
            pdf_author=(md.get("author") or None),
            pdf_producer=(md.get("producer") or None),
            pdf_creation_date=(md.get("creationDate") or None),
        )
    )
    ctx.doc = doc

    allowed = set(ctx.options.page_range) if ctx.options.page_range else None

    for index in range(pdf.page_count):
        number = index + 1
        page = pdf[index]
        metrics = _page_metrics(page)
        route, needs_ocr = _decide_route(metrics, ctx)

        excluded = allowed is not None and number not in allowed
        if excluded:
            # Fora do recorte: registrada nos metadados e ignorada por
            # todos os estágios seguintes, tabelas e imagens inclusive.
            route = PageRoute.EMPTY
            needs_ocr = False

        doc.pages.append(
            PageInfo(
                number=number,
                width=float(page.rect.width),
                height=float(page.rect.height),
                rotation=int(page.rotation or 0),
                route=route,
                excluded=excluded,
                needs_ocr=needs_ocr,
                native_char_count=metrics["native_char_count"],
                image_count=metrics["image_count"],
                image_area_ratio=round(metrics["image_area_ratio"], 4),
                text_area_ratio=round(metrics["text_area_ratio"], 4),
                vector_line_count=metrics["vector_line_count"],
            )
        )

        ctx.progress(
            "ingest",
            0.0 + 0.5 * (number / pdf.page_count) / 100,
            f"Analisando página {number} de {pdf.page_count}",
        )

    routes = {r: 0 for r in PageRoute}
    for p in doc.pages:
        routes[p.route] += 1

    ctx.scratch["route_counts"] = {k.value: v for k, v in routes.items()}
    log.info("rotas de página: %s", ctx.scratch["route_counts"])

    if routes[PageRoute.OCR] and not ctx.settings.ocr_enabled:
        ctx.warn(
            "OCR_DISABLED",
            f"{routes[PageRoute.OCR]} página(s) precisam de OCR, mas o OCR está "
            "desligado na configuração. Essas páginas sairão vazias.",
            severity=Severity.ERROR,
        )

    precisam_ocr = [p.number for p in doc.pages if p.needs_ocr]
    if ctx.options.ocr_mode == OcrMode.NEVER and precisam_ocr:
        ctx.warn(
            "OCR_SKIPPED",
            f"{len(precisam_ocr)} página(s) não têm camada de texto e o OCR "
            f"foi desativado nesta conversão; elas sairão vazias: "
            f"{', '.join(map(str, precisam_ocr[:20]))}"
            + ("…" if len(precisam_ocr) > 20 else ""),
            severity=Severity.ERROR,
            pages=precisam_ocr,
        )

    empties = [
        p.number
        for p in doc.pages
        if p.route == PageRoute.EMPTY and not p.excluded and not p.needs_ocr
    ]
    if empties:
        ctx.warn(
            "PAGES_EMPTY",
            f"{len(empties)} página(s) em branco: "
            f"{', '.join(map(str, empties[:20]))}"
            + ("…" if len(empties) > 20 else ""),
            severity=Severity.INFO,
            pages=empties,
        )
