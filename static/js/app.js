/* ─── State ────────────────────────────────────────────────── */
let state = {
  sessionId: null,
  filename: '',
  fileHash: '',
  columns: [],
  analysisResult: null,
  allRows: [],
  sortCol: null,
  sortDir: 1,
  benfordChart: null,
  secondChart: null,
  sampled: false,
  ocrUsed: false,
  uploadedFileExt: '',
};

/* ─── Welcome Screen ───────────────────────────────────────── */
function startInvestigation() {
  const ws = document.getElementById('welcome-screen');
  ws.classList.add('fading');
  setTimeout(() => {
    ws.classList.add('hidden');
    document.getElementById('app-header').classList.remove('hidden');
    document.getElementById('app-main').classList.remove('hidden');
  }, 280);
}

function openBenfordModal() {
  document.getElementById('benford-modal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeBenfordModal() {
  document.getElementById('benford-modal').classList.add('hidden');
  document.body.style.overflow = '';
}

function openInvNote() {
  openBenfordModal();
  const details = document.querySelector('.inv-note');
  if (details) details.open = true;
  setTimeout(() => {
    if (details) details.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 120);
}

document.getElementById('benford-modal').addEventListener('click', function(e) {
  if (e.target === this) closeBenfordModal();
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeBenfordModal();
});

/* ─── Tabs ─────────────────────────────────────────────────── */
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('hidden'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.remove('hidden');
    if (btn.dataset.tab === 'audit') loadAudit();
  });
});

/* ─── Drag & Drop ──────────────────────────────────────────── */
const dropZone = document.getElementById('drop-zone');
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleFileUpload(file);
});
document.getElementById('file-input').addEventListener('change', e => {
  if (e.target.files[0]) handleFileUpload(e.target.files[0]);
});

/* ─── File Upload ──────────────────────────────────────────── */
function resetForNewFile() {
  if (state.benfordChart) { state.benfordChart.destroy(); state.benfordChart = null; }
  if (state.secondChart)  { state.secondChart.destroy();  state.secondChart  = null; }

  state.sessionId      = null;
  state.filename       = '';
  state.fileHash       = '';
  state.columns        = [];
  state.analysisResult = null;
  state.allRows        = [];
  state.sortCol        = null;
  state.sortDir        = 1;
  state.sampled        = false;
  state.ocrUsed        = false;
  state.uploadedFileExt = '';

  hide('results-container');
  hide('upload-preview');
  hide('summary-data-notice');
  hide('col-selectors');
  hide('analyze-btn');
  hide('file-info');
  show('drop-zone');

  document.getElementById('upload-preview-content').innerHTML = '';
  document.getElementById('upload-preview-meta').innerHTML    = '';
  document.getElementById('transactions-table').innerHTML     = '';

  const sn = document.getElementById('sample-notice');
  if (sn) sn.className = 'hidden';
  const ow = document.getElementById('ocr-warning-banner');
  if (ow) ow.className = 'hidden';

  resetInterpretation();

  document.getElementById('file-input').value = '';
}

