"""Endpoints HTTP.

    POST   /api/jobs                cria a conversão (upload)
    GET    /api/jobs                lista os jobs vivos
    GET    /api/jobs/{id}           estado + relatório
    GET    /api/jobs/{id}/events    progresso via SSE
    GET    /api/jobs/{id}/markdown  o Markdown em texto puro
    GET    /api/jobs/{id}/preview   o Markdown renderizado (HTML)
    GET    /api/jobs/{id}/arquivo   um arquivo gerado (<nome>.md, assets/…)
    GET    /api/jobs/{id}/download  tudo num .zip
    DELETE /api/jobs/{id}           purga imediata
    GET    /api/capacidades         o que esta instalação sabe fazer
"""

from __future__ import annotations

import json
import logging
import queue
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from app.ai.providers import describe_providers
from app.core.errors import JobNotFoundError, Pdf2MdError
from app.core.job_manager import JobStatus
from app.extractors import ocr_tesseract, tables_fallback
from app.pipeline.options import ConversionOptions, OcrMode

log = logging.getLogger("pdf2md.api")

router = APIRouter(prefix="/api", tags=["conversao"])

PDF_MAGIC = b"%PDF-"


def _manager(request: Request):
    return request.app.state.jobs


def _settings(request: Request):
    return request.app.state.settings


def _job_or_404(request: Request, job_id: str):
    try:
        return _manager(request).get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/capacidades")
def capacidades(request: Request) -> dict:
    """O que esta instalação consegue fazer — a interface se adapta a isto."""
    settings = _settings(request)
    ocr_ok = ocr_tesseract.is_available(settings.tesseract_cmd)
    langs = (
        ocr_tesseract.available_languages(settings.tesseract_cmd, settings.tessdata_prefix)
        if ocr_ok
        else []
    )
    return {
        "ocr": {
            "disponivel": ocr_ok,
            "caminho": settings.tesseract_cmd if ocr_ok else None,
            "idiomas": langs,
            "idioma_padrao": settings.ocr_lang if settings.ocr_lang in langs else (
                langs[0] if langs else None
            ),
            "dpi": settings.ocr_dpi,
        },
        "tabelas": {
            "motor_primario": "lines (réguas vetoriais)",
            "motor_secundario": "stream (alinhamento)",
            "reservas": tables_fallback.describe_availability(),
        },
        "ia": {
            "provedores": describe_providers(settings),
            "exige_consentimento": settings.ai_require_consent,
        },
        "limites": {
            "tamanho_maximo_mb": settings.max_upload_mb,
            "ttl_minutos": settings.job_ttl_minutes,
            "lote_max_arquivos": settings.max_batch_files,
            "lote_max_mb": settings.max_batch_mb,
        },
        "privacidade": {
            "processamento_local": True,
            "guarda_pdf_de_entrada": settings.keep_source_pdf,
            "endereco": f"{settings.host}:{settings.port}",
        },
    }


@router.post("/jobs", status_code=202)
async def criar_job(
    request: Request,
    arquivo: UploadFile = File(...),
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
    settings = _settings(request)

    data = await arquivo.read()
    if not data:
        raise HTTPException(status_code=400, detail="arquivo vazio")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"arquivo acima do limite de {settings.max_upload_mb} MB",
        )
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=400,
            detail="o arquivo enviado não é um PDF (assinatura %PDF- ausente)",
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
        job = _manager(request).create(arquivo.filename or "documento.pdf", data, options)
    except Pdf2MdError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    return {"id": job.id, "status": job.status.value}


@router.get("/jobs")
def listar_jobs(request: Request) -> dict:
    return {"jobs": _manager(request).list()}


@router.get("/jobs/{job_id}")
def obter_job(request: Request, job_id: str) -> dict:
    return _job_or_404(request, job_id).snapshot()


@router.delete("/jobs/{job_id}")
def apagar_job(request: Request, job_id: str) -> dict:
    try:
        _manager(request).delete(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"purgado": True}


@router.get("/jobs/{job_id}/events")
def eventos(request: Request, job_id: str) -> StreamingResponse:
    job = _job_or_404(request, job_id)

    terminais = (JobStatus.DONE, JobStatus.FAILED, JobStatus.EXPIRED)

    def stream():
        # Estado atual primeiro: quem conecta no meio não perde o contexto.
        yield _sse({"tipo": "estado", **job.snapshot()})

        if job.status in terminais:
            return

        while True:
            try:
                event = job.events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                if job.status in terminais:
                    yield _sse({"tipo": "estado", **job.snapshot()})
                    break
                continue

            if event.get("tipo") in ("purgado", "expirado"):
                yield _sse(event)
                break

            yield _sse(event)

            # A autoridade sobre o fim do job é o status, não o nome do estágio
            # emitido pelo pipeline: ele termina antes de o relatório ser gravado.
            if job.status in terminais and job.events.empty():
                yield _sse({"tipo": "estado", **job.snapshot()})
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _require_done(job):
    if job.status != JobStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"a conversão ainda não terminou (status: {job.status.value})",
        )


@router.get("/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def markdown(request: Request, job_id: str) -> PlainTextResponse:
    job = _job_or_404(request, job_id)
    _require_done(job)
    if not job.workspace.markdown_path.exists():
        raise HTTPException(status_code=404, detail="Markdown não encontrado")
    return PlainTextResponse(
        job.workspace.markdown_path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/jobs/{job_id}/preview", response_class=HTMLResponse)
def preview(request: Request, job_id: str) -> HTMLResponse:
    """Renderiza o Markdown no servidor — nenhuma biblioteca externa, nenhuma rede."""
    job = _job_or_404(request, job_id)
    _require_done(job)
    if not job.workspace.markdown_path.exists():
        raise HTTPException(status_code=404, detail="Markdown não encontrado")

    from markdown_it import MarkdownIt

    text = job.workspace.markdown_path.read_text(encoding="utf-8")
    # O cabeçalho YAML não é Markdown; mostrá-lo cru poluiria a pré-visualização.
    if text.startswith("---\n"):
        _, _, rest = text[4:].partition("\n---\n")
        text = rest or text

    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    html = md.render(text)
    # As imagens são servidas pela rota de arquivos deste mesmo job.
    html = html.replace('src="assets/', f'src="/api/jobs/{job_id}/arquivo/assets/')
    return HTMLResponse(html)


@router.get("/jobs/{job_id}/arquivo/{caminho:path}")
def arquivo(request: Request, job_id: str, caminho: str) -> FileResponse:
    job = _job_or_404(request, job_id)
    _require_done(job)

    base = job.workspace.output_dir.resolve()
    target = (base / caminho).resolve()
    # Impede que "../" escape do diretório do job.
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(status_code=404, detail="arquivo não encontrado")

    return FileResponse(target, filename=Path(caminho).name)


@router.get("/jobs/{job_id}/download")
def download(request: Request, job_id: str) -> FileResponse:
    job = _job_or_404(request, job_id)
    _require_done(job)
    bundle = job.workspace.bundle()
    stem = Path(job.filename).stem
    return FileResponse(
        bundle, filename=f"{stem}-markdown.zip", media_type="application/zip"
    )
