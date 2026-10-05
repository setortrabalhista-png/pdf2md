"""Validação do resultado contra o PDF de origem.

Três confrontos independentes, porque um só engana:

1. **Cobertura de caracteres** — quantos caracteres o IR carrega em relação aos
   que existiam na origem (camada nativa + OCR). Detecta texto perdido no
   caminho: bloco engolido por região de tabela, coluna não lida, página pulada.

2. **Segunda opinião textual (pdfplumber)** — outro motor lê o mesmo PDF. Onde
   os dois discordam muito, há algo errado com o PDF ou com a nossa leitura, e
   isso vira aviso em vez de passar batido.

3. **Sanidade léxica** — proporção de tokens implausíveis em português. É o
   sintoma clássico de OCR ruim, e aparece mesmo quando a confiança média do
   Tesseract está alta.
"""

from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path

from app.model.blocks import BlockKind, block_text
from app.model.document import DocumentModel, PageRoute
from app.model.provenance import Severity, Warning_

log = logging.getLogger("pdf2md.quality")

VOWELS = set("aeiouáàâãéêíóôõúüAEIOUÁÀÂÃÉÊÍÓÔÕÚÜ")
TOKEN = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
REPEATED = re.compile(r"(.)\1{3,}")


def _is_suspect_token(token: str) -> bool:
    """Token que dificilmente é uma palavra em português."""
    if len(token) >= 4 and not any(c in VOWELS for c in token):
        return True
    if REPEATED.search(token):
        return True
    # Alternância de caixa no meio da palavra: "cOnTrAtO" é ruído de OCR.
    core = token[1:]
    if len(token) >= 5 and any(c.isupper() for c in core) and any(c.islower() for c in core):
        transitions = sum(
            1
            for a, b in zip(token, token[1:], strict=False)
            if a.isupper() != b.isupper()
        )
        if transitions >= 3:
            return True
    return False


def lexical_sanity(text: str, sample_limit: int = 60000) -> dict:
    sample = text[:sample_limit]
    tokens = TOKEN.findall(sample)
    if not tokens:
        return {"tokens": 0, "suspect_tokens": 0, "suspect_ratio": 0.0}
    suspect = sum(1 for t in tokens if _is_suspect_token(t))
    return {
        "tokens": len(tokens),
        "suspect_tokens": suspect,
        "suspect_ratio": round(suspect / len(tokens), 4),
    }


def source_char_counts(doc: DocumentModel) -> dict[int, int]:
    """Caracteres que deveriam chegar ao Markdown, por página.

    Desconta o boilerplate recorrente: cabeçalho de tribunal, numeração de
    folhas e carimbo de assinatura eletrônica são removidos por decisão do
    sistema. Contá-los como perda transformaria todo processo bem convertido
    num alarme falso.
    """
    return {
        p.number: p.expected_char_count for p in doc.pages if not p.excluded
    }


def boilerplate_counts(doc: DocumentModel) -> dict[int, int]:
    return {p.number: p.boilerplate_char_count for p in doc.pages}


def ir_char_counts(doc: DocumentModel) -> dict[int, int]:
    counts: dict[int, int] = {p.number: 0 for p in doc.pages}
    for block in doc.blocks:
        if block.kind == BlockKind.PAGE_BREAK:
            continue
        counts[block.page] = counts.get(block.page, 0) + len(block_text(block))
    return counts


