/* Interface do pdf2md.

   Um documento e um lote são o mesmo fluxo: a fila sempre existe, só que com
   um item quando o usuário escolhe um PDF só. Isso evita dois caminhos de
   código e dois conjuntos de bugs. */

const $ = (id) => document.getElementById(id);

const estado = {
  arquivos: [],       // File[] escolhidos
  loteId: null,
  fonte: null,        // EventSource
  documentos: [],     // snapshots dos jobs do lote
  atual: null,        // job em exibição
  capacidades: null,
};

/* ── Utilidades ──────────────────────────────────────────────── */

function tamanho(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapar(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

function mostrar(painel) {
  for (const id of ["painel-envio", "painel-progresso", "painel-resultado", "painel-erro"]) {
    $(id).hidden = id !== painel;
  }
}

function falhar(mensagem) {
  $("erro-mensagem").textContent = mensagem;
  mostrar("painel-erro");
}

const ehPdf = (arquivo) =>
  arquivo.type === "application/pdf" || arquivo.name.toLowerCase().endsWith(".pdf");

/* ── Capacidades da instalação ───────────────────────────────── */

async function carregarCapacidades() {
  try {
    estado.capacidades = await (await fetch("/api/capacidades")).json();
  } catch {
    $("privacidade").textContent = "servidor indisponível";
    return;
  }

  const c = estado.capacidades;

  $("privacidade").textContent = `🔒 local · ${c.privacidade.endereco}`;
  $("rodape-privacidade").textContent =
    `Os arquivos ficam apenas nesta máquina e são apagados automaticamente após ` +
    `${c.limites.ttl_minutos} minutos. Até ${c.limites.lote_max_arquivos} PDFs por ` +
    `lote, ${c.limites.tamanho_maximo_mb} MB por arquivo.`;
  atualizarNota();

  const idioma = $("idioma");
  idioma.innerHTML = "";
  if (c.ocr.disponivel && c.ocr.idiomas.length) {
    const nomes = { por: "Português", eng: "Inglês", spa: "Espanhol" };
    for (const codigo of c.ocr.idiomas) {
      if (codigo === "osd") continue;
      const opcao = document.createElement("option");
      opcao.value = codigo;
      opcao.textContent = nomes[codigo] || codigo;
      opcao.selected = codigo === c.ocr.idioma_padrao;
      idioma.appendChild(opcao);
    }
    $("ocr-estado").textContent = `Tesseract disponível · ${c.ocr.dpi} dpi`;
  } else {
    $("ocr").value = "never";
    $("ocr").disabled = true;
    idioma.disabled = true;
    $("ocr-estado").textContent =
      "Tesseract não encontrado — páginas digitalizadas sairão sem texto.";
  }

  const ia = $("ia");
  ia.innerHTML = "";
  for (const p of c.ia.provedores) {
    const opcao = document.createElement("option");
    opcao.value = p.id;
    opcao.textContent = p.available ? p.label : `${p.label} — indisponível`;
    opcao.disabled = !p.available;
    opcao.dataset.externo = String(p.sends_data_externally);
    ia.appendChild(opcao);
  }
  ia.value = "null";
  atualizarAvisoIa();
}

function atualizarAvisoIa() {
  const opcao = $("ia").selectedOptions[0];
  const externo = opcao && opcao.dataset.externo === "true";
  $("alerta-ia").hidden = !externo;
  if (!externo) $("ia-consentimento").checked = false;
  $("ia-estado").textContent = externo
    ? "Envia trechos dos documentos para fora desta máquina."
    : "Nenhum dado sai da máquina.";
}

function atualizarNota() {
  const c = estado.capacidades;
  $("dz-nota").textContent = c
    ? `Somente .pdf · até ${c.limites.lote_max_arquivos} arquivos · ` +
      `${c.limites.tamanho_maximo_mb} MB cada`
    : "Somente arquivos .pdf";
}

/* ── Seleção de arquivos ─────────────────────────────────────── */

function adicionarArquivos(novos) {
  const limite = estado.capacidades?.limites.lote_max_arquivos ?? 100;
  const maxBytes = (estado.capacidades?.limites.tamanho_maximo_mb ?? 200) * 1024 * 1024;

  const recusados = [];
  for (const arquivo of novos) {
    if (!ehPdf(arquivo)) {
      recusados.push(`${arquivo.name} — não é PDF`);
      continue;
    }
    if (arquivo.size > maxBytes) {
      recusados.push(`${arquivo.name} — ${tamanho(arquivo.size)}, acima do limite`);
      continue;
    }
    // Mesma origem duas vezes (arrastar a pasta duas vezes) não duplica.
    const repetido = estado.arquivos.some(
      (a) => a.name === arquivo.name && a.size === arquivo.size
    );
    if (repetido) continue;

    if (estado.arquivos.length >= limite) {
      recusados.push(`${arquivo.name} — lote cheio (máximo ${limite})`);
      continue;
    }
    estado.arquivos.push(arquivo);
  }

  renderizarSelecao(recusados);
}

function removerArquivo(indice) {
  estado.arquivos.splice(indice, 1);
  renderizarSelecao([]);
}

function renderizarSelecao(recusados) {
  const total = estado.arquivos.length;
  const bytes = estado.arquivos.reduce((s, a) => s + a.size, 0);

  $("selecao").hidden = total === 0;
  $("converter").disabled = total === 0;
  $("dropzone").classList.toggle("carregado", total > 0);

  $("selecao-titulo").textContent =
    total === 1
      ? `1 documento · ${tamanho(bytes)}`
      : `${total} documentos · ${tamanho(bytes)}`;

  $("lista-selecao").innerHTML = estado.arquivos
    .map(
      (a, i) =>
        `<li><span class="nome">${escapar(a.name)}</span>` +
        `<span class="tam">${tamanho(a.size)}</span>` +
        `<button class="remover" data-indice="${i}" title="Remover">×</button></li>`
    )
    .join("");

  for (const botao of document.querySelectorAll("#lista-selecao .remover")) {
    botao.addEventListener("click", () => removerArquivo(Number(botao.dataset.indice)));
  }

  const caixa = $("recusados");
  caixa.hidden = !recusados.length;
  if (recusados.length) {
    caixa.innerHTML =
      `<strong>${recusados.length} ignorado(s):</strong> ` +
      recusados.map(escapar).join(" · ");
  }

  $("converter").textContent =
    total > 1 ? `Converter ${total} documentos` : "Converter";
}

/** Percorre uma pasta arrastada, recursivamente. */
async function lerEntrada(entrada, acumulador) {
  if (entrada.isFile) {
    await new Promise((resolve) =>
      entrada.file((arquivo) => {
        if (ehPdf(arquivo)) acumulador.push(arquivo);
        resolve();
      }, resolve)
    );
    return;
  }
  if (!entrada.isDirectory) return;

  const leitor = entrada.createReader();
  // readEntries devolve no máximo 100 por chamada; é preciso insistir.
  while (true) {
    const lote = await new Promise((resolve) =>
      leitor.readEntries(resolve, () => resolve([]))
    );
    if (!lote.length) break;
    for (const filho of lote) await lerEntrada(filho, acumulador);
  }
}

function ligarDropzone() {
  const zona = $("dropzone");
  const entrada = $("arquivo");
  const entradaPasta = $("arquivo-pasta");

  $("escolher-arquivos").addEventListener("click", (e) => {
    e.stopPropagation();
    entrada.click();
  });
  $("escolher-pasta").addEventListener("click", (e) => {
    e.stopPropagation();
    entradaPasta.click();
  });
  zona.addEventListener("click", () => entrada.click());
  zona.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      entrada.click();
    }
  });

  entrada.addEventListener("change", () => {
    adicionarArquivos([...entrada.files]);
    entrada.value = "";
  });
  entradaPasta.addEventListener("change", () => {
    adicionarArquivos([...entradaPasta.files]);
    entradaPasta.value = "";
  });

  for (const evento of ["dragenter", "dragover"]) {
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.add("ativo");
    });
  }
  zona.addEventListener("dragleave", (e) => {
    e.preventDefault();
    zona.classList.remove("ativo");
  });

  zona.addEventListener("drop", async (e) => {
    e.preventDefault();
    zona.classList.remove("ativo");

    const itens = [...(e.dataTransfer.items || [])];
    const temEntradas = itens.some((i) => i.webkitGetAsEntry);

    if (temEntradas) {
      $("dz-nota").textContent = "Lendo…";
      const encontrados = [];
      const entradas = itens
        .map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry())
        .filter(Boolean);
      for (const raiz of entradas) await lerEntrada(raiz, encontrados);
      atualizarNota();
      adicionarArquivos(encontrados);
    } else {
      adicionarArquivos([...e.dataTransfer.files]);
    }
  });

  $("limpar-selecao").addEventListener("click", () => {
    estado.arquivos = [];
    renderizarSelecao([]);
  });
}

