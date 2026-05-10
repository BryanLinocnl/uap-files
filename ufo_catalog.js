#!/usr/bin/env node
/**
 * ufo_catalog.js
 * Gera/atualiza catalog.csv — fonte de verdade de todos os arquivos UFO.
 *
 * Uso:
 *   node ufo_catalog.js          # Atualiza catálogo (abre Chrome, busca CSV remoto)
 *   node ufo_catalog.js --status # Só mostra resumo (sem abrir Chrome)
 *
 * Ao rodar novamente, novos documentos publicados pelo governo são acrescentados
 * automaticamente, preservando o status dos arquivos já baixados/traduzidos.
 */

const puppeteer = require('puppeteer-core');
const fs        = require('fs');
const path      = require('path');

const CHROME_PATH  = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CSV_REMOTE   = 'https://www.war.gov/Portals/1/Interactive/2026/UFO/uap-csv.csv';
const CATALOG_FILE = path.join(__dirname, 'catalog.csv');

const COLUMNS = [
  'id', 'title', 'type', 'dl_url', 'dvids_id', 'agency', 'release_date',
  'filename', 'local_dir', 'status', 'local_path', 'bytes', 'downloaded_at',
  'translated', 'translated_at',
];

// ── CSV helpers ────────────────────────────────────────────────────────────────
function csvEscape(val) {
  const s = String(val ?? '');
  return (s.includes(',') || s.includes('"') || s.includes('\n'))
    ? '"' + s.replace(/"/g, '""') + '"'
    : s;
}

function tokenizeCSV(text) {
  const rows = [];
  let row = [], cur = '', inQ = false, i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '"') {
      if (inQ && text[i + 1] === '"') { cur += '"'; i += 2; continue; }
      inQ = !inQ;
    } else if (ch === ',' && !inQ) {
      row.push(cur); cur = '';
    } else if ((ch === '\r' || ch === '\n') && !inQ) {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cur); cur = '';
      if (row.some(Boolean)) rows.push(row);
      row = [];
    } else {
      cur += ch;
    }
    i++;
  }
  if (cur || row.length) { row.push(cur); if (row.some(Boolean)) rows.push(row); }
  return rows;
}

function parseCatalog() {
  if (!fs.existsSync(CATALOG_FILE)) return [];
  const rows = tokenizeCSV(fs.readFileSync(CATALOG_FILE, 'utf8').replace(/^﻿/, ''));
  if (rows.length < 2) return [];
  const header = rows[0];
  return rows.slice(1).map(cols => {
    const r = {};
    for (const col of COLUMNS) r[col] = cols[header.indexOf(col)] ?? '';
    return r;
  });
}

function writeCatalog(records) {
  const lines = [COLUMNS.join(',')];
  for (const r of records) lines.push(COLUMNS.map(c => csvEscape(r[c] ?? '')).join(','));
  fs.writeFileSync(CATALOG_FILE, lines.join('\n') + '\n', 'utf8');
}

// ── Remote CSV parser ──────────────────────────────────────────────────────────
function dirForUrl(url) {
  const ext = path.extname(url).toLowerCase();
  if (['.mp4', '.mov', '.avi', '.wmv', '.webm', '.mkv', '.mpg', '.mpeg', '.m3u8'].includes(ext)) return 'VID';
  if (['.jpg', '.jpeg', '.png', '.gif', '.webp', '.tiff', '.bmp'].includes(ext)) return 'IMG';
  return 'PDF';
}

// Para HLS (.m3u8), ffmpeg baixa como .mp4 — ajusta o filename de destino
function filenameForUrl(url) {
  const base = fileNameFromUrl(url);
  return base.endsWith('.m3u8') ? base.slice(0, -5) + '.mp4' : base;
}

function fileNameFromUrl(url) {
  return decodeURIComponent(url.split('/').pop().split('?')[0]);
}

