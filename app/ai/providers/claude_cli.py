"""Provedor que usa o Claude Code CLI já instalado na máquina.

Aproveita a assinatura existente (plano Max ou Pro): não exige chave de API nem
cobrança por token. O binário é invocado em modo headless, sem nenhuma
ferramenta habilitada — ele só lê o payload e devolve JSON.

**Este provedor envia trechos do documento para os servidores da Anthropic.**
Por isso ele exige consentimento explícito e nunca é o padrão. O que sai da
máquina é apenas o payload estrutural: identificador de bloco, tipo, página,
pistas geométricas e um recorte de texto de até 400 caracteres por bloco. O PDF
nunca é enviado.

## Autenticação

O CLI tem credenciais próprias, **separadas** das do aplicativo Claude Desktop.
Antes do primeiro uso é preciso autenticá-lo uma vez, num terminal:

    claude          # e então, dentro dele:  /login

Sem isso, o provedor devolve "Not logged in" — a conversão continua normalmente,
apenas sem o refino, e o relatório registra o motivo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.ai.operations import OperationList

log = logging.getLogger("pdf2md.ai.claude_cli")

INSTRUCTIONS = """\
Você é um analisador de ESTRUTURA de documentos. Você NÃO escreve, NÃO corrige e
NÃO reescreve texto — apenas reorganiza blocos que já existem.

Receberá um JSON com os blocos extraídos de um PDF. Devolva EXCLUSIVAMENTE um
JSON no formato:

{"operations": [ {"op": "...", ...}, ... ]}

Operações permitidas (nenhuma outra é aceita):

- {"op":"merge_blocks","ids":["p1","p2"],"reason":"..."}          une parágrafos partidos
- {"op":"set_heading_level","id":"h3","level":2,"reason":"..."}   corrige nível de título
- {"op":"promote_to_heading","id":"p9","level":3,"reason":"..."}  parágrafo que é título
- {"op":"demote_to_paragraph","id":"h7","reason":"..."}           título que é parágrafo
- {"op":"promote_to_list","ids":["p4","p5"],"ordered":true,"reason":"..."}
- {"op":"mark_as_footnote","id":"p8","marker":"1","reason":"..."}
- {"op":"mark_as_quote","id":"p2","reason":"..."}
- {"op":"reorder","ids":["p3","p4"],"reason":"..."}               apenas blocos contíguos
- {"op":"set_table_header_rows","id":"t1","header_rows":1,"reason":"..."}
- {"op":"flag_review","id":"t2","note":"..."}                     aponta inconsistência

REGRAS ABSOLUTAS:
1. Nunca proponha alterar, acrescentar ou remover uma única palavra de texto.
   Operações que mudem o conteúdo são detectadas e descartadas automaticamente.