async function handleFileUpload(file) {
  resetForNewFile();
  const allowed = ['.csv', '.xlsx', '.xls', '.pdf', '.png', '.jpg', '.jpeg'];
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
  if (!allowed.includes(ext)) { showToast('Unsupported file type. Accepted formats: CSV, Excel (.xlsx / .xls), PDF, and images (PNG/JPG).', 'error'); return; }

  showProgress(true);
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    let data;
    try {
      data = await res.json();
    } catch {
      if (res.status === 413) {
        showToast('File too large (max 250 MB). Please upload a smaller file.', 'error');
      } else if (res.status >= 500) {
        showToast('A server error occurred during upload. Please try again.', 'error');
      } else {
        showToast('Upload could not be completed. Please try a different file or format.', 'error');
      }
      showProgress(false);
      return;
    }
    if (!res.ok) {
      if (data.rejection_type === 'summary_data') {
        showSummaryDataNotice();
      } else {
        showToast(data.error || 'File upload failed. Please try again.', 'error');
      }
      showProgress(false);
      return;
    }
    state.sessionId = data.session_id;
    state.filename = data.filename;
    state.fileHash = data.file_hash;
    state.columns = data.columns;
    state.sampled = data.sampled || false;
    state.ocrUsed = data.ocr_used || false;
    populateColumnSelectors(data.columns, data.suggested_amount_col);

    // Build preview meta bar
    const imgExts = ['.png', '.jpg', '.jpeg'];
    const rowsText = imgExts.includes(ext) ? 'image' : `${data.rows} rows`;
    const sampleTag = data.sampled ? ' &nbsp;<span class="sample-tag">&#9888; sampled</span>' : '';
    const shortHash = data.file_hash.substring(0, 16) + '&hellip;';
    document.getElementById('upload-preview-meta').innerHTML =
      `<div class="upm-filename">${escHtml(data.filename)}</div>` +
      `<div class="upm-meta-row">` +
        `<span class="upm-chip"><strong>${rowsText}</strong>${sampleTag}</span>` +
        `<span class="upm-chip">SHA-256:&nbsp;<span class="mono">${shortHash}</span></span>` +
      `</div>`;

    // Build preview content
    const previewContent = document.getElementById('upload-preview-content');
    if (imgExts.includes(ext)) {
      const reader = new FileReader();
      reader.onload = e => {
        previewContent.innerHTML = `<img src="${e.target.result}" alt="Uploaded image preview">`;
      };
      reader.readAsDataURL(file);
    } else if (ext === '.pdf') {
      if (data.pdf_preview) {
        previewContent.innerHTML = `<img src="${data.pdf_preview}" alt="PDF page 1 preview">`;
      } else {
        previewContent.innerHTML = `<p class="upm-no-preview">PDF page preview unavailable</p>`;
      }
    } else {
      renderUploadPreviewTable(previewContent, data.preview, data.columns);
    }

    hide('drop-zone');
    show('upload-preview');
    show('col-selectors');
    show('analyze-btn');

    // Right-column file info (compact, for reference while configuring columns)
    document.getElementById('file-info').classList.remove('hidden');
    const sampleNote = data.sampled ? ' &nbsp;<span class="sample-tag">&#9888; First 50,000 rows sampled</span>' : '';
    document.getElementById('file-info').innerHTML =
      `<strong>${escHtml(data.filename)}</strong> &nbsp;|&nbsp; ${data.rows} rows &nbsp;|&nbsp; SHA-256: <span class="mono">${data.file_hash}</span>${sampleNote}`;

    const ocrBanner = document.getElementById('ocr-warning-banner');
    if (state.ocrUsed) {
      ocrBanner.className = 'ocr-warning-banner';
      ocrBanner.innerHTML = '&#9888; <strong>OCR-Extracted Data</strong> &mdash; This file was processed using Optical Character Recognition. OCR can misread digits (e.g. 0&rarr;O, 1&rarr;I, 5&rarr;S). <strong>Verify all figures against the source document before relying on this analysis.</strong>';
    } else {
      ocrBanner.className = 'hidden';
    }
  } catch (e) {
    showToast('Connection error — ' + e.message, 'error');
  } finally {
    showProgress(false);
  }
}

function showSummaryDataNotice() {
  hide('drop-zone');
  show('summary-data-notice');
}

function showProgress(on) {
  document.getElementById('drop-inner').classList.toggle('hidden', on);
  document.getElementById('drop-progress').classList.toggle('hidden', !on);
}

function populateColumnSelectors(cols, suggestedAmountCol) {
  ['amount-col', 'vendor-col', 'invoice-col'].forEach(id => {
    const sel = document.getElementById(id);
    const isAmount = id === 'amount-col';
    sel.innerHTML = isAmount ? '' : '<option value="">— none —</option>';
    cols.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });
    if (isAmount) {
      // Prefer the server's scored suggestion; fall back to keyword matching
      if (suggestedAmountCol && cols.includes(suggestedAmountCol)) {
        sel.value = suggestedAmountCol;
      } else {
        autoSelectColumn(sel, cols, ['amount', 'amt', 'total', 'value', 'price', 'sum']);
      }
    }
    if (id === 'vendor-col') autoSelectColumn(sel, cols, ['vendor', 'supplier', 'company', 'name', 'payee']);
    if (id === 'invoice-col') autoSelectColumn(sel, cols, ['invoice', 'inv', 'ref', 'reference', 'id', 'number']);
  });
}

function autoSelectColumn(sel, cols, keywords) {
  for (const kw of keywords) {
    const match = cols.find(c => c.toLowerCase().includes(kw));
    if (match) { sel.value = match; return; }
  }
}

