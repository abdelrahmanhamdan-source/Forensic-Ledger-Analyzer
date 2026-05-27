import os
import io
import csv
import json
import uuid
import hashlib
import datetime
import math
import re
import gc
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
import pandas as pd
import numpy as np
from scipy import stats
from difflib import SequenceMatcher
import pdfplumber

try:
    from PIL import Image as PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    PILImage = None

try:
    import pytesseract
    _OCR_AVAILABLE = _PIL_AVAILABLE
    # On Windows, point pytesseract at the Tesseract binary if it isn't on PATH.
    _TESS_PATHS = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _tp in _TESS_PATHS:
        if os.path.exists(_tp):
            pytesseract.pytesseract.tesseract_cmd = _tp
            print(f"[OCR] Tesseract found at: {_tp}")
            break
    else:
        print("[OCR] Tesseract not found at default Windows paths; relying on system PATH")
except ImportError:
    _OCR_AVAILABLE = False
    print("[OCR] pytesseract not installed — OCR disabled")

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024  # 250MB

SAMPLE_ROW_LIMIT = 50_000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
AUDIT_FILE = os.path.join(BASE_DIR, 'audit_trail.json')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

BENFORD_EXPECTED = {
    1: 30.103, 2: 17.609, 3: 12.494, 4: 9.691,
    5: 7.918, 6: 6.695, 7: 5.799, 8: 5.115, 9: 4.576
}

BENFORD_SECOND = {
    0: 11.968, 1: 11.389, 2: 10.882, 3: 10.433,
    4: 10.031, 5: 9.668, 6: 9.337, 7: 9.035, 8: 8.757, 9: 8.500
}


def load_audit():
    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, 'r') as f:
            return json.load(f)
    return []


def save_audit(records):
    with open(AUDIT_FILE, 'w') as f:
        json.dump(records, f, indent=2)


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_leading_digit(value):
    try:
        v = abs(float(value))
        if v == 0:
            return None
        s = f"{v:.10e}"
        for ch in s:
            if ch.isdigit() and ch != '0':
                return int(ch)
        return None
    except (ValueError, TypeError):
        return None


def get_second_digit(value):
    try:
        v = abs(float(value))
        if v == 0:
            return None
        s = str(v).replace('.', '').lstrip('0')
        if len(s) < 2:
            return None
        return int(s[1])
    except (ValueError, TypeError):
        return None


def benford_analysis(amounts):
    first_digits = [get_leading_digit(a) for a in amounts]
    first_digits = [d for d in first_digits if d is not None]
    n = len(first_digits)
    if n == 0:
        return None

    counts = {d: 0 for d in range(1, 10)}
    for d in first_digits:
        if d in counts:
            counts[d] += 1

    actual_pct = {d: (counts[d] / n * 100) for d in range(1, 10)}
    expected_pct = BENFORD_EXPECTED

    mad = sum(abs(actual_pct[d] - expected_pct[d]) for d in range(1, 10)) / 9 / 100

    chi2 = sum(
        ((counts[d] - n * expected_pct[d] / 100) ** 2) / (n * expected_pct[d] / 100)
        for d in range(1, 10)
    )
    chi2_p = 1 - stats.chi2.cdf(chi2, df=8)

    z_scores = {}
    for d in range(1, 10):
        p = expected_pct[d] / 100
        observed_p = actual_pct[d] / 100
        se = math.sqrt(p * (1 - p) / n)
        z_scores[d] = (observed_p - p) / se if se > 0 else 0

    if mad < 0.006:
        verdict = 'close_conformity'
        verdict_label = 'Close Conformity'
        verdict_color = 'green'
    elif mad < 0.012:
        verdict = 'acceptable'
        verdict_label = 'Acceptable Conformity'
        verdict_color = 'yellow'
    elif mad < 0.015:
        verdict = 'marginal'
        verdict_label = 'Marginal Conformity'
        verdict_color = 'orange'
    else:
        verdict = 'nonconformity'
        verdict_label = 'Nonconformity — Warrants Further Investigation'
        verdict_color = 'red'

    try:
        non_zero_abs = [abs(float(a)) for a in amounts if float(a) != 0]
        if len(non_zero_abs) >= 2:
            magnitude_span = math.log10(max(non_zero_abs)) - math.log10(min(non_zero_abs))
        else:
            magnitude_span = 0.0
    except (ValueError, TypeError):
        magnitude_span = 0.0
    range_limited = magnitude_span < 2.0

    second_digits = [get_second_digit(a) for a in amounts]
    second_digits = [d for d in second_digits if d is not None]
    n2 = len(second_digits)
    second_counts = {d: 0 for d in range(0, 10)}
    for d in second_digits:
        if d in second_counts:
            second_counts[d] += 1
    second_actual = {d: (second_counts[d] / n2 * 100) if n2 > 0 else 0 for d in range(0, 10)}

    return {
        'n': n,
        'counts': {str(k): v for k, v in counts.items()},
        'actual_pct': {str(k): round(v, 3) for k, v in actual_pct.items()},
        'expected_pct': {str(k): round(v, 3) for k, v in expected_pct.items()},
        'mad': round(mad, 6),
        'chi2': round(chi2, 4),
        'chi2_p': round(chi2_p, 6),
        'z_scores': {str(k): round(v, 4) for k, v in z_scores.items()},
        'verdict': verdict,
        'verdict_label': verdict_label,
        'verdict_color': verdict_color,
        'magnitude_span': round(magnitude_span, 3),
        'range_limited': range_limited,
        'second_actual': {str(k): round(v, 3) for k, v in second_actual.items()},
        'second_expected': {str(k): round(v, 3) for k, v in BENFORD_SECOND.items()},
        'second_n': n2,
    }


