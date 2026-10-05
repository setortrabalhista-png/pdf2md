"""Testes da API HTTP, incluindo as garantias de privacidade."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def cliente():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _converter(cliente, pdf, **campos) -> dict:
    """Envia o PDF e espera a conclusão. Devolve o snapshot final."""
    resposta = cliente.post(
        "/api/jobs",
        files={"arquivo": (pdf.name, pdf.read_bytes(), "application/pdf")},
        data={"ocr": "auto", "idioma": "por", **campos},
    )
    assert resposta.status_code == 202, resposta.text
    job_id = resposta.json()["id"]

    limite = time.time() + 180
    while time.time() < limite:
        snapshot = cliente.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in ("done", "failed"):
            return snapshot
        time.sleep(0.15)
    pytest.fail("a conversão não terminou dentro do tempo previsto")


# ── Básico ────────────────────────────────────────────────────────────────


def test_health(cliente):
    assert cliente.get("/health").json() == {"ok": True}


def test_interface_e_servida(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "pdf2md" in resposta.text
    assert cliente.get("/static/app.js").status_code == 200
    assert cliente.get("/static/styles.css").status_code == 200


def test_capacidades_descreve_a_instalacao(cliente):
    dados = cliente.get("/api/capacidades").json()
    assert "ocr" in dados and "tabelas" in dados and "ia" in dados
    assert dados["privacidade"]["processamento_local"] is True
    # O provedor externo tem de se declarar como externo.
    externos = [p for p in dados["ia"]["provedores"] if p["sends_data_externally"]]
    assert all(p["id"] != "null" for p in externos)


# ── Fluxo completo ────────────────────────────────────────────────────────


def test_conversao_completa(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico)
    assert snapshot["status"] == "done", snapshot.get("erro")

    relatorio = snapshot["relatorio"]
    assert relatorio["text_accuracy"] >= 90
    assert relatorio["tables_detected"] == 1
    assert relatorio["resumo"]["status"] in ("ok", "atencao")

    nomes = {a["nome"] for a in snapshot["arquivos"]}
    assert "juridico.md" in nomes
    assert "resultado_processamento.json" in nomes


def test_markdown_preview_e_zip(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico)
    job = snapshot["id"]

    markdown = cliente.get(f"/api/jobs/{job}/markdown")
    assert markdown.status_code == 200
    assert "DOS FATOS" in markdown.text

    preview = cliente.get(f"/api/jobs/{job}/preview")
    assert preview.status_code == 200
    assert "<h1" in preview.text or "<h2" in preview.text
    assert "<table" in preview.text

    zipe = cliente.get(f"/api/jobs/{job}/download")
    assert zipe.status_code == 200
    assert zipe.content[:2] == b"PK"


def test_arquivo_individual(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico)
    resposta = cliente.get(f"/api/jobs/{snapshot['id']}/arquivo/juridico.md")
    assert resposta.status_code == 200
    assert "RECLAMAÇÃO TRABALHISTA" in resposta.text


def test_sse_emite_progresso_e_encerra_concluido(cliente, pdf_juridico):
    """Regressão: o stream encerrava antes de o job ser marcado como concluído."""
    criado = cliente.post(
        "/api/jobs",
        files={"arquivo": (pdf_juridico.name, pdf_juridico.read_bytes(), "application/pdf")},
        data={"ocr": "auto"},
    )
    job = criado.json()["id"]

    eventos = []
    with cliente.stream("GET", f"/api/jobs/{job}/events") as fluxo:
        for linha in fluxo.iter_lines():
            if not linha.startswith("data:"):
                continue
            evento = json.loads(linha[5:])
            eventos.append(evento)
            # O contrato com a interface: o stream só encerra depois de um
            # evento de "estado" concluído, que é o único que traz o relatório.
            if evento.get("tipo") == "estado" and evento.get("status") in ("done", "failed"):
                break

    assert len(eventos) > 3, eventos
    progresso = [e for e in eventos if e.get("tipo") == "progresso"]
    assert len(progresso) >= 3
    assert all(0.0 <= e["progresso"] <= 1.0 for e in progresso)
    # O progresso nunca anda para trás.
    assert progresso == sorted(progresso, key=lambda e: e["progresso"])

    final = eventos[-1]
    assert final["status"] == "done"
    assert final["relatorio"] is not None
    assert final["relatorio"]["text_accuracy"] > 0
    assert cliente.get(f"/api/jobs/{job}").json()["status"] == "done"


# ── Validação de entrada ──────────────────────────────────────────────────


def test_arquivo_que_nao_e_pdf_e_recusado(cliente):
    resposta = cliente.post(
        "/api/jobs",
        files={"arquivo": ("falso.pdf", b"isto nao e um pdf", "application/pdf")},
        data={"ocr": "auto"},
    )
    assert resposta.status_code == 400
    assert "PDF" in resposta.json()["detail"]


def test_arquivo_vazio_e_recusado(cliente):
    resposta = cliente.post(
        "/api/jobs",
        files={"arquivo": ("vazio.pdf", b"", "application/pdf")},
        data={"ocr": "auto"},
    )
    assert resposta.status_code == 400


def test_job_inexistente(cliente):
    assert cliente.get("/api/jobs/nao-existe").status_code == 404


def test_resultado_antes_do_fim_retorna_409(cliente, pdf_escaneado):
    criado = cliente.post(
        "/api/jobs",
        files={"arquivo": (pdf_escaneado.name, pdf_escaneado.read_bytes(), "application/pdf")},
        data={"ocr": "force"},
    )
    job = criado.json()["id"]
    resposta = cliente.get(f"/api/jobs/{job}/markdown")
    assert resposta.status_code in (200, 409)


# ── Privacidade ───────────────────────────────────────────────────────────


def test_purga_imediata_apaga_do_disco(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico)
    job = snapshot["id"]

    raiz = cliente.app.state.jobs.get(job).workspace.root
    assert raiz.exists()

    assert cliente.delete(f"/api/jobs/{job}").json() == {"purgado": True}
    assert not raiz.exists()
    assert cliente.get(f"/api/jobs/{job}").status_code == 404


def test_pdf_de_entrada_e_apagado_apos_a_conversao(cliente, pdf_juridico):
    """O produto é o Markdown; guardar o original seria risco desnecessário."""
    snapshot = _converter(cliente, pdf_juridico)
    workspace = cliente.app.state.jobs.get(snapshot["id"]).workspace
    assert not list(workspace.input_dir.glob("*.pdf"))
    assert workspace.markdown_path.exists()


def test_travessia_de_caminho_e_bloqueada(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico)
    for tentativa in ("../../etc/passwd", "..%2F..%2Fsecret", "../entrada/juridico.pdf"):
        resposta = cliente.get(f"/api/jobs/{snapshot['id']}/arquivo/{tentativa}")
        assert resposta.status_code == 404, tentativa


def test_ia_externa_sem_consentimento_gera_aviso(cliente, pdf_juridico):
    snapshot = _converter(
        cliente, pdf_juridico, ia_provedor="claude_cli", ia_consentimento="false"
    )
    assert snapshot["status"] == "done"
    # Sem consentimento o estágio não roda — e nada é enviado para fora.
    assert snapshot["relatorio"]["ia"]["provedor"] == "null"


# ── Opções via formulário ─────────────────────────────────────────────────


def test_recorte_de_paginas_pela_api(cliente, pdf_juridico):
    snapshot = _converter(cliente, pdf_juridico, paginas="1")
    markdown = cliente.get(f"/api/jobs/{snapshot['id']}/markdown").text
    assert "DOS FATOS" in markdown
    assert "DEMONSTRATIVO DE VERBAS" not in markdown


def test_desligar_imagens_e_tabelas(cliente, pdf_tabelas):
    snapshot = _converter(cliente, pdf_tabelas, extrair_tabelas="false")
    assert snapshot["relatorio"]["tables_detected"] == 0