function renderUploadPreviewTable(container, rows, cols) {
  if (!rows || !rows.length) {
    container.innerHTML = '<p class="upm-no-preview">No preview data available</p>';
    return;
  }
  let html = '<table class="data-table upm-table"><thead><tr>' +
    cols.map(c => `<th>${escHtml(String(c))}</th>`).join('') +
    '</tr></thead><tbody>';
  rows.forEach(row => {
    html += '<tr>' + cols.map(c => `<td>${escHtml(row[c] === null || row[c] === undefined ? '' : String(row[c]))}</td>`).join('') + '</tr>';
  });
  container.innerHTML = html + '</tbody></table>';
}

function resetUpload() {
  resetForNewFile();
  document.getElementById('upload-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ─── Analysis ─────────────────────────────────────────────── */
async function runAnalysis() {
  const investigatorId = document.getElementById('investigator-id').value.trim();
  if (!investigatorId) { showToast('Investigator ID is required before initiating analysis.', 'error'); return; }
  if (!state.sessionId) { showToast('No data file on record. Upload a file before running analysis.', 'error'); return; }

  const amountCol = document.getElementById('amount-col').value;
  const vendorCol = document.getElementById('vendor-col').value;
  const invoiceCol = document.getElementById('invoice-col').value;

  const btn = document.getElementById('analyze-btn');
  btn.disabled = true; btn.textContent = 'Analyzing…';

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.sessionId,
        amount_col: amountCol,
        vendor_col: vendorCol,
        invoice_col: invoiceCol,
        investigator_id: investigatorId,
        filename: state.filename,
        file_hash: state.fileHash,
      })
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || 'Analysis could not be completed. Verify your inputs and try again.', 'error'); return; }
    state.analysisResult = data;
    state.allRows = data.rows;
    renderResults(data);
    show('results-container');
    document.getElementById('results-container').scrollIntoView({ behavior: 'smooth' });
  } catch (e) {
    showToast('Connection error — ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Run Analysis';
  }
}

/* ─── Render Results ───────────────────────────────────────── */
function renderResults(d) {
  renderSampleNotice();
  renderCasePanel(d);
  renderBenfordPanel(d.benford);
  renderFindings(d);
  resetInterpretation();
  renderTransactionsTable(d.rows, d.amount_col, d.vendor_col, d.invoice_col);
}

function renderSampleNotice() {
  let el = document.getElementById('sample-notice');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sample-notice';
    const container = document.getElementById('results-container');
    container.insertBefore(el, container.firstChild);
  }
  if (state.sampled) {
    el.className = 'sample-notice-banner';
    el.innerHTML = '&#9888; <strong>Large file — sample analyzed:</strong> Only the first 50,000 rows were loaded for analysis. Results reflect a sample, not the complete dataset.';
  } else {
    el.className = 'hidden';
  }
}

function renderCasePanel(d) {
  document.getElementById('case-grid').innerHTML = [
    { label: 'Case Number', value: d.case_number },
    { label: 'Investigator', value: d.investigator_id },
    { label: 'Timestamp (UTC)', value: d.timestamp.replace('T', ' ') },
    { label: 'File', value: d.filename },
    { label: 'SHA-256', value: d.file_hash.substring(0, 20) + '…' },
    { label: 'Records Analyzed', value: d.benford ? d.benford.n : 0 },
  ].map(c => `<div class="case-card"><div class="label">${c.label}</div><div class="value mono">${escHtml(String(c.value))}</div></div>`).join('');
}

function renderBenfordPanel(b) {
  if (!b) return;
  const banner = document.getElementById('verdict-banner');
  banner.className = 'verdict-banner ' + b.verdict_color;
  const icons = { green: '✓', yellow: '⚠', orange: '⚠', red: '⚠' };
  banner.innerHTML = `<span>${icons[b.verdict_color] || '⚠'}</span> <span><strong>MAD Result:</strong> ${escHtml(b.verdict_label)}</span>`;

  const rangeNote = document.getElementById('range-limited-note');
  if (b.range_limited) {
    const spanStr = (b.magnitude_span || 0).toFixed(1);
    rangeNote.innerHTML = `<strong>⚠ Range-Limited Data:</strong> This dataset spans only <strong>${spanStr}</strong> order${parseFloat(spanStr) === 1.0 ? '' : 's'} of magnitude — less than the ~2 orders Benford's Law assumes for reliable results. Observed deviations may be artifacts of the narrow numeric range rather than indicators of manipulation. Interpret the MAD result above with caution.`;
    rangeNote.classList.remove('hidden');
  } else {
    rangeNote.innerHTML = '';
    rangeNote.classList.add('hidden');
  }

  document.getElementById('stats-row').innerHTML = [
    { label: 'Records', value: b.n },
    { label: 'MAD', value: b.mad.toFixed(6) },
    { label: 'Chi-Square', value: b.chi2 },
    { label: 'Chi-Square p', value: b.chi2_p.toFixed(6) },
    { label: 'MAD Threshold', value: madBand(b.mad) },
  ].map(s => `<div class="stat-chip"><span class="stat-label">${s.label}</span><span class="stat-value">${s.value}</span></div>`).join('');

  renderBenfordChart(b);
  renderZScores(b);
  renderSecondChart(b);
}

