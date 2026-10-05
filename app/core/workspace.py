"""Área de trabalho temporária de um job.

Regras de privacidade materializadas aqui:

* cada job tem o seu diretório isolado, com nome aleatório;
* o PDF de entrada é apagado assim que a conversão termina (configurável);
* o diretório inteiro é destruído quando o TTL expira ou quando o usuário pede;
* nada é gravado fora deste diretório.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pdf2md.workspace")


@dataclass
class Workspace:
    job_id: str
    root: Path
    created_at: float = field(default_factory=time.time)

    @property
    def input_dir(self) -> Path:
        return self.root / "entrada"

    @property
    def output_dir(self) -> Path:
        return self.root / "saida"

    @property
    def assets_dir(self) -> Path:
        return self.output_dir / "assets"

    @property
    def markdown_path(self) -> Path:
        """O único .md na raiz da saída.

        O nome vem do PDF de origem, então não é conhecido de antemão. Os
        demais arquivos gerados ficam em `assets/`, o que torna a busca na
        raiz inequívoca.
        """
        encontrados = sorted(self.output_dir.glob("*.md"))
        return encontrados[0] if encontrados else self.output_dir / "documento.md"

    @property
    def report_path(self) -> Path:
        return self.output_dir / "resultado_processamento.json"

    @property
    def bundle_path(self) -> Path:
        return self.root / "conversao.zip"

    def prepare(self) -> Workspace:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self

    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def discard_source(self) -> None:
        """Apaga o PDF de entrada — o produto é o Markdown, não a origem."""
        if not self.input_dir.exists():
            return
        for path in self.input_dir.iterdir():
            try:
                path.unlink()
            except OSError as exc:
                log.warning("não foi possível apagar %s: %s", path, exc)

    def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def bundle(self) -> Path:
        """Empacota Markdown, assets e relatório num único .zip."""
        if self.bundle_path.exists():
            self.bundle_path.unlink()
        archive = shutil.make_archive(
            str(self.root / "conversao"), "zip", root_dir=str(self.output_dir)
        )
        return Path(archive)

    def output_files(self) -> list[dict]:
        """Inventário do que foi gerado, para exibir na interface."""
        if not self.output_dir.exists():
            return []
        entries = []
        for path in sorted(self.output_dir.rglob("*")):
            if path.is_dir():
                continue
            entries.append(
                {
                    "nome": path.relative_to(self.output_dir).as_posix(),
                    "bytes": path.stat().st_size,
                    "tipo": path.suffix.lstrip(".").lower() or "arquivo",
                }
            )
        return entries


def create(root: Path, job_id: str) -> Workspace:
    return Workspace(job_id=job_id, root=root / job_id).prepare()