def find_duplicates(df, amount_col, invoice_col=None):
    dup_amounts = []
    amount_counts = df[amount_col].value_counts()
    dup_vals = amount_counts[amount_counts > 1].index.tolist()
    for val in dup_vals:
        rows = df[df[amount_col] == val].index.tolist()
        dup_amounts.append({'amount': str(val), 'row_indices': rows, 'count': len(rows)})

    dup_invoices = []
    if invoice_col and invoice_col in df.columns:
        inv_counts = df[invoice_col].astype(str).value_counts()
        dup_invs = inv_counts[inv_counts > 1].index.tolist()
        for inv in dup_invs:
            if inv.lower() in ('nan', 'none', ''):
                continue
            rows = df[df[invoice_col].astype(str) == inv].index.tolist()
            dup_invoices.append({'invoice': inv, 'row_indices': rows, 'count': len(rows)})

    return dup_amounts, dup_invoices


def _token_similarity(a, b):
    """Jaccard similarity on word tokens with prefix matching (handles plurals/abbreviations)."""
    ta = set(a.split())
    tb_list = list(b.split())
    used = set()
    matched = 0
    for tok_a in ta:
        for i, tok_b in enumerate(tb_list):
            if i in used:
                continue
            if tok_a == tok_b:
                matched += 1
                used.add(i)
                break
            if len(tok_a) >= 4 and len(tok_b) >= 4:
                ml = min(len(tok_a), len(tok_b))
                if tok_a[:ml] == tok_b[:ml]:
                    matched += 1
                    used.add(i)
                    break
    union = len(ta) + len(tb_list) - matched
    return matched / union if union else 0.0


def fuzzy_vendor_match(df, vendor_col, char_threshold=0.72, token_threshold=0.45):
    if not vendor_col or vendor_col not in df.columns:
        return []
    vendors = df[vendor_col].dropna().astype(str).unique().tolist()
    matches = []
    for i in range(len(vendors)):
        for j in range(i + 1, len(vendors)):
            a, b = vendors[i], vendors[j]
            al, bl = a.lower(), b.lower()
            if al == bl:
                continue
            char_ratio = SequenceMatcher(None, al, bl).ratio()
            token_ratio = _token_similarity(al, bl)
            if char_ratio >= char_threshold or token_ratio >= token_threshold:
                matches.append({
                    'vendor_a': a,
                    'vendor_b': b,
                    'similarity': round(max(char_ratio, token_ratio), 4)
                })
    return sorted(matches, key=lambda x: -x['similarity'])


def flag_rows(df, amount_col, vendor_col, invoice_col, dup_amounts, dup_invoices, fuzzy_matches):
    flagged = set()
    dup_amount_vals = {d['amount'] for d in dup_amounts}
    dup_invoice_vals = {d['invoice'] for d in dup_invoices}
    fuzzy_vendors = set()
    for m in fuzzy_matches:
        fuzzy_vendors.add(m['vendor_a'])
        fuzzy_vendors.add(m['vendor_b'])

    flags_map = {}
    for idx, row in df.iterrows():
        row_flags = []
        if str(row[amount_col]) in dup_amount_vals:
            row_flags.append('duplicate_amount')
        if invoice_col and invoice_col in df.columns:
            if str(row[invoice_col]) in dup_invoice_vals:
                row_flags.append('duplicate_invoice')
        if vendor_col and vendor_col in df.columns:
            if str(row[vendor_col]) in fuzzy_vendors:
                row_flags.append('fuzzy_vendor')
        if row_flags:
            flagged.add(idx)
        flags_map[idx] = row_flags

    return flags_map


def _strip_currency(val):
    """Remove $, commas, spaces from a value and return float or None."""
    if val is None:
        return None
    s = re.sub(r'[$,\s%]', '', str(val)).strip()
    try:
        return float(s)
    except ValueError:
        return None