2. Na dúvida, não proponha nada. Uma lista vazia é uma resposta correta.
3. Responda apenas o JSON, sem comentários e sem cercas de código.
"""

LOGIN_HINT = (
    "O Claude Code CLI não está autenticado. Ele tem credenciais próprias, "
    "separadas das do aplicativo. Abra um terminal, execute 'claude' e use "
    "'/login' uma única vez."
)


def _discover_cli() -> str | None:
    """Localiza o binário do Claude Code, inclusive o embutido no aplicativo."""
    found = shutil.which("claude")
    if found:
        return found

    candidates: list[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        bundled = Path(appdata) / "Claude" / "claude-code"
        if bundled.is_dir():
            for version_dir in bundled.iterdir():
                exe = version_dir / "claude.exe"
                if exe.exists():
                    candidates.append(exe)

    home = Path.home()
    for relative in (".local/bin/claude", ".local/bin/claude.exe"):
        path = home / relative
        if path.exists():
            candidates.append(path)

    if not candidates:
        return None

    def version_key(path: Path) -> tuple:
        parts = re.findall(r"\d+", path.parent.name)
        return tuple(int(p) for p in parts) if parts else (0,)

    candidates.sort(key=version_key, reverse=True)
    return str(candidates[0])


class ClaudeCliProvider:
    name = "claude_cli"
    sends_data_externally = True

    def __init__(self, cli_path: str = "", model: str = ""):
        self.cli_path = cli_path or _discover_cli() or ""
        self.model = model

    def is_available(self) -> tuple[bool, str]:
        """Só verifica o binário — conferir a sessão custaria uma chamada.

        Se as credenciais estiverem faltando, o erro aparece na primeira
        proposta, com a orientação de login, e a conversão segue sem o refino.
        """
        if not self.cli_path:
            return False, (
                "Claude Code CLI não encontrado. Instale-o ou informe o caminho "
                "em PDF2MD_AI_CLAUDE_CLI_PATH."
            )
        if not Path(self.cli_path).exists():
            return False, f"caminho inválido: {self.cli_path}"
        return True, ""

    def propose(self, payload: dict, timeout: int) -> OperationList:
        ok, reason = self.is_available()
        if not ok:
            raise RuntimeError(reason)

        prompt = (
            INSTRUCTIONS
            + "\n\nDOCUMENTO:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

        # O prompt vai por stdin, não por argumento: um documento grande gera
        # dezenas de KB, e a linha de comando do Windows tem limite de tamanho
        # e regras de escape que quebrariam o JSON.
        command = [
            self.cli_path,
            "-p",
            "--output-format", "json",
            # Nenhuma ferramenta: o modelo só lê o payload e devolve JSON. Sem
            # isso ele poderia tentar ler arquivos da máquina.
            "--disallowed-tools", "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch",
            "--permission-mode", "default",
        ]
        if self.model:
            command += ["--model", self.model]

        log.info("chamando o Claude Code CLI (%s blocos)", len(payload.get("blocks", [])))
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

        conteudo, erro = _read_envelope(completed.stdout)

        if erro:
            raise RuntimeError(_humanize(erro))

        if completed.returncode != 0 and not conteudo:
            detalhe = (completed.stderr or "").strip()[:400] or "sem detalhes"
            raise RuntimeError(
                f"o CLI retornou código {completed.returncode}: {detalhe}"
            )

        return OperationList(**_parse_operations(conteudo))


def _humanize(mensagem: str) -> str:
    """Transforma o erro cru do CLI em algo acionável."""
    if "not logged in" in mensagem.lower() or "/login" in mensagem.lower():
        return LOGIN_HINT
    if "rate limit" in mensagem.lower() or "usage limit" in mensagem.lower():
        return (
            "Limite de uso do plano atingido. O refino por IA foi ignorado; "
            "a conversão continua válida."
        )
    return mensagem


def _read_envelope(stdout: str) -> tuple[str, str | None]:
    """Separa o conteúdo da resposta do erro reportado pelo CLI.

    O CLI devolve um envelope JSON em que `is_error` marca a falha e `result`
    traz tanto o texto do modelo quanto a mensagem de erro, conforme o caso.
    """
    raw = (stdout or "").strip()
    if not raw:
        return "", None

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None

    if not isinstance(envelope, dict):
        return raw, None

    if envelope.get("is_error"):
        return "", str(envelope.get("result") or "erro não especificado pelo CLI")

    if "result" in envelope:
        return str(envelope["result"]).strip(), None
    if "operations" in envelope:
        return json.dumps(envelope), None

    return raw, None


def _parse_operations(conteudo: str) -> dict:
    """Extrai a lista de operações do texto devolvido pelo modelo."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (conteudo or "").strip(), flags=re.MULTILINE)
    if not raw:
        return {"operations": []}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            log.warning("resposta da IA não continha JSON reconhecível")
            return {"operations": []}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            log.warning("resposta da IA continha JSON inválido")
            return {"operations": []}

    if isinstance(parsed, list):
        return {"operations": parsed}
    if isinstance(parsed, dict):
        return {"operations": parsed.get("operations", [])}
    return {"operations": []}
