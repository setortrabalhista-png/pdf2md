"""Aplicação FastAPI: API + interface, num único processo.

    python -m app.main
    uvicorn app.main:app --host 127.0.0.1 --port 8000

Escuta em 127.0.0.1 por padrão. Expor na rede exige mudar `PDF2MD_HOST`
deliberadamente — e aí o documento passa a trafegar fora da máquina.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes_batches import router as batches_router
from app.api.routes_jobs import router as jobs_router
from app.config import get_settings
from app.core.errors import Pdf2MdError
from app.core.job_manager import JobManager

log = logging.getLogger("pdf2md")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)

    app.state.settings = settings
    app.state.jobs = JobManager(settings)

    log.info("pdf2md em http://%s:%s", settings.host, settings.port)
    log.info("área temporária: %s (TTL %s min)", settings.workspace_root, settings.job_ttl_minutes)

    yield

    # Encerramento limpo: nenhum documento fica para trás.
    app.state.jobs.shutdown()
    log.info("jobs encerrados e área temporária limpa")


def create_app() -> FastAPI:
    app = FastAPI(
        title="pdf2md",
        description="Conversão local de PDF para Markdown estruturado.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.include_router(jobs_router)
    app.include_router(batches_router)

    @app.exception_handler(Pdf2MdError)
    async def handle_domain_error(_request: Request, exc: Pdf2MdError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": str(exc), "codigo": exc.code},
        )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s %(name)-22s %(message)s",
    )
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
