#!/usr/bin/env python3
"""UAP Files 1.0 — Orchestrador CLI"""

import re, sys, shutil, subprocess, os, tty, termios, csv, select, json

# ── ANSI ──────────────────────────────────────────────────────────────────────
ANSI = re.compile(r'\033\[[0-9;]*[mK]')
vlen = lambda s: len(ANSI.sub('', s))
rpad = lambda s, w: s + ' ' * max(0, w - vlen(s))

R   = "\033[0m"
B   = "\033[1m"
A   = "\033[38;5;214m"
W   = "\033[97m"
G   = "\033[38;5;245m"
GR  = "\033[38;5;82m"
DIM = "\033[2m"
RED = "\033[38;5;196m"

LOGO = [
    "██╗   ██╗ █████╗ ██████╗     ███████╗██╗██╗     ███████╗███████╗",
    "██║   ██║██╔══██╗██╔══██╗    ██╔════╝██║██║     ██╔════╝██╔════╝",
    "██║   ██║███████║██████╔╝    █████╗  ██║██║     █████╗  ███████╗",
    "██║   ██║██╔══██║██╔═══╝     ██╔══╝  ██║██║     ██╔══╝  ╚════██║",
    "╚██████╔╝██║  ██║██║         ██║     ██║███████╗███████╗███████║",
    " ╚═════╝ ╚═╝  ╚═╝╚═╝         ╚═╝     ╚═╝╚══════╝╚══════╝╚══════╝",
]
LW = max(len(l) for l in LOGO)

MAIN_MENU = [
    "Atualizar UAP Files",
    "Baixar UAP Files pendentes",
    "Traduzir UAP Files pendentes",
    "Resetar Acervo (limpar tudo)",
]
MAIN_CMDS = [
    None,
    ["node", "ufo_downloader.js"],
    ["python3", "ufo_translate.py"],
    None,
]
ATUALIZAR_MENU = [
    "Verificar atualizações",
    "Ver lista atual",
]
TRADUZIR_MENU = [
    "Traduzir todos os pendentes",
    "Traduzir um arquivo",
]

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CATALOG_FILE = os.path.join(SCRIPT_DIR, 'catalog.csv')

# Header height = 1 top-border + max(11,12) content + 1 bottom + blank + status + blank
_BOX_CONTENT_H = max(2 + len(LOGO) + 3, 12)   # 12
HEADER_H       = 1 + _BOX_CONTENT_H + 1 + 3   # 17

_download_progress:  dict = {}
_translate_progress: dict = {}


# ── Input ─────────────────────────────────────────────────────────────────────
def read_key():
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b'\x1b':
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                return 'ESC'
            ch2 = os.read(fd, 1)
            if ch2 == b'[':
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    return 'ESC'
                ch3 = os.read(fd, 1)
                if ch3 == b'A': return 'UP'
                if ch3 == b'B': return 'DOWN'
            return 'ESC'
        return ch.decode('utf-8', errors='ignore')
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── Catalog ───────────────────────────────────────────────────────────────────
def catalog_exists():
    return os.path.exists(CATALOG_FILE)