/* ── Conversão ───────────────────────────────────────────────── */

function opcoesDoFormulario(dados) {
  dados.append("ocr", $("ocr").value);
  dados.append("idioma", $("idioma").value || "por");
  dados.append("paginas", $("paginas").value.trim());
  dados.append("extrair_imagens", $("op-imagens").checked);
  dados.append("extrair_tabelas", $("op-tabelas").checked);
  dados.append("figuras_vetoriais", $("op-vetores").checked);
  dados.append("marcadores_de_pagina", $("op-paginas").checked);
  dados.append("unir_entre_paginas", $("op-unir").checked);
  dados.append("ia_provedor", $("ia").value);
  dados.append("ia_consentimento", $("ia-consentimento").checked);
  return dados;
}

async function converter() {
  if (!estado.arquivos.length) return;

  if (!$("alerta-ia").hidden && !$("ia-consentimento").checked) {
    $("ia-estado").textContent =
      "Marque a autorização acima ou escolha 'Nenhum' para prosseguir sem IA.";
    return;
  }

  const dados = new FormData();
  for (const arquivo of estado.arquivos) dados.append("arquivos", arquivo);
  opcoesDoFormulario(dados);

  $("converter").disabled = true;
  $("progresso-titulo").textContent =
    estado.arquivos.length > 1
      ? `Convertendo ${estado.arquivos.length} documentos`
      : `Convertendo ${estado.arquivos[0].name}`;
  $("barra").style.width = "0%";
  $("percentual").textContent = "0%";
  $("progresso-mensagem").textContent = "Enviando os arquivos…";
  $("fila").innerHTML = estado.arquivos
    .map(
      (a, i) =>
        `<li class="fila-item" data-indice="${i}">` +
        `<span class="fila-estado" data-estado="queued">•</span>` +
        `<span class="fila-nome">${escapar(a.name)}</span>` +
        `<span class="fila-msg">na fila</span></li>`
    )
    .join("");
  mostrar("painel-progresso");

  let lote;
  try {
    const resposta = await fetch("/api/lotes", { method: "POST", body: dados });
    lote = await resposta.json();
    if (!resposta.ok) throw new Error(lote.detail || "falha ao criar o lote");
  } catch (erro) {
    $("converter").disabled = false;
    falhar(erro.message);
    return;
  }

  if (lote.recusados?.length) {
    $("progresso-mensagem").textContent =
      `${lote.recusados.length} arquivo(s) recusado(s) pelo servidor.`;
  }

  estado.loteId = lote.id;
  acompanhar(lote.id);
}