function madBand(mad) {
  if (mad < 0.006) return '< 0.006 (Close)';
  if (mad < 0.012) return '< 0.012 (Acceptable)';
  if (mad < 0.015) return '< 0.015 (Marginal)';
  return '> 0.015 (Nonconformity)';
}

function renderBenfordChart(b) {
  if (state.benfordChart) state.benfordChart.destroy();
  const ctx = document.getElementById('benford-chart').getContext('2d');
  const labels = ['1','2','3','4','5','6','7','8','9'];
  const actual = labels.map(d => b.actual_pct[d]);
  const expected = labels.map(d => b.expected_pct[d]);
  state.benfordChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Actual %',
          data: actual,
          backgroundColor: labels.map((d, i) => Math.abs(parseFloat(b.z_scores[d])) > 1.96 ? 'rgba(220,38,38,0.7)' : 'rgba(78,127,255,0.7)'),
          borderColor: labels.map((d, i) => Math.abs(parseFloat(b.z_scores[d])) > 1.96 ? '#dc2626' : '#4e7fff'),
          borderWidth: 1,
        },
        {
          label: 'Expected (Benford) %',
          data: expected,
          type: 'line',
          borderColor: '#22c55e',
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointBackgroundColor: '#22c55e',
          pointRadius: 4,
          tension: 0.3,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#e8eaf0' } } },
      scales: {
        x: { ticks: { color: '#7c84a0' }, grid: { color: '#2a3048' } },
        y: { ticks: { color: '#7c84a0' }, grid: { color: '#2a3048' }, title: { display: true, text: 'Percentage (%)', color: '#7c84a0' } }
      }
    }
  });
}

function renderZScores(b) {
  document.getElementById('zscore-grid').innerHTML = ['1','2','3','4','5','6','7','8','9'].map(d => {
    const z = parseFloat(b.z_scores[d]);
    const cls = Math.abs(z) > 1.96 ? 'zscore-cell alert' : Math.abs(z) > 1.28 ? 'zscore-cell warn' : 'zscore-cell';
    return `<div class="${cls}"><div class="d">Digit ${d}</div><div class="z">${z.toFixed(2)}</div></div>`;
  }).join('');
}

function renderSecondChart(b) {
  if (state.secondChart) state.secondChart.destroy();
  const ctx = document.getElementById('second-chart').getContext('2d');
  const labels = ['0','1','2','3','4','5','6','7','8','9'];
  state.secondChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Actual 2nd Digit %',
          data: labels.map(d => b.second_actual[d]),
          backgroundColor: 'rgba(78,127,255,0.6)',
          borderColor: '#4e7fff', borderWidth: 1,
        },
        {
          label: 'Expected %',
          data: labels.map(d => b.second_expected[d]),
          type: 'line',
          borderColor: '#22c55e', backgroundColor: 'transparent',
          borderWidth: 2, pointRadius: 3, tension: 0.3,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#e8eaf0' } } },
      scales: {
        x: { ticks: { color: '#7c84a0' }, grid: { color: '#2a3048' } },
        y: { ticks: { color: '#7c84a0' }, grid: { color: '#2a3048' } }
      }
    }
  });
}