def read_catalog():
    if not catalog_exists():
        return []
    with open(CATALOG_FILE, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def record_key(r):
    return r.get('filename') or r.get('dvids_id') or r.get('title', '')

def dl_icon(r):
    s = r.get('status', '')
    if s == 'downloaded': return f"{GR}✓{R}"
    if s == 'pending':    return f"{A}○{R}"
    if s == 'failed':     return f"{RED}✗{R}"
    if s == 'no_url':     return f"{G}–{R}"
    return " "

def tr_icon(r):
    if r.get('translated') == 'yes': return f"{GR}T{R}"
    return f"{DIM}·{R}"

def can_open(r):
    if r.get('status') != 'downloaded': return False
    lp = r.get('local_path', '')
    return bool(lp) and os.path.exists(os.path.join(SCRIPT_DIR, lp))

def lista_viewport_h(rows):
    # after header (17) + breadcrumb-line (1) + blank (1) + col-hdr (1) + sep (1) = 21; footer = 1
    return max(3, rows - 22)


# ── Draw ──────────────────────────────────────────────────────────────────────
def draw(page, sel_main, sel_sub, sel_lista, scroll_lista, records):
    cols, rows = shutil.get_terminal_size((100, 30))

    left  = LW + 4
    right = cols - left - 3 - 2
    if right < 22:              right = 22
    if left + right + 3 > cols: right = cols - left - 3
    margin = " " * max(0, (cols - (left + right + 3)) // 2)
    ind = margin + "  "  # indent for content below box

    right_rows = [
        "",
        f"  {B}{W}Versão{R}", f"  1.0", "",
        f"  {B}{W}Fonte{R}",  f"  war.gov/UFO", "",
        f"  {B}{W}Formatos{R}", f"  PDF  ·  IMG  ·  VID", "",
        f"  {B}{W}Status{R}", f"  {GR}● Pronto{R}",
    ]
    tagline   = f"  {A}✦ Arquivos UAP Desclassificados ✦{R}"
    left_rows = [""] * 2 + ["  " + A + B + l + R for l in LOGO] + ["", tagline, ""]
    n = max(len(left_rows), len(right_rows))

    title = " UAP Files 1.0 "
    fill  = left - len(title)
    fl, fr = fill // 2, fill - fill // 2

    W_ = sys.stdout.write  # shorthand

    W_("\033[2J\033[H")
    W_(A + B + margin + "┌" + "─" * fl + title + "─" * fr + "┬" + "─" * right + "┐" + R + "\n")
    for i in range(n):
        lc = left_rows[i]  if i < len(left_rows)  else ""
        rc = right_rows[i] if i < len(right_rows) else ""
        W_(margin + "│" + rpad(lc, left) + "│" + rpad(rc, right) + "│\n")
    W_(A + B + margin + "└" + "─" * left + "┴" + "─" * right + "┘" + R + "\n")
    W_("\n")
    W_(ind + f"{A}{B}✦ UAP Files{R}  {G}— war.gov/UFO · v1.0{R}\n")
    W_("\n")
    # header: 17 lines printed so far

    if page == 'main':
        W_("\n")
        for i, label in enumerate(MAIN_MENU):
            if i == sel_main:
                W_(ind + f"{A}{B}▶  {label}{R}\n")
            else:
                W_(ind + f"{DIM}   {label}{R}\n")
        hint = "↑↓ navegar  ·  Enter selecionar  ·  q sair"

    elif page == 'atualizar':
        W_(ind + f"{G}Início{R}  {DIM}›{R}  {A}{B}Atualizar UAP Files{R}\n")
        W_("\n")
        for i, opt in enumerate(ATUALIZAR_MENU):
            if i == sel_sub:
                W_(ind + f"{A}{B}▶  {opt}{R}\n")
            else:
                W_(ind + f"{DIM}   {opt}{R}\n")
        hint = "↑↓ navegar  ·  Enter selecionar  ·  ESC/q voltar"

    elif page == 'lista':
        n_rec = len(records)
        vp    = lista_viewport_h(rows)
        end   = min(scroll_lista + vp, n_rec)

        W_(ind + f"{G}Início  ›  Atualizar UAP Files  ›{R}  {A}{B}Lista de Arquivos{R}  "
           f"{G}({n_rec} entradas · {scroll_lista+1}–{end}/{n_rec}){R}\n")
        W_("\n")
        # ↓=download  Tr=traduzido  #=id  T=tipo
        W_(ind + f"{B}↓  Tr  {'#':>4}  {'T':<3}  Título{R}\n")
        W_(ind + f"{G}─  ──  {'────':>4}  {'───':<3}  {'─' * max(10, cols - 22)}{R}\n")

        if not records:
            W_(ind + f"{G}Catálogo vazio.{R}\n")
            hint = "ESC/q voltar"
        else:
            for i, rec in enumerate(records[scroll_lista:scroll_lista + vp]):
                idx   = scroll_lista + i
                di    = dl_icon(rec)
                ti    = tr_icon(rec)
                id_s  = rec.get('id', '').rjust(4)
                ftype = (rec.get('local_dir') or '   ')[:3].ljust(3)
                title = rec.get('title', '')
                max_t = cols - 22
                if len(title) > max_t:
                    title = title[:max_t - 1] + '…'
                if idx == sel_lista:
                    W_(ind + f"{di}  {ti}  {A}{B}{id_s}  {ftype}  {title}{R}\n")
                else:
                    W_(ind + f"{di}  {ti}  {DIM}{id_s}  {ftype}  {title}{R}\n")
            h_open = "Enter abrir  ·  " if can_open(records[sel_lista]) else ""
            hint   = f"{h_open}↑↓ navegar  ·  ○ pendente  ✓ baixado  T traduzido  ESC/q voltar"

    elif page == 'baixar_confirm':
        n_pending = sum(1 for r in records if r.get('status') == 'pending')
        n_failed  = sum(1 for r in records if r.get('status') == 'failed')
        n_dvids   = sum(1 for r in records if r.get('status') == 'no_url' and r.get('dvids_id'))
        n_total   = n_pending + n_failed + n_dvids
        W_(ind + f"{G}Início  ›{R}  {A}{B}Baixar UAP Files pendentes{R}\n")
        W_("\n")
        if n_total == 0:
            W_(ind + f"{GR}Nenhum arquivo pendente — tudo já está baixado!{R}\n")
            hint = "Enter/ESC/q voltar"
        else:
            plural = 's' if n_total > 1 else ''
            W_(ind + f"{W}Deseja processar {A}{B}{n_total}{R}{W} UAP File{plural}?{R}\n")
            W_("\n")
            if n_pending: W_(ind + f"  {A}○  {n_pending} pendente(s){R}\n")
            if n_failed:  W_(ind + f"  {RED}✗  {n_failed} falha(s) — tentar novamente{R}\n")
            if n_dvids:   W_(ind + f"  {G}▶  {n_dvids} vídeo(s) DVIDS — resolver URL{R}\n")
            W_("\n")
            W_(ind + f"  {A}[S]{R} Sim    {G}[N]{R} Não\n")
            hint = "S confirmar  ·  N/ESC cancelar"

    elif page == 'baixar_progress':
        prog  = _download_progress
        phase = prog.get('phase', 'chrome')
        W_(ind + f"{G}Início  ›{R}  {A}{B}Baixar UAP Files pendentes{R}\n")
        W_("\n")
        if phase == 'resolving_dvids':
            done_d  = prog.get('dvids_done', 0)
            total_d = prog.get('dvids_total', 0)
            msg_d   = prog.get('dvids_msg', 'Abrindo Chrome...')
            W_(ind + f"{A}Resolvendo URLs de vídeos DVIDS...{R}\n")
            if total_d:
                bar = build_bar(done_d, total_d, min(36, cols - 28))
                W_(ind + f"{A}{bar}{R}  {G}({done_d}/{total_d}){R}\n")
            else:
                W_(ind + f"{DIM}{msg_d[:max(10, cols - 14)]}{R}\n")
            hint = "aguardando DVIDS..."
        elif phase == 'chrome':
            W_(ind + f"{G}Abrindo Chrome... aguardando sessão Akamai{R}\n")
            W_(ind + f"{DIM}(o Chrome vai abrir na tela — é esperado){R}\n")
            hint = "aguardando..."
        elif phase == 'downloading':
            fname  = prog.get('filename', '...')
            done   = prog.get('done', 0)
            total  = prog.get('total', 1)
            max_fn = max(10, cols - 14)
            if len(fname) > max_fn: fname = fname[:max_fn - 1] + '…'
            bar    = build_bar(done, total, min(36, cols - 28))
            W_(ind + f"{B}Arquivo:{R} {fname}\n")
            W_(ind + f"{A}{bar}{R}  {G}({done}/{total}){R}\n")
            hint = "Ctrl+C interromper"
        elif phase in ('done', 'error'):
            ok     = prog.get('ok', 0)
            failed = prog.get('failed', 0)
            W_(ind + f"{GR}✓ {ok} arquivo(s) baixado(s){R}\n")
            if failed:
                W_(ind + f"{RED}✗ {failed} com erro(s){R}\n")
            if phase == 'error':
                W_(ind + f"{RED}Erro: {prog.get('error', '?')}{R}\n")
            W_("\n")
            W_(ind + f"{G}Pressione Enter para voltar...{R}\n")
            hint = "Enter voltar"

    elif page == 'traduzir':
        n_dl  = sum(1 for r in records if r.get('local_dir') == 'PDF' and r.get('status') == 'downloaded')
        n_tr  = sum(1 for r in records if r.get('translated') == 'yes')
        n_pnd = n_dl - n_tr
        W_(ind + f"{G}Início  ›{R}  {A}{B}Traduzir UAP Files pendentes{R}\n")
        W_("\n")
        W_(ind + f"  {GR}✓  {n_tr} PDF(s) já traduzido(s){R}\n")
        W_(ind + f"  {A}○  {n_pnd} PDF(s) pendente(s){R}\n")
        W_("\n")
        for i, opt in enumerate(TRADUZIR_MENU):
            if i == sel_sub:
                W_(ind + f"{A}{B}▶  {opt}{R}\n")
            else:
                W_(ind + f"{DIM}   {opt}{R}\n")
        hint = "↑↓ navegar  ·  Enter selecionar  ·  ESC/q voltar"

    elif page == 'traduzir_lista':
        pdf_recs = [r for r in records if r.get('local_dir') == 'PDF' and r.get('status') == 'downloaded']
        n_rec = len(pdf_recs)
        vp    = lista_viewport_h(rows)
        end   = min(scroll_lista + vp, n_rec)
        W_(ind + f"{G}Início  ›  Traduzir  ›{R}  {A}{B}Escolher Arquivo{R}  "
           f"{G}({n_rec} PDFs · {scroll_lista+1}–{end}/{n_rec}){R}\n")
        W_("\n")
        W_(ind + f"{B}Tr  {'#':>4}  Título{R}\n")
        W_(ind + f"{G}──  {'────':>4}  {'─' * max(10, cols - 16)}{R}\n")
        if not pdf_recs:
            W_(ind + f"{G}Nenhum PDF baixado.{R}\n")
            hint = "ESC/q voltar"
        else:
            for i, rec in enumerate(pdf_recs[scroll_lista:scroll_lista + vp]):
                idx   = scroll_lista + i
                ti    = tr_icon(rec)
                id_s  = rec.get('id', '').rjust(4)
                title = rec.get('title', '')
                max_t = cols - 16
                if len(title) > max_t: title = title[:max_t - 1] + '…'
                if idx == sel_lista:
                    W_(ind + f"{ti}  {A}{B}{id_s}  {title}{R}\n")
                else:
                    W_(ind + f"{ti}  {DIM}{id_s}  {title}{R}\n")
            hint = "↑↓ navegar  ·  Enter selecionar  ·  ESC/q voltar"

    elif page == 'traduzir_progress':
        prog   = _translate_progress
        phase  = prog.get('phase', 'starting')
        W_(ind + f"{G}Início  ›{R}  {A}{B}Traduzir UAP Files pendentes{R}\n")
        W_("\n")
        if phase == 'starting':
            W_(ind + f"{G}Iniciando tradução...{R}\n")
            hint = "aguardando..."
        elif phase == 'translating':
            fname       = prog.get('filename', '...')
            file_num    = prog.get('file_num', 0)
            total_files = prog.get('total_files', 1)
            page_n      = prog.get('page', 0)
            pages       = prog.get('pages', 0)
            max_fn = max(10, cols - 14)
            if len(fname) > max_fn: fname = fname[:max_fn - 1] + '…'
            W_(ind + f"{B}Arquivo:{R} {fname}\n")
            if total_files > 1:
                bar = build_bar(prog.get('completed', 0), total_files, min(36, cols - 28))
                W_(ind + f"{A}{bar}{R}  {G}({file_num}/{total_files} arquivos){R}\n")
                if pages:
                    W_(ind + f"  {DIM}pág. {page_n}/{pages}{R}\n")
            else:
                bar = build_bar(page_n, pages, min(36, cols - 28)) if pages else build_bar(0, 1)
                W_(ind + f"{A}{bar}{R}  {G}(pág. {page_n}/{pages}){R}\n")
            hint = "Ctrl+C interromper"
        elif phase in ('done', 'error'):
            ok     = prog.get('ok', 0)
            failed = prog.get('failed', 0)
            W_(ind + f"{GR}✓ {ok} arquivo(s) traduzido(s){R}\n")
            if failed:
                W_(ind + f"{RED}✗ {failed} com erro(s){R}\n")
            W_("\n")
            W_(ind + f"{G}Pressione Enter para voltar...{R}\n")
            hint = "Enter voltar"

    elif page == 'reset_confirm':
        acervo  = os.path.join(SCRIPT_DIR, 'ACERVO')
        n_files = {}
        total_bytes = 0
        for sub in ('PDF', 'IMG', 'VID'):
            d = os.path.join(acervo, sub)
            n_files[sub] = 0
            if os.path.isdir(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp):
                        n_files[sub] += 1
                        total_bytes  += os.path.getsize(fp)
        tr_dir = os.path.join(SCRIPT_DIR, 'translated')
        n_tr   = sum(1 for f in os.listdir(tr_dir)
                     if os.path.isfile(os.path.join(tr_dir, f))) if os.path.isdir(tr_dir) else 0
        cat    = catalog_exists()
        mb     = total_bytes / 1024 / 1024
        has_anything = cat or sum(n_files.values()) > 0 or n_tr > 0

        W_(ind + f"{G}Início  ›{R}  {RED}{B}Resetar Acervo{R}\n")
        W_("\n")
        if not has_anything:
            W_(ind + f"{GR}Acervo já está vazio.{R}\n")
            hint = "Enter/ESC/q voltar"
        else:
            W_(ind + f"{RED}{B}ATENÇÃO: esta ação é irreversível!{R}\n")
            W_("\n")
            W_(ind + f"{W}Será excluído:{R}\n")
            if cat:              W_(ind + f"  {G}·  catalog.csv{R}\n")
            if n_files['PDF']:   W_(ind + f"  {G}·  {n_files['PDF']} PDF(s)  →  ACERVO/PDF/{R}\n")
            if n_files['IMG']:   W_(ind + f"  {G}·  {n_files['IMG']} imagem(ns)  →  ACERVO/IMG/{R}\n")
            if n_files['VID']:   W_(ind + f"  {G}·  {n_files['VID']} vídeo(s)  →  ACERVO/VID/{R}\n")
            if n_tr:             W_(ind + f"  {G}·  {n_tr} PDF(s) traduzido(s)  →  translated/{R}\n")
            if total_bytes:      W_(ind + f"  {DIM}  (~{mb:.0f} MB liberados){R}\n")
            W_("\n")
            W_(ind + f"  {RED}[S]{R} Confirmar    {G}[N]{R} Cancelar\n")
            hint = "S excluir tudo  ·  N/ESC cancelar"

    W_(f"\033[{rows};1H  {G}{hint}{R}")
    W_("\033[?25l")
    sys.stdout.flush()


# ── Subprocess screens ────────────────────────────────────────────────────────
def _wait_enter():
    print(f"\n  {G}Pressione Enter para voltar...{R}", end="", flush=True)
    try:    input()
    except (KeyboardInterrupt, EOFError): pass

def build_bar(done, total, width=32):
    pct  = int(done / total * 100) if total else 0
    fill = int(done / total * width) if total else 0
    return f"[{'█' * fill}{'░' * (width - fill)}] {pct:3}%"


def execute_download(n_total, n_dvids=0):
    import queue, threading
    global _download_progress

    _download_progress = {
        'phase':       'resolving_dvids' if n_dvids > 0 else 'chrome',
        'filename':    '', 'done': 0, 'total': n_total,
        'failed':      0, 'ok': 0,
        'dvids_msg':   '', 'dvids_done': 0, 'dvids_total': n_dvids,
    }
    draw('baixar_progress', 0, 0, 0, 0, [])

    # ── Phase 1: resolve DVIDS URLs ───────────────────────────────────────────
    if n_dvids > 0:
        q = queue.Queue()
        dvids_proc = subprocess.Popen(
            ['caffeinate', '-dims', 'node', 'ufo_catalog.js', '--resolve-dvids'],
            cwd=SCRIPT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, bufsize=0,
        )

        def _dvids_reader(stderr, q):
            buf = ''
            while True:
                ch = stderr.read(1)
                if not ch:
                    if buf.strip(): q.put(buf.strip())
                    q.put(None); break
                if ch in ('\r', '\n'):
                    if buf.strip(): q.put(buf.strip())
                    buf = ''
                else:
                    buf += ch

        threading.Thread(target=_dvids_reader, args=(dvids_proc.stderr, q), daemon=True).start()

        try:
            while True:
                try: msg = q.get(timeout=0.08)
                except queue.Empty:
                    draw('baixar_progress', 0, 0, 0, 0, []); continue
                if msg is None: break
                _download_progress['dvids_msg'] = msg
                m = re.match(r'\[\s*(\d+)%\]\s+\((\d+)/(\d+)\)', msg)
                if m:
                    _download_progress['dvids_done']  = int(m.group(2))
                    _download_progress['dvids_total'] = int(m.group(3))
                draw('baixar_progress', 0, 0, 0, 0, [])
        except KeyboardInterrupt:
            dvids_proc.terminate(); dvids_proc.wait()
            _download_progress.update({'phase': 'done', 'ok': 0})
            draw('baixar_progress', 0, 0, 0, 0, [])
            sys.stdout.write("\033[?25h"); sys.stdout.flush()
            while True:
                key = read_key()
                if key in ('\r', '\n', 'q', 'Q', 'ESC', '\x03'): break
            return

        dvids_proc.wait()
        updated_recs = read_catalog()
        n_total = sum(1 for r in updated_recs if r.get('status') in ('pending', 'failed'))
        _download_progress['total'] = n_total

    # ── Phase 2: download via ufo_downloader.js ───────────────────────────────
    _download_progress['phase'] = 'chrome'
    _download_progress['done']  = 0
    draw('baixar_progress', 0, 0, 0, 0, [])

    q    = queue.Queue()
    proc = subprocess.Popen(
        ['caffeinate', '-dims', 'node', 'ufo_downloader.js', '--retry-failed'],
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )

    def _reader(stdout, q):
        for line in stdout: q.put(line.strip())
        q.put(None)

    threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True).start()

    try:
        while True:
            try: raw = q.get(timeout=0.08)
            except queue.Empty:
                draw('baixar_progress', 0, 0, 0, 0, []); continue
            if raw is None: break
            if not raw:     continue
            try: msg = json.loads(raw)
            except json.JSONDecodeError: continue

            mtype = msg.get('type')
            if mtype == 'start':
                _download_progress['total'] = msg.get('total', _download_progress['total'])
                _download_progress['phase'] = 'downloading'
            elif mtype in ('progress', 'skip', 'error'):
                _download_progress['filename'] = msg.get('file', '')
                _download_progress['done']     = msg.get('done', 0)
                _download_progress['total']    = msg.get('total', _download_progress['total'])
                if mtype == 'error': _download_progress['failed'] += 1
                _download_progress['phase'] = 'downloading'
            elif mtype == 'done':
                failed = msg.get('failed', 0)
                _download_progress.update({
                    'phase': 'done',
                    'ok':    _download_progress['total'] - failed,
                    'failed': failed,
                })
            elif mtype == 'fatal':
                _download_progress.update({'phase': 'error', 'error': msg.get('error', '?')})
            draw('baixar_progress', 0, 0, 0, 0, [])

    except KeyboardInterrupt:
        proc.terminate()
        _download_progress.update({'phase': 'done', 'ok': _download_progress.get('done', 0)})

    proc.wait()
    if _download_progress['phase'] not in ('done', 'error'):
        _download_progress.update({'phase': 'done', 'ok': _download_progress.get('done', 0)})

    draw('baixar_progress', 0, 0, 0, 0, [])
    sys.stdout.write("\033[?25h"); sys.stdout.flush()

    while True:
        key = read_key()
        if key in ('\r', '\n', 'q', 'Q', 'ESC', '\x03'): break


def execute_translate(mode, rec_id=None):
    import queue, threading
    global _translate_progress
    _translate_progress = {
        'phase': 'starting', 'mode': mode,
        'filename': '', 'file_num': 0, 'total_files': 0,
        'completed': 0, 'page': 0, 'pages': 0,
        'ok': 0, 'failed': 0,
    }
    draw('traduzir_progress', 0, 0, 0, 0, [])

    cmd = ['python3', 'ufo_translate.py', '--json-progress']
    if mode == 'one' and rec_id:
        cmd += ['--translate-id', str(rec_id)]

    q    = queue.Queue()
    proc = subprocess.Popen(
        cmd, cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )

    def _reader(stdout, q):
        for line in stdout: q.put(line.strip())
        q.put(None)

    threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True).start()

    try:
        while True:
            try: raw = q.get(timeout=0.08)
            except queue.Empty:
                draw('traduzir_progress', 0, 0, 0, 0, []); continue
            if raw is None: break
            if not raw:     continue
            try: msg = json.loads(raw)
            except json.JSONDecodeError: continue

            mtype = msg.get('type')
            if mtype == 'start':
                _translate_progress['total_files'] = msg.get('total', 0)
                _translate_progress['phase'] = 'translating'
            elif mtype == 'file_start':
                _translate_progress.update({
                    'filename':    msg.get('file', ''),
                    'file_num':    msg.get('num', 0),
                    'total_files': msg.get('total', _translate_progress['total_files']),
                    'pages':       msg.get('pages', 0),
                    'page':        0,
                    'phase':       'translating',
                })
            elif mtype == 'page':
                _translate_progress.update({
                    'page':  msg.get('page', 0),
                    'pages': msg.get('pages', 0),
                    'phase': 'translating',
                })
            elif mtype == 'file_done':
                if msg.get('ok'):
                    _translate_progress['completed'] += 1
                else:
                    _translate_progress['failed'] += 1
            elif mtype == 'done':
                _translate_progress.update({
                    'phase':  'done',
                    'ok':     msg.get('ok', 0),
                    'failed': msg.get('failed', 0),
                })
            draw('traduzir_progress', 0, 0, 0, 0, [])

    except KeyboardInterrupt:
        proc.terminate()
        _translate_progress.update({'phase': 'done', 'ok': _translate_progress.get('completed', 0)})

    proc.wait()
    if _translate_progress['phase'] not in ('done', 'error'):
        _translate_progress.update({'phase': 'done', 'ok': _translate_progress.get('completed', 0)})

    draw('traduzir_progress', 0, 0, 0, 0, [])
    sys.stdout.write("\033[?25h"); sys.stdout.flush()

    while True:
        key = read_key()
        if key in ('\r', '\n', 'q', 'Q', 'ESC', '\x03'): break


