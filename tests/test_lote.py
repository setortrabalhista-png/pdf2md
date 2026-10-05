"""Testes de conversão em lote pela API."""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def cliente():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _enviar(cliente, pdfs, **campos):
    arquivos = [
        ("arquivos", (p.name, p.read_bytes(), "application/pdf")) for p in pdfs
    ]
    resposta = cliente.post("/api/lotes", files=arquivos, data={"ocr": "auto", **campos})
    return resposta


def _aguardar(cliente, batch_id, limite_segundos=240):
    fim = time.time() + limite_segundos
    while time.time() < fim:
        estado = cliente.get(f"/api/lotes/{batch_id}").json()
        if estado["terminado"]:
            return estado
        time.sleep(0.2)
    pytest.fail("o lote não terminou dentro do tempo previsto")


# ── Criação ───────────────────────────────────────────────────────────────


def test_cria_lote_com_varios_documentos(cliente, pdf_juridico, pdf_tabelas):
    resposta = _enviar(cliente, [pdf_juridico, pdf_tabelas])
    assert resposta.status_code == 202, resposta.text
    dados = resposta.json()
    assert dados["total"] == 2
    assert dados["recusados"] == []
    assert dados["id"]


def test_lote_recusa_nao_pdf_mas_processa_o_resto(cliente, pdf_juridico, tmp_path):
    """Um arquivo ruim no meio da pasta não pode inviabilizar o lote inteiro."""
    lixo = tmp_path / "planilha.pdf"
    lixo.write_bytes(b"isto nao e um pdf")

    resposta = _enviar(cliente, [pdf_juridico, lixo])
    assert resposta.status_code == 202
    dados = resposta.json()
    assert dados["total"] == 1
    assert len(dados["recusados"]) == 1
    assert dados["recusados"][0]["arquivo"] == "planilha.pdf"
    assert "não é um PDF" in dados["recusados"][0]["motivo"]


def test_lote_sem_nenhum_arquivo_valido(cliente, tmp_path):
    lixo = tmp_path / "a.pdf"
    lixo.write_bytes(b"nada")
    assert _enviar(cliente, [lixo]).status_code == 400


# ── Execução ──────────────────────────────────────────────────────────────


