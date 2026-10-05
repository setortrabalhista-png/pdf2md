"""Gerência de jobs de conversão.

Estado em memória e arquivos em disco temporário: nada de banco de dados, nada
de fila externa. Um único processo, na máquina do usuário — que é exatamente o
modelo de implantação pretendido.

O progresso é publicado numa fila por job e consumido pelo endpoint SSE.

Um **lote** é uma coleção de jobs criada de uma vez. Ele tem fila própria de
eventos, para que a interface acompanhe o conjunto sem abrir N streams, e
empacota tudo num único .zip ao final. Os jobs continuam independentes: um
documento que falha não derruba os outros.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.config import Settings
from app.core.errors import JobNotFoundError, Pdf2MdError
from app.core.workspace import Workspace
from app.core.workspace import create as create_workspace
from app.pipeline.options import ConversionOptions
from app.pipeline.runner import run_pipeline

log = logging.getLogger("pdf2md.jobs")


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class Job:
    id: str
    filename: str
    workspace: Workspace
    options: ConversionOptions
    status: JobStatus = JobStatus.QUEUED
    stage: str = ""
    message: str = "Na fila"
    progress: float = 0.0
    error: str | None = None
    error_code: str | None = None
    report: dict | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    events: queue.Queue = field(default_factory=queue.Queue)
    # Preenchido quando o job faz parte de um lote.
    batch_id: str | None = None
    # Caminho do PDF, guardado quando a partida é adiada (lotes).
    pending_start: Path | None = None
    # Ordem dentro do lote e nome da pasta no .zip final.
    batch_index: int = 0
    folder: str = ""

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "arquivo": self.filename,
            "lote": self.batch_id,
            "pasta": self.folder,
            "status": self.status.value,
            "estagio": self.stage,
            "mensagem": self.message,
            "progresso": round(self.progress, 4),
            "erro": self.error,
            "codigo_erro": self.error_code,
            "criado_em": self.created_at,
            "concluido_em": self.finished_at,
            "relatorio": self.report,
            "arquivos": self.workspace.output_files() if self.status == JobStatus.DONE else [],
        }


TERMINAIS = (JobStatus.DONE, JobStatus.FAILED, JobStatus.EXPIRED)


@dataclass
class Batch:
    """Conjunto de documentos convertidos numa tacada."""

    id: str
    job_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    events: queue.Queue = field(default_factory=queue.Queue)
    bundle_path: Path | None = None

    def jobs(self, registry: dict) -> list:
        return [registry[i] for i in self.job_ids if i in registry]

    def aggregate(self, registry: dict) -> dict:
        jobs = self.jobs(registry)
        total = len(jobs) or 1
        concluidos = sum(1 for j in jobs if j.status in TERMINAIS)

        # O progresso do lote é a média do progresso individual: uma barra que
        # anda de forma contínua, em vez de saltar a cada documento pronto.
        progresso = sum(j.progress for j in jobs) / total

        return {
            "id": self.id,
            "total": len(jobs),
            "concluidos": concluidos,
            "convertidos": sum(1 for j in jobs if j.status == JobStatus.DONE),
            "falharam": sum(1 for j in jobs if j.status == JobStatus.FAILED),
            "na_fila": sum(1 for j in jobs if j.status == JobStatus.QUEUED),
            "processando": sum(1 for j in jobs if j.status == JobStatus.RUNNING),
            "progresso": round(progresso, 4),
            "terminado": concluidos == len(jobs) and len(jobs) > 0,
            "criado_em": self.created_at,
        }

    def snapshot(self, registry: dict) -> dict:
        return {
            **self.aggregate(registry),
            "documentos": [j.snapshot() for j in self.jobs(registry)],
            "resumo": self.summary(registry),
        }

    def summary(self, registry: dict) -> list[dict]:
        """Quadro de conferência: uma linha por documento.

        É o que substitui abrir N relatórios. A coluna `status` separa o que
        pode ser usado direto do que precisa de conferência humana.
        """
        linhas = []
        for job in self.jobs(registry):
            relatorio = job.report or {}
            resumo = relatorio.get("resumo") or {}
            linhas.append(
                {
                    "id": job.id,
                    "arquivo": job.filename,
                    "pasta": job.folder,
                    "estado": job.status.value,
                    "status": resumo.get("status")
                    or ("falhou" if job.status == JobStatus.FAILED else None),
                    "cobertura": relatorio.get("text_accuracy"),
                    "paginas": (relatorio.get("documento") or {}).get("paginas"),
                    "tabelas": relatorio.get("tables_detected"),
                    "tabelas_para_revisar": (relatorio.get("tabelas") or {}).get(
                        "para_revisar"
                    ),
                    "imagens": relatorio.get("images_extracted"),
                    "paginas_com_ocr": (relatorio.get("ocr") or {}).get(
                        "paginas_com_ocr"
                    ),
                    "erro": job.error,
                    "pendencias": resumo.get("pendencias") or [],
                }
            )
        return linhas


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.batches: dict[str, Batch] = {}
        self.lock = threading.Lock()
        # Conversão é dominada por CPU (render e OCR); dois trabalhadores
        # mantêm a interface responsiva sem brigar pelo processador.
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf2md")
        self._stop = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
        self._sweeper.start()

    # ── Ciclo de vida ──────────────────────────────────────────────────

    def create(
        self,
        filename: str,
        data: bytes,
        options: ConversionOptions,
        *,
        batch: Batch | None = None,
        folder: str = "",
        start: bool = True,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        workspace = create_workspace(self.settings.workspace_root, job_id)

        safe_name = Path(filename).name or "documento.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        target = workspace.input_dir / safe_name
        target.write_bytes(data)

        job = Job(
            id=job_id,
            filename=safe_name,
            workspace=workspace,
            options=options,
            batch_id=batch.id if batch else None,
            batch_index=len(batch.job_ids) if batch else 0,
            folder=folder or Path(safe_name).stem,
        )
        with self.lock:
            self.jobs[job_id] = job
            if batch is not None:
                batch.job_ids.append(job_id)

        log.info("job %s criado para %s (%.1f KB)", job_id, safe_name, len(data) / 1024)

        if start:
            self.executor.submit(self._run, job, target)
        else:
            # Num lote, a partida é adiada até que todos os documentos estejam
            # registrados: o progresso agregado é a média sobre o total, e um
            # documento que começa antes de os outros existirem faria a barra
            # andar para trás quando o total crescesse.
            job.pending_start = target

        return job

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"job não encontrado: {job_id}")
        return job

    def delete(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job is None:
            raise JobNotFoundError(f"job não encontrado: {job_id}")
        job.workspace.destroy()
        job.events.put({"tipo": "purgado"})
        log.info("job %s purgado a pedido", job_id)

    def list(self) -> list[dict]:
        with self.lock:
            return [j.snapshot() for j in self.jobs.values()]

    def shutdown(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            jobs = list(self.jobs.values())
            batches = list(self.batches.values())
            self.jobs.clear()
            self.batches.clear()
        for job in jobs:
            job.workspace.destroy()
        for batch in batches:
            if batch.bundle_path and batch.bundle_path.exists():
                batch.bundle_path.unlink(missing_ok=True)


    # ── Lotes ──────────────────────────────────────────────────────────

    def create_batch(
        self, arquivos: list[tuple[str, bytes]], options: ConversionOptions
    ) -> Batch:
        """Cria um lote e enfileira um job por documento.

        Nomes repetidos ganham sufixo: dois "peticao.pdf" vindos de pastas
        diferentes não podem se sobrescrever dentro do .zip final.
        """
        batch = Batch(id=secrets.token_urlsafe(12))
        with self.lock:
            self.batches[batch.id] = batch

        usados: set[str] = set()
        criados: list[Job] = []

        # Primeiro registra todos, sem começar nenhum.
        for filename, data in arquivos:
            base = Path(filename).stem or "documento"
            pasta = base
            sufixo = 2
            while pasta.lower() in usados:
                pasta = f"{base}-{sufixo}"
                sufixo += 1
            usados.add(pasta.lower())
            criados.append(
                self.create(
                    filename, data, options, batch=batch, folder=pasta, start=False
                )
            )

        log.info("lote %s criado com %s documento(s)", batch.id, len(batch.job_ids))
        self._publish_batch(batch, {"tipo": "lote_criado"})

        # Só agora a fila anda: o total do lote já é definitivo, e o progresso
        # agregado nunca vai regredir.
        for job in criados:
            alvo, job.pending_start = job.pending_start, None
            if alvo is not None:
                self.executor.submit(self._run, job, alvo)

        return batch

    def get_batch(self, batch_id: str) -> Batch:
        with self.lock:
            batch = self.batches.get(batch_id)
        if batch is None:
            raise JobNotFoundError(f"lote não encontrado: {batch_id}")
        return batch

    def batch_snapshot(self, batch: Batch) -> dict:
        with self.lock:
            registry = dict(self.jobs)
        return batch.snapshot(registry)

    def batch_aggregate(self, batch: Batch) -> dict:
        with self.lock:
            registry = dict(self.jobs)
        return batch.aggregate(registry)

    def delete_batch(self, batch_id: str) -> int:
        batch = self.get_batch(batch_id)
        apagados = 0
        for job_id in list(batch.job_ids):
            try:
                self.delete(job_id)
                apagados += 1
            except JobNotFoundError:
                continue
        with self.lock:
            self.batches.pop(batch_id, None)
        if batch.bundle_path and batch.bundle_path.exists():
            batch.bundle_path.unlink(missing_ok=True)
        batch.events.put({"tipo": "purgado"})
        log.info("lote %s purgado (%s documento(s))", batch_id, apagados)
        return apagados

    def bundle_batch(self, batch: Batch) -> Path:
        """Empacota todos os documentos convertidos num único .zip.

        Estrutura:

            _lote.json          índice do lote, com o quadro de conferência
            <documento>/
                <documento>.md
                resultado_processamento.json
                assets/...
        """
        with self.lock:
            registry = dict(self.jobs)

        prontos = [j for j in batch.jobs(registry) if j.status == JobStatus.DONE]
        if not prontos:
            raise Pdf2MdError("nenhum documento do lote foi convertido com sucesso")

        destino = self.settings.workspace_root / f"lote-{batch.id}.zip"
        if destino.exists():
            destino.unlink()

        indice = {
            "lote": batch.id,
            "gerado_em": time.time(),
            "documentos": batch.summary(registry),
        }

        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(
                "_lote.json", json.dumps(indice, ensure_ascii=False, indent=2)
            )
            for job in prontos:
                raiz = job.workspace.output_dir
                if not raiz.exists():
                    continue
                for caminho in sorted(raiz.rglob("*")):
                    if caminho.is_dir():
                        continue
                    interno = Path(job.folder) / caminho.relative_to(raiz)
                    zf.write(caminho, interno.as_posix())

        batch.bundle_path = destino
        log.info(
            "lote %s empacotado: %s documento(s), %.1f MB",
            batch.id,
            len(prontos),
            destino.stat().st_size / 1024 / 1024,
        )
        return destino

    def _publish_batch(self, batch: Batch, event: dict) -> None:
        """Publica um evento no stream do lote, com o agregado do momento.

        O cálculo do agregado e a publicação são atômicos de propósito. Com dois
        documentos terminando ao mesmo tempo, calcular fora do lock permitiria
        que a thread A medisse 30%, a thread B medisse 35%, e B enfileirasse
        primeiro — a barra da interface andaria para trás.
        """
        with self.lock:
            batch.events.put({**event, "lote": batch.aggregate(self.jobs)})

    # ── Execução ───────────────────────────────────────────────────────

    def _run(self, job: Job, pdf_path: Path) -> None:
        job.status = JobStatus.RUNNING
        self._publish(job, "Iniciando", 0.0, "start")

        def progress(stage: str, fraction: float, message: str) -> None:
            job.stage = stage
            self._publish(job, message, fraction, stage)

        try:
            run_pipeline(
                pdf_path,
                job.workspace.output_dir,
                job.options,
                progress,
                self.settings,
            )
        except Pdf2MdError as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.error_code = exc.code
            job.finished_at = time.time()
            self._publish(job, str(exc), job.progress, "erro")
            log.warning("job %s falhou: %s", job.id, exc)
            return
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            job.error_code = "INTERNAL_ERROR"
            job.finished_at = time.time()
            self._publish(job, job.error, job.progress, "erro")
            log.exception("job %s falhou de forma inesperada", job.id)
            return

        import json

        try:
            job.report = json.loads(job.workspace.report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            job.report = None

        if not self.settings.keep_source_pdf:
            job.workspace.discard_source()

        job.status = JobStatus.DONE
        job.progress = 1.0
        job.finished_at = time.time()
        self._publish(job, "Concluído", 1.0, "done")
        log.info("job %s concluído em %.1fs", job.id, job.finished_at - job.created_at)

    def _publish(self, job: Job, message: str, fraction: float, stage: str) -> None:
        job.message = message
        job.progress = max(job.progress, min(1.0, fraction))
        evento = {
            "tipo": "progresso",
            "status": job.status.value,
            "estagio": stage,
            "mensagem": message,
            "progresso": round(job.progress, 4),
        }
        job.events.put(evento)

        # O mesmo evento alimenta o stream do lote, com o job identificado — a
        # interface acompanha N documentos por uma conexão só.
        if job.batch_id:
            batch = self.batches.get(job.batch_id)
            if batch is not None:
                terminou = job.status in TERMINAIS
                self._publish_batch(
                    batch,
                    {
                        # `evento` primeiro: o que vem depois é que prevalece, e
                        # o tipo do evento de lote não pode ser sobrescrito pelo
                        # "progresso" do evento individual.
                        **evento,
                        "tipo": "documento",
                        "job": job.id,
                        "arquivo": job.filename,
                        "indice": job.batch_index,
                        # Quando o documento termina, o snapshot vai junto: a
                        # interface preenche a linha do quadro na hora.
                        "documento": job.snapshot() if terminou else None,
                    },
                )

    # ── Expiração ──────────────────────────────────────────────────────

    def _sweep_loop(self) -> None:
        interval = max(30, self.settings.sweep_interval_seconds)
        while not self._stop.wait(interval):
            try:
                self.sweep()
            except Exception:
                log.exception("varredura de expiração falhou")

    def sweep(self) -> int:
        ttl = self.settings.job_ttl_minutes * 60
        if ttl <= 0:
            return 0

        expired: list[Job] = []
        with self.lock:
            for job_id, job in list(self.jobs.items()):
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    continue
                if job.workspace.age_seconds() > ttl:
                    expired.append(self.jobs.pop(job_id))

        for job in expired:
            job.status = JobStatus.EXPIRED
            job.workspace.destroy()
            job.events.put({"tipo": "expirado"})

        if expired:
            log.info("%s job(s) expirados e apagados do disco", len(expired))

        self._sweep_batches()
        return len(expired)

    def _sweep_batches(self) -> None:
        """Remove lotes cujos jobs já sumiram, e o .zip que ficou para trás."""
        with self.lock:
            vivos = set(self.jobs)
            orfaos = [
                b
                for b in self.batches.values()
                if not any(i in vivos for i in b.job_ids)
            ]
            for batch in orfaos:
                self.batches.pop(batch.id, None)

        for batch in orfaos:
            if batch.bundle_path and batch.bundle_path.exists():
                batch.bundle_path.unlink(missing_ok=True)
            batch.events.put({"tipo": "expirado"})

        if orfaos:
            log.info("%s lote(s) expirados", len(orfaos))
