#!/usr/bin/env python3
"""
ufo_translate.py
Traduz PDFs de war.gov/UFO de EN→PT usando OCR + Google Translate público.
Lê/atualiza catalog.csv — gerado por ufo_catalog.js.

Uso:
    python ufo_translate.py                    # Traduz todos os PDFs baixados
    python ufo_translate.py --limit 5          # Só os 5 primeiros (teste)
    python ufo_translate.py --translate-only arquivo.pdf
    python ufo_translate.py --workers 2        # Tradução paralela (CLI)
    python ufo_translate.py --dpi 250          # OCR com maior resolução
    python ufo_translate.py --json-progress    # Saída JSON para TUI
    python ufo_translate.py --translate-id N   # Traduz apenas o ID N do catálogo
"""

import os, sys, re, time, json, csv, argparse, logging, textwrap, threading
import urllib.request, urllib.parse
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Dependências ──────────────────────────────────────────────────────────────
try:
    import fitz
    import pytesseract
    from PIL import Image
except ImportError as e:
    sys.exit(f"Dependência faltando: {e}\nRode: pip install pymupdf pytesseract pillow")

# ── Configuração ──────────────────────────────────────────────────────────────
CATALOG_FILE   = Path("catalog.csv")
ACERVO_DIR     = Path("ACERVO")
TRANSLATED_DIR = Path("translated")
LOG_FILE       = Path("pipeline.log")

DPI        = 200
BATCH_SIZE = 10
SLEEP_SEC  = 0.3

CATALOG_COLUMNS = [
    'id', 'title', 'type', 'dl_url', 'dvids_id', 'agency', 'release_date',
    'filename', 'local_dir', 'status', 'local_path', 'bytes', 'downloaded_at',
    'translated', 'translated_at',
]

_json_mode    = False
_catalog_lock = threading.Lock()

log = logging.getLogger(__name__)


def jout(obj):
    if _json_mode:
        sys.stdout.write(json.dumps(obj) + '\n')
        sys.stdout.flush()


# ── Catalog helpers ───────────────────────────────────────────────────────────
def read_catalog() -> list[dict]:
    if not CATALOG_FILE.exists():
        log.error("catalog.csv não encontrado. Rode: node ufo_catalog.js")
        sys.exit(1)
    with open(CATALOG_FILE, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def write_catalog(records: list[dict]):
    with open(CATALOG_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)

def update_record(records: list[dict], record_id: str, fields: dict):
    with _catalog_lock:
        for r in records:
            if r['id'] == record_id:
                r.update(fields)
                break
        write_catalog(records)


# ── OCR ───────────────────────────────────────────────────────────────────────
def _blocks_overlap(a: dict, b: dict, threshold: float = 0.4) -> bool:
    ix0, iy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    ix1, iy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), 1)
    return (inter / area_a) > threshold


def _run_tesseract(img, scale_x: float, scale_y: float, psm: int) -> list[dict]:
    data = pytesseract.image_to_data(
        img, lang="eng", output_type=pytesseract.Output.DICT,
        config=f"--oem 3 --psm {psm}"
    )
    par_words = defaultdict(list)
    n = len(data["text"])
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word or int(data["conf"][i]) < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i])
        par_words[key].append({
            "text":   word,
            "left":   data["left"][i],
            "top":    data["top"][i],
            "width":  data["width"][i],
            "height": data["height"][i],
            "line":   data["line_num"][i],
        })
    blocks = []
    for words in par_words.values():
        if not words:
            continue
        words.sort(key=lambda w: (w["line"], w["left"]))
        text  = " ".join(w["text"] for w in words)
        x0_px = min(w["left"] for w in words)
        y0_px = min(w["top"]  for w in words)
        x1_px = max(w["left"] + w["width"]  for w in words)
        y1_px = max(w["top"]  + w["height"] for w in words)
        avg_h = sum(w["height"] for w in words) / len(words)
        blocks.append({
            "text":      text,
            "x0":        x0_px * scale_x,
            "y0":        y0_px * scale_y,
            "x1":        x1_px * scale_x,
            "y1":        y1_px * scale_y,
            "font_size": max(6, avg_h * scale_y * 0.85),
        })
    return blocks