function acompanhar(loteId) {
  if (estado.fonte) estado.fonte.close();

  const fonte = new EventSource(`/api/lotes/${loteId}/events`);
  estado.fonte = fonte;

  fonte.onmessage = (evento) => {
    const dados = JSON.parse(evento.data);

    const agregado = dados.tipo === "estado" ? dados : dados.lote;
    if (agregado) {
      const fracao = agregado.progresso ?? 0;
      $("barra").style.width = `${(fracao * 100).toFixed(1)}%`;
      $("percentual").textContent = `${Math.round(fracao * 100)}%`;
      $("progresso-mensagem").textContent =
        `${agregado.concluidos} de ${agregado.total} concluído(s)` +
        (agregado.falharam ? ` · ${agregado.falharam} com falha` : "");
    }

    if (dados.tipo === "documento") {
      atualizarLinhaDaFila(dados);
    }

    if (dados.tipo === "estado" && dados.terminado) {
      fonte.close();
      apresentar(dados);
    }
  };

  fonte.onerror = () => {
    fonte.close();
    fetch(`/api/lotes/${loteId}`)
      .then((r) => r.json())
      .then((snapshot) => {
        if (snapshot.terminado) apresentar(snapshot);
        else falhar("a conexão com o servidor foi interrompida");
      })
      .catch(() => falhar("a conexão com o servidor foi interrompida"));
  };
}

function atualizarLinhaDaFila(evento) {
  const item = document.querySelector(`.fila-item[data-indice="${evento.indice}"]`);
  if (!item) return;

  const marcador = item.querySelector(".fila-estado");
  const mensagem = item.querySelector(".fila-msg");

  marcador.dataset.estado = evento.status;
  marcador.textContent =
    evento.status === "done" ? "✓" : evento.status === "failed" ? "✕" : "●";

  if (evento.status === "done") {
    const cobertura = evento.documento?.relatorio?.text_accuracy;
    mensagem.textContent = cobertura != null ? `${cobertura}%` : "pronto";
  } else if (evento.status === "failed") {
    mensagem.textContent = "falhou";
  } else {
    mensagem.textContent = evento.mensagem || "processando";
  }
}

