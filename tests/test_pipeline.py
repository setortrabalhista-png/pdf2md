"""Testes de ponta a ponta sobre os PDFs de referência."""

from __future__ import annotations

import json

import pytest

from app.model.blocks import BlockKind
from app.model.document import PageRoute
from app.pipeline.options import ConversionOptions, OcrMode
from app.pipeline.runner import run_pipeline

# ── Peça jurídica nativa ──────────────────────────────────────────────────


def test_gera_os_tres_produtos(converter_juridico):
    _doc, destino = converter_juridico
    assert (destino / "juridico.md").exists()
    assert (destino / "resultado_processamento.json").exists()
    assert (destino / "assets").exists()


def test_relatorio_tem_as_chaves_especificadas(converter_juridico):
    _doc, destino = converter_juridico
    relatorio = json.loads((destino / "resultado_processamento.json").read_text("utf-8"))
    for chave in ("text_accuracy", "tables_detected", "images_extracted", "warnings"):
        assert chave in relatorio
    assert isinstance(relatorio["warnings"], list)
    assert 0 <= relatorio["text_accuracy"] <= 100


def test_cobertura_de_texto_alta(converter_juridico):
    _doc, destino = converter_juridico
    relatorio = json.loads((destino / "resultado_processamento.json").read_text("utf-8"))
    assert relatorio["text_accuracy"] >= 92, relatorio["fidelidade"]


def test_hierarquia_de_titulos_preservada(converter_juridico):
    doc, _destino = converter_juridico
    titulos = doc.blocks_of_kind(BlockKind.HEADING)
    textos = [t.text for t in titulos]
    assert any("DOS FATOS" in t for t in textos), textos
    assert any("verbas" in t.lower() for t in textos), textos
    # A subseção 1.1 tem de ficar abaixo da seção 1.
    fatos = next(t for t in titulos if "DOS FATOS" in t.text)
    sub = next(t for t in titulos if "verbas não quitadas" in t.text)
    assert sub.level > fatos.level


def test_lista_de_alineas_preservada(converter_juridico):
    doc, destino = converter_juridico
    itens = doc.blocks_of_kind(BlockKind.LIST_ITEM)
    assert len(itens) >= 5
    markdown = (destino / "juridico.md").read_text("utf-8")
    # As alíneas originais têm de sobreviver — renumerar é alterar conteúdo.
    for alinea in ("a)", "b)", "c)", "d)", "e)"):
        assert alinea in markdown, f"alínea {alinea} perdida"


def test_cabecalho_e_rodape_recorrentes_saem_do_corpo(converter_juridico):
    doc, destino = converter_juridico
    markdown = (destino / "juridico.md").read_text("utf-8")
    corpo = markdown.split("---", 2)[-1]   # descarta o cabeçalho YAML

    assert "PODER JUDICIARIO" not in corpo
    assert "fls. 1 de 2" not in corpo
    # Mas ficam registrados para auditoria.
    assert doc.discarded_headers or doc.discarded_footers


def test_nota_de_rodape_identificada(converter_juridico):
    doc, _destino = converter_juridico
    notas = doc.blocks_of_kind(BlockKind.FOOTNOTE)
    assert any("Súmula 437" in n.text for n in notas), [n.text for n in notas]


def test_valores_monetarios_intactos(converter_juridico):
    """Fidelidade numérica é o requisito inegociável num documento jurídico."""
    _doc, destino = converter_juridico
    markdown = (destino / "juridico.md").read_text("utf-8")
    for valor in ("2.400,00", "1.120,00", "2.960,00", "1.866,67", "3.686,40", "9.216,00"):
        assert valor in markdown, f"valor {valor} não chegou ao Markdown"


def test_datas_intactas(converter_juridico):
    _doc, destino = converter_juridico
    markdown = (destino / "juridico.md").read_text("utf-8")
    assert "03/02/2020" in markdown
    assert "14/06/2026" in markdown


