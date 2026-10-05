"""Registro de provedores de IA."""

from __future__ import annotations

from app.ai.providers.null import NullProvider


def get_provider(name: str, settings):
    """Instancia o provedor pedido. Desconhecido ⇒ nulo (falha segura)."""
    key = (name or "null").strip().lower()

    if key in ("", "null", "none", "off"):
        return NullProvider()

    if key in ("claude_cli", "claude-code", "claude"):
        from app.ai.providers.claude_cli import ClaudeCliProvider

        return ClaudeCliProvider(
            cli_path=settings.ai_claude_cli_path, model=settings.ai_model
        )

    return NullProvider()


def describe_providers(settings) -> list[dict]:
    """Lista os provedores e o estado de cada um, para a interface."""
    out = []
    for key, label in (
        ("null", "Nenhum (apenas heurística local)"),
        ("claude_cli", "Claude Code CLI (plano Max, sem chave de API)"),
    ):
        provider = get_provider(key, settings)
        available, reason = provider.is_available()
        out.append(
            {
                "id": key,
                "label": label,
                "available": available,
                "reason": reason,
                "sends_data_externally": provider.sends_data_externally,
            }
        )
    return out
