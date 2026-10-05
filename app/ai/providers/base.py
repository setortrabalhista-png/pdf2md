"""Contrato dos provedores de IA."""

from __future__ import annotations

from typing import Protocol

from app.ai.operations import OperationList


class AiProvider(Protocol):
    name: str
    sends_data_externally: bool

    def is_available(self) -> tuple[bool, str]:
        """(disponível, motivo quando indisponível)."""
        ...

    def propose(self, payload: dict, timeout: int) -> OperationList:
        """Recebe a descrição estrutural do documento e devolve operações."""
        ...