def test_tabela_com_bordas_completa(converter_juridico):
    doc, _destino = converter_juridico
    tabelas = doc.blocks_of_kind(BlockKind.TABLE)
    assert len(tabelas) == 1
    tabela = tabelas[0]
    assert tabela.n_cols == 3
    assert tabela.n_rows == 6          # cabeçalho + 5 verbas
    assert tabela.header_rows == 1
    assert tabela.provenance.confidence >= 0.8


def test_markdown_e_valido_como_tabela_gfm(converter_juridico):
    _doc, destino = converter_juridico
    linhas = (destino / "juridico.md").read_text("utf-8").splitlines()
    separadores = [
        linha for linha in linhas
        if linha.startswith("| :---") or linha.startswith("| ---")
    ]
    assert separadores, "nenhuma tabela GFM encontrada"
    for separador in separadores:
        colunas_sep = separador.count("|") - 1
        indice = linhas.index(separador)
        assert linhas[indice - 1].count("|") - 1 == colunas_sep


# ── Tabelas ───────────────────────────────────────────────────────────────


def test_tres_tabelas_detectadas(converter_tabelas):
    doc, _destino = converter_tabelas
    tabelas = doc.blocks_of_kind(BlockKind.TABLE)
    assert len(tabelas) == 3, [(t.engine, t.n_rows, t.n_cols) for t in tabelas]


def test_motores_de_tabela_usados(converter_tabelas):
    doc, _destino = converter_tabelas
    motores = {t.engine for t in doc.blocks_of_kind(BlockKind.TABLE)}
    assert "lines" in motores      # com bordas
    assert "stream" in motores     # sem bordas


def test_celula_mesclada_marcada_e_nao_duplicada(converter_tabelas):
    doc, destino = converter_tabelas
    mescladas = [t for t in doc.blocks_of_kind(BlockKind.TABLE) if t.has_merged_cells]
    assert mescladas, "a tabela com mescla não foi reconhecida"

    tabela = mescladas[0]
    assert tabela.needs_review
    assert "mesclada" in tabela.review_reason

    # O texto mesclado tem de aparecer uma vez só.
    markdown = (destino / "tabelas.md").read_text("utf-8")
    assert markdown.count("RESUMO DO PERÍODO CONTRATUAL") == 1


def test_tabela_sem_bordas_com_colunas_certas(converter_tabelas):
    doc, _destino = converter_tabelas
    sem_bordas = [t for t in doc.blocks_of_kind(BlockKind.TABLE) if t.engine == "stream"]
    assert sem_bordas
    tabela = sem_bordas[0]
    assert tabela.n_cols == 3
    assert tabela.n_rows == 5
    conteudo = " ".join(c.text for linha in tabela.rows for c in linha)
    assert "3.272,72" in conteudo and "1.636,36" in conteudo


def test_coluna_numerica_alinhada_a_direita(converter_tabelas):
    from app.model.blocks import Alignment

    doc, _destino = converter_tabelas
    tabela = doc.blocks_of_kind(BlockKind.TABLE)[0]
    assert tabela.alignments[-1] == Alignment.RIGHT


# ── OCR ───────────────────────────────────────────────────────────────────


def test_pagina_escaneada_roteada_para_ocr(pdf_escaneado, tmp_path, ocr_disponivel):
    if not ocr_disponivel:
        pytest.skip("Tesseract com português não instalado")
    doc = run_pipeline(pdf_escaneado, tmp_path)
    assert doc.pages[0].route == PageRoute.OCR
    assert doc.pages[0].native_char_count < 60


def test_ocr_reconhece_o_conteudo(pdf_escaneado, tmp_path, ocr_disponivel):
    if not ocr_disponivel:
        pytest.skip("Tesseract com português não instalado")
    doc = run_pipeline(pdf_escaneado, tmp_path)
    texto = doc.plain_text()
    assert "RESCIS" in texto.upper()
    assert "12.345.678/0001-90" in texto
    assert doc.pages[0].ocr_mean_confidence and doc.pages[0].ocr_mean_confidence > 70