function renderFindings(d) {
  const dupAmtCount = d.dup_amounts.reduce((s, x) => s + x.count, 0);
  const dupInvCount = d.dup_invoices.reduce((s, x) => s + x.count, 0);
  const fuzzyCount = d.fuzzy_vendors.length;

  let dupAmtList = d.dup_amounts.slice(0, 8).map(x =>
    `<li>Amount <strong>${escHtml(String(x.amount))}</strong> × ${x.count}</li>`).join('');
  let dupInvList = d.dup_invoices.slice(0, 8).map(x =>
    `<li>Invoice <strong>${escHtml(String(x.invoice))}</strong> × ${x.count}</li>`).join('');
  let fuzzyList = d.fuzzy_vendors.slice(0, 8).map(x =>
    `<li>${escHtml(x.vendor_a)} <br>vs <em>${escHtml(x.vendor_b)}</em><span class="sim">${(x.similarity * 100).toFixed(1)}%</span></li>`).join('');

  document.getElementById('findings-grid').innerHTML = `
    <div class="finding-card">
      <h3>Duplicate Amounts</h3>
      <div class="count ${dupAmtCount > 0 ? 'alert' : 'ok'}">${dupAmtCount}</div>
      <ul class="finding-list">${dupAmtList || '<li class="muted">None detected</li>'}</ul>
    </div>
    <div class="finding-card">
      <h3>Duplicate Invoices</h3>
      <div class="count ${dupInvCount > 0 ? 'alert' : 'ok'}">${dupInvCount}</div>
      <ul class="finding-list">${dupInvList || '<li class="muted">None detected</li>'}</ul>
    </div>
    <div class="finding-card">
      <h3>Similar Vendor Names</h3>
      <div class="count ${fuzzyCount > 0 ? 'alert' : 'ok'}">${fuzzyCount} pair${fuzzyCount !== 1 ? 's' : ''}</div>
      <ul class="finding-list">${fuzzyList || '<li class="muted">None detected</li>'}</ul>
    </div>`;
}

/* ─── Transactions Table ───────────────────────────────────── */
function renderTransactionsTable(rows, amountCol, vendorCol, invoiceCol) {
  state.allRows = rows;
  state.sortCol = null;
  displayTransactionRows(rows, amountCol, vendorCol, invoiceCol);
}

function displayTransactionRows(rows, amountCol, vendorCol, invoiceCol) {
  if (!rows || !rows.length) return;
  const cols = Object.keys(rows[0]).filter(k => !k.startsWith('_'));
  const tbl = document.getElementById('transactions-table');

  let thead = '<thead><tr>';
  cols.forEach(c => {
    thead += `<th onclick="sortTable('${escHtml(c)}')" data-col="${escHtml(c)}">${escHtml(c)}<span class="sort-arrow"></span></th>`;
  });
  thead += '<th>Flags</th></tr></thead>';

  const showFlagged = document.getElementById('show-flagged-only').checked;

  let tbody = '<tbody>';
  rows.forEach(row => {
    const flags = row._row_flags || [];
    if (showFlagged && flags.length === 0) return;
    const cls = flags.length ? ' class="flagged"' : '';
    tbody += `<tr${cls}>`;
    cols.forEach(c => { tbody += `<td>${escHtml(row[c] === null || row[c] === undefined ? '' : String(row[c]))}</td>`; });
    const flagLabels = flags.map(f => {
      const labels = { duplicate_amount: 'Dup.Amt', duplicate_invoice: 'Dup.Inv', fuzzy_vendor: 'Similar Vendor' };
      return `<span class="badge-flag">${labels[f] || f}</span>`;
    }).join(' ');
    tbody += `<td class="flag-cell">${flagLabels}</td></tr>`;
  });
  tbody += '</tbody>';
  tbl.innerHTML = thead + tbody;
}

function filterTable() {
  if (!state.analysisResult) return;
  const d = state.analysisResult;
  displayTransactionRows(state.allRows, d.amount_col, d.vendor_col, d.invoice_col);
}

function sortTable(col) {
  if (state.sortCol === col) state.sortDir *= -1;
  else { state.sortCol = col; state.sortDir = 1; }

  state.allRows = [...state.allRows].sort((a, b) => {
    let va = a[col], vb = b[col];
    const na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) return (na - nb) * state.sortDir;
    return String(va || '').localeCompare(String(vb || '')) * state.sortDir;
  });

  document.querySelectorAll('#transactions-table th').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === col) th.classList.add(state.sortDir === 1 ? 'sort-asc' : 'sort-desc');
  });
  filterTable();
}

/* ─── Forensic Interpretation ──────────────────────────────── */
function resetInterpretation() {
  document.getElementById('interpret-cta').classList.remove('hidden');
  document.getElementById('interpret-loading').classList.add('hidden');
  document.getElementById('interpret-result').classList.add('hidden');
  document.getElementById('interpret-result').innerHTML = '';
}

