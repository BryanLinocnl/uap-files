#!/usr/bin/env node
/**
 * ufo_downloader.js
 * Baixa arquivos listados em catalog.csv para ACERVO/{PDF,IMG,VID}/
 * Atualiza catalog.csv com status, bytes e data de cada download.
 *
 * Uso:
 *   node ufo_downloader.js                        # Baixa todos os pendentes
 *   node ufo_downloader.js --limit N              # Baixa só os N primeiros pendentes
 *   node ufo_downloader.js --retry-failed         # Reprocessa entradas com status=failed
 *   node ufo_downloader.js --base-dir ./ACERVO    # Pasta destino (padrão: ./ACERVO)
 *
 * Pré-requisito: rode "node ufo_catalog.js" antes para gerar catalog.csv.
 */

const puppeteer    = require('puppeteer-core');
const fs           = require('fs');
const path         = require('path');
const { execFileSync } = require('child_process');

const CHROME_PATH  = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CATALOG_FILE = path.join(__dirname, 'catalog.csv');

const COLUMNS = [
  'id', 'title', 'type', 'dl_url', 'dvids_id', 'agency', 'release_date',
  'filename', 'local_dir', 'status', 'local_path', 'bytes', 'downloaded_at',
  'translated', 'translated_at',
];

// ── CLI args ──────────────────────────────────────────────────────────────────
const cliArgs    = process.argv.slice(2);
const baseDir    = argVal(cliArgs, '--base-dir') || path.join(__dirname, 'ACERVO');
const limitArg   = parseInt(argVal(cliArgs, '--limit') || '0', 10);
const retryFailed = cliArgs.includes('--retry-failed');

function argVal(arr, flag) {
  const i = arr.indexOf(flag);
  return i !== -1 ? arr[i + 1] : null;
}

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

let _allRecords = null;

function loadCatalog() {
  if (!fs.existsSync(CATALOG_FILE)) {
    log('ERRO: catalog.csv não encontrado. Rode: node ufo_catalog.js');
    process.exit(1);
  }
  const rows = tokenizeCSV(fs.readFileSync(CATALOG_FILE, 'utf8').replace(/^﻿/, ''));
  if (rows.length < 2) { log('ERRO: catalog.csv vazio.'); process.exit(1); }
  const header = rows[0];
  _allRecords = rows.slice(1).map(cols => {
    const r = {};
    for (const col of COLUMNS) r[col] = cols[header.indexOf(col)] ?? '';
    return r;
  });
  return _allRecords;
}

function saveCatalog() {
  const lines = [COLUMNS.join(',')];
  for (const r of _allRecords) lines.push(COLUMNS.map(c => csvEscape(r[c] ?? '')).join(','));
  fs.writeFileSync(CATALOG_FILE, lines.join('\n') + '\n', 'utf8');
}

function updateRecord(id, fields) {
  const r = _allRecords.find(x => x.id === id);
  if (r) Object.assign(r, fields);
  saveCatalog();
}

// ── Output ─────────────────────────────────────────────────────────────────────
function log(msg)  { process.stderr.write(msg + '\n'); }
function out(obj)  { process.stdout.write(JSON.stringify(obj) + '\n'); }

function progressBar(done, total) {
  const pct  = total ? Math.floor((done / total) * 100) : 0;
  const fill = Math.floor(pct / 5);
  return `[${'█'.repeat(fill)}${'░'.repeat(20 - fill)}] ${String(pct).padStart(3)}%  (${done}/${total})`;
}

// ── HLS download via ffmpeg (.m3u8 → .mp4) ────────────────────────────────────
function hasFfmpeg() {
  try { execFileSync('ffmpeg', ['-version'], { stdio: 'pipe' }); return true; }
  catch { return false; }
}

function downloadHLS(m3u8Url, destPath) {
  execFileSync('ffmpeg', ['-y', '-i', m3u8Url, '-c', 'copy', destPath], {
    stdio: 'pipe',
    timeout: 600000, // 10 min por vídeo
  });
  return fs.statSync(destPath).size;
}

