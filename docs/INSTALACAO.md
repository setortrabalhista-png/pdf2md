# Instalar em outro computador

## O que não funciona

**Copiar a pasta inteira.** O ambiente virtual (`.venv`) grava dentro de si o
caminho absoluto da máquina onde foi criado:

```
executable = C:\Users\VLV Advogados\AppData\Local\Programs\Python\Python312\python.exe
```

Em outro computador esse caminho não existe, e nada roda. O `.venv` também
responde por quase 90% do tamanho da pasta — 340 MB dos 381 MB.

O que precisa viajar são **0,2 MB**. O resto é reconstruído no destino.

---

## Caminho recomendado: pacote + instalador

### Neste computador

```powershell
.\empacotar.ps1
```

Gera um `.zip` na Área de Trabalho com só o que é portátil: código,
documentação e scripts. Deixa de fora o `.venv`, a pasta `saida/` — que pode
conter documento de cliente — e os PDFs de teste, que são regerados no destino.

Se o outro computador não tiver internet boa, leve os modelos de OCR junto
(fica com ~18 MB):

```powershell
.\empacotar.ps1 -ComModelosOcr
```

Para gravar direto num pendrive:

```powershell
.\empacotar.ps1 -Destino "E:\"
```

### No outro computador

1. Descompacte o `.zip`.
2. Abra a pasta `pdf2md`.
3. Clique com o botão direito em **`instalar.ps1`** → **Executar com o PowerShell**.
4. Depois, duplo clique em **`iniciar.cmd`**.

O instalador cuida de tudo o que faltar:

| Passo | O que faz |
|---|---|
| 1 | Instala o Python 3.12 (via `winget`) se não houver |
| 2 | Instala o Tesseract OCR se não houver |
| 3 | Cria o ambiente virtual e instala as bibliotecas |
| 4 | Baixa o modelo de português do OCR |
| 5 | Converte dois PDFs de teste e confere o resultado |

Pode ser rodado quantas vezes quiser: o que já existe é detectado e pulado.

Ao final ele diz o que ficou pronto:

```
5. Verificacao
--------------
    [ok]   Documento nativo: 97.3% do texto, 1 tabela(s) -> juridico.md
    [ok]   Documento digitalizado: OCR com 95.22% de confianca

  Instalacao concluida.
```

Se algo falhar, ele diz exatamente o quê — e o sistema ainda funciona sem OCR,
com aviso, caso só o Tesseract não instale.

**Tempo:** cerca de 3 minutos numa máquina que já tenha Python. Uns 8 se
precisar instalar Python e Tesseract.

---

## Se a política do Windows bloquear o script

Alguns computadores recusam scripts PowerShell não assinados. A mensagem fala
em *"execução de scripts foi desabilitada neste sistema"*. Abra o PowerShell na
pasta e rode:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
```

Isso libera só aquela execução, sem mudar a política da máquina.

---

## Alternativa: pelo Git

Se o projeto estiver versionado, o outro computador só precisa de Git e do
instalador:

```powershell
git clone <endereco-do-repositorio>
```

```powershell
cd pdf2md; .\instalar.ps1
```

A vantagem é a atualização: `git pull` traz as correções sem refazer nada. A
desvantagem é depender de o repositório estar acessível — e, se for privado,
de credencial configurada na outra máquina.

---

## Alternativa: Docker

Se o outro computador já tiver Docker, é o caminho com menos peças móveis — a
imagem traz Tesseract, Ghostscript e Java prontos, o que ainda habilita os
motores de reserva de tabela (Camelot e Tabula), indisponíveis numa instalação
comum.

```powershell
docker compose up --build
```

Depois, `http://127.0.0.1:8000` como sempre.

---

## Alternativa: acessar pela rede, sem instalar nada

Se os computadores estiverem na mesma rede do escritório, dá para deixar o
sistema rodando **num** deles e acessar dos outros pelo navegador.

**Leia a ressalva antes de fazer isso.** O sistema não tem senha nem
criptografia — foi desenhado para uso local. Ao expô-lo na rede, qualquer pessoa
com acesso a ela pode enviar e baixar documentos, e o tráfego passa em claro. É
aceitável numa rede de escritório cabeada e confiável; **não** é aceitável em
Wi-Fi compartilhado, rede de coworking ou qualquer lugar com acesso de
terceiros.

No computador que vai hospedar:

```powershell
$env:PDF2MD_HOST = "0.0.0.0"; .\run.ps1
```

Descubra o endereço dele:

```powershell
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne 'WellKnown' }).IPAddress
```

Nos outros computadores, abra `http://ENDERECO:8000` no navegador.

Talvez seja preciso liberar a porta no firewall — o Windows costuma perguntar
na primeira vez. Se não perguntar:

```powershell
New-NetFirewallRule -DisplayName "pdf2md" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

Para voltar ao modo local, feche o servidor e rode `.\run.ps1` normalmente —
`PDF2MD_HOST` só vale para aquela sessão do terminal.

---

## Comparação

| Caminho | Instala o quê no destino | Atualizar depois | Quando usar |
|---|---|---|---|
| **Pacote + instalador** | Python, Tesseract, bibliotecas | Novo pacote | O padrão. Funciona offline depois de instalado. |
| **Git** | Git, Python, Tesseract, bibliotecas | `git pull` | Vários computadores que você mantém |
| **Docker** | Docker | `docker compose build` | Onde já houver Docker |
| **Rede** | Nada | Nada | Uso ocasional, rede confiável, sem documento sigiloso |

---

## Requisitos do computador de destino

| Item | Mínimo | Observação |
|---|---|---|
| Sistema | Windows 10/11 | Há `run.sh` para Linux e macOS |
| Python | 3.11 ou 3.12 | O instalador põe o 3.12; **3.13+ ainda não serve** |
| Disco | ~600 MB | 340 MB são bibliotecas Python |
| Memória | 4 GB | 8 GB para OCR em documento longo |
| Internet | Só na instalação | Depois roda offline |

O processador é o que manda no tempo de conversão — OCR é trabalho pesado de
CPU. Um PDF nativo de 200 páginas leva de 10 a 30 segundos; uma página
digitalizada, de 2 a 5 segundos cada.

---

## Conferir uma instalação existente

```powershell
.\instalar.ps1 -PularTestes
```

Mostra o que está instalado sem refazer nada de pesado. Para a verificação
completa, rode sem o parâmetro: ele converte dois PDFs e confere cobertura e
confiança de OCR.

Para desenvolvedores, a suíte completa:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