def suggest_amount_col(df):
    """Return the column name most likely to contain monetary transaction amounts.

    Scores each column by:
    - Fraction of cells that parse as numbers (after stripping $, commas, spaces)
    - Bonus for decimal values and larger magnitudes (typical of money)
    - Heavy penalty for columns that look like years (1900-2100)
    - Penalty for sequential integers that look like IDs or row numbers
    """
    best_col = None
    best_score = -9999.0

    for col in df.columns:
        vals = [v for v in df[col] if v is not None and str(v).strip() != '']
        if not vals:
            continue

        numeric_vals = []
        for v in vals:
            n = _strip_currency(v)
            if n is not None:
                numeric_vals.append(n)

        numeric_frac = len(numeric_vals) / len(vals)
        if numeric_frac < 0.5 or len(numeric_vals) < 3:
            continue

        # Drop NaN and infinite values before any int() conversion or arithmetic
        import math as _math
        numeric_vals = [v for v in numeric_vals if not (_math.isnan(v) or _math.isinf(v))]
        if len(numeric_vals) < 3:
            continue

        score = numeric_frac  # base 0.5–1.0

        # Bonus: decimal values are a strong signal for money
        decimal_frac = sum(1 for v in numeric_vals if v % 1 != 0) / len(numeric_vals)
        score += decimal_frac * 0.5

        # Bonus: wide value range (monetary data spans many orders of magnitude)
        val_range = max(numeric_vals) - min(numeric_vals)
        if val_range > 0:
            score += min(math.log10(val_range + 1) / 10.0, 0.4)

        # Bonus: larger average magnitude
        mean_abs = sum(abs(v) for v in numeric_vals) / len(numeric_vals)
        if mean_abs >= 1000:
            score += 0.3
        elif mean_abs >= 100:
            score += 0.15
        elif mean_abs >= 10:
            score += 0.05

        # Heavy penalty: values concentrated in year range 1900–2100
        year_frac = sum(1 for v in numeric_vals if 1900 <= v <= 2100 and v % 1 == 0) / len(numeric_vals)
        if year_frac >= 0.8:
            score -= 2.0

        # Penalty: sequential integers (row numbers, contract IDs, etc.)
        int_vals = [v for v in numeric_vals if v % 1 == 0]
        if len(int_vals) >= 3:
            sorted_ints = sorted(set(int(v) for v in int_vals))
            if len(sorted_ints) >= 3:
                diffs = [sorted_ints[i + 1] - sorted_ints[i] for i in range(len(sorted_ints) - 1)]
                seq_frac = sum(1 for d in diffs if 1 <= d <= 3) / len(diffs)
                if seq_frac >= 0.7:
                    score -= 0.8

        if score > best_score:
            best_score = score
            best_col = col

    return best_col


def _normalize_raw_table(raw_table):
    """
    Normalize a raw pdfplumber table (list of lists) to (headers, data_rows).
    Detects modal column count so blank columns and short rows never crash.
    """
    from collections import Counter
    if not raw_table or len(raw_table) < 2:
        return None, None

    col_counts = Counter(len(r) for r in raw_table if r is not None)
    if not col_counts:
        return None, None
    modal_ncols = col_counts.most_common(1)[0][0]
    if modal_ncols < 1:
        return None, None

    raw_headers = list(raw_table[0] or [])
    if len(raw_headers) < modal_ncols:
        raw_headers += [None] * (modal_ncols - len(raw_headers))
    raw_headers = raw_headers[:modal_ncols]

    headers = []
    for i, h in enumerate(raw_headers):
        clean = str(h).strip() if h else ''
        headers.append(clean if clean else f'Column_{i + 1}')

    seen = {}
    final_headers = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            final_headers.append(f'{h}_{seen[h]}')
        else:
            seen[h] = 0
            final_headers.append(h)

    data_rows = []
    for row in raw_table[1:]:
        r = [str(v).strip() if v is not None else '' for v in (row or [])]
        if len(r) < modal_ncols:
            r += [''] * (modal_ncols - len(r))
        data_rows.append(r[:modal_ncols])

    return final_headers, data_rows


def _text_strategy_tables(pdf):
    """
    Strategy C: reconstruct columns by aligning words to x-position buckets.
    Returns list of raw tables (list of lists of strings).
    """
    page_tables = []
    for page in pdf.pages:
        words = page.extract_words() or []
        if not words:
            continue

        lines = {}
        for word in words:
            y_bucket = round(word['top'] / 5) * 5
            lines.setdefault(y_bucket, []).append(word)

        sorted_lines = [sorted(ws, key=lambda w: w['x0']) for _, ws in sorted(lines.items())]
        if len(sorted_lines) < 2:
            continue

        header_line = max(sorted_lines, key=len)
        if len(header_line) < 2:
            continue

        col_xs = [w['x0'] for w in header_line]
        ncols = len(col_xs)

        table_rows = []
        for line_words in sorted_lines:
            row = [''] * ncols
            for word in line_words:
                dists = [abs(word['x0'] - cx) for cx in col_xs]
                col_idx = dists.index(min(dists))
                row[col_idx] = (row[col_idx] + ' ' + word['text']).strip()
            table_rows.append(row)

        if len(table_rows) >= 2:
            page_tables.append(table_rows)

    return page_tables


def _combine_page_tables(tables):
    """
    Merge tables from different pages that share identical headers.
    Input/output: list of [headers, data_rows].
    """
    combined = []
    for headers, data_rows in tables:
        for existing in combined:
            if existing[0] == headers:
                existing[1].extend(data_rows)
                break
        else:
            combined.append([headers, list(data_rows)])
    return combined


def _build_dataframe_from_normalized(normalized):
    """Build and clean a DataFrame from (headers, data_rows) list. Returns (df, None) or (None, error_code)."""
    combined = _combine_page_tables(normalized)
    best_headers, best_rows = max(combined, key=lambda t: len(t[1]) * len(t[0]))

    df = pd.DataFrame(best_rows, columns=best_headers)
    df = df[~df.apply(
        lambda r: all(str(v).strip() == '' for v in r), axis=1
    )].reset_index(drop=True)

    def _col_numeric_frac(series):
        vals = [v for v in series if v is not None and str(v).strip() != '']
        if not vals:
            return 0.0
        return sum(1 for v in vals if _strip_currency(v) is not None) / len(vals)

    def _coerce(val):
        if val is None or str(val).strip() == '':
            return None
        num = _strip_currency(val)
        return num if num is not None else (str(val).strip() or None)

    for col in df.columns:
        if _col_numeric_frac(df[col]) >= 0.5:
            df[col] = df[col].apply(_coerce)

    for col in df.columns:
        df[col] = df[col].map(
            lambda v: None if (isinstance(v, str) and v.strip() == '') else v
        )

    numeric_count = sum(
        len(pd.to_numeric(df[col], errors='coerce').dropna())
        for col in df.columns
    )
    if numeric_count < 10:
        return None, 'no_numeric_data'

    return df, None


