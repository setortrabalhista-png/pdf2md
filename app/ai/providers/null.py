"""Provedor nulo — o padrão.

Nenhuma chamada, nenhuma rede, nenhum dado saindo da máquina. Existe para que a
camada de IA seja opcional de verdade: o pipeline roda idêntico com ela ligada
ou desligada.
"""

from __future__ import annotations

from app.ai.operations import OperationList


class NullProvider:
    name = "null"
    sends_data_externally = False

    def is_available(self) -> tuple[bool, str]:
        return True, ""

    def propose(self, payload: dict, timeout: int) -> OperationList:
        return OperationList(operations=[])