def pdfplumber_second_opinion(
    pdf_path: Path, pages: list[int], sample_size: int = 12
) -> dict:
    """Conta caracteres com um segundo motor, numa amostra de páginas."""
    try:
        import pdfplumber
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    if not pages:
        return {"available": False, "reason": "nenhuma página elegível"}

    step = max(1, len(pages) // sample_size)
    sample = pages[::step][:sample_size]

    counts: dict[int, int] = {}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for number in sample:
                if number - 1 >= len(pdf.pages):
                    continue
                text = pdf.pages[number - 1].extract_text() or ""
                counts[number] = len(text.strip())
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    return {"available": True, "counts": counts, "sampled_pages": sample}


def validate(
    doc: DocumentModel,
    pdf_path: Path,
    scratch: dict,
    table_threshold: float = 0.65,
) -> dict:
    """Roda todos os confrontos e devolve as métricas + avisos novos."""
    source = source_char_counts(doc)
    produced = ir_char_counts(doc)

    total_source = sum(source.values())
    total_produced = sum(produced.values())

    coverage = (total_produced / total_source) if total_source else (1.0 if not total_produced else 0.0)
    # A cobertura pode passar de 100% (marcadores de lista, células repetidas em
    # cabeçalho de tabela). O que interessa é o que falta, não o que sobra.
    coverage_capped = min(1.0, coverage)

    new_warnings: list[Warning_] = []

    # ── Páginas com perda relevante ────────────────────────────────────
    lossy_pages = []
    for number, available in source.items():
        if available < 40:
            continue
        got = produced.get(number, 0)
        if got / available < 0.75:
            lossy_pages.append(
                {"page": number, "origem": available, "markdown": got,
                 "cobertura": round(got / available, 3)}
            )

    if lossy_pages:
        worst = sorted(lossy_pages, key=lambda d: d["cobertura"])[:20]
        new_warnings.append(
            Warning_(
                code="PAGE_TEXT_LOSS",
                severity=Severity.WARNING,
                message=(
                    f"{len(lossy_pages)} página(s) com menos de 75% do texto de "
                    "origem no Markdown. Verifique se há coluna, tabela ou caixa "
                    "de texto não capturada."
                ),
                detail={"paginas": worst},
            )
        )

    # ── Páginas sem nenhum texto ───────────────────────────────────────
    empty_pages = [
        p.number
        for p in doc.pages
        if produced.get(p.number, 0) == 0
        and not p.excluded
        # Página genuinamente em branco não é problema; página que
        # precisava de OCR e ficou sem texto, é.
        and (p.route != PageRoute.EMPTY or p.needs_ocr)
    ]
    if empty_pages:
        new_warnings.append(
            Warning_(
                code="PAGE_NO_TEXT",
                severity=Severity.ERROR,
                message=(
                    f"{len(empty_pages)} página(s) não produziram texto algum: "
                    f"{', '.join(map(str, empty_pages[:20]))}"
                    + ("…" if len(empty_pages) > 20 else "")
                ),
                detail={"paginas": empty_pages},
            )
        )

    # ── Segunda opinião ────────────────────────────────────────────────
    eligible = [p.number for p in doc.pages if p.route in (PageRoute.NATIVE, PageRoute.HYBRID)]
    second = pdfplumber_second_opinion(pdf_path, eligible)
    divergent: list[dict] = []
    if second.get("available"):
        boilerplate = boilerplate_counts(doc)
        for number, plumber_chars in second["counts"].items():
            ours = produced.get(number, 0)
            # O segundo motor lê a página inteira, boilerplate incluído.
            comparable = max(1, plumber_chars - boilerplate.get(number, 0))
            if comparable < 40:
                continue
            ratio = ours / comparable
            if ratio < 0.8 or ratio > 1.6:
                divergent.append(
                    {
                        "page": number,
                        "pdfplumber": plumber_chars,
                        "pdfplumber_sem_boilerplate": comparable,
                        "pdf2md": ours,
                        "razao": round(ratio, 3),
                    }
                )
        if divergent:
            new_warnings.append(
                Warning_(
                    code="EXTRACTOR_DISAGREEMENT",
                    severity=Severity.WARNING,
                    message=(
                        f"Os dois motores de extração discordam em {len(divergent)} "
                        "página(s) da amostra. Pode indicar codificação de fonte "
                        "irregular no PDF de origem."
                    ),
                    detail={"paginas": divergent[:20]},
                )
            )

    # ── Sanidade léxica ────────────────────────────────────────────────
    lexical = lexical_sanity(doc.plain_text())
    if lexical["tokens"] >= 200 and lexical["suspect_ratio"] > 0.06:
        new_warnings.append(
            Warning_(
                code="OCR_QUALITY_SUSPECT",
                severity=Severity.WARNING,
                message=(
                    f"{lexical['suspect_ratio']:.1%} das palavras não parecem "
                    "português válido. Indício de erro de reconhecimento óptico "
                    "ou de fonte com codificação quebrada."
                ),
                detail=lexical,
            )
        )

    # ── Imagens ────────────────────────────────────────────────────────
    declared = scratch.get("images_declared", 0)
    extracted = scratch.get("images_extracted", 0)
    vector = scratch.get("vector_figures", 0)
    suppressed = scratch.get("page_scans_suppressed", 0)
    embedded = max(0, extracted - vector)
    declared = max(0, declared - suppressed)
    if declared and embedded < declared * 0.6:
        new_warnings.append(
            Warning_(
                code="IMAGES_MISSING",
                severity=Severity.WARNING,
                message=(
                    f"O PDF declara {declared} imagem(ns), mas {embedded} foram "
                    "extraídas. Parte pode ter sido descartada por tamanho mínimo "
                    "ou fundida na recomposição de páginas digitalizadas."
                ),
                detail={"declaradas": declared, "extraidas": embedded},
            )
        )

    # ── OCR ────────────────────────────────────────────────────────────
    ocr_confidences = [
        p.ocr_mean_confidence for p in doc.pages if p.ocr_mean_confidence is not None
    ]
    ocr_mean = round(statistics.fmean(ocr_confidences), 2) if ocr_confidences else None

    tables = doc.blocks_of_kind(BlockKind.TABLE)
    review_tables = [t for t in tables if t.needs_review]
    below_threshold = [
        t for t in tables if t.provenance.confidence < table_threshold
    ]

    return {
        "coverage": round(coverage_capped, 4),
        "coverage_raw": round(coverage, 4),
        "source_chars": total_source,
        "boilerplate_chars": sum(boilerplate_counts(doc).values()),
        "markdown_chars": total_produced,
        "lossy_pages": lossy_pages,
        "empty_pages": empty_pages,
        "second_opinion": {
            "available": second.get("available", False),
            "reason": second.get("reason"),
            "sampled_pages": second.get("sampled_pages", []),
            "divergent_pages": divergent,
        },
        "lexical": lexical,
        "ocr_mean_confidence": ocr_mean,
        "ocr_pages": len([p for p in doc.pages if p.route == PageRoute.OCR]),
        "tables_total": len(tables),
        "tables_low_confidence": len(review_tables),
        "tables_below_threshold": len(below_threshold),
        "new_warnings": new_warnings,
    }