def execute_reset():
    acervo = os.path.join(SCRIPT_DIR, 'ACERVO')
    for sub in ('PDF', 'IMG', 'VID'):
        d = os.path.join(acervo, sub)
        if os.path.isdir(d):
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if os.path.isfile(fp): os.unlink(fp)
    tr_dir = os.path.join(SCRIPT_DIR, 'translated')
    if os.path.isdir(tr_dir):
        for f in os.listdir(tr_dir):
            fp = os.path.join(tr_dir, f)
            if os.path.isfile(fp): os.unlink(fp)
    if os.path.exists(CATALOG_FILE):
        os.unlink(CATALOG_FILE)


def run_subprocess_screen(label, cmd):
    sys.stdout.write("\033[?25h\033[2J\033[H"); sys.stdout.flush()
    print(f"\n  {A}{B}▶  {label}{R}\n")
    try:    subprocess.run(cmd, cwd=SCRIPT_DIR)
    except FileNotFoundError: print(f"\n  Erro: '{cmd[0]}' não encontrado.")
    except KeyboardInterrupt:  pass
    _wait_enter()

def run_criar_catalogo():
    sys.stdout.write("\033[?25h\033[2J\033[H"); sys.stdout.flush()
    print(f"\n  {A}{B}✦ Atualizar UAP Files{R}\n")
    print(f"  {W}O catálogo ainda não foi criado. Deseja criá-lo agora?{R}")
    print(f"\n  {A}[S]{R} Sim    {G}[N]{R} Não\n")
    sys.stdout.write(f"  {B}>{R} "); sys.stdout.flush()
    while True:
        key = read_key()
        if key in ('s', 'S'):
            sys.stdout.write(f"{W}S{R}\n\n"); sys.stdout.flush()
            subprocess.run(['node', 'ufo_catalog.js'], cwd=SCRIPT_DIR)
            _wait_enter(); return
        elif key in ('n', 'N', 'ESC', '\x03'):
            sys.stdout.write(f"{W}N{R}\n"); sys.stdout.flush(); return

