"""Estágio 09 — Refino estrutural por IA (opcional, desligado por padrão).

Fluxo:

    IR  →  payload estrutural  →  provedor  →  lista de operações
                                                      ↓
                              guard: aplica UMA operação, confere a impressão
                              digital de conteúdo, aceita ou reverte
                                                      ↓
                                                     IR

O guard é o que transforma "a IA não deve inventar" em "a IA não pode inventar".
Cada operação é validada isoladamente: uma proposta ruim é descartada sem
contaminar as boas.
"""

from __future__ import annotations

import logging

from app.ai import guard
from app.ai.operations import OperationError, apply_operation
from app.ai.providers import get_provider
from app.model.blocks import BlockKind
from app.model.provenance import Severity
from app.pipeline.runner import PipelineContext

log = logging.getLogger("pdf2md.s09")

TEXT_PREVIEW = 400


def _block_payload(block, page_width: float) -> dict:
    entry: dict = {
        "id": block.id,
        "kind": block.kind.value,
        "page": block.page,
    }

    if block.kind == BlockKind.HEADING:
        entry["level"] = block.level
        entry["text"] = block.text[:TEXT_PREVIEW]
        if block.numbering:
            entry["numbering"] = block.numbering
    elif block.kind == BlockKind.PARAGRAPH:
        entry["style"] = block.style.value
        entry["text"] = block.text[:TEXT_PREVIEW]
        entry["truncated"] = len(block.text) > TEXT_PREVIEW
        entry["ends_complete"] = block.text.rstrip().endswith((".", "!", "?", ";", ":"))
    elif block.kind == BlockKind.LIST_ITEM:
        entry["marker"] = block.marker
        entry["ordered"] = block.ordered
        entry["text"] = block.text[:TEXT_PREVIEW]
    elif block.kind == BlockKind.FOOTNOTE:
        entry["marker"] = block.marker
        entry["text"] = block.text[:TEXT_PREVIEW]
    elif block.kind == BlockKind.TABLE:
        entry["rows"] = block.n_rows
        entry["cols"] = block.n_cols
        entry["header_rows"] = block.header_rows
        entry["confidence"] = block.provenance.confidence
        entry["first_rows"] = [
            [c.text[:60] for c in row] for row in block.rows[:3]
        ]
    elif block.kind == BlockKind.FIGURE:
        entry["caption"] = block.caption
    elif block.kind == BlockKind.PAGE_BREAK:
        return {}

    # Pistas geométricas ajudam a IA a distinguir citação recuada de corpo.
    if page_width:
        entry["indent"] = round(block.bbox.x0 / page_width, 3)
        entry["width"] = round(block.bbox.width / page_width, 3)

    if block.provenance.source.value == "ocr":
        entry["from_ocr"] = True
        entry["ocr_confidence"] = round(block.provenance.confidence, 3)

    return entry


def _build_windows(doc, max_blocks: int) -> list[list[dict]]:
    widths = {p.number: p.width for p in doc.pages}
    payloads = []
    for block in doc.blocks:
        entry = _block_payload(block, widths.get(block.page, 0.0))
        if entry:
            payloads.append(entry)

    # Janelas com sobreposição de 5 blocos: uma união de parágrafos na fronteira
    # da janela seria invisível para o modelo sem isso.
    windows = []
    step = max(1, max_blocks - 5)
    for start in range(0, len(payloads), step):
        window = payloads[start : start + max_blocks]
        if window:
            windows.append(window)
        if start + max_blocks >= len(payloads):
            break
    return windows


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    doc = ctx.doc
    settings = ctx.settings

    provider_name = ctx.options.ai_provider or settings.ai_provider
    provider = get_provider(provider_name, settings)

    if (
        provider.sends_data_externally
        and settings.ai_require_consent
        and not ctx.options.ai_consent
    ):
        ctx.warn(
            "AI_CONSENT_MISSING",
            f"O provedor de IA '{provider.name}' envia trechos do documento "
            "para fora desta máquina e o consentimento não foi dado. "
            "O refino por IA foi ignorado.",
            severity=Severity.WARNING,
        )
        return

    available, reason = provider.is_available()
    if not available:
        ctx.warn(
            "AI_UNAVAILABLE",
            f"Provedor de IA '{provider.name}' indisponível: {reason}",
            severity=Severity.WARNING,
        )
        return

    report = guard.GuardReport()
    baseline = guard.fingerprint(doc)
    windows = _build_windows(doc, settings.ai_max_blocks_per_call)

    for index, window in enumerate(windows, start=1):
        ctx.progress(
            "ai",
            0.78 + 0.12 * (index / max(1, len(windows))),
            f"Refinando estrutura com IA ({index} de {len(windows)})",
        )

        payload = {
            "document": doc.meta.source_filename,
            "total_pages": doc.meta.page_count,
            "window": f"{index}/{len(windows)}",
            "blocks": window,
        }

        try:
            proposal = provider.propose(payload, settings.ai_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — IA indisponível não perde a conversão
            log.error("provedor de IA falhou na janela %s: %s", index, exc)
            ctx.warn(
                "AI_CALL_FAILED",
                f"A chamada de IA falhou e foi ignorada: {exc}",
                severity=Severity.WARNING,
            )
            continue

        for operation in proposal.operations:
            snapshot = [b.model_copy(deep=True) for b in doc.blocks]
            try:
                effect = apply_operation(doc, operation)
            except OperationError as exc:
                doc.blocks = snapshot
                report.failed.append({"op": operation.op.value, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001
                doc.blocks = snapshot
                report.failed.append({"op": operation.op.value, "error": repr(exc)})
                continue

            after = guard.fingerprint(doc)
            if not guard.content_preserved(baseline, after):
                detail = guard.diff_summary(baseline, after)
                doc.blocks = snapshot
                report.rejected.append({"op": operation.op.value, "diff": detail})
                log.warning("operação %s rejeitada: %s", operation.op.value, detail)
                continue

            report.applied.append(f"{operation.op.value}: {effect}")

    doc.resequence()
    ctx.scratch["ai_report"] = report.as_dict()
    ctx.scratch["ai_provider"] = provider.name

    log.info(
        "IA: %s propostas, %s aplicadas, %s rejeitadas por alterar conteúdo, %s inválidas",
        report.total_proposed,
        len(report.applied),
        len(report.rejected),
        len(report.failed),
    )

    if report.rejected:
        ctx.warn(
            "AI_OPERATIONS_REJECTED",
            f"{len(report.rejected)} operação(ões) da IA foram descartadas por "
            "tentarem alterar o conteúdo do documento. Nada foi modificado por elas.",
            severity=Severity.INFO,
            detail=report.rejected[:10],
        )

    # Conferência final de integridade sobre o documento inteiro.
    final = guard.fingerprint(doc)
    if not guard.content_preserved(baseline, final):
        ctx.warn(
            "AI_CONTENT_DRIFT",
            "Divergência de conteúdo detectada após o refino por IA: "
            + guard.diff_summary(baseline, final),
            severity=Severity.ERROR,
        )
