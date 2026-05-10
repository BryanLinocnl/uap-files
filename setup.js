#!/usr/bin/env node
/**
 * setup.js — Instala todos os pré-requisitos do UAP Files.
 * Uso: node setup.js  |  npm run setup
 */

const { execSync, spawnSync } = require('child_process');
const os   = require('os');
const fs   = require('fs');
const path = require('path');

const isMac   = os.platform() === 'darwin';
const isLinux = os.platform() === 'linux';
const isWin   = os.platform() === 'win32';

const C = {
  reset:  '\x1b[0m',
  bold:   '\x1b[1m',
  green:  '\x1b[32m',
  yellow: '\x1b[33m',
  red:    '\x1b[31m',
  cyan:   '\x1b[36m',
  dim:    '\x1b[2m',
};

function ok(msg)   { console.log(`  ${C.green}✓${C.reset}  ${msg}`); }
function warn(msg) { console.log(`  ${C.yellow}⚠${C.reset}  ${msg}`); }
function info(msg) { console.log(`  ${C.cyan}→${C.reset}  ${msg}`); }
function err(msg)  { console.log(`  ${C.red}✗${C.reset}  ${msg}`); }
function head(msg) { console.log(`\n${C.bold}${msg}${C.reset}`); }

function has(cmd) {
  try { execSync(cmd, { stdio: 'pipe' }); return true; }
  catch { return false; }
}

function run(cmd, label) {
  if (label) info(label);
  const r = spawnSync(cmd, { shell: true, stdio: 'inherit' });
  return r.status === 0;
}

function version(cmd) {
  try { return execSync(cmd, { stdio: 'pipe' }).toString().trim().split('\n')[0]; }
  catch { return null; }
}

// ─────────────────────────────────────────────────────────────────────────────

console.log('\n' + C.bold + '╔══════════════════════════════════╗' + C.reset);
console.log(C.bold       + '║     UAP Files — Setup            ║' + C.reset);
console.log(C.bold       + '╚══════════════════════════════════╝' + C.reset);

let warnings = 0;
let errors   = 0;

// ── Homebrew (macOS) ──────────────────────────────────────────────────────────
const hasBrew = has('brew --version');
if (isMac && !hasBrew) {
  head('Homebrew');
  warn('Homebrew não encontrado. Instalando...');
  run('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
      'Instalando Homebrew...');
}
const hasApt = isLinux && has('apt-get --version');

// ── Python ────────────────────────────────────────────────────────────────────
head('Python');
const pyCmd = has('python3 --version') ? 'python3'
            : has('python --version')  ? 'python'
            : null;

if (pyCmd) {
  ok(`${pyCmd} encontrado: ${version(`${pyCmd} --version`)}`);
} else {
  warn('Python não encontrado. Instalando...');
  if (isMac)        run('brew install python', 'brew install python');
  else if (hasApt)  run('sudo apt-get install -y python3 python3-pip', 'apt-get install python3');
  else if (isWin)   warn('Instale Python 3.10+ em: https://www.python.org/downloads/');
}

// ── pip ───────────────────────────────────────────────────────────────────────
head('Dependências Python (pymupdf pytesseract pillow)');
const pip = has('pip3 --version') ? 'pip3' : has('pip --version') ? 'pip' : null;
if (pip) {
  const success = run(`${pip} install pymupdf pytesseract pillow`, 'pip install pymupdf pytesseract pillow');
  if (success) ok('pymupdf, pytesseract, pillow instalados');
  else { err('Falha no pip install'); errors++; }
} else {
  err('pip não encontrado. Instale Python e tente novamente.');
  errors++;
}

// ── Tesseract ─────────────────────────────────────────────────────────────────
head('Tesseract OCR');
if (has('tesseract --version')) {
  ok(`Tesseract encontrado: ${version('tesseract --version')}`);
} else {
  warn('Tesseract não encontrado. Instalando...');
  let installed = false;
  if (isMac && hasBrew)  installed = run('brew install tesseract', 'brew install tesseract');
  else if (hasApt)       installed = run('sudo apt-get install -y tesseract-ocr', 'apt-get install tesseract-ocr');
  else if (isWin) {
    warn('Baixe o instalador em: https://github.com/UB-Mannheim/tesseract/wiki');
    warnings++;
  }
  if (!installed && !isWin) { err('Falha ao instalar Tesseract.'); errors++; }
  else if (installed) ok('Tesseract instalado');
}

// ── ffmpeg (opcional) ─────────────────────────────────────────────────────────
head('ffmpeg (opcional — vídeos HLS)');
if (has('ffmpeg -version')) {
  ok(`ffmpeg encontrado: ${version('ffmpeg -version')}`);
} else {
  warn('ffmpeg não encontrado (necessário apenas para vídeos HLS .m3u8).');
  warn('Para instalar: ' + (isMac ? 'brew install ffmpeg' : hasApt ? 'sudo apt install ffmpeg' : 'https://ffmpeg.org/download.html'));
  warnings++;
}

// ── npm install ───────────────────────────────────────────────────────────────
head('Dependências Node.js (puppeteer-core)');
const success = run('npm install', 'npm install');
if (success) ok('puppeteer-core instalado');
else { err('Falha no npm install'); errors++; }

// ── Google Chrome ─────────────────────────────────────────────────────────────
head('Google Chrome');
const chromePaths = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium-browser',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
];
const chromeFound = chromePaths.some(p => fs.existsSync(p));
if (chromeFound) {
  ok('Google Chrome encontrado');
} else {
  warn('Google Chrome não encontrado.');
  warn('Baixe em: https://www.google.com/chrome/');
  warn('Necessário para contornar proteção Akamai do war.gov.');
  warnings++;
}

// ── Resumo ────────────────────────────────────────────────────────────────────
console.log('\n' + '─'.repeat(40));
if (errors > 0) {
  err(`Setup concluído com ${errors} erro(s). Verifique acima.`);
  process.exit(1);
} else if (warnings > 0) {
  warn(`Setup concluído com ${warnings} aviso(s). Verifique acima.`);
} else {
  ok('Setup concluído com sucesso!');
}

console.log(`\n  ${C.bold}Próximo passo:${C.reset}  python3 uap_files.py\n`);