// ── state ────────────────────────────────────────────────────────────────────
let allChannels = [];      // raw data from /api/notion-channels
let checkQueue = [];       // queue for "Check All" operation
let checkingAll = false;
let stopRequested = false;

// ── single channel check ─────────────────────────────────────────────────────
async function checkChannel() {
    const input = document.getElementById('giphy-url');
    const btn = document.getElementById('check-btn');
    const btnText = document.getElementById('btn-text');
    const btnLoader = document.getElementById('btn-loader');
    const resultDiv = document.getElementById('single-result');

    const url = input.value.trim();
    if (!url) { showSingleError('Please enter a Giphy URL'); return; }

    btn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline-block';
    resultDiv.style.display = 'none';

    try {
        const res = await fetch('/api/check-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Error checking channel');
        renderSingleResult(data, resultDiv);
    } catch (e) {
        showSingleError(e.message);
    } finally {
        btn.disabled = false;
        btnText.style.display = 'inline';
        btnLoader.style.display = 'none';
    }
}

function renderSingleResult(data, container) {
    const details = data.details || {};
    const username = details.username || data.channel_id || data.channel_identifier_from_url || '—';
    const status = giphyStatusBadge(data);

    container.innerHTML = `
        <div class="single-result-card">
            <div class="single-result-row">
                <span class="single-label">Username:</span>
                <span>${escapeHtml(username)}</span>
            </div>
            ${details.display_name ? `<div class="single-result-row"><span class="single-label">Display Name:</span><span>${escapeHtml(details.display_name)}</span></div>` : ''}
            <div class="single-result-row">
                <span class="single-label">Giphy Status:</span>
                <span>${status}</span>
            </div>
            ${details.total_uploads !== undefined ? `<div class="single-result-row"><span class="single-label">GIFs Uploaded:</span><span>${details.total_uploads.toLocaleString()}</span></div>` : ''}
        </div>
    `;
    container.style.display = 'block';
}

function showSingleError(msg) {
    const resultDiv = document.getElementById('single-result');
    resultDiv.innerHTML = `<div class="error-inline">⚠️ ${escapeHtml(msg)}</div>`;
    resultDiv.style.display = 'block';
}

// ── load channels from Notion ─────────────────────────────────────────────────
async function loadChannels() {
    const btn = document.getElementById('load-btn');
    const btnText = document.getElementById('load-btn-text');
    const btnLoader = document.getElementById('load-btn-loader');

    btn.disabled = true;
    btnText.style.display = 'none';
    btnLoader.style.display = 'inline-block';

    try {
        const res = await fetch('/api/notion-channels');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to load channels');

        allChannels = (data.channels || []).map((ch, i) => ({
            ...ch,
            _idx: i,
            _giphy_status: null,   // null = not checked
            _checking: false,
            _error: null
        }));

        document.getElementById('channel-count').textContent = allChannels.length;
        document.getElementById('check-all-btn').disabled = false;
        document.getElementById('summary-bar').style.display = 'flex';
        renderTable();
        updateSummary();
    } catch (e) {
        alert('Error loading channels: ' + e.message);
    } finally {
        btn.disabled = false;
        btnText.style.display = 'inline';
        btnLoader.style.display = 'none';
    }
}

// ── render table ──────────────────────────────────────────────────────────────
function renderTable() {
    const tbody = document.getElementById('channels-tbody');
    const table = document.getElementById('channels-table');
    const empty = document.getElementById('channels-empty');
    const notionFilter = document.getElementById('status-filter').value;
    const giphyFilter = document.getElementById('giphy-filter').value;

    const filtered = allChannels.filter(ch => {
        if (notionFilter !== 'all' && ch.notion_status !== notionFilter) return false;
        if (giphyFilter !== 'all') {
            if (giphyFilter === 'unchecked' && ch._giphy_status !== null) return false;
            if (giphyFilter !== 'unchecked' && ch._giphy_status !== giphyFilter) return false;
        }
        return true;
    });

    if (allChannels.length === 0) {
        table.style.display = 'none';
        empty.style.display = 'block';
        return;
    }

    table.style.display = 'table';
    empty.style.display = 'none';

    tbody.innerHTML = filtered.map((ch, displayIdx) => {
        const rowId = `row-${ch._idx}`;
        const statusCell = renderGiphyStatusCell(ch);
        const notionBadge = notionStatusBadge(ch.notion_status);
        const displayUrl = ch.giphy_url.length > 35 ? ch.giphy_url.slice(0, 35) + '…' : ch.giphy_url;
        const actionBtn = ch._checking
            ? `<button class="btn-sm" disabled><span class="loader-sm"></span></button>`
            : `<button class="btn-sm" onclick="checkSingleRow(${ch._idx})">Check</button>`;

        return `
            <tr id="${rowId}" class="channel-row ${ch._giphy_status ? 'row-' + ch._giphy_status : ''}">
                <td class="col-num">${displayIdx + 1}</td>
                <td class="col-name">${escapeHtml(ch.name || '—')}</td>
                <td class="col-url"><a href="${escapeHtml(ch.giphy_url)}" target="_blank" title="${escapeHtml(ch.giphy_url)}">${escapeHtml(displayUrl)}</a></td>
                <td class="col-notion">${notionBadge}</td>
                <td class="col-giphy" id="giphy-cell-${ch._idx}">${statusCell}</td>
                <td class="col-action" id="action-cell-${ch._idx}">${actionBtn}</td>
            </tr>
        `;
    }).join('');
}

