"""Montagem do `resultado_processamento.json`.

As chaves de topo `text_accuracy`, `tables_detected`, `images_extracted` e
`warnings` existem exatamente como especificado. Em volta delas vai o detalhe
que torna o número auditável: nenhum indicador aqui é opaco.

`text_accuracy` é **cobertura de texto**: percentual dos caracteres disponíveis
na origem (camada nativa do PDF + saída do OCR) que chegaram ao Markdown. Não é
acurácia contra um gabarito — não existe gabarito. Um documento pode ter 100% de
cobertura e ainda assim conter erro de OCR; por isso a sanidade léxica e a
confiança média do OCR aparecem separadas.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.model.blocks import BlockKind
from app.model.document import DocumentModel

SCHEMA_VERSION = "1.0"


def build(doc: DocumentModel, metrics: dict, scratch: dict, options) -> dict:
    counts = {
        kind.value: len(doc.blocks_of_kind(kind))
        for kind in BlockKind
        if kind != BlockKind.PAGE_BREAK
    }

    warnings = [w.model_dump(mode="json") for w in doc.warnings]

    report = {
        # ── Indicadores pedidos, na forma pedida ───────────────────────
        "text_accuracy": round(metrics["coverage"] * 100, 1),
        "tables_detected": metrics["tables_total"],
        "images_extracted": scratch.get("images_extracted", 0),
        "warnings": warnings,

        # ── Contexto ───────────────────────────────────────────────────
        "schema_version": SCHEMA_VERSION,
        "documento": {
            "arquivo": doc.meta.source_filename,
            "sha256": doc.meta.source_sha256,
            "bytes": doc.meta.size_bytes,
            "paginas": doc.meta.page_count,
            "titulo_pdf": doc.meta.pdf_title,
            "autor_pdf": doc.meta.pdf_author,
            "produtor_pdf": doc.meta.pdf_producer,
            "arquivo_markdown": scratch.get("markdown_name", "documento.md"),
        },

        # ── Fidelidade ─────────────────────────────────────────────────
        "fidelidade": {
            "cobertura_texto_pct": round(metrics["coverage"] * 100, 2),
            "cobertura_bruta_pct": round(metrics["coverage_raw"] * 100, 2),
            "caracteres_origem_uteis": metrics["source_chars"],
            "caracteres_boilerplate_descartados": metrics.get("boilerplate_chars", 0),
            "caracteres_markdown": metrics["markdown_chars"],
            "paginas_sem_texto": metrics["empty_pages"],
            "paginas_com_perda": metrics["lossy_pages"],
            "segunda_opiniao": metrics["second_opinion"],
        },

        # ── OCR ────────────────────────────────────────────────────────
        "ocr": {
            "paginas_com_ocr": metrics["ocr_pages"],
            "idioma": scratch.get("ocr_lang"),
            "confianca_media_pct": metrics["ocr_mean_confidence"],
            "palavras_suspeitas_pct": round(metrics["lexical"]["suspect_ratio"] * 100, 2),
            "tokens_avaliados": metrics["lexical"]["tokens"],
        },

        # ── Tabelas ────────────────────────────────────────────────────
        "tabelas": {
            "total": metrics["tables_total"],
            # "para_revisar" inclui tabela com célula mesclada, que pode ter
            # confiança alta e ainda assim não caber em Markdown.
            "para_revisar": metrics["tables_low_confidence"],
            "abaixo_do_limiar": metrics["tables_below_threshold"],
            "motores_de_reserva": scratch.get("table_fallbacks", {}),
            "detalhe": [
                {
                    "id": t.id,
                    "pagina": t.page,
                    "linhas": t.n_rows,
                    "colunas": t.n_cols,
                    "motor": t.engine,
                    "confianca": round(t.provenance.confidence, 3),
                    "celulas_mescladas": t.has_merged_cells,
                    "revisar": t.needs_review,
                    "motivo": t.review_reason,
                }
                for t in doc.blocks_of_kind(BlockKind.TABLE)
            ],
        },

        # ── Imagens ────────────────────────────────────────────────────
        "imagens": {
            "extraidas": scratch.get("images_extracted", 0),
            "declaradas_no_pdf": scratch.get("images_declared", 0),
            "figuras_vetoriais": scratch.get("vector_figures", 0),
            "arquivos": doc.assets,
        },

        # ── Estrutura ──────────────────────────────────────────────────
        "estrutura": {
            "blocos": counts,
            "estilo_do_corpo": scratch.get("body_style"),
            "niveis_de_titulo": scratch.get("heading_styles", {}),
            "dehifenizacao": scratch.get("dehyphenate", False),
            "paragrafos_unidos_entre_paginas": scratch.get("paragraphs_merged", 0),
            "legendas_promovidas": scratch.get("captions_promoted", 0),
            "cabecalhos_removidos": doc.discarded_headers,
            "rodapes_removidos": doc.discarded_footers,
        },

        # ── Páginas ────────────────────────────────────────────────────
        "paginas": [
            {
                "numero": p.number,
                "rota": p.route.value,
                "caracteres_nativos": p.native_char_count,
                "caracteres_ocr": p.ocr_char_count,
                "confianca_ocr": p.ocr_mean_confidence,
                "imagens": p.image_count,
                "cabecalho": p.header_text,
                "rodape": p.footer_text,
            }
            for p in doc.pages
        ],

        # ── Execução ───────────────────────────────────────────────────
        "execucao": {
            "estagios": doc.stage_log,
            "opcoes": options.model_dump(mode="json") if options else {},
        },

        # ── IA ─────────────────────────────────────────────────────────
        "ia": {
            "provedor": scratch.get("ai_provider", "null"),
            **(scratch.get("ai_report") or {}),
        },
    }

    report["resumo"] = _summarize(report)
    return report


def _summarize(report: dict) -> dict:
    """Veredito legível — o que um humano precisa saber em cinco segundos."""
    warnings = report["warnings"]
    errors = [w for w in warnings if w.get("severity") == "error"]
    alerts = [w for w in warnings if w.get("severity") == "warning"]

    coverage = report["fidelidade"]["cobertura_texto_pct"]

    if errors or coverage < 85:
        status = "revisar"
    elif alerts or report["tabelas"]["para_revisar"]:
        status = "atencao"
    else:
        status = "ok"

    pendencias = []
    if report["fidelidade"]["paginas_sem_texto"]:
        pendencias.append(
            f"{len(report['fidelidade']['paginas_sem_texto'])} página(s) sem texto"
        )
    if report["tabelas"]["para_revisar"]:
        pendencias.append(
            f"{report['tabelas']['para_revisar']} tabela(s) para conferir"
        )
    if report["ocr"]["confianca_media_pct"] is not None and report["ocr"]["confianca_media_pct"] < 80:
        pendencias.append(
            f"OCR com confiança média de {report['ocr']['confianca_media_pct']:.0f}%"
        )
    suspeitas = report["ocr"]["palavras_suspeitas_pct"]
    if suspeitas and suspeitas > 6:
        pendencias.append(f"{suspeitas:.1f}% de palavras improváveis")

    return {
        "status": status,
        "cobertura_texto_pct": coverage,
        "erros": len(errors),
        "avisos": len(alerts),
        "pendencias": pendencias,
    }


def write(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