def run_verificar():
    sys.stdout.write("\033[?25h\033[2J\033[H"); sys.stdout.flush()
    print(f"\n  {A}{B}▶  Verificar atualizações{R}\n")

    before      = read_catalog()
    before_keys = {record_key(r) for r in before}
    subprocess.run(['node', 'ufo_catalog.js'], cwd=SCRIPT_DIR)
    after       = read_catalog()
    after_keys  = {record_key(r) for r in after}

    new_items     = [r for r in after  if record_key(r) not in before_keys]
    removed_items = [r for r in before if record_key(r) not in after_keys]

    print(f"\n  {'─'*52}")
    print(f"  {B}{W}Resultado:{R}\n")
    print(f"  {A if new_items else GR}● {len(new_items)} arquivo(s) novo(s){R}")
    print(f"  {RED if removed_items else GR}● {len(removed_items)} arquivo(s) excluído(s){R}")

    if new_items:
        print(f"\n  {G}Novos:{R}")
        for r in new_items[:10]:
            print(f"    {DIM}· {r.get('title','')[:70]}{R}")
        if len(new_items) > 10:
            print(f"    {DIM}... e mais {len(new_items)-10}{R}")
    if removed_items:
        print(f"\n  {G}Excluídos:{R}")
        for r in removed_items[:10]:
            print(f"    {DIM}· {r.get('title','')[:70]}{R}")
        if len(removed_items) > 10:
            print(f"    {DIM}... e mais {len(removed_items)-10}{R}")
    _wait_enter()


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    page         = 'main'
    sel_main     = 0
    sel_sub      = 0
    sel_lista    = 0
    scroll_lista = 0
    records: list = []

    try:
        while True:
            draw(page, sel_main, sel_sub, sel_lista, scroll_lista, records)
            key = read_key()

            # ── main ──────────────────────────────────────────────────────────
            if page == 'main':
                if key in ('q', 'Q', '\x03', '\x04'):
                    break
                elif key == 'UP':
                    sel_main = max(0, sel_main - 1)
                elif key == 'DOWN':
                    sel_main = min(len(MAIN_MENU) - 1, sel_main + 1)
                elif key in ('\r', '\n'):
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    if sel_main == 0:
                        if not catalog_exists():
                            run_criar_catalogo()
                        else:
                            page = 'atualizar'
                    elif sel_main == 1:
                        records = read_catalog()
                        page    = 'baixar_confirm'
                    elif sel_main == 2:
                        records = read_catalog()
                        sel_sub = 0
                        page    = 'traduzir'
                    elif sel_main == 3:
                        page = 'reset_confirm'
                    else:
                        run_subprocess_screen(MAIN_MENU[sel_main], MAIN_CMDS[sel_main])

            # ── baixar_confirm ────────────────────────────────────────────────
            elif page == 'baixar_confirm':
                n_pending = sum(1 for r in records if r.get('status') == 'pending')
                n_failed  = sum(1 for r in records if r.get('status') == 'failed')
                n_dvids   = sum(1 for r in records if r.get('status') == 'no_url' and r.get('dvids_id'))
                n_total   = n_pending + n_failed + n_dvids
                if key in ('q', 'Q', 'ESC', '\x03', 'n', 'N'):
                    page = 'main'
                elif key in ('\r', '\n') and n_total == 0:
                    page = 'main'
                elif key in ('s', 'S') and n_total > 0:
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    execute_download(n_total, n_dvids=n_dvids)
                    page = 'main'

            # ── traduzir ──────────────────────────────────────────────────────
            elif page == 'traduzir':
                if key in ('q', 'Q', 'ESC', '\x03'):
                    page = 'main'
                elif key == 'UP':
                    sel_sub = max(0, sel_sub - 1)
                elif key == 'DOWN':
                    sel_sub = min(len(TRADUZIR_MENU) - 1, sel_sub + 1)
                elif key in ('\r', '\n'):
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    if sel_sub == 0:
                        execute_translate('all')
                        records = read_catalog()
                        page    = 'traduzir'
                    else:
                        sel_lista    = 0
                        scroll_lista = 0
                        page         = 'traduzir_lista'

            # ── traduzir_lista ────────────────────────────────────────────────
            elif page == 'traduzir_lista':
                pdf_recs = [r for r in records if r.get('local_dir') == 'PDF' and r.get('status') == 'downloaded']
                _, rows_now = shutil.get_terminal_size((100, 30))
                viewport   = lista_viewport_h(rows_now)
                if key in ('q', 'Q', 'ESC', '\x03'):
                    page = 'traduzir'
                elif key == 'UP' and sel_lista > 0:
                    sel_lista -= 1
                    if sel_lista < scroll_lista:
                        scroll_lista = sel_lista
                elif key == 'DOWN' and pdf_recs and sel_lista < len(pdf_recs) - 1:
                    sel_lista += 1
                    if sel_lista >= scroll_lista + viewport:
                        scroll_lista = sel_lista - viewport + 1
                elif key in ('\r', '\n') and pdf_recs:
                    rec = pdf_recs[sel_lista]
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    execute_translate('one', rec_id=rec['id'])
                    records = read_catalog()
                    page    = 'traduzir'

            # ── reset_confirm ─────────────────────────────────────────────────
            elif page == 'reset_confirm':
                if key in ('q', 'Q', 'ESC', '\x03', 'n', 'N', '\r', '\n'):
                    page = 'main'
                elif key in ('s', 'S'):
                    execute_reset()
                    records = []
                    page    = 'main'

            # ── atualizar ─────────────────────────────────────────────────────
            elif page == 'atualizar':
                if key in ('q', 'Q', 'ESC', '\x03'):
                    page = 'main'
                elif key == 'UP':
                    sel_sub = max(0, sel_sub - 1)
                elif key == 'DOWN':
                    sel_sub = min(len(ATUALIZAR_MENU) - 1, sel_sub + 1)
                elif key in ('\r', '\n'):
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    if sel_sub == 0:
                        run_verificar()
                    else:
                        records      = read_catalog()
                        sel_lista    = 0
                        scroll_lista = 0
                        page         = 'lista'

            # ── lista ─────────────────────────────────────────────────────────
            elif page == 'lista':
                _, rows  = shutil.get_terminal_size((100, 30))
                viewport = lista_viewport_h(rows)
                if key in ('q', 'Q', 'ESC', '\x03'):
                    page = 'atualizar'
                elif key == 'UP' and sel_lista > 0:
                    sel_lista -= 1
                    if sel_lista < scroll_lista:
                        scroll_lista = sel_lista
                elif key == 'DOWN' and records and sel_lista < len(records) - 1:
                    sel_lista += 1
                    if sel_lista >= scroll_lista + viewport:
                        scroll_lista = sel_lista - viewport + 1
                elif key in ('\r', '\n') and records and can_open(records[sel_lista]):
                    lp = os.path.join(SCRIPT_DIR, records[sel_lista]['local_path'])
                    sys.stdout.write("\033[?25h"); sys.stdout.flush()
                    subprocess.run(['open', lp])
                    sys.stdout.write("\033[?25l"); sys.stdout.flush()

    finally:
        sys.stdout.write("\033[?25h"); sys.stdout.flush()
        print()


if __name__ == "__main__":
    main()