function parseRemoteCSV(rawText) {
  const text = rawText.replace(/^﻿/, '');
  const rows = tokenizeCSV(text);
  if (rows.length < 2) return [];
  const header = rows[0];
  const col = name => header.findIndex(h => h.trim() === name);

  const iTitle   = col('Title');
  const iType    = col('Type');
  const iDlUrl   = col('PDF | Image Link');
  const iDvids   = col('DVIDS Video ID');
  const iAgency  = col('Agency');
  const iRelease = col('Release Date');

  const seen = new Set();
  const out  = [];
  for (const cols of rows.slice(1)) {
    const title = (cols[iTitle] || '').replace(/\s+/g, ' ').trim();
    if (!title) continue;
    const dlUrl   = (cols[iDlUrl] || '').trim();
    const dvidsId = (cols[iDvids] || '').trim();
    const filename = dlUrl ? fileNameFromUrl(dlUrl) : '';
    const key = filename || dvidsId || title;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      title,
      type:         (cols[iType]    || '').trim().toUpperCase(),
      dl_url:       dlUrl,
      dvids_id:     dvidsId,
      agency:       (cols[iAgency]  || '').trim(),
      release_date: (cols[iRelease] || '').trim(),
      filename,
      local_dir:    dlUrl ? dirForUrl(dlUrl) : (dvidsId ? 'VID' : ''),
    });
  }
  return out;
}

// ── Status display ─────────────────────────────────────────────────────────────
function showStatus(records) {
  const cnt = {};
  for (const r of records) cnt[r.status] = (cnt[r.status] || 0) + 1;
  const pad = n => String(n).padStart(4);

  console.error('\n' + '═'.repeat(52));
  console.error(`  Catálogo: ${records.length} entradas únicas`);
  console.error('─'.repeat(52));
  for (const [s, n] of Object.entries(cnt)) {
    console.error(`  ${s.padEnd(16)}: ${pad(n)}`);
  }
  const dlPdf = records.filter(r => r.local_dir === 'PDF' && r.status === 'downloaded');
  const tr    = dlPdf.filter(r => r.translated === 'yes');
  console.error('─'.repeat(52));
  console.error(`  PDFs baixados   : ${pad(dlPdf.length)}`);
  console.error(`  PDFs traduzidos : ${pad(tr.length)}`);
  console.error('═'.repeat(52) + '\n');
}

