"""Estágio 03 — OCR seletivo.

Só as páginas que o estágio 01 marcou como carentes passam por aqui. Em página
híbrida (texto nativo + anexo digitalizado colado), o OCR roda na página
inteira, mas apenas os blocos que **não** colidem com texto nativo são
aproveitados — preservar a camada original é sempre melhor que reconhecê-la de
novo.
"""

from __future__ import annotations

import logging

from app.core.errors import OcrUnavailableError
from app.extractors import ocr_tesseract
from app.model.document import PageRoute
from app.model.layout import PageLayout, RawBlock
from app.model.provenance import Severity
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s03")

OCR_ROUTES = {PageRoute.OCR, PageRoute.HYBRID}

# Sobreposição acima da qual consideramos que o bloco de OCR está apenas
# redizendo o que a camada nativa já traz.
NATIVE_OVERLAP_THRESHOLD = 0.35


def _drop_duplicates_of_native(
    ocr_blocks: list[RawBlock], layout: PageLayout
) -> tuple[list[RawBlock], int]:
    """Descarta blocos de OCR que recobrem texto nativo já extraído."""
    native = [b for b in layout.blocks if not b.is_ocr]
    if not native:
        return ocr_blocks, 0

    kept: list[RawBlock] = []
    dropped = 0
    for ob in ocr_blocks:
        overlap = max(
            (ob.bbox.intersection_area(nb.bbox) / (ob.bbox.area or 1.0)) for nb in native
        )
        if overlap >= NATIVE_OVERLAP_THRESHOLD:
            dropped += 1
            continue
        kept.append(ob)
    return kept, dropped


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None and ctx.pdf is not None
    settings = ctx.settings
    layouts: dict[int, PageLayout] = ctx.scratch.setdefault("layouts", {})

    targets = [p for p in ctx.doc.pages if p.route in OCR_ROUTES]
    if not targets:
        log.info("nenhuma página precisa de OCR")
        return

    if not settings.ocr_enabled:
        return

    if not ocr_tesseract.is_available(settings.tesseract_cmd):
        raise OcrUnavailableError(
            f"{len(targets)} página(s) precisam de OCR, mas o Tesseract não foi "
            f"encontrado em {settings.tesseract_cmd!r}."
        )

    lang = ctx.options.ocr_lang or settings.ocr_lang
    installed = ocr_tesseract.available_languages(
        settings.tesseract_cmd, settings.tessdata_prefix
    )
    if installed and lang not in installed:
        fallback = "por" if "por" in installed else (installed[0] if installed else "eng")
        ctx.warn(
            "OCR_LANG_MISSING",
            f"Idioma de OCR '{lang}' não instalado. Usando '{fallback}'.",
            severity=Severity.WARNING,
            requested=lang,
            available=installed,
        )
        lang = fallback

    low_confidence_pages: list[int] = []
    empty_after_ocr: list[int] = []
    total = len(targets)

    for i, page_info in enumerate(targets, start=1):
        number = page_info.number
        page = ctx.pdf[number - 1]
        ctx.progress(
            "ocr",
            0.20 + 0.30 * (i / total),
            f"OCR — página {number} ({i} de {total})",
        )

        try:
            blocks, mean_conf, ocr_words = ocr_tesseract.ocr_page(
                page,
                number,
                lang=lang,
                dpi=settings.ocr_dpi,
                psm=settings.ocr_psm,
                oem=settings.ocr_oem,
                min_word_conf=settings.ocr_min_word_confidence,
                do_deskew=settings.ocr_deskew,
                do_binarize=settings.ocr_binarize,
                tesseract_cmd=settings.tesseract_cmd,
                tessdata_prefix=settings.tessdata_prefix,
            )
        except Exception as exc:  # noqa: BLE001 — uma página ruim não perde o resto
            log.error("OCR falhou na página %s: %s", number, exc)
            ctx.warn(
                "OCR_PAGE_FAILED",
                f"Falha no OCR da página {number}: {exc}",
                page=number,
                severity=Severity.ERROR,
            )
            continue

        layout = layouts.setdefault(
            number,
            PageLayout(page=number, width=page_info.width, height=page_info.height),
        )

        if page_info.route == PageRoute.HYBRID:
            blocks, dropped = _drop_duplicates_of_native(blocks, layout)
            if dropped:
                log.debug("página %s: %s blocos de OCR redundantes descartados", number, dropped)

        layout.blocks.extend(blocks)
        layout.words.extend(ocr_words)
        # Renumera para manter uma ordem estável antes do estágio 08.
        for order, b in enumerate(sorted(layout.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))):
            b.number = order

        page_info.ocr_char_count = sum(b.char_count for b in blocks)
        page_info.ocr_mean_confidence = round(mean_conf, 2) if blocks else 0.0

        if not blocks:
            empty_after_ocr.append(number)
        elif mean_conf < settings.ocr_low_confidence_page:
            low_confidence_pages.append(number)

    if empty_after_ocr:
        ctx.warn(
            "OCR_NO_TEXT",
            f"O OCR não encontrou texto em {len(empty_after_ocr)} página(s): "
            f"{', '.join(map(str, empty_after_ocr[:20]))}"
            + ("…" if len(empty_after_ocr) > 20 else ""),
            severity=Severity.ERROR,
            pages=empty_after_ocr,
        )

    if low_confidence_pages:
        ctx.warn(
            "OCR_LOW_CONFIDENCE",
            f"{len(low_confidence_pages)} página(s) com confiança de OCR abaixo de "
            f"{settings.ocr_low_confidence_page:.0f}%. Recomenda-se conferência "
            f"manual: {', '.join(map(str, low_confidence_pages[:20]))}"
            + ("…" if len(low_confidence_pages) > 20 else ""),
            severity=Severity.WARNING,
            pages=low_confidence_pages,
        )

    ctx.scratch["ocr_pages"] = [p.number for p in targets]
    ctx.scratch["ocr_lang"] = lang