function generateInterpretation() {
  if (!state.analysisResult) return;
  document.getElementById('interpret-cta').classList.add('hidden');
  document.getElementById('interpret-loading').classList.remove('hidden');
  setTimeout(() => {
    document.getElementById('interpret-loading').classList.add('hidden');
    const el = document.getElementById('interpret-result');
    el.innerHTML = buildInterpretation(state.analysisResult);
    el.classList.remove('hidden');
  }, 1000);
}

function buildInterpretation(d) {
  const b = d.benford;
  const dupAmtCount = d.dup_amounts.reduce((s, x) => s + x.count, 0);
  const dupInvCount = d.dup_invoices.reduce((s, x) => s + x.count, 0);
  const fuzzyCount = d.fuzzy_vendors.length;
  const madVal = b.mad.toFixed(6);
  const rangeLimited = !!b.range_limited;
  const spanStr = rangeLimited ? (b.magnitude_span || 0).toFixed(1) : null;

  // --- Benford verdict paragraph ---
  let benfordPara;
  if (b.verdict === 'close_conformity') {
    benfordPara = `The dataset's leading-digit distribution closely conforms to Benford's Law. The Mean Absolute Deviation (MAD) of <strong>${madVal}</strong> falls within the close conformity threshold (MAD &lt; 0.006), indicating digit patterns consistent with naturally occurring financial data. The chi-square statistic of ${b.chi2} (p&nbsp;=&nbsp;${b.chi2_p.toFixed(4)}) corroborates this finding.`;
  } else if (b.verdict === 'acceptable') {
    benfordPara = `The dataset's leading-digit distribution falls within acceptable conformity of Benford's Law. The MAD of <strong>${madVal}</strong> falls in the acceptable range (0.006–0.012), suggesting the data is broadly consistent with naturally occurring financial patterns, though minor digit-level variation is present. Chi-square: ${b.chi2} (p&nbsp;=&nbsp;${b.chi2_p.toFixed(4)}).`;
  } else if (b.verdict === 'marginal') {
    benfordPara = `The dataset's leading-digit distribution exhibits <strong>marginal conformity</strong> with Benford's Law. The MAD of <strong>${madVal}</strong> falls in the marginal range (0.012–0.015), approaching the threshold that warrants investigative attention. The data's digit patterns diverge more than is typically observed in unmanipulated financial records. Chi-square: ${b.chi2} (p&nbsp;=&nbsp;${b.chi2_p.toFixed(4)}).`;
  } else {
    benfordPara = `The dataset's leading-digit distribution shows <strong>statistically significant nonconformity</strong> with Benford's Law. The MAD of <strong>${madVal}</strong> exceeds the 0.015 nonconformity threshold, indicating the digit patterns deviate meaningfully from what is expected in naturally occurring financial records. This constitutes a primary analytical flag warranting further examination. Chi-square: ${b.chi2} (p&nbsp;=&nbsp;${b.chi2_p.toFixed(4)}).`;
  }

  // Range-limited caveat appended to verdict paragraph
  if (rangeLimited) {
    benfordPara += ` <strong>Important caveat:</strong> This dataset spans only <strong>${spanStr}</strong> order${parseFloat(spanStr) === 1.0 ? '' : 's'} of magnitude — less than the ~2 orders Benford's Law assumes for reliable results. When data is concentrated within a narrow numeric range (e.g. all values between $2,000 and $85,000), the leading-digit distribution naturally deviates from Benford's expectation without any manipulation. Observed deviations in this dataset may be artifacts of that limited range rather than indicators of fraud or error, and should not be treated as conclusive without corroborating evidence.`;
  }

  // --- Digit-level deviation (marginal / nonconformity only) ---
  let digitSection = '';
  if (b.verdict === 'marginal' || b.verdict === 'nonconformity') {
    const zEntries = Object.entries(b.z_scores)
      .map(([digit, z]) => ({ digit, z: parseFloat(z) }))
      .filter(x => Math.abs(x.z) > 1.96)
      .sort((a, c) => Math.abs(c.z) - Math.abs(a.z));

    let digitPara;
    if (zEntries.length > 0) {
      const parts = zEntries.map(x => {
        const dir = x.z > 0 ? 'over-represented' : 'under-represented';
        return `digit&nbsp;<strong>${x.digit}</strong> (observed:&nbsp;${b.actual_pct[x.digit]}%, expected:&nbsp;${b.expected_pct[x.digit]}%, Z&nbsp;=&nbsp;${x.z.toFixed(2)}, ${dir})`;
      });
      const top = zEntries[0];
      const topDir = top.z > 0 ? 'appears more frequently than expected' : 'appears less frequently than expected';
      digitPara = `Digit-level analysis identifies the following leading digits with statistically significant deviations (|Z|&nbsp;&gt;&nbsp;1.96): ${parts.join('; ')}. The highest deviation is observed at digit <strong>${top.digit}</strong>, which ${topDir} — a pattern that merits targeted review of transactions within this leading-digit range.`;
    } else {
      digitPara = `While the overall MAD indicates ${b.verdict === 'marginal' ? 'marginal conformity' : 'nonconformity'}, no individual digit reaches the conventional threshold for statistical significance (|Z|&nbsp;&gt;&nbsp;1.96) at the 95% confidence level. The deviation appears distributed across multiple digits rather than concentrated at a single leading digit.`;
    }
    digitSection = `<div class="interp-section"><div class="interp-section-label">Digit-Level Deviation</div><p>${digitPara}</p></div>`;
  }

  // --- Supplementary findings ---
  const suppParts = [];
  if (dupAmtCount > 0)
    suppParts.push(`<strong>${dupAmtCount}</strong> duplicate transaction amount${dupAmtCount !== 1 ? 's' : ''} across <strong>${d.dup_amounts.length}</strong> unique value${d.dup_amounts.length !== 1 ? 's' : ''}`);
  if (dupInvCount > 0)
    suppParts.push(`<strong>${dupInvCount}</strong> duplicate invoice number${dupInvCount !== 1 ? 's' : ''} across <strong>${d.dup_invoices.length}</strong> unique reference${d.dup_invoices.length !== 1 ? 's' : ''}`);
  if (fuzzyCount > 0)
    suppParts.push(`<strong>${fuzzyCount}</strong> similar vendor name pair${fuzzyCount !== 1 ? 's' : ''} flagged by fuzzy matching (potential vendor duplication or name-variation scheme)`);

  const findingsPara = suppParts.length > 0
    ? `Supplementary analysis identified the following anomalies: ${suppParts.join('; ')}.`
    : `Supplementary analysis detected no duplicate transaction amounts, no duplicate invoice numbers, and no similar vendor name pairs within the examined dataset.`;

  // --- Risk characterization ---
  let benfordScore = 0;
  if (b.verdict === 'marginal') benfordScore = 1;
  else if (b.verdict === 'nonconformity') benfordScore = 2;

  const anomalyCount = (dupAmtCount > 0 ? 1 : 0) + (dupInvCount > 0 ? 1 : 0) + (fuzzyCount > 0 ? 1 : 0);

  // When range-limited, Benford deviation alone cannot drive HIGH priority.
  // Cap its score contribution to 1 so it takes independent anomalies to reach HIGH.
  const effectiveBenfordScore = rangeLimited ? Math.min(benfordScore, 1) : benfordScore;
  const totalScore = effectiveBenfordScore + anomalyCount;
  const benfordTempered = rangeLimited && benfordScore > effectiveBenfordScore;

  let riskLevel, riskClass, riskDesc;
  if (totalScore === 0) {
    riskLevel = 'LOW';
    riskClass = 'risk-low';
    riskDesc = 'The dataset presents no statistically significant anomalies. Benford\'s Law conformity is within expected parameters and no supplementary indicators of concern were identified.';
  } else if (totalScore <= 2) {
    riskLevel = benfordTempered ? 'MODERATE — interpret with caution due to limited numeric range' : 'MODERATE';
    riskClass = 'risk-moderate';
    if (benfordTempered) {
      riskDesc = `One or more analytical indicators warrant further review. Note: the Benford nonconformity alone was not used to elevate this to HIGH priority because the dataset spans a narrow numeric range (${spanStr} orders of magnitude), making Benford deviations less reliable. This priority was tempered accordingly. Independent anomalies, if present, may still warrant targeted follow-up.`;
    } else {
      riskDesc = 'One or more analytical indicators warrant further review. While not conclusive, the combination of findings elevates this dataset above baseline risk and supports targeted follow-up examination.';
    }
  } else {
    riskLevel = 'HIGH';
    riskClass = 'risk-high';
    if (rangeLimited) {
      riskDesc = `Multiple analytical indicators — including strong independent anomalies beyond Benford's Law alone — converge to suggest elevated investigative priority. Although the dataset spans a narrow numeric range (${spanStr} orders of magnitude), the corroborating supplementary findings (${suppParts.length} anomaly categor${suppParts.length === 1 ? 'y' : 'ies'}) independently support comprehensive forensic review.`;
    } else {
      riskDesc = 'Multiple analytical indicators converge to suggest elevated investigative priority. The combination of Benford\'s Law deviation and supplementary anomalies warrants comprehensive forensic review of the underlying transactions.';
    }
  }

  const ocrInterpWarning = state.ocrUsed ? `
    <div class="ocr-warning-interp">
      <strong>&#9888; OCR-Extracted Data &mdash; Verify Before Relying on This Assessment</strong><br>
      This dataset was extracted from a scanned or image-based document using Optical Character Recognition (OCR).
      OCR can silently misread digits (e.g. <code>0&rarr;O</code>, <code>1&rarr;I</code>, <code>5&rarr;S</code>),
      corrupting numeric values and distorting statistical results.
      <strong>All figures must be verified against the original source document before drawing any conclusions.</strong>
    </div>` : '';

  return `${ocrInterpWarning}
    <div class="interp-header">
      <span class="interp-title">Forensic Assessment</span>
      <span class="interp-case mono">${escHtml(d.case_number)}</span>
    </div>
    <div class="interp-section">
      <div class="interp-section-label">Benford's Law Assessment</div>
      <p>${benfordPara}</p>
    </div>
    ${digitSection}
    <div class="interp-section">
      <div class="interp-section-label">Supplementary Findings</div>
      <p>${findingsPara}</p>
    </div>
    <div class="interp-section">
      <div class="interp-section-label">Overall Risk Characterization</div>
      <div class="risk-badge-row">
        <span class="risk-badge ${riskClass}">${riskLevel} INVESTIGATIVE PRIORITY</span>
      </div>
      <p>${riskDesc}</p>
    </div>
    <div class="interp-disclaimer">
      <strong>&#9432; Disclaimer:</strong> This is an automated interpretation of statistical indicators computed from the submitted dataset. Anomalies identified herein are indicators for further human investigation and do not, by themselves, constitute proof of fraud, error, or misconduct. All findings should be evaluated by a qualified investigator in the context of the specific data source, industry norms, and applicable professional standards.
    </div>`;
}