// ── DVIDS resolver ─────────────────────────────────────────────────────────────
// Navega até a página do vídeo no dvidshub.net e tenta extrair URL de download.
async function resolveDvidsUrl(browser, dvidsId) {
  const page = await browser.newPage();
  try {
    await page.goto(`https://www.dvidshub.net/video/${dvidsId}`, {
      waitUntil: 'networkidle2', timeout: 30000,
    });

    const url = await page.evaluate(() => {
      // 1. Elemento <video><source>
      const src = document.querySelector('video source[src]');
      if (src?.src && src.src.includes('http')) return src.src;

      // 2. Link de download explícito
      const dlLinks = Array.from(document.querySelectorAll('a[href]'));
      const mp4 = dlLinks.find(a => /\.(mp4|mov|webm|avi)/i.test(a.href));
      if (mp4) return mp4.href;

      // 3. Atributo data-* em players de vídeo comuns
      const player = document.querySelector('[data-src],[data-video-src],[data-url]');
      if (player) {
        return player.dataset.src || player.dataset.videoSrc || player.dataset.url || null;
      }

      // 4. URL embebida em JSON inline (jwplayer / videojs)
      const scripts = Array.from(document.querySelectorAll('script:not([src])'));
      for (const s of scripts) {
        const m = s.textContent.match(/"file"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"/i)
               || s.textContent.match(/sources\s*:\s*\[\s*\{\s*src\s*:\s*['"]([^'"]+\.mp4[^'"]*)/i)
               || s.textContent.match(/['"]?(https?:\/\/[^'">\s]+\.mp4)/i);
        if (m) return m[1];
      }

      return null;
    });

    return url || null;
  } catch {
    return null;
  } finally {
    await page.close().catch(() => {});
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);

  if (args.includes('--status')) {
    const cat = parseCatalog();
    if (!cat.length) console.error('Catálogo vazio. Rode: node ufo_catalog.js');
    else showStatus(cat);
    return;
  }

  // ── --resolve-dvids: extrai URLs dos vídeos DVIDS e corrige entradas .m3u8 ──
  if (args.includes('--resolve-dvids')) {
    const catalog = parseCatalog();
    // Processa: sem URL + entradas que foram resolvidas como .m3u8 (manifesto HLS)
    const toProcess = catalog.filter(r =>
      r.dvids_id && (
        r.status === 'no_url' ||
        (r.dl_url && path.extname(r.dl_url).toLowerCase() === '.m3u8')
      )
    );
    if (!toProcess.length) {
      console.error('Nenhuma entrada DVIDS pendente. Rode com --status para ver o estado.');
      return;
    }
    console.error(`Processando ${toProcess.length} vídeos DVIDS... Abrindo Chrome.`);
    const browser = await puppeteer.launch({
      executablePath: CHROME_PATH,
      headless: false,
      args: ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check', '--window-position=-2000,-2000'],
    });
    try {
      let resolved = 0, failed = 0;
      for (let i = 0; i < toProcess.length; i++) {
        const rec = toProcess[i];
        const pct = Math.floor(((i + 1) / toProcess.length) * 100);
        process.stderr.write(`  [${String(pct).padStart(3)}%] (${i+1}/${toProcess.length}) ${rec.title.slice(0, 50)}\r`);

        // Apaga arquivo .m3u8 salvo incorretamente (apenas o manifesto, ~0 KB)
        if (rec.local_path) {
          const wrong = path.join(__dirname, rec.local_path);
          if (fs.existsSync(wrong) && fs.statSync(wrong).size < 4096) fs.unlinkSync(wrong);
        }

        const url = await resolveDvidsUrl(browser, rec.dvids_id);
        const entry = catalog.find(r => r.id === rec.id);
        if (url) {
          const filename = filenameForUrl(url); // .m3u8 → .mp4
          Object.assign(entry, {
            dl_url: url, filename, local_dir: 'VID',
            status: 'pending', local_path: '', bytes: '', downloaded_at: '',
          });
          resolved++;
          console.error(`\n  ✓ ${rec.dvids_id} → ${filename}`);
        } else {
          Object.assign(entry, { status: 'no_url', local_path: '', bytes: '', downloaded_at: '' });
          failed++;
        }
      }
      writeCatalog(catalog);
      console.error(`\nDVIDS resolvidos: ${resolved}  |  Sem URL pública: ${failed}`);
      showStatus(catalog);
    } finally {
      await browser.close();
    }
    return;
  }

  console.error('Abrindo Chrome para buscar catálogo atualizado de war.gov...');
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: false,
    args: ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check'],
  });

  let remoteText;
  try {
    const page = await browser.newPage();
    await page.goto('https://www.war.gov/UFO/', { waitUntil: 'networkidle2', timeout: 60000 });
    console.error('Sessão Akamai estabelecida. Baixando CSV...');
    remoteText = await page.evaluate(async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.text();
    }, CSV_REMOTE);
    await page.close();
  } finally {
    await browser.close();
  }

  const remoteRecords = parseRemoteCSV(remoteText);
  console.error(`${remoteRecords.length} entradas únicas no catálogo remoto.`);

  // Merge: preserva status/tracking dos existentes, acrescenta novos
  const existing  = parseCatalog();
  const exByKey   = {};
  for (const r of existing) exByKey[r.filename || r.dvids_id || r.title] = r;

  let added = 0;
  const merged = remoteRecords.map(r => {
    const key = r.filename || r.dvids_id || r.title;
    const ex  = exByKey[key];
    if (ex) {
      // Atualiza metadados, preserva status de download/tradução
      return {
        ...ex,
        title: r.title, type: r.type, dl_url: r.dl_url, dvids_id: r.dvids_id,
        agency: r.agency, release_date: r.release_date,
        filename: r.filename, local_dir: r.local_dir,
      };
    }
    added++;
    return {
      id: '', title: r.title, type: r.type, dl_url: r.dl_url, dvids_id: r.dvids_id,
      agency: r.agency, release_date: r.release_date, filename: r.filename, local_dir: r.local_dir,
      status:        !r.dl_url && r.dvids_id ? 'no_url' : 'pending',
      local_path:    '', bytes: '', downloaded_at: '', translated: '', translated_at: '',
    };
  });

  merged.forEach((r, i) => { r.id = String(i + 1); });
  writeCatalog(merged);

  console.error(`\ncatalog.csv atualizado — ${added} nova(s) entrada(s) adicionada(s)`);
  showStatus(merged);
}

main().catch(e => { console.error(`ERRO: ${e.message}`); process.exit(1); });
