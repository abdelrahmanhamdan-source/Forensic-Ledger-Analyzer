import os
import io
import csv
import json
import uuid
import hashlib
import datetime
import math
import re
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
import pandas as pd
import numpy as np
from scipy import stats
from difflib import SequenceMatcher
import pdfplumber

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

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


def extract_pdf_dataframe(file_bytes):
    """
    Extract tabular data from a text-based PDF using pdfplumber.
    Returns (df, error_code):
      (DataFrame, None)        — success
      (None, 'scanned')        — no extractable text; likely a scanned/image PDF
      (None, 'no_tables')      — text found but no parseable tables
      (None, 'no_numeric_data')— tables found but fewer than 10 numeric values
      (None, 'parse_error:…')  — unexpected exception
    """
    try:
        all_tables = []
        total_chars = 0

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                total_chars += len(text.strip())
                for tbl in (page.extract_tables() or []):
                    if tbl and len(tbl) > 1:
                        all_tables.append(tbl)

        if total_chars == 0:
            return None, 'scanned'

        if not all_tables:
            return None, 'no_tables'

        best = max(all_tables, key=lambda t: len(t) * (len(t[0]) if t and t[0] else 0))

        raw_headers = best[0] or []
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

        ncols = len(final_headers)
        normalized = []
        for row in best[1:]:
            r = list(row) if row else []
            if len(r) < ncols:
                r += [None] * (ncols - len(r))
            normalized.append(r[:ncols])

        df = pd.DataFrame(normalized, columns=final_headers)

        numeric_count = sum(
            len(pd.to_numeric(df[col], errors='coerce').dropna())
            for col in df.columns
        )
        if numeric_count < 10:
            return None, 'no_numeric_data'

        return df, None

    except Exception as exc:
        return None, f'parse_error:{exc}'


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'error': 'File is too large. The maximum allowed upload size is 100 MB.'}), 413


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    filename = file.filename.lower()
    if not (filename.endswith('.csv') or filename.endswith('.xlsx')
            or filename.endswith('.xls') or filename.endswith('.pdf')):
        return jsonify({'error': 'Only CSV, Excel, and text-based PDF files are supported'}), 400

    file_bytes = file.read()
    file_hash = sha256_of_bytes(file_bytes)

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes), nrows=SAMPLE_ROW_LIMIT + 1)
        elif filename.endswith('.pdf'):
            df, err = extract_pdf_dataframe(file_bytes)
            if err == 'scanned':
                return jsonify({
                    'error': (
                        'This PDF appears to be a scanned image with no extractable text. '
                        'OCR processing is not supported because misread digits would compromise '
                        'the integrity of the analysis. Please upload a CSV, Excel, or '
                        'digital (text-based) PDF version of your data instead.'
                    )
                }), 400
            elif err == 'no_tables':
                return jsonify({
                    'error': (
                        'No structured tables were found in this PDF. '
                        'Please export your data as CSV or Excel for reliable results.'
                    )
                }), 400
            elif err == 'no_numeric_data':
                return jsonify({
                    'error': (
                        'The tables extracted from this PDF contain fewer than 10 numeric values. '
                        'Please upload a CSV or Excel file with transaction-level data.'
                    )
                }), 400
            elif err and err.startswith('parse_error:'):
                return jsonify({'error': f'Could not parse PDF: {err[len("parse_error:"):]}'}), 400
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), nrows=SAMPLE_ROW_LIMIT + 1)
    except Exception as e:
        return jsonify({'error': f'Could not parse file: {str(e)}'}), 400

    sampled = len(df) > SAMPLE_ROW_LIMIT
    if sampled:
        df = df.iloc[:SAMPLE_ROW_LIMIT]

    df = df.where(pd.notnull(df), None)
    preview = df.head(10).to_dict(orient='records')
    columns = list(df.columns)

    session_id = str(uuid.uuid4())
    temp_path = os.path.join(UPLOAD_FOLDER, f"{session_id}.pkl")
    df.to_pickle(temp_path)

    return jsonify({
        'session_id': session_id,
        'filename': file.filename,
        'file_hash': file_hash,
        'rows': len(df),
        'columns': columns,
        'preview': preview,
        'sampled': sampled,
    })


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
        return jsonify({'error': 'Too few numeric values in the selected column (minimum 10)'}), 400

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

    writer.writerow(['FORENSIC LEDGER ANALYZER — FINDINGS REPORT'])
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