/* ─── Export CSV ───────────────────────────────────────────── */
async function exportCSV() {
  if (!state.analysisResult) { showToast('No analysis results available for export.', 'error'); return; }
  const res = await fetch('/api/export/csv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.analysisResult)
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `forensic_report_${state.analysisResult.case_number}.csv`;
  a.click(); URL.revokeObjectURL(url);
}

/* ─── Audit Trail ──────────────────────────────────────────── */
async function loadAudit() {
  const wrap = document.getElementById('audit-table-wrap');
  wrap.innerHTML = '<p class="muted">Retrieving audit records…</p>';
  try {
    const res = await fetch('/api/audit');
    const data = await res.json();
    if (!data.length) { wrap.innerHTML = '<p class="muted">No audit records on file.</p>'; return; }
    const reversed = [...data].reverse();
    let html = '<div class="table-scroll"><table class="audit-table"><thead><tr><th>Case #</th><th>Timestamp (UTC)</th><th>Investigator</th><th>File</th><th>Rows</th><th>Verdict</th><th>SHA-256</th></tr></thead><tbody>';
    reversed.forEach(r => {
      html += `<tr>
        <td class="mono">${escHtml(r.case_number)}</td>
        <td>${escHtml(r.timestamp)}</td>
        <td>${escHtml(r.investigator_id)}</td>
        <td>${escHtml(r.filename)}</td>
        <td>${r.rows_analyzed}</td>
        <td>${escHtml(r.verdict)}</td>
        <td class="mono">${r.file_hash.substring(0, 16)}…</td>
      </tr>`;
    });
    html += '</tbody></table></div>';
    wrap.innerHTML = html;
  } catch (e) {
    wrap.innerHTML = '<p class="muted">Unable to retrieve the audit trail.</p>';
  }
}

/* ─── Sample Data ──────────────────────────────────────────── */
async function loadSample() {
  const res = await fetch('/api/sample');
  const blob = await res.blob();
  const file = new File([blob], 'sample_transactions.csv', { type: 'text/csv' });
  handleFileUpload(file);
  if (!document.getElementById('investigator-id').value) {
    document.getElementById('investigator-id').value = 'DEMO-Investigator';
  }
}

/* ─── Helpers ──────────────────────────────────────────────── */
function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

let toastTimer;
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (type ? ' ' + type : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 4000);
}