def extract_pdf_dataframe(file_bytes):
    """
    Extract tabular data from a PDF.  For text-based PDFs, tries three strategies
    in order (A → B → C).  For scanned/image-based PDFs (< 50 chars of extractable
    text), falls back to OCR via pytesseract + pdfplumber page rendering.

    Returns (df, None, ocr_used) on success, or (None, error_code, ocr_used) on failure.
    Error codes: 'ocr_unavailable' | 'no_tables' | 'no_numeric_data' | 'parse_error:<msg>'
    """
    try:
        total_chars = 0

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                total_chars += len((page.extract_text() or '').strip())

            if total_chars < 50:
                print(f"[PDF] Extractable text: {total_chars} chars — below threshold, triggering OCR fallback")
                return _extract_pdf_via_ocr(file_bytes)

            print(f"[PDF] Extractable text: {total_chars} chars — using text extraction strategies")

            # Strategy A: default extract_tables()
            raw_tables = []
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    if tbl and len(tbl) > 1:
                        raw_tables.append(tbl)

            # Strategy B: explicit text strategy (if A found nothing)
            if not raw_tables:
                ts = {"vertical_strategy": "text", "horizontal_strategy": "text"}
                for page in pdf.pages:
                    for tbl in (page.extract_tables(table_settings=ts) or []):
                        if tbl and len(tbl) > 1:
                            raw_tables.append(tbl)

            normalized = []
            for rt in raw_tables:
                h, rows = _normalize_raw_table(rt)
                if h and rows:
                    normalized.append((h, rows))

            # Strategy C: word-position alignment (if A and B found nothing)
            if not normalized:
                for rt in _text_strategy_tables(pdf):
                    h, rows = _normalize_raw_table(rt)
                    if h and rows:
                        normalized.append((h, rows))

        if not normalized:
            return None, 'no_tables', False

        df, err = _build_dataframe_from_normalized(normalized)
        return (None, err, False) if err else (df, None, False)

    except Exception as exc:
        app.logger.error('PDF extraction error: %s', exc, exc_info=True)
        return None, f'parse_error:{exc}', False