/* ── Resultado ───────────────────────────────────────────────── */

async function apresentar(snapshot) {
  estado.documentos = snapshot.documentos || [];
  const quadro = snapshot.resumo || [];
  const varios = estado.documentos.length > 1;

  const status = vereditoDoLote(quadro);
  const rotulos = { ok: "Pronto", atencao: "Atenção", revisar: "Revisar" };
  const selo = $("selo-status");
  selo.textContent = rotulos[status] || status;
  selo.className = `selo ${status}`;

  $("resultado-titulo").textContent = varios
    ? `${snapshot.total} documentos convertidos`
    : estado.documentos[0]?.arquivo || "Conversão concluída";

  $("indicadores").innerHTML = indicadoresDoLote(snapshot, quadro)
    .map(
      ([rotulo, valor]) =>
        `<div class="indicador"><b>${valor}</b><span>${rotulo}</span></div>`
    )
    .join("");

  const problemas = quadro.filter((l) => l.status === "revisar" || l.estado === "failed");
  $("pendencias").hidden = problemas.length === 0;
  if (problemas.length) {
    $("pendencias").innerHTML =
      `<strong>Confira antes de usar:</strong><ul>` +
      problemas
        .map(
          (l) =>
            `<li>${escapar(l.arquivo)} — ${escapar(l.erro || (l.pendencias || []).join("; ") || "revisar")}</li>`
        )
        .join("") +
      `</ul>`;
  }

  $("quadro-lote").hidden = !varios;
  $("baixar-csv").hidden = !varios;
  $("baixar-md").hidden = varios;
  $("baixar").textContent = varios
    ? `Baixar os ${snapshot.convertidos} documentos (.zip)`
    : "Baixar .zip (Markdown + imagens + relatório)";

  if (varios) renderizarQuadro(quadro);

  const primeiro = estado.documentos.find((d) => d.status === "done");
  if (primeiro) {
    await exibirDocumento(primeiro.id, varios);
  } else {
    $("visualizador").hidden = true;
  }

  mostrar("painel-resultado");
}

function vereditoDoLote(quadro) {
  if (quadro.some((l) => l.status === "revisar" || l.estado === "failed")) return "revisar";
  if (quadro.some((l) => l.status === "atencao")) return "atencao";
  return "ok";
}

function indicadoresDoLote(snapshot, quadro) {
  const soma = (campo) =>
    quadro.reduce((total, linha) => total + (linha[campo] || 0), 0);

  const coberturas = quadro.map((l) => l.cobertura).filter((c) => c != null);
  const media = coberturas.length
    ? (coberturas.reduce((a, b) => a + b, 0) / coberturas.length).toFixed(1)
    : "—";

  if (snapshot.total === 1) {
    const l = quadro[0] || {};
    return [
      ["Cobertura do texto", l.cobertura != null ? `${l.cobertura}%` : "—"],
      ["Páginas", l.paginas ?? "—"],
      ["Tabelas", `${l.tabelas ?? 0}${l.tabelas_para_revisar ? ` (${l.tabelas_para_revisar}⚠)` : ""}`],
      ["Imagens", l.imagens ?? 0],
      ["Páginas com OCR", l.paginas_com_ocr ?? 0],
    ];
  }

  return [
    ["Documentos", `${snapshot.convertidos}/${snapshot.total}`],
    ["Cobertura média", `${media}%`],
    ["Páginas", soma("paginas")],
    ["Tabelas", soma("tabelas")],
    ["Imagens", soma("imagens")],
    ["Páginas com OCR", soma("paginas_com_ocr")],
  ];
}

function renderizarQuadro(quadro) {
  const rotulos = { ok: "Pronto", atencao: "Atenção", revisar: "Revisar", falhou: "Falhou" };

  $("corpo-quadro").innerHTML = quadro
    .map((l) => {
      const status = l.estado === "failed" ? "falhou" : l.status || l.estado;
      const clicavel = l.estado === "done";
      return (
        `<tr data-job="${l.id}" class="${clicavel ? "clicavel" : "inerte"}">` +
        `<td class="doc">${escapar(l.arquivo)}</td>` +
        `<td><span class="marca marca-${status}">${rotulos[status] || status}</span></td>` +
        `<td class="num">${l.cobertura != null ? l.cobertura + "%" : "—"}</td>` +
        `<td class="num">${l.paginas ?? "—"}</td>` +
        `<td class="num">${l.tabelas ?? "—"}</td>` +
        `<td class="num">${l.tabelas_para_revisar ? "⚠ " + l.tabelas_para_revisar : "—"}</td>` +
        `<td class="num">${l.imagens ?? "—"}</td>` +
        `<td class="num">${l.paginas_com_ocr ?? "—"}</td>` +
        `</tr>`
      );
    })
    .join("");

  for (const linha of document.querySelectorAll("#corpo-quadro tr.clicavel")) {
    linha.addEventListener("click", () => exibirDocumento(linha.dataset.job, true));
  }
}