def test_pagina_scan_inteira_nao_vira_imagem(pdf_escaneado, tmp_path, ocr_disponivel):
    """Reproduzir a página digitalizada como imagem só poluiria o Markdown."""
    if not ocr_disponivel:
        pytest.skip("Tesseract com português não instalado")
    doc = run_pipeline(pdf_escaneado, tmp_path)
    assert not doc.blocks_of_kind(BlockKind.FIGURE)
    relatorio = json.loads((tmp_path / "resultado_processamento.json").read_text("utf-8"))
    assert not [w for w in relatorio["warnings"] if w["code"] == "IMAGES_MISSING"]


def test_documento_hibrido_usa_as_duas_rotas(pdf_hibrido, tmp_path, ocr_disponivel):
    """Peça nativa + anexo digitalizado: o caso comum no PJe."""
    if not ocr_disponivel:
        pytest.skip("Tesseract com português não instalado")
    doc = run_pipeline(pdf_hibrido, tmp_path)
    rotas = {p.route for p in doc.pages}
    assert PageRoute.NATIVE in rotas
    assert PageRoute.OCR in rotas

    texto = doc.plain_text()
    assert "JUNTADA DE DOCUMENTO" in texto        # veio da camada nativa
    assert "12.345.678/0001-90" in texto          # veio do OCR


def test_ocr_never_nao_inventa_texto(pdf_escaneado, tmp_path):
    doc = run_pipeline(
        pdf_escaneado, tmp_path, ConversionOptions(ocr_mode=OcrMode.NEVER)
    )
    assert doc.char_count() == 0
    relatorio = json.loads((tmp_path / "resultado_processamento.json").read_text("utf-8"))
    # E o relatório precisa dizer isso em alto e bom som.
    assert any(w["code"] == "PAGE_NO_TEXT" for w in relatorio["warnings"])


# ── Opções ────────────────────────────────────────────────────────────────


def test_recorte_de_paginas(pdf_juridico, tmp_path):
    doc = run_pipeline(pdf_juridico, tmp_path, ConversionOptions(page_range=[1]))
    paginas_com_conteudo = {b.page for b in doc.blocks if b.kind != BlockKind.PAGE_BREAK}
    assert paginas_com_conteudo == {1}


def test_desligar_tabelas(pdf_tabelas, tmp_path):
    doc = run_pipeline(pdf_tabelas, tmp_path, ConversionOptions(extract_tables=False))
    assert not doc.blocks_of_kind(BlockKind.TABLE)


def test_sem_marcadores_de_pagina(pdf_juridico, tmp_path):
    doc = run_pipeline(pdf_juridico, tmp_path, ConversionOptions(page_markers=False))
    assert not doc.blocks_of_kind(BlockKind.PAGE_BREAK)
    assert "<!-- página" not in (tmp_path / "juridico.md").read_text("utf-8")


def test_conversao_e_deterministica(pdf_juridico, tmp_path):
    """Mesmo PDF, mesma saída — pré-requisito para os testes de regressão."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    run_pipeline(pdf_juridico, a)
    run_pipeline(pdf_juridico, b)
    assert (a / "juridico.md").read_text("utf-8") == (b / "juridico.md").read_text("utf-8")


# ── Robustez ──────────────────────────────────────────────────────────────


def test_pdf_invalido_falha_com_erro_do_dominio(tmp_path):
    from app.core.errors import InvalidPdfError

    ruim = tmp_path / "ruim.pdf"
    ruim.write_bytes(b"%PDF-1.4\nisto nao e um pdf de verdade\n")
    with pytest.raises(InvalidPdfError):
        run_pipeline(ruim, tmp_path / "saida")


def test_arquivo_inexistente(tmp_path):
    from app.core.errors import InvalidPdfError

    with pytest.raises(InvalidPdfError):
        run_pipeline(tmp_path / "nao-existe.pdf", tmp_path / "saida")


def test_ia_sem_consentimento_e_ignorada(pdf_juridico, tmp_path):
    """Provedor externo sem autorização não pode ser acionado."""
    doc = run_pipeline(
        pdf_juridico,
        tmp_path,
        ConversionOptions(ai_enabled=True, ai_consent=False, ai_provider="claude_cli"),
    )
    # O estágio nem chega a rodar: fica desabilitado por falta de consentimento.
    assert not any(e["stage"] == "ai" for e in doc.stage_log)