def _ocr_words_from_image(img):
    """Run pytesseract on a PIL image; return list of {text, x0, top} dicts (conf >= 30)."""
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = []
    for i, text in enumerate(data['text']):
        text = str(text).strip()
        if not text:
            continue
        try:
            conf = int(data['conf'][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < 30:
            continue
        words.append({'text': text, 'x0': data['left'][i], 'top': data['top'][i]})
    return words


def _words_to_raw_table(words):
    """Align word-position dicts into a raw table (list of rows) using x-coordinate bucketing."""
    if not words:
        return None
    lines = {}
    for word in words:
        y_bucket = round(word['top'] / 5) * 5
        lines.setdefault(y_bucket, []).append(word)
    sorted_lines = [sorted(ws, key=lambda w: w['x0']) for _, ws in sorted(lines.items())]
    if len(sorted_lines) < 2:
        return None
    header_line = max(sorted_lines, key=len)
    if len(header_line) < 2:
        return None
    col_xs = [w['x0'] for w in header_line]
    ncols = len(col_xs)
    table_rows = []
    for line_words in sorted_lines:
        row = [''] * ncols
        for word in line_words:
            dists = [abs(word['x0'] - cx) for cx in col_xs]
            col_idx = dists.index(min(dists))
            row[col_idx] = (row[col_idx] + ' ' + word['text']).strip()
        table_rows.append(row)
    return table_rows if len(table_rows) >= 2 else None


_OCR_MAX_SIDE = 2500   # pixels — downscale before OCR if larger
_OCR_RESOLUTION = 150  # DPI for rasterization (lower = less memory)


def _extract_pdf_via_ocr(file_bytes):
    """
    OCR fallback for scanned/image-based PDFs (called when extractable text < 50 chars).
    Uses pdfplumber's page.to_image() (backed by pypdfium2) — no poppler required.
    Returns (df, None, True) on success, (None, error_code, True) on failure.
    """
    if not _OCR_AVAILABLE:
        return None, 'ocr_unavailable', True
    print(f"[OCR] Triggering OCR fallback — rasterizing PDF pages at {_OCR_RESOLUTION} DPI")
    try:
        normalized = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for page_num in range(total_pages):
                page = pdf.pages[page_num]
                print(f"[OCR] Rasterizing page {page_num + 1}/{total_pages} at {_OCR_RESOLUTION} DPI...")
                img_obj = None
                img = None
                try:
                    img_obj = page.to_image(resolution=_OCR_RESOLUTION)
                    img = img_obj.original
                    w, h = img.size
                    print(f"[OCR] Page {page_num + 1} rasterized: {w}x{h} px, mode={img.mode}")
                    if max(w, h) > _OCR_MAX_SIDE:
                        img.thumbnail((_OCR_MAX_SIDE, _OCR_MAX_SIDE), PILImage.Resampling.LANCZOS)
                        w2, h2 = img.size
                        print(f"[OCR] Page {page_num + 1} downscaled to {w2}x{h2} px")
                except Exception as render_exc:
                    print(f"[OCR] Page {page_num + 1} rasterization failed: {render_exc}")
                    del img_obj, img
                    gc.collect()
                    continue
                try:
                    words = _ocr_words_from_image(img)
                finally:
                    del img, img_obj
                    gc.collect()
                char_count = sum(len(w['text']) for w in words)
                print(f"[OCR] Page {page_num + 1}: extracted {char_count} characters via OCR")
                raw_table = _words_to_raw_table(words)
                if raw_table:
                    h_cols, rows = _normalize_raw_table(raw_table)
                    if h_cols and rows:
                        normalized.append((h_cols, rows))
        if not normalized:
            print("[OCR] OCR completed but no structured table detected across all pages")
            return None, 'no_tables', True
        df, err = _build_dataframe_from_normalized(normalized)
        if err:
            print(f"[OCR] DataFrame construction failed: {err}")
            return None, err, True
        numeric_count = sum(
            len(pd.to_numeric(df[col], errors='coerce').dropna())
            for col in df.columns
        )
        print(f"[OCR] OCR extraction successful — {len(df)} rows, {numeric_count} numeric values parsed")
        return df, None, True
    except pytesseract.TesseractNotFoundError:
        print("[OCR] TesseractNotFoundError — Tesseract binary not found")
        return None, 'ocr_unavailable', True
    except Exception as exc:
        app.logger.error('OCR extraction error: %s', exc, exc_info=True)
        print(f"[OCR] Unexpected error during OCR: {exc}")
        return None, f'parse_error:{exc}', True


def _render_pdf_first_page_preview(file_bytes):
    """Render first PDF page at low resolution for the upload preview UI. Returns data URL or None."""
    if not _PIL_AVAILABLE:
        return None
    import base64
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                return None
            img_obj = pdf.pages[0].to_image(resolution=96)
            img = img_obj.original
            w, h = img.size
            print(f"[Preview] PDF first page: {w}x{h} px")
            if max(w, h) > 600:
                img.thumbnail((600, 600), PILImage.Resampling.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=75, optimize=True)
            del img, img_obj
            return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        print(f"[Preview] PDF first-page render failed: {exc}")
        return None


def extract_image_dataframe(file_bytes):
    """
    Extract tabular data from a PNG/JPG image using OCR.
    Returns (df, None, True) on success, (None, error_code, True) on failure.
    """
    if not _OCR_AVAILABLE:
        return None, 'ocr_unavailable', True
    try:
        img = PILImage.open(io.BytesIO(file_bytes))
    except Exception as exc:
        return None, f'parse_error:{exc}', True
    try:
        words = _ocr_words_from_image(img)
        raw_table = _words_to_raw_table(words)
        if not raw_table:
            return None, 'no_tables', True
        h, rows = _normalize_raw_table(raw_table)
        if not h or not rows:
            return None, 'no_tables', True
        df, err = _build_dataframe_from_normalized([(h, rows)])
        return (None, err, True) if err else (df, None, True)
    except pytesseract.TesseractNotFoundError:
        return None, 'ocr_unavailable', True
    except Exception as exc:
        app.logger.error('Image OCR extraction error: %s', exc, exc_info=True)
        return None, f'parse_error:{exc}', True


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File too large (max 250 MB). Please upload a smaller file.'}), 413


@app.errorhandler(500)
def internal_server_error(error):
    app.logger.error('Internal server error: %s', error, exc_info=True)
    return jsonify({'error': 'An unexpected server error occurred. Please try again or contact support.'}), 500


@app.errorhandler(Exception)
def unhandled_exception(error):
    app.logger.error('Unhandled exception: %s', error, exc_info=True)
    return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        filename = file.filename.lower()
        _img_exts = ('.png', '.jpg', '.jpeg')
        if not (filename.endswith('.csv') or filename.endswith('.xlsx')
                or filename.endswith('.xls') or filename.endswith('.pdf')
                or filename.endswith(_img_exts)):
            return jsonify({'error': 'Only CSV, Excel, PDF, and image (PNG/JPG/JPEG) files are supported'}), 400

        try:
            file_bytes = file.read()
        except Exception as e:
            app.logger.error('File read error: %s', e, exc_info=True)
            return jsonify({'error': 'Failed to read the uploaded file. Please try again.'}), 400

        file_hash = sha256_of_bytes(file_bytes)
        ocr_used = False
        pdf_preview = None

        try:
            if filename.endswith('.csv'):
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), nrows=SAMPLE_ROW_LIMIT + 1)
                except pd.errors.ParserError:
                    return jsonify({'error': (
                        "We couldn't reliably read this file's table structure. "
                        "This often happens with CSV files that have inconsistent column counts or "
                        "non-standard formatting. Please verify the file is a properly formatted CSV, "
                        "or export a fresh copy from your source system."
                    )}), 400
            elif filename.endswith('.pdf'):
                df, err, ocr_used = extract_pdf_dataframe(file_bytes)
                if err == 'ocr_unavailable':
                    return jsonify({
                        'error': (
                            'This PDF appears to be scanned or image-based, but the OCR engine (Tesseract) '
                            'was not found. Install Tesseract OCR (available at github.com/UB-Mannheim/tesseract/wiki) '
                            'and restart the app. Alternatively, upload a digital (text-based) PDF, '
                            'or export the data as CSV or Excel.'
                        )
                    }), 400
                elif err == 'no_tables':
                    return jsonify({
                        'error': (
                            'Text was found in this PDF (or OCR was attempted), but its layout could '
                            'not be parsed into a structured data table. This commonly occurs with '
                            'formatted financial statements, annual reports, or documents that mix '
                            'narrative text with visually formatted tables — layouts that cannot be '
                            'reliably converted to transaction-level data. '
                            'Please upload the underlying data as a CSV or Excel file instead.'
                        )
                    }), 400
                elif err == 'no_numeric_data':
                    return jsonify({
                        'error': (
                            'A table was found in this PDF, but it appears to contain summary or '
                            'aggregate figures (such as totals, subtotals, or category-level amounts) '
                            "rather than the many individual transaction amounts Benford's Law requires. "
                            'Summary financial statements — income statements, balance sheets, budget '
                            'summaries — are not suitable for this analysis. '
                            'Please upload a transaction-level ledger, invoice list, or journal export '
                            'as a CSV or Excel file.'
                        )
                    }), 400
                elif err and err.startswith('parse_error:'):
                    app.logger.error('PDF parse_error returned: %s', err)
                    return jsonify({'error': (
                        'This PDF could not be processed — text extraction and OCR both failed or '
                        'produced unusable results. Please upload the data as a CSV or Excel file.'
                    )}), 400
                if not err:
                    pdf_preview = _render_pdf_first_page_preview(file_bytes)
            elif filename.endswith(_img_exts):
                df, err, ocr_used = extract_image_dataframe(file_bytes)
                if err == 'ocr_unavailable':
                    return jsonify({'error': (
                        'The OCR engine (Tesseract) was not found — image files require OCR to extract text. '
                        'Install Tesseract OCR and restart the app, or upload a CSV or Excel file instead.'
                    )}), 400
                elif err == 'no_tables':
                    return jsonify({'error': (
                        'No table structure could be detected in this image. OCR works best on '
                        'clear, high-contrast images of tabular data. If possible, export the data '
                        'directly as a CSV or Excel file.'
                    )}), 400
                elif err == 'no_numeric_data':
                    return jsonify({'error': (
                        'A table was found in this image but it contains insufficient numeric data '
                        'for analysis. Please verify the image shows transaction-level financial data '
                        'with at least 10 numeric values.'
                    )}), 400
                elif err and err.startswith('parse_error:'):
                    app.logger.error('Image OCR parse_error: %s', err)
                    return jsonify({'error': (
                        'The image could not be processed. Please try a higher-resolution scan '
                        'or upload the data as a CSV or Excel file.'
                    )}), 400
            else:
                df = pd.read_excel(io.BytesIO(file_bytes), nrows=SAMPLE_ROW_LIMIT + 1)
        except Exception as e:
            app.logger.error('File parse error: %s', e, exc_info=True)
            return jsonify({'error': (
                'The file could not be read. It may be password-protected, corrupted, or in an '
                'unsupported format. Please verify the file and try again, or upload a CSV or Excel version.'
            )}), 400

        try:
            sampled = len(df) > SAMPLE_ROW_LIMIT
            if sampled:
                df = df.iloc[:SAMPLE_ROW_LIMIT]

            df = df.where(pd.notnull(df), None)
            _preview_raw = df.head(10).to_dict(orient='records')
            preview = [
                {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
                for row in _preview_raw
            ]
            columns = list(df.columns)

            # Early check: at least one column must have ≥10 usable numeric values
            usable_cols = [
                col for col in df.columns
                if len(pd.to_numeric(df[col], errors='coerce').dropna()) >= 10
            ]
            if not usable_cols:
                return jsonify({
                    'error': (
                        'This file appears to contain summary or aggregate figures rather than '
                        "individual transaction amounts. Benford's Law requires many individual "
                        'numeric values — such as a ledger, invoice list, or journal export — '
                        'not a summary financial statement or report. '
                        'Please upload transaction-level data where at least one column contains '
                        '10 or more individual numeric amounts.'
                    )
                }), 400

            suggested_amount_col = suggest_amount_col(df)

            session_id = str(uuid.uuid4())
            temp_path = os.path.join(UPLOAD_FOLDER, f"{session_id}.pkl")
            df.to_pickle(temp_path)
        except Exception as e:
            app.logger.error('Post-parse processing error: %s', e, exc_info=True)
            return jsonify({'error': (
                'The file could not be prepared for analysis due to an unexpected formatting issue. '
                'Please try uploading a CSV or Excel version of the data.'
            )}), 400

        return jsonify({
            'session_id': session_id,
            'filename': file.filename,
            'file_hash': file_hash,
            'rows': len(df),
            'columns': columns,
            'preview': preview,
            'sampled': sampled,
            'suggested_amount_col': suggested_amount_col,
            'ocr_used': ocr_used,
            'pdf_preview': pdf_preview,
        })

    except Exception as e:
        app.logger.error('Unexpected upload error: %s', e, exc_info=True)
        return jsonify({'error': 'An unexpected error occurred during upload. Please try again.'}), 500


@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    session_id = data.get('session_id')
    amount_col = data.get('amount_col')
    vendor_col = data.get('vendor_col', '')
    invoice_col = data.get('invoice_col', '')
    investigator_id = data.get('investigator_id', 'Unknown')
    filename = data.get('filename', '')
    file_hash = data.get('file_hash', '')

    if not session_id or not amount_col:
        return jsonify({'error': 'Missing session_id or amount_col'}), 400

    temp_path = os.path.join(UPLOAD_FOLDER, f"{session_id}.pkl")
    if not os.path.exists(temp_path):
        return jsonify({'error': 'Session expired or not found'}), 404

    df = pd.read_pickle(temp_path)

    if amount_col not in df.columns:
        return jsonify({'error': f'Column "{amount_col}" not found'}), 400

    amounts_raw = df[amount_col].dropna()
    amounts = pd.to_numeric(amounts_raw, errors='coerce').dropna().tolist()

    if len(amounts) < 10:
        return jsonify({
            'error': (
                f'The selected column "{amount_col}" contains only {len(amounts)} numeric '
                'value(s) — at least 10 are required. '
                "Benford's Law analysis needs a dataset of many individual transaction amounts "
                '(e.g. a ledger or invoice list), not a summary or report. '
                'Try selecting a different column from the dropdown above, or upload a file '
                'with transaction-level data.'
            )
        }), 400

    benford = benford_analysis(amounts)
    dup_amounts, dup_invoices = find_duplicates(df, amount_col, invoice_col or None)
    fuzzy = fuzzy_vendor_match(df, vendor_col or None)
    flags_map = flag_rows(df, amount_col, vendor_col or None, invoice_col or None,
                          dup_amounts, dup_invoices, fuzzy)

    case_number = f"CASE-{datetime.datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
    timestamp = datetime.datetime.utcnow().isoformat() + 'Z'

    audit_record = {
        'case_number': case_number,
        'timestamp': timestamp,
        'investigator_id': investigator_id,
        'filename': filename,
        'file_hash': file_hash,
        'rows_analyzed': len(amounts),
        'verdict': benford['verdict_label'] if benford else 'N/A',
    }
    audit = load_audit()
    audit.append(audit_record)
    save_audit(audit)

    rows_out = []
    for idx, row in df.iterrows():
        r = {k: (None if (isinstance(v, float) and math.isnan(v)) else v)
             for k, v in row.items()}
        r['_row_flags'] = flags_map.get(idx, [])
        r['_idx'] = int(idx)
        rows_out.append(r)

    return jsonify({
        'case_number': case_number,
        'timestamp': timestamp,
        'investigator_id': investigator_id,
        'filename': filename,
        'file_hash': file_hash,
        'benford': benford,
        'dup_amounts': dup_amounts[:50],
        'dup_invoices': dup_invoices[:50],
        'fuzzy_vendors': fuzzy[:30],
        'rows': rows_out[:500],
        'amount_col': amount_col,
        'vendor_col': vendor_col,
        'invoice_col': invoice_col,
    })


@app.route('/api/audit', methods=['GET'])
def get_audit():
    return jsonify(load_audit())


@app.route('/api/export/csv', methods=['POST'])
def export_csv():
    data = request.json
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['WEBCERTAIN APPLICATION ANALYZER — FINDINGS REPORT'])
    writer.writerow(['Case Number', data.get('case_number', '')])
    writer.writerow(['Investigator', data.get('investigator_id', '')])
    writer.writerow(['Timestamp (UTC)', data.get('timestamp', '')])
    writer.writerow(['File', data.get('filename', '')])
    writer.writerow(['SHA-256', data.get('file_hash', '')])
    writer.writerow([])

    b = data.get('benford', {})
    writer.writerow(['BENFORD\'S LAW ANALYSIS'])
    writer.writerow(['Records Analyzed', b.get('n', '')])
    writer.writerow(['MAD', b.get('mad', '')])
    writer.writerow(['Chi-Square', b.get('chi2', '')])
    writer.writerow(['Chi-Square p-value', b.get('chi2_p', '')])
    writer.writerow(['Verdict', b.get('verdict_label', '')])
    writer.writerow([])
    writer.writerow(['Digit', 'Actual %', 'Expected %', 'Z-Score'])
    for d in range(1, 10):
        writer.writerow([
            str(d),
            b.get('actual_pct', {}).get(str(d), ''),
            b.get('expected_pct', {}).get(str(d), ''),
            b.get('z_scores', {}).get(str(d), ''),
        ])
    writer.writerow([])

    writer.writerow(['DUPLICATE AMOUNTS'])
    for dup in data.get('dup_amounts', []):
        writer.writerow([f"Amount {dup['amount']} appears {dup['count']} times at rows: {dup['row_indices']}"])
    writer.writerow([])

    writer.writerow(['DUPLICATE INVOICES'])
    for dup in data.get('dup_invoices', []):
        writer.writerow([f"Invoice {dup['invoice']} appears {dup['count']} times at rows: {dup['row_indices']}"])
    writer.writerow([])

    writer.writerow(['FUZZY VENDOR MATCHES'])
    for m in data.get('fuzzy_vendors', []):
        writer.writerow([f"{m['vendor_a']} vs {m['vendor_b']} (similarity: {m['similarity']})"])
    writer.writerow([])

    rows = data.get('rows', [])
    if rows:
        headers = [k for k in rows[0].keys() if not k.startswith('_')]
        writer.writerow(['FLAGGED TRANSACTIONS'] + [''] * len(headers))
        writer.writerow(headers + ['Flags'])
        for row in rows:
            if row.get('_row_flags'):
                writer.writerow([row.get(h, '') for h in headers] + [', '.join(row['_row_flags'])])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"forensic_report_{data.get('case_number', 'report')}.csv"
    )


