# UAP Files

Baixa e traduz os documentos desclassificados de OVNIs de **war.gov/UFO** (PDF, imagens e vídeos).

---

## Arquitetura

```
war.gov/UFO/
    │
    ▼
ufo_catalog.js        → catalog.csv          (fonte de verdade)
    │
    ▼
ufo_downloader.js     → ACERVO/PDF/
                        ACERVO/IMG/
                        ACERVO/VID/
    │
    ▼
ufo_translate.py      → translated/           (PDFs com texto PT sobreposto)
```

O `catalog.csv` registra o status de cada arquivo: `pending → downloaded → translated`.
Ao rodar `ufo_catalog.js` novamente, novos documentos publicados pelo governo são
acrescentados automaticamente — arquivos já baixados e traduzidos não são afetados.

O `uap_files.py` é a **interface interativa** (TUI) que orquestra todos os passos acima
a partir de um menu no terminal.

---

## Instalação

### Pré-requisito único: Node.js 18+

O único requisito manual é ter Node.js instalado. Tudo o mais é instalado automaticamente.

```bash
# macOS
brew install node

# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Windows / outros: https://nodejs.org
```

### Setup automático

```bash
# 1. Baixar o projeto
git clone <url-do-repositorio>
cd ufo_pipeline

# 2. Instalar tudo
npm run setup
```

O `npm run setup` detecta e instala automaticamente:

| Dependência | macOS | Linux | Windows |
|---|---|---|---|
| Python 3 | `brew install python` | `apt-get install python3` | avisa com link |
| pymupdf, pytesseract, pillow | `pip3 install` | `pip3 install` | `pip install` |
| Tesseract OCR | `brew install tesseract` | `apt-get install tesseract-ocr` | avisa com link |
| puppeteer-core (Node) | `npm install` | `npm install` | `npm install` |
| ffmpeg | avisa (opcional) | avisa (opcional) | avisa (opcional) |
| Google Chrome | avisa com link | avisa com link | avisa com link |

> **Google Chrome** precisa ser instalado manualmente: https://www.google.com/chrome/
> Necessário para contornar a proteção Akamai do war.gov (scripts abrem Chrome real).

### Iniciar

```bash
npm start
# equivalente a: python3 uap_files.py
```

---

## Uso — Interface interativa (recomendado)

```bash
python3 uap_files.py
```

Menu navegável com setas. Opções disponíveis:

| Opção | O que faz |
|---|---|
| Atualizar UAP Files | Busca catálogo atualizado em war.gov e acrescenta novos documentos |
| Baixar UAP Files pendentes | Baixa todos os arquivos com `status=pending` |
| Traduzir UAP Files pendentes | Traduz todos os PDFs baixados ainda não traduzidos |
| Resetar Acervo | Remove todos os arquivos e reseta o catálogo |

---

## Uso — Scripts individuais

### 1. Gerar / atualizar catálogo

```bash
node ufo_catalog.js                  # busca CSV remoto, gera/atualiza catalog.csv
node ufo_catalog.js --status         # mostra resumo sem abrir Chrome
node ufo_catalog.js --resolve-dvids  # resolve URLs de vídeos DVIDS pendentes
```

### 2. Baixar arquivos

```bash
node ufo_downloader.js               # baixa todos os pendentes
node ufo_downloader.js --limit 5     # só os 5 primeiros (teste)
node ufo_downloader.js --retry-failed  # reprocessa entradas com status=failed
```

Arquivos salvos em `ACERVO/PDF/`, `ACERVO/IMG/`, `ACERVO/VID/`.
O `catalog.csv` é atualizado após cada download — interrompa e retome à vontade.

### 3. Traduzir PDFs (EN → PT)

```bash
python3 ufo_translate.py                           # traduz todos os PDFs baixados
python3 ufo_translate.py --limit 3                 # só 3 (teste rápido)
python3 ufo_translate.py --workers 2               # tradução paralela
python3 ufo_translate.py --dpi 250                 # OCR com maior resolução (scans borrados)
python3 ufo_translate.py --translate-id 23         # só o registro ID 23 do catálogo
python3 ufo_translate.py --translate-only arq.pdf  # PDF avulso, sem tocar no catálogo
```

PDFs traduzidos vão para `translated/`. O scan original é preservado com texto PT
sobreposto em cima de cada parágrafo detectado.

**Como a tradução funciona:**
1. Cada página é renderizada como imagem
2. Tesseract OCR extrai texto em 3 passagens (PSM 3, PSM 11 binarizado, PSM 11 escala de cinza) e mescla blocos únicos detectados
3. Parágrafos são enviados em lote para o endpoint público do Google Translate (`translate.googleapis.com`) — sem chave de API
4. Texto traduzido é sobreposto na posição original via PyMuPDF

---

## Estrutura gerada

```
.
├── catalog.csv          ← fonte de verdade (gerado por ufo_catalog.js)
├── pipeline.log         ← log completo da tradução
├── ACERVO/
│   ├── PDF/             ← PDFs originais baixados
│   ├── IMG/             ← imagens originais baixadas
│   └── VID/             ← vídeos baixados
└── translated/          ← PDFs com tradução PT sobreposta
```

---

## Observações

### Akamai WAF

O site usa proteção Akamai que bloqueia Python e Chrome headless.
Os scripts Node.js abrem Chrome não-headless para contornar isso.

### Vídeos DVIDS

Alguns registros têm apenas ID DVIDS (sem URL direta). Rode:

```bash
node ufo_catalog.js --resolve-dvids
```

Os resolvidos ficam `status=pending` e são baixados normalmente.
Os sem URL pública ficam com `status=no_url`.

### Rate-limit na tradução

O endpoint do Google Translate não exige chave mas pode rate-limitar.
Se ocorrer, aumente `SLEEP_SEC` no início de `ufo_translate.py` (padrão: `0.3`s).

### Idempotência

Todos os scripts são idempotentes — interrompa e retome à vontade.
O `catalog.csv` guarda o estado de cada arquivo.