"""Testes do provedor Claude CLI — sem chamar a rede.

Exercitam o parsing do envelope, o tratamento de erro e a descoberta do
binário. A chamada real depende de sessão autenticada e por isso não entra na
suíte automática.
"""

from __future__ import annotations

import json

from app.ai.providers.claude_cli import (
    LOGIN_HINT,
    ClaudeCliProvider,
    _humanize,
    _parse_operations,
    _read_envelope,
)

# ── Envelope do CLI ───────────────────────────────────────────────────────


def test_envelope_de_sucesso():
    envelope = json.dumps({"is_error": False, "result": '{"operations":[]}'})
    conteudo, erro = _read_envelope(envelope)
    assert erro is None
    assert conteudo == '{"operations":[]}'


def test_envelope_de_erro_traz_a_mensagem():
    envelope = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    conteudo, erro = _read_envelope(envelope)
    assert conteudo == ""
    assert "Not logged in" in erro


def test_envelope_vazio():
    assert _read_envelope("") == ("", None)


def test_texto_solto_e_tratado_como_conteudo():
    conteudo, erro = _read_envelope('{"operations": []}')
    assert erro is None
    assert "operations" in conteudo


# ── Mensagens acionáveis ──────────────────────────────────────────────────


def test_erro_de_login_vira_orientacao():
    assert _humanize("Not logged in · Please run /login") == LOGIN_HINT


def test_limite_de_uso_e_explicado():
    assert "Limite de uso" in _humanize("Usage limit reached for this account")


def test_erro_desconhecido_passa_intacto():
    assert _humanize("connection reset by peer") == "connection reset by peer"


# ── Parsing das operações ─────────────────────────────────────────────────


def test_operacoes_em_json_puro():
    saida = _parse_operations('{"operations":[{"op":"merge_blocks","ids":["p1","p2"]}]}')
    assert len(saida["operations"]) == 1


def test_operacoes_dentro_de_cerca_de_codigo():
    texto = '```json\n{"operations":[{"op":"mark_as_quote","id":"p1"}]}\n```'
    assert len(_parse_operations(texto)["operations"]) == 1


def test_operacoes_com_texto_em_volta():
    texto = 'Aqui está a análise:\n{"operations":[{"op":"mark_as_quote","id":"p1"}]}\nEspero ajudar.'
    assert len(_parse_operations(texto)["operations"]) == 1


def test_lista_solta_e_aceita():
    assert len(_parse_operations('[{"op":"mark_as_quote","id":"p1"}]')["operations"]) == 1


def test_resposta_sem_json_devolve_lista_vazia():
    assert _parse_operations("Não encontrei nada a corrigir.")["operations"] == []


def test_json_invalido_devolve_lista_vazia():
    assert _parse_operations('{"operations": [ {"op": }')["operations"] == []


def test_resposta_vazia():
    assert _parse_operations("")["operations"] == []


# ── Descoberta do binário ─────────────────────────────────────────────────


def test_caminho_inexistente_e_indisponivel():
    provedor = ClaudeCliProvider(cli_path="/nao/existe/claude")
    disponivel, motivo = provedor.is_available()
    assert not disponivel
    assert "inválido" in motivo


def test_provedor_se_declara_externo():
    assert ClaudeCliProvider().sends_data_externally is True


def test_registro_expoe_os_dois_provedores():
    from app.ai.providers import describe_providers
    from app.config import get_settings

    provedores = {p["id"]: p for p in describe_providers(get_settings())}
    assert provedores["null"]["sends_data_externally"] is False
    assert provedores["claude_cli"]["sends_data_externally"] is True