@app.route('/api/sample', methods=['GET'])
def get_sample():
    rows = [
        {'invoice': 'INV-001', 'vendor': 'Apex Build Services', 'amount': 12500.00},
        {'invoice': 'INV-002', 'vendor': 'Metro Supplies Ltd', 'amount': 3200.50},
        {'invoice': 'INV-003', 'vendor': 'Apex Builds Inc', 'amount': 8750.00},
        {'invoice': 'INV-004', 'vendor': 'City Contractors', 'amount': 1450.75},
        {'invoice': 'INV-005', 'vendor': 'Metro Supplies Ltd', 'amount': 9999.99},
        {'invoice': 'INV-006', 'vendor': 'Delta Logistics', 'amount': 23100.00},
        {'invoice': 'INV-007', 'vendor': 'Apex Build Services', 'amount': 5600.00},
        {'invoice': 'INV-008', 'vendor': 'Summit Tech Group', 'amount': 14200.00},
        {'invoice': 'INV-009', 'vendor': 'City Contractors', 'amount': 3200.50},
        {'invoice': 'INV-010', 'vendor': 'Pinnacle Consult', 'amount': 9999.99},
        {'invoice': 'INV-011', 'vendor': 'Delta Logistics', 'amount': 18750.00},
        {'invoice': 'INV-012', 'vendor': 'Apex Build Srvcs', 'amount': 6200.00},
        {'invoice': 'INV-013', 'vendor': 'Metro Supplies Ltd', 'amount': 4100.25},
        {'invoice': 'INV-014', 'vendor': 'Summit Tech Group', 'amount': 11000.00},
        {'invoice': 'INV-015', 'vendor': 'Pinnacle Consult', 'amount': 7825.00},
        {'invoice': 'INV-016', 'vendor': 'Harbor Freight Co', 'amount': 29500.00},
        {'invoice': 'INV-017', 'vendor': 'Delta Logistics', 'amount': 3750.80},
        {'invoice': 'INV-018', 'vendor': 'City Contractors', 'amount': 8200.00},
        {'invoice': 'INV-019', 'vendor': 'Summit Tech Group', 'amount': 15400.00},
        {'invoice': 'INV-020', 'vendor': 'Harbor Freight Co', 'amount': 2950.00},
        {'invoice': 'INV-021', 'vendor': 'Apex Build Services', 'amount': 19800.00},
        {'invoice': 'INV-022', 'vendor': 'Metro Supplies Ltd', 'amount': 6500.00},
        {'invoice': 'INV-023', 'vendor': 'Pinnacle Consult', 'amount': 13200.00},
        {'invoice': 'INV-024', 'vendor': 'Delta Logistics', 'amount': 4800.00},
        {'invoice': 'INV-025', 'vendor': 'City Contractors', 'amount': 9100.00},
        {'invoice': 'INV-026', 'vendor': 'Harbor Freight Co', 'amount': 31000.00},
        {'invoice': 'INV-027', 'vendor': 'Summit Tech Group', 'amount': 5250.00},
        {'invoice': 'INV-028', 'vendor': 'Apex Build Services', 'amount': 17600.00},
        {'invoice': 'INV-029', 'vendor': 'Metro Supplies Ltd', 'amount': 2200.00},
        {'invoice': 'INV-030', 'vendor': 'Pinnacle Consult', 'amount': 9999.99},
        {'invoice': 'INV-031', 'vendor': 'Delta Logistics', 'amount': 44000.00},
        {'invoice': 'INV-032', 'vendor': 'City Contractors', 'amount': 7300.00},
        {'invoice': 'INV-033', 'vendor': 'Harbor Freight Co', 'amount': 16500.00},
        {'invoice': 'INV-034', 'vendor': 'Summit Tech Group', 'amount': 3900.00},
        {'invoice': 'INV-035', 'vendor': 'Apex Build Services', 'amount': 11250.00},
        {'invoice': 'INV-036', 'vendor': 'Metro Supplies Ltd', 'amount': 8600.00},
        {'invoice': 'INV-037', 'vendor': 'Pinnacle Consult', 'amount': 4400.00},
        {'invoice': 'INV-038', 'vendor': 'Delta Logistics', 'amount': 21000.00},
        {'invoice': 'INV-039', 'vendor': 'City Contractors', 'amount': 5800.00},
        {'invoice': 'INV-040', 'vendor': 'Harbor Freight Co', 'amount': 9999.99},
        {'invoice': 'INV-041', 'vendor': 'Summit Tech Group', 'amount': 13750.00},
        {'invoice': 'INV-042', 'vendor': 'Apex Build Services', 'amount': 2700.00},
        {'invoice': 'INV-043', 'vendor': 'Metro Supplies Ltd', 'amount': 34500.00},
        {'invoice': 'INV-044', 'vendor': 'Pinnacle Consult', 'amount': 6100.00},
        {'invoice': 'INV-045', 'vendor': 'Delta Logistics', 'amount': 18200.00},
        {'invoice': 'INV-046', 'vendor': 'City Contractors', 'amount': 3100.00},
        {'invoice': 'INV-047', 'vendor': 'Harbor Freight Co', 'amount': 9800.00},
        {'invoice': 'INV-048', 'vendor': 'Summit Tech Group', 'amount': 4650.00},
        {'invoice': 'INV-049', 'vendor': 'Apex Build Services', 'amount': 27300.00},
        {'invoice': 'INV-050', 'vendor': 'Metro Supplies Ltd', 'amount': 1980.00},
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['invoice', 'vendor', 'amount'])
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='sample_transactions.csv'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
