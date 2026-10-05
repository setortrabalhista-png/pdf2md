"""Endpoints de lote — vários documentos numa tacada.

    POST   /api/lotes                cria o lote (upload múltiplo)
    GET    /api/lotes/{id}           estado + quadro de conferência
    GET    /api/lotes/{id}/events     progresso de todos, num stream só
    GET    /api/lotes/{id}/resumo.csv o quadro em CSV, para planilha
    GET    /api/lotes/{id}/download   tudo num .zip
    DELETE /api/lotes/{id}            purga o lote inteiro

Os jobs continuam individuais: `/api/jobs/{id}` segue valendo para cada
documento do lote, inclusive para baixar só um deles.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import queue

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from app.core.errors import JobNotFoundError, Pdf2MdError
from app.pipeline.options import ConversionOptions, OcrMode

log = logging.getLogger("pdf2md.api.lotes")

router = APIRouter(prefix="/api/lotes", tags=["lote"])

PDF_MAGIC = b"%PDF-"


def _manager(request: Request):
    return request.app.state.jobs


def _batch_or_404(request: Request, batch_id: str):
    try:
        return _manager(request).get_batch(batch_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=202)
async def criar_lote(
    request: Request,
    arquivos: list[UploadFile] = File(...),
    ocr: str = Form(OcrMode.AUTO.value),
    idioma: str = Form("por"),
    paginas: str = Form(""),
    extrair_imagens: bool = Form(True),
    extrair_tabelas: bool = Form(True),
    figuras_vetoriais: bool = Form(True),
    marcadores_de_pagina: bool = Form(True),
    unir_entre_paginas: bool = Form(True),
    ia_provedor: str = Form(""),
    ia_consentimento: bool = Form(False),
) -> dict:
    settings = request.app.state.settings

    if not arquivos:
        raise HTTPException(status_code=400, detail="nenhum arquivo enviado")
    if len(arquivos) > settings.max_batch_files:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{len(arquivos)} arquivos: acima do limite de "
                f"{settings.max_batch_files} por lote"
            ),
        )

    aceitos: list[tuple[str, bytes]] = []
    recusados: list[dict] = []
    total_bytes = 0

    for upload in arquivos:
        nome = upload.filename or "documento.pdf"
        dados = await upload.read()

        if not dados:
            recusados.append({"arquivo": nome, "motivo": "arquivo vazio"})
            continue
        if not dados.startswith(PDF_MAGIC):
            recusados.append({"arquivo": nome, "motivo": "não é um PDF"})
            continue
        if len(dados) > settings.max_upload_bytes:
            recusados.append(
                {
                    "arquivo": nome,
                    "motivo": f"acima de {settings.max_upload_mb} MB",
                }
            )
            continue

        total_bytes += len(dados)
        if total_bytes > settings.max_batch_bytes:
            recusados.append(
                {
                    "arquivo": nome,
                    "motivo": f"lote acima de {settings.max_batch_mb} MB no total",
                }
            )
            continue

        aceitos.append((nome, dados))

    if not aceitos:
        raise HTTPException(
            status_code=400,
            detail="nenhum arquivo válido no lote",
            headers={},
        )

    from app.cli import parse_pages

    try:
        ocr_mode = OcrMode(ocr)
    except ValueError:
        ocr_mode = OcrMode.AUTO

    options = ConversionOptions(
        ocr_mode=ocr_mode,
        ocr_lang=idioma or settings.ocr_lang,
        extract_images=extrair_imagens,
        extract_tables=extrair_tabelas,
        extract_vector_figures=figuras_vetoriais,
        page_markers=marcadores_de_pagina,
        merge_across_pages=unir_entre_paginas,
        page_range=parse_pages(paginas) if paginas.strip() else [],
        ai_enabled=bool(ia_provedor and ia_provedor != "null"),
        ai_consent=ia_consentimento,
        ai_provider=ia_provedor or None,
    )

    try:
        batch = _manager(request).create_batch(aceitos, options)
    except Pdf2MdError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    return {
        "id": batch.id,
        "total": len(aceitos),
        "recusados": recusados,
    }


@router.get("/{batch_id}")
def obter_lote(request: Request, batch_id: str) -> dict:
    batch = _batch_or_404(request, batch_id)
    return _manager(request).batch_snapshot(batch)


@router.delete("/{batch_id}")
def apagar_lote(request: Request, batch_id: str) -> dict:
    batch = _batch_or_404(request, batch_id)
    apagados = _manager(request).delete_batch(batch.id)
    return {"purgado": True, "documentos": apagados}


@router.get("/{batch_id}/events")
def eventos_do_lote(request: Request, batch_id: str) -> StreamingResponse:
    batch = _batch_or_404(request, batch_id)
    manager = _manager(request)

    def stream():
        # Retrato completo primeiro: quem conecta no meio vê a fila inteira.
        yield _sse({"tipo": "estado", **manager.batch_snapshot(batch)})

        if manager.batch_aggregate(batch)["terminado"]:
            return

        while True:
            try:
                evento = batch.events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                if manager.batch_aggregate(batch)["terminado"]:
                    yield _sse({"tipo": "estado", **manager.batch_snapshot(batch)})
                    break
                continue

            if evento.get("tipo") in ("purgado", "expirado"):
                yield _sse(evento)
                break

            yield _sse(evento)

            # O lote acabou quando todos os documentos chegaram a um estado
            # terminal — não quando o último evento chegou.
            if manager.batch_aggregate(batch)["terminado"] and batch.events.empty():
                yield _sse({"tipo": "estado", **manager.batch_snapshot(batch)})
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@router.get("/{batch_id}/resumo.csv", response_class=PlainTextResponse)
def resumo_csv(request: Request, batch_id: str) -> PlainTextResponse:
    """O quadro de conferência em CSV — abre direto no Excel."""
    batch = _batch_or_404(request, batch_id)
    linhas = _manager(request).batch_snapshot(batch)["resumo"]

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\n")
    escritor.writerow(
        [
            "arquivo", "status", "cobertura_%", "paginas", "tabelas",
            "tabelas_para_revisar", "imagens", "paginas_com_ocr", "pendencias",
        ]
    )
    for linha in linhas:
        escritor.writerow(
            [
                linha["arquivo"],
                linha["status"] or linha["estado"],
                linha["cobertura"] if linha["cobertura"] is not None else "",
                linha["paginas"] or "",
                linha["tabelas"] if linha["tabelas"] is not None else "",
                linha["tabelas_para_revisar"] if linha["tabelas_para_revisar"] is not None else "",
                linha["imagens"] if linha["imagens"] is not None else "",
                linha["paginas_com_ocr"] if linha["paginas_com_ocr"] is not None else "",
                " | ".join(linha["pendencias"]) or (linha["erro"] or ""),
            ]
        )

    # BOM para o Excel reconhecer o UTF-8 sem estragar os acentos.
    return PlainTextResponse(
        "﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="lote-{batch_id}-resumo.csv"'
        },
    )


@router.get("/{batch_id}/download")
def baixar_lote(request: Request, batch_id: str) -> FileResponse:
    batch = _batch_or_404(request, batch_id)
    manager = _manager(request)

    agregado = manager.batch_aggregate(batch)
    if not agregado["terminado"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"o lote ainda está em andamento "
                f"({agregado['concluidos']} de {agregado['total']})"
            ),
        )

    try:
        caminho = manager.bundle_batch(batch)
    except Pdf2MdError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return FileResponse(
        caminho,
        filename=f"lote-{len(batch.job_ids)}-documentos.zip",
        media_type="application/zip",
    )