function renderGiphyStatusCell(ch) {
    if (ch._checking) return '<span class="loader-sm"></span>';
    if (ch._error) return `<span class="status-pill error" title="${escapeHtml(ch._error)}">ERROR</span>`;
    if (ch._giphy_status === null) return '<span class="status-pill unchecked">—</span>';
    if (ch._giphy_status === 'working') return '<span class="status-pill working">✅ Working</span>';
    if (ch._giphy_status === 'shadow_banned') return '<span class="status-pill shadow-banned">👻 Shadow Banned</span>';
    if (ch._giphy_status === 'banned') return '<span class="status-pill banned">🚫 Banned</span>';
    return `<span class="status-pill unknown">${escapeHtml(ch._giphy_status)}</span>`;
}

function notionStatusBadge(status) {
    if (status === 'Verified') return '<span class="status-pill notion-verified">✓ Verified</span>';
    if (status === 'Declined') return '<span class="status-pill notion-declined">✗ Declined</span>';
    return `<span class="status-pill unknown">${escapeHtml(status || '—')}</span>`;
}

function giphyStatusBadge(data) {
    if (data.banned) return '<span class="status-pill banned">🚫 Banned</span>';
    if (data.shadow_banned) return '<span class="status-pill shadow-banned">👻 Shadow Banned</span>';
    if (data.working) return '<span class="status-pill working">✅ Working</span>';
    return '<span class="status-pill unknown">Unknown</span>';
}

function applyFilter() {
    renderTable();
}

// ── check single row ──────────────────────────────────────────────────────────
async function checkSingleRow(idx) {
    const ch = allChannels[idx];
    if (!ch) return;

    ch._checking = true;
    ch._error = null;
    updateRow(idx);

    try {
        const res = await fetch('/api/check-notion-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: ch.giphy_url })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Check failed');

        ch._giphy_status = data.status || (data.banned ? 'banned' : data.shadow_banned ? 'shadow_banned' : data.working ? 'working' : 'unknown');
    } catch (e) {
        ch._error = e.message;
    } finally {
        ch._checking = false;
        updateRow(idx);
        updateSummary();
    }
}

function updateRow(idx) {
    const ch = allChannels[idx];
    const giphyCell = document.getElementById(`giphy-cell-${idx}`);
    const actionCell = document.getElementById(`action-cell-${idx}`);
    const row = document.getElementById(`row-${idx}`);

    if (!giphyCell || !actionCell) return;

    giphyCell.innerHTML = renderGiphyStatusCell(ch);
    actionCell.innerHTML = ch._checking
        ? `<button class="btn-sm" disabled><span class="loader-sm"></span></button>`
        : `<button class="btn-sm" onclick="checkSingleRow(${idx})">Check</button>`;

    if (row) {
        const statusClass = ch._giphy_status ? 'row-' + ch._giphy_status : '';
        const activeClass = ch._checking ? 'row-active' : '';
        row.className = `channel-row ${statusClass} ${activeClass}`.trim();

        if (ch._checking) {
            row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }
}

// ── check all channels ────────────────────────────────────────────────────────
async function checkAllChannels() {
    if (checkingAll) return;
    if (allChannels.length === 0) { alert('Load channels first'); return; }

    checkingAll = true;
    stopRequested = false;

    const checkAllBtn = document.getElementById('check-all-btn');
    const checkAllText = document.getElementById('check-all-text');
    const checkAllLoader = document.getElementById('check-all-loader');
    const stopBtn = document.getElementById('stop-btn');
    const progressWrap = document.getElementById('progress-bar-wrap');

    checkAllBtn.disabled = true;
    checkAllText.style.display = 'none';
    checkAllLoader.style.display = 'inline-block';
    stopBtn.style.display = 'inline-block';
    progressWrap.style.display = 'flex';

    // Filter to not-yet-checked channels
    const toCheck = allChannels.filter(ch => ch._giphy_status === null && !ch._checking);
    let done = 0;
    const total = toCheck.length;

    updateProgress(done, total);

    // Check 3 concurrently
    const CONCURRENCY = 1;
    const queue = [...toCheck];

    async function worker() {
        while (queue.length > 0 && !stopRequested) {
            const ch = queue.shift();
            if (!ch) break;
            updateProgress(done, total, ch.name || ch.giphy_url);
            await checkSingleRow(ch._idx);
            done++;
            updateProgress(done, total);
        }
    }

    const workers = Array.from({ length: CONCURRENCY }, worker);
    await Promise.all(workers);

    checkingAll = false;
    checkAllBtn.disabled = false;
    checkAllText.style.display = 'inline';
    checkAllLoader.style.display = 'none';
    stopBtn.style.display = 'none';
    progressWrap.style.display = done === total ? 'none' : 'flex';
}

function stopChecking() {
    stopRequested = true;
}

function updateProgress(done, total, currentName) {
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    document.getElementById('progress-bar').style.width = pct + '%';
    const label = currentName
        ? `${done} / ${total} — checking: ${currentName}`
        : `${done} / ${total}`;
    document.getElementById('progress-text').textContent = label;
}

// ── summary bar ───────────────────────────────────────────────────────────────
function updateSummary() {
    let working = 0, shadow = 0, banned = 0, error = 0, pending = 0;
    allChannels.forEach(ch => {
        if (ch._checking) return;
        if (ch._error) { error++; return; }
        if (ch._giphy_status === null) { pending++; return; }
        if (ch._giphy_status === 'working') working++;
        else if (ch._giphy_status === 'shadow_banned') shadow++;
        else if (ch._giphy_status === 'banned') banned++;
        else pending++;
    });
    document.getElementById('s-working').textContent = working;
    document.getElementById('s-shadow').textContent = shadow;
    document.getElementById('s-banned').textContent = banned;
    document.getElementById('s-error').textContent = error;
    document.getElementById('s-pending').textContent = pending;
}

// ── helpers ───────────────────────────────────────────────────────────────────
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
