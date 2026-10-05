# Histórico de versões

O número da versão vive no `pyproject.toml`. Ao publicar uma nova:

1. Atualize `version` no `pyproject.toml` e acrescente a seção aqui.
2. Gere o pacote: `.\empacotar.ps1` (sai como `pdf2md-vX.Y.Z.zip`).
3. Crie a Release no GitHub com a tag `vX.Y.Z` e anexe o `.zip`.

---

## v1.0.0

Primeira versão.

**Conversão**
- PDF nativo, digitalizado e híbrido, com OCR decidido página a página
- Títulos, listas, citações, notas de rodapé e numeração preservados
- Tabelas com borda (réguas vetoriais) e sem borda (alinhamento), unidas
  através de quebra de página
- Imagens em qualidade original e figuras vetoriais rasterizadas
- Cabeçalho e rodapé recorrentes removidos do corpo e registrados
- O Markdown leva o nome do PDF de origem

**Qualidade**
- Relatório `resultado_processamento.json` com cobertura de texto, segunda
  opinião de outro extrator, sanidade léxica e confiança de OCR
- Blocos duvidosos marcados com `<!-- REVISAR -->` no Markdown

**Uso**
- Interface web com conversão avulsa e em lote (arquivos ou pasta inteira),
  fila com progresso individual, quadro de conferência e `.zip` único
- Linha de comando e `lote.ps1`, com retomada de lote interrompido
- `instalar.ps1` para máquina nova e `empacotar.ps1` para distribuição

**IA (opcional, desligada por padrão)**
- Refino estrutural via Claude Code CLI, com consentimento explícito
- Verificador que barra qualquer alteração de conteúdo, inclusive
  transposição de dígitos