def test_lote_completo(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    estado = _aguardar(cliente, batch_id)

    assert estado["total"] == 2
    assert estado["convertidos"] == 2
    assert estado["falharam"] == 0
    assert estado["progresso"] == pytest.approx(1.0, abs=0.01)

    documentos = estado["documentos"]
    assert {d["arquivo"] for d in documentos} == {"juridico.pdf", "tabelas.pdf"}
    assert all(d["status"] == "done" for d in documentos)


def test_quadro_de_conferencia(cliente, pdf_juridico, pdf_tabelas):
    """O quadro é o que substitui abrir N relatórios."""
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    estado = _aguardar(cliente, batch_id)

    quadro = {linha["arquivo"]: linha for linha in estado["resumo"]}
    assert set(quadro) == {"juridico.pdf", "tabelas.pdf"}

    juridico = quadro["juridico.pdf"]
    assert juridico["status"] == "ok"
    assert juridico["cobertura"] >= 92
    assert juridico["paginas"] == 2
    assert juridico["tabelas"] == 1

    tabelas = quadro["tabelas.pdf"]
    assert tabelas["tabelas"] == 3
    assert tabelas["tabelas_para_revisar"] == 1


def test_documentos_do_lote_continuam_acessiveis_um_a_um(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    estado = _aguardar(cliente, batch_id)

    for documento in estado["documentos"]:
        job = documento["id"]
        assert documento["lote"] == batch_id
        markdown = cliente.get(f"/api/jobs/{job}/markdown")
        assert markdown.status_code == 200
        assert markdown.text.strip()


# ── Empacotamento ─────────────────────────────────────────────────────────


def test_zip_unico_com_uma_pasta_por_documento(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    _aguardar(cliente, batch_id)

    resposta = cliente.get(f"/api/lotes/{batch_id}/download")
    assert resposta.status_code == 200
    assert resposta.content[:2] == b"PK"

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as zf:
        nomes = zf.namelist()
        assert "_lote.json" in nomes
        assert "juridico/juridico.md" in nomes
        assert "tabelas/tabelas.md" in nomes
        assert "juridico/resultado_processamento.json" in nomes

        indice = json.loads(zf.read("_lote.json"))
        assert len(indice["documentos"]) == 2

        markdown = zf.read("juridico/juridico.md").decode("utf-8")
        assert "DOS FATOS" in markdown


def test_nomes_repetidos_nao_se_sobrescrevem(cliente, pdf_juridico):
    """Dois 'peticao.pdf' de pastas diferentes precisam coexistir no .zip."""
    batch_id = _enviar(cliente, [pdf_juridico, pdf_juridico]).json()["id"]
    _aguardar(cliente, batch_id)

    resposta = cliente.get(f"/api/lotes/{batch_id}/download")
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as zf:
        nomes = zf.namelist()
        assert "juridico/juridico.md" in nomes
        assert "juridico-2/juridico.md" in nomes


def test_download_antes_do_fim_retorna_409(cliente, pdf_escaneado, pdf_hibrido):
    batch_id = _enviar(cliente, [pdf_escaneado, pdf_hibrido], ocr="force").json()["id"]
    resposta = cliente.get(f"/api/lotes/{batch_id}/download")
    # Ou ainda está rodando (409), ou terminou rápido demais (200).
    assert resposta.status_code in (200, 409)
    _aguardar(cliente, batch_id)


def test_resumo_em_csv(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    _aguardar(cliente, batch_id)

    resposta = cliente.get(f"/api/lotes/{batch_id}/resumo.csv")
    assert resposta.status_code == 200
    texto = resposta.text.lstrip("﻿")
    linhas = texto.strip().splitlines()
    assert linhas[0].startswith("arquivo;status;cobertura")
    assert len(linhas) == 3
    assert "juridico.pdf" in texto and "tabelas.pdf" in texto


# ── Progresso ─────────────────────────────────────────────────────────────


def test_sse_do_lote_acompanha_todos_os_documentos(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]

    eventos = []
    with cliente.stream("GET", f"/api/lotes/{batch_id}/events") as fluxo:
        for linha in fluxo.iter_lines():
            if not linha.startswith("data:"):
                continue
            evento = json.loads(linha[5:])
            eventos.append(evento)
            if evento.get("tipo") == "estado" and evento.get("terminado"):
                break

    assert eventos[0]["tipo"] == "estado"
    assert eventos[0]["total"] == 2

    por_documento = [e for e in eventos if e.get("tipo") == "documento"]
    assert por_documento, "nenhum evento por documento"
    # Os dois documentos aparecem identificados no mesmo stream.
    assert len({e["arquivo"] for e in por_documento}) == 2
    # O agregado acompanha junto e nunca anda para trás.
    progressos = [e["lote"]["progresso"] for e in por_documento]
    assert progressos == sorted(progressos)

    final = eventos[-1]
    assert final["terminado"] is True
    assert final["convertidos"] == 2
    assert len(final["resumo"]) == 2


# ── Privacidade ───────────────────────────────────────────────────────────


def test_purga_do_lote_apaga_tudo(cliente, pdf_juridico, pdf_tabelas):
    batch_id = _enviar(cliente, [pdf_juridico, pdf_tabelas]).json()["id"]
    estado = _aguardar(cliente, batch_id)
    jobs = [d["id"] for d in estado["documentos"]]

    raizes = [cliente.app.state.jobs.get(j).workspace.root for j in jobs]
    assert all(r.exists() for r in raizes)

    resposta = cliente.delete(f"/api/lotes/{batch_id}")
    assert resposta.json()["purgado"] is True
    assert resposta.json()["documentos"] == 2

    assert not any(r.exists() for r in raizes)
    assert cliente.get(f"/api/lotes/{batch_id}").status_code == 404
    for job in jobs:
        assert cliente.get(f"/api/jobs/{job}").status_code == 404


def test_limite_de_arquivos_por_lote(cliente, pdf_juridico, monkeypatch):
    settings = cliente.app.state.settings
    original = settings.max_batch_files
    object.__setattr__(settings, "max_batch_files", 1)
    try:
        resposta = _enviar(cliente, [pdf_juridico, pdf_juridico])
        assert resposta.status_code == 413
        assert "acima do limite" in resposta.json()["detail"]
    finally:
        object.__setattr__(settings, "max_batch_files", original)