// ── Download via fetch() no browser ───────────────────────────────────────────
// Nova aba por arquivo → evita frame detachment em downloads longos.
// Navega para war.gov antes do fetch → mesmo domínio (sem bloqueio CORS).
async function downloadFile(browser, url, destPath) {
  const page = await browser.newPage();
  try {
    await page.goto('https://www.war.gov/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    const base64 = await page.evaluate(async (fileUrl) => {
      const r = await fetch(fileUrl);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const buffer = await r.arrayBuffer();
      const bytes  = new Uint8Array(buffer);
      let binary = '';
      const chunk = 8192;
      for (let i = 0; i < bytes.length; i += chunk)
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
      return btoa(binary);
    }, url);
    const buf = Buffer.from(base64, 'base64');
    fs.writeFileSync(destPath, buf);
    return buf.length;
  } finally {
    await page.close().catch(() => {});
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const allRecords = loadCatalog();

  const queue = allRecords
    .filter(r => r.status === 'pending' || (retryFailed && r.status === 'failed'))
    .slice(0, limitArg > 0 ? limitArg : undefined);

  if (!queue.length) {
    log('Nada a baixar. Rode "node ufo_catalog.js --status" para ver o estado atual.');
    return;
  }

  for (const dir of ['PDF', 'IMG', 'VID'])
    fs.mkdirSync(path.join(baseDir, dir), { recursive: true });

  const hasHLS = queue.some(r => path.extname(r.dl_url || '').toLowerCase() === '.m3u8');
  if (hasHLS && !hasFfmpeg()) {
    log('AVISO: ffmpeg não encontrado — vídeos HLS serão ignorados.');
    log('  Instale: brew install ffmpeg  (macOS)  |  apt install ffmpeg  (Linux)');
  }

  log(`\nA baixar: ${queue.length} arquivo(s)  →  ${path.resolve(baseDir)}/`);
  log('Abrindo Chrome (necessário para autenticação Akamai)...');

  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: false,
    args: ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check', '--window-position=-2000,-2000'],
  });

  try {
    const warmup = await browser.newPage();
    await warmup.goto('https://www.war.gov/UFO/', { waitUntil: 'networkidle2', timeout: 60000 });
    await warmup.close();
    log('Sessão Akamai estabelecida.\n');

    out({ type: 'start', total: queue.length });

    let done = 0, failed = 0;

    for (const rec of queue) {
      const destPath = path.join(baseDir, rec.local_dir, rec.filename);

      // Arquivo já existe localmente mas catálogo não foi atualizado
      if (fs.existsSync(destPath) && fs.statSync(destPath).size > 1024) {
        const bytes = fs.statSync(destPath).size;
        updateRecord(rec.id, {
          status: 'downloaded',
          local_path: path.relative(__dirname, destPath),
          bytes: String(bytes),
          downloaded_at: new Date().toISOString(),
        });
        done++;
        log(`  ↷  ${progressBar(done, queue.length)}  já existe: ${rec.filename}`);
        out({ type: 'skip', file: rec.filename, done, total: queue.length });
        continue;
      }

      const isHLS = path.extname(rec.dl_url || '').toLowerCase() === '.m3u8';
      if (isHLS && !hasFfmpeg()) {
        log(`  ⚠  ${progressBar(done + 1, queue.length)}  IGNORADO (sem ffmpeg): ${rec.filename}`);
        done++;
        continue;
      }

      try {
        const bytes = isHLS
          ? downloadHLS(rec.dl_url, destPath)
          : await downloadFile(browser, rec.dl_url, destPath);
        const mb    = (bytes / 1024 / 1024).toFixed(1);
        updateRecord(rec.id, {
          status: 'downloaded',
          local_path: path.relative(__dirname, destPath),
          bytes: String(bytes),
          downloaded_at: new Date().toISOString(),
        });
        done++;
        log(`  ✓  ${progressBar(done, queue.length)}  ${rec.filename}  (${mb} MB)`);
        out({ type: 'progress', file: rec.filename, file_type: rec.local_dir, bytes, done, total: queue.length });
      } catch (e) {
        updateRecord(rec.id, { status: 'failed' });
        failed++;
        done++;
        log(`  ✗  ${progressBar(done, queue.length)}  ERRO: ${rec.filename} — ${e.message}`);
        out({ type: 'error', file: rec.filename, error: e.message, done, total: queue.length });
        if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
      }
    }

    out({ type: 'done', total: queue.length, failed });

    log('\n' + '═'.repeat(60));
    log(`  DOWNLOAD CONCLUÍDO`);
    log(`  Processados : ${done}`);
    log(`  Falhas      : ${failed}`);
    log(`  Destino     : ${path.resolve(baseDir)}/`);
    log('═'.repeat(60) + '\n');

  } finally {
    await browser.close();
  }
}

main().catch(e => {
  out({ type: 'fatal', error: e.message });
  log(`\nERRO FATAL: ${e.message}`);
  process.exit(1);
});