def ocr_page(page: fitz.Page) -> list[dict]:
    mat = fitz.Matrix(DPI / 72, DPI / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    scale_x = page.rect.width  / pix.width
    scale_y = page.rect.height / pix.height

    img_gray = img.convert("L")
    img_bin  = img_gray.point(lambda x: 0 if x < 150 else 255, 'L')

    # Pass 1 — PSM 3 (auto page segmentation): good for headings and clear blocks
    blocks = _run_tesseract(img_bin, scale_x, scale_y, psm=3)

    # Pass 2 — PSM 11 (sparse text): catches isolated blocks PSM 3 misses in
    # complex newspaper layouts; merge only non-overlapping new blocks
    for b in _run_tesseract(img_bin, scale_x, scale_y, psm=11):
        if not any(_blocks_overlap(b, ex) for ex in blocks):
            blocks.append(b)

    # Pass 3 — PSM 11 on raw grayscale (no binarization): helps pages where
    # binarization over-thresholds light-ink columns to white
    for b in _run_tesseract(img_gray, scale_x, scale_y, psm=11):
        if not any(_blocks_overlap(b, ex) for ex in blocks):
            blocks.append(b)

    for b in blocks:
        b["page_w"] = page.rect.width
        b["page_h"] = page.rect.height
    return blocks


# ── Tradução ──────────────────────────────────────────────────────────────────
def translate_batch(texts: list[str]) -> list[str]:
    if not texts:
        return []
    SEP    = " ||||| "
    joined = SEP.join(texts)
    endpoints = [
        f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=pt&dt=t&q={urllib.parse.quote(joined)}",
        f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl=pt&q={urllib.parse.quote(joined)}",
    ]
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
            data = json.loads(raw)
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
            elif isinstance(data, dict) and "sentences" in data:
                translated = "".join(s.get("trans", "") for s in data["sentences"])
            else:
                continue
            parts = [p.strip() for p in translated.split("|||||")]
            if len(parts) == len(texts):
                return parts
            return [translated] + [""] * (len(texts) - 1)
        except Exception as e:
            log.debug(f"    Endpoint falhou: {e}")
            continue
    log.warning("    Todos os endpoints de tradução falharam — mantendo original")
    return texts

def translate_blocks(blocks: list[dict]) -> list[dict]:
    texts = [b["text"] for b in blocks]
    translated = []
    for i in range(0, len(texts), BATCH_SIZE):
        result = translate_batch(texts[i : i + BATCH_SIZE])
        translated.extend(result)
        time.sleep(SLEEP_SEC)
    for b, t in zip(blocks, translated):
        b["translated"] = t
    return blocks


# ── Geração do PDF traduzido ──────────────────────────────────────────────────
def build_translated_pdf(src_pdf: Path, dest_pdf: Path, on_page=None) -> bool:
    log.info(f"  Processando: {src_pdf.name} → {dest_pdf.name}")
    try:
        doc = fitz.open(str(src_pdf))
    except Exception as e:
        log.error(f"    Não foi possível abrir {src_pdf}: {e}")
        return False

    out_doc = fitz.open()

    for page_num in range(len(doc)):
        page = doc[page_num]
        log.info(f"    Página {page_num + 1}/{len(doc)}")
        if on_page:
            on_page(page_num + 1, len(doc))

        mat       = fitz.Matrix(1, 1)
        pix       = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img_bytes = pix.tobytes("png")

        new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(fitz.Rect(0, 0, page.rect.width, page.rect.height), stream=img_bytes)

        try:
            blocks = ocr_page(page)
            if blocks:
                blocks = translate_blocks(blocks)
                _overlay_text(new_page, blocks)
        except Exception as e:
            log.warning(f"    OCR/tradução falhou na página {page_num + 1}: {e}")

    try:
        out_doc.save(str(dest_pdf), garbage=4, deflate=True)
        log.info(f"    ✓ Salvo: {dest_pdf} ({dest_pdf.stat().st_size / 1024 / 1024:.1f} MB)")
        return True
    except Exception as e:
        log.error(f"    ✗ Falha ao salvar {dest_pdf}: {e}")
        return False

def _overlay_text(page: fitz.Page, blocks: list[dict]):
    for b in blocks:
        text = b.get("translated") or b["text"]
        if not text.strip():
            continue
        rect = fitz.Rect(b["x0"] - 2, b["y0"] - 2, b["x1"] + 2, b["y1"] + 2)
        page.draw_rect(rect, color=None, fill=(1, 1, 1), fill_opacity=0.95)
        # Try progressively smaller font sizes until text fits in the paragraph box
        font_size = b["font_size"]
        for fs in [font_size, font_size * 0.85, font_size * 0.7, 6]:
            rc = page.insert_textbox(
                rect, text,
                fontsize=max(6, fs),
                fontname="helv",
                color=(0, 0, 0),
                align=0,
            )
            if rc >= 0:
                break


# ── Pipeline ──────────────────────────────────────────────────────────────────
def run(args):
    global DPI, _json_mode
    DPI        = args.dpi
    _json_mode = getattr(args, 'json_progress', False)

    # Logging: always write to file; stderr only in non-JSON mode
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")
        fh  = logging.FileHandler(LOG_FILE)
        fh.setFormatter(fmt)
        log.addHandler(fh)
        if not _json_mode:
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            log.addHandler(sh)
        log.setLevel(logging.INFO)

    TRANSLATED_DIR.mkdir(exist_ok=True)

    if args.translate_only:
        pdf_path = Path(args.translate_only)
        build_translated_pdf(pdf_path, TRANSLATED_DIR / pdf_path.name)
        return

    records = read_catalog()

    translate_id = getattr(args, 'translate_id', None)
    if translate_id:
        rec = next((r for r in records if r['id'] == str(translate_id)), None)
        if not rec:
            log.error(f"ID {translate_id} não encontrado no catálogo")
            jout({'type': 'done', 'ok': 0, 'failed': 0})
            return
        to_translate = [rec]
    else:
        to_translate = [
            r for r in records
            if r['local_dir'] == 'PDF'
            and r['status'] == 'downloaded'
            and r.get('translated') != 'yes'
        ]
        if args.limit:
            to_translate = to_translate[:args.limit]

    total = len(to_translate)
    jout({'type': 'start', 'total': total})
    log.info(f"{total} PDF(s) para traduzir")

    if not to_translate:
        log.info("Nada a fazer. Todos os PDFs baixados já foram traduzidos.")
        jout({'type': 'done', 'ok': 0, 'failed': 0})
        return

    ok_count     = 0
    failed_count = 0

    workers = args.workers or 1
    if workers > 1 and not _json_mode:
        # Parallel mode — CLI only, no JSON progress
        def _process_parallel(rec):
            local    = rec.get('local_path')
            pdf_path = Path(local) if local else ACERVO_DIR / 'PDF' / rec['filename']
            if not pdf_path.exists():
                log.warning(f"  Arquivo não encontrado: {rec['filename']}")
                return False
            dest = TRANSLATED_DIR / pdf_path.name
            ok   = build_translated_pdf(pdf_path, dest)
            if ok:
                update_record(records, rec['id'], {
                    'translated': 'yes', 'translated_at': datetime.now().isoformat(),
                })
            return ok

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_process_parallel, r): r for r in to_translate}
            for fut in as_completed(futures):
                try:
                    if fut.result(): ok_count += 1
                    else:            failed_count += 1
                except Exception as e:
                    log.error(f"Erro inesperado: {e}")
                    failed_count += 1
    else:
        # Sequential mode — used by TUI (json-progress) and default CLI
        for file_num, rec in enumerate(to_translate, 1):
            local    = rec.get('local_path')
            pdf_path = Path(local) if local else ACERVO_DIR / 'PDF' / rec['filename']
            if not pdf_path.exists():
                log.warning(f"  Arquivo não encontrado: {rec['filename']}")
                jout({'type': 'file_done', 'file': rec['filename'], 'ok': False,
                      'num': file_num, 'total': total})
                failed_count += 1
                continue

            try:
                _tmp    = fitz.open(str(pdf_path))
                n_pages = len(_tmp)
                _tmp.close()
            except Exception:
                n_pages = 0

            jout({'type': 'file_start', 'file': rec['filename'],
                  'num': file_num, 'total': total, 'pages': n_pages})

            def _on_page(p, tp, fn=file_num, tot=total):
                jout({'type': 'page', 'page': p, 'pages': tp, 'num': fn, 'total': tot})

            dest = TRANSLATED_DIR / pdf_path.name
            ok   = build_translated_pdf(pdf_path, dest, on_page=_on_page)

            if ok:
                update_record(records, rec['id'], {
                    'translated': 'yes', 'translated_at': datetime.now().isoformat(),
                })
                ok_count += 1
            else:
                failed_count += 1

            jout({'type': 'file_done', 'file': rec['filename'], 'ok': ok,
                  'num': file_num, 'total': total})

    jout({'type': 'done', 'ok': ok_count, 'failed': failed_count})

    final        = read_catalog()
    n_translated = sum(1 for r in final if r.get('translated') == 'yes')
    log.info(f"\n{'=' * 50}")
    log.info(f"  PDFs traduzidos: {n_translated}  |  Destino: ./{TRANSLATED_DIR}/")
    log.info(f"  Log completo:    ./{LOG_FILE}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Traduz PDFs de war.gov/UFO de EN→PT (requer catalog.csv gerado por ufo_catalog.js)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Exemplos:
              python ufo_translate.py                      # traduz tudo
              python ufo_translate.py --limit 3            # só 3 (teste rápido)
              python ufo_translate.py --translate-only arquivo.pdf
              python ufo_translate.py --workers 2          # paralelo
              python ufo_translate.py --dpi 250            # OCR mais nítido
              python ufo_translate.py --json-progress      # saída JSON (TUI)
              python ufo_translate.py --translate-id 5     # só ID 5 do catálogo
        """)
    )
    ap.add_argument("--translate-only", metavar="PDF",
                    help="Traduz apenas este PDF local (sem atualizar catálogo)")
    ap.add_argument("--translate-id",   metavar="N",
                    help="Traduz apenas o registro ID N do catálogo")
    ap.add_argument("--limit",          type=int,
                    help="Máximo de PDFs a processar")
    ap.add_argument("--workers",        type=int, default=1,
                    help="Workers paralelos (padrão: 1, desativado em --json-progress)")
    ap.add_argument("--dpi",            type=int, default=DPI,
                    help=f"DPI do OCR (padrão: {DPI})")
    ap.add_argument("--json-progress",  action="store_true",
                    help="Emite eventos JSON no stdout (usado pelo TUI)")
    run(ap.parse_args())