async function exibirDocumento(jobId, marcarLinha) {
  estado.atual = jobId;
  $("visualizador").hidden = false;

  const documento = estado.documentos.find((d) => d.id === jobId);
  $("documento-atual").hidden = !marcarLinha;
  if (marcarLinha && documento) {
    $("documento-atual").textContent = `Exibindo: ${documento.arquivo}`;
  }

  for (const linha of document.querySelectorAll("#corpo-quadro tr")) {
    linha.classList.toggle("selecionada", linha.dataset.job === jobId);
  }

  $("aba-relatorio").textContent = JSON.stringify(documento?.relatorio ?? {}, null, 2);
  $("aba-arquivos").innerHTML =
    `<ul class="lista-arquivos">` +
    (documento?.arquivos || [])
      .map(
        (a) =>
          `<li><a href="/api/jobs/${jobId}/arquivo/${encodeURI(a.nome)}" target="_blank" ` +
          `rel="noopener">${escapar(a.nome)}</a>` +
          `<span class="tam">${tamanho(a.bytes)}</span></li>`
      )
      .join("") +
    `</ul>`;

  $("aba-preview").innerHTML = "<p class='carregando'>Carregando…</p>";
  $("aba-fonte").textContent = "";

  const [markdown, preview] = await Promise.all([
    fetch(`/api/jobs/${jobId}/markdown`).then((r) => r.text()).catch(() => ""),
    fetch(`/api/jobs/${jobId}/preview`).then((r) => r.text()).catch(() => ""),
  ]);

  // Uma troca rápida de documento pode chegar fora de ordem.
  if (estado.atual !== jobId) return;

  $("aba-fonte").textContent = markdown;
  $("aba-preview").innerHTML = preview;
  selecionarAba("preview");
}

function selecionarAba(nome) {
  for (const botao of document.querySelectorAll(".aba")) {
    botao.classList.toggle("ativa", botao.dataset.aba === nome);
  }
  for (const chave of ["preview", "fonte", "arquivos", "relatorio"]) {
    $(`aba-${chave}`).hidden = chave !== nome;
  }
}

/* ── Ações ───────────────────────────────────────────────────── */

function reiniciar() {
  if (estado.fonte) estado.fonte.close();
  estado.arquivos = [];
  estado.loteId = null;
  estado.documentos = [];
  estado.atual = null;
  $("arquivo").value = "";
  $("arquivo-pasta").value = "";
  $("dropzone").classList.remove("carregado");
  renderizarSelecao([]);
  atualizarNota();
  mostrar("painel-envio");
}

async function apagar() {
  if (!estado.loteId) {
    reiniciar();
    return;
  }
  try {
    await fetch(`/api/lotes/${estado.loteId}`, { method: "DELETE" });
  } catch {
    /* pode já ter expirado */
  }
  reiniciar();
}

/* ── Ligações ────────────────────────────────────────────────── */

ligarDropzone();
carregarCapacidades();

$("converter").addEventListener("click", converter);
$("ia").addEventListener("change", atualizarAvisoIa);
$("novo").addEventListener("click", reiniciar);
$("apagar").addEventListener("click", apagar);
$("cancelar").addEventListener("click", apagar);
$("erro-voltar").addEventListener("click", reiniciar);

$("baixar").addEventListener("click", () => {
  window.location.href = `/api/lotes/${estado.loteId}/download`;
});
$("baixar-md").addEventListener("click", () => {
  if (!estado.atual) return;
  // O .md leva o nome do PDF de origem; o nome real vem no relatório.
  const documento = estado.documentos.find((d) => d.id === estado.atual);
  const nome =
    documento?.relatorio?.documento?.arquivo_markdown ||
    documento?.arquivos?.find((a) => a.nome.endsWith(".md"))?.nome ||
    "documento.md";
  window.location.href =
    `/api/jobs/${estado.atual}/arquivo/${encodeURIComponent(nome)}`;
});
$("baixar-csv").addEventListener("click", () => {
  window.location.href = `/api/lotes/${estado.loteId}/resumo.csv`;
});

for (const botao of document.querySelectorAll(".aba")) {
  botao.addEventListener("click", () => selecionarAba(botao.dataset.aba));
}
