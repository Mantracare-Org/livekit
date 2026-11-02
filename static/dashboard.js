// ── Auth ─────────────────────────────────────────────────────────────────
const TOKEN = localStorage.getItem('token');
if (!TOKEN) {
    window.location.href = '/';
}

function apiHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${TOKEN}`,
    };
}

async function apiFetch(url, opts = {}) {
    const res = await fetch(url, { ...opts, headers: { ...apiHeaders(), ...opts.headers } });
    if (res.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/';
        throw new Error('Unauthorized');
    }
    return res.json();
}

// ── Logout ───────────────────────────────────────────────────────────────
document.getElementById('logout-btn').addEventListener('click', () => {
    localStorage.removeItem('token');
    window.location.href = '/';
});

// ── Metrics Bar ──────────────────────────────────────────────────────────
let isFetchingMetrics = false;

async function loadMetrics() {
    if (isFetchingMetrics) return;
    isFetchingMetrics = true;
    try {
        const data = await apiFetch('/v1/dashboard/metrics');
        if (data.error) return;

        document.getElementById('metric-total').textContent = data.total_calls;
        document.getElementById('metric-answer-rate').textContent = `${data.answer_rate}%`;
        document.getElementById('metric-avg-duration').textContent =
            data.avg_duration_seconds ? `${Math.floor(data.avg_duration_seconds / 60)}m ${data.avg_duration_seconds % 60}s` : '—';
    } finally {
        isFetchingMetrics = false;
    }
}

async function loadSystemStatus() {
    const indicator = document.getElementById('system-status');
    if (!indicator) return;

    try {
        const data = await apiFetch('/health');
        const healthy = data.healthy === true;
        indicator.className = `system-status ${healthy ? 'operational' : 'degraded'}`;
        indicator.textContent = healthy ? 'All systems operational' : 'Systems degraded';
        indicator.title = healthy ? 'All service health checks passed' : 'One or more service health checks failed';
        indicator.disabled = false;
    } catch (error) {
        indicator.className = 'system-status unavailable';
        indicator.textContent = 'Status unavailable';
        indicator.title = 'Could not reach the service health endpoint';
        indicator.disabled = true;
    }
}

async function toggleSystemStatusDetails() {
    const popover = document.getElementById('system-status-popover');
    const indicator = document.getElementById('system-status');
    if (!popover || !indicator || indicator.disabled) return;

    if (!popover.hidden) {
        popover.hidden = true;
        indicator.setAttribute('aria-expanded', 'false');
        return;
    }

    popover.innerHTML = '<div class="system-status-title">Service status</div><div class="system-status-check">Checking...</div>';
    popover.hidden = false;
    indicator.setAttribute('aria-expanded', 'true');

    try {
        const data = await apiFetch('/health/details');
        const checks = Object.entries(data.checks || {});
        const failed = checks.filter(([, healthy]) => !healthy);
        popover.innerHTML = `
            <div class="system-status-title">${failed.length ? 'Services needing attention' : 'All services are healthy'}</div>
            ${failed.length
                ? failed.map(([service]) => `<div class="system-status-check failed"><span>${escapeHtml(service)}</span><span>DOWN</span></div>`).join('')
                : '<div class="system-status-check"><span>No issues detected</span><span>OK</span></div>'}
        `;
    } catch (error) {
        popover.innerHTML = '<div class="system-status-check failed"><span>Health details</span><span>UNAVAILABLE</span></div>';
    }
}

// ── Active Calls + Queue ─────────────────────────────────────────────────
let activeCallIntervalStarted = false;

function renderActiveCalls(activeDetails) {
    const container = document.getElementById('active-calls-list');
    if (!activeDetails || activeDetails.length === 0) {
        container.innerHTML = `<div class="empty-state">No active calls</div>`;
        return;
    }

    container.innerHTML = activeDetails.map(c => `
        <div class="call-card">
            <div class="call-card-header">
                <span class="call-id">${escapeHtml(c.call_id)}</span>
                <span class="status-badge status-${escapeHtml(c.status)}"><span class="active-pulse"></span>${escapeHtml(c.status)}</span>
            </div>
            <div class="call-card-body">
                <span class="call-room">${escapeHtml(c.room_name)}</span>
                <span class="call-time" data-call-start="${escapeHtml(c.call_id)}">active</span>
            </div>
        </div>
    `).join('');

    if (!activeCallIntervalStarted) {
        activeCallIntervalStarted = true;
        setInterval(() => {
            document.querySelectorAll('[data-call-start]').forEach(el => {
                el.textContent = 'active';
            });
        }, 1000);
    }
}

function renderQueueGauge(pending, active, maxConcurrency) {
    const usage = maxConcurrency > 0 ? Math.round(active / maxConcurrency * 100) : 0;
    const fill = document.getElementById('queue-fill');
    fill.style.width = `${Math.min(usage, 100)}%`;
    fill.classList.remove('low', 'medium', 'high');
    fill.classList.add(usage > 80 ? 'high' : usage > 50 ? 'medium' : 'low');

    document.getElementById('queue-active-stat').textContent = active;
    document.getElementById('queue-pending-stat').textContent = pending;
    document.getElementById('queue-max-stat').textContent = maxConcurrency;
    document.getElementById('metric-active').textContent = active;
}

// ── Activity Feed ────────────────────────────────────────────────────────
const feedContainer = document.getElementById('feed-list');
let feedItems = [];

function addFeedItem(message, type = 'info') {
    const time = new Date().toLocaleTimeString();
    feedItems.unshift({ message, type, time });
    if (feedItems.length > 50) feedItems.pop();
    renderFeed();
}

function renderFeed() {
    feedContainer.innerHTML = feedItems.map(item => `
        <div class="feed-item feed-${item.type}">
            <span class="feed-time">${item.time}</span>
            <span class="feed-msg">${item.message}</span>
        </div>
    `).join('');
}

// ── SSE Connection ───────────────────────────────────────────────────────
let lastActiveIds = new Set();

function connectSSE() {
    const evtSource = new EventSource(`/v1/dashboard/stream?token=${TOKEN}`);

    evtSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.error) return;

            const { pending_calls, active_calls, max_concurrency, active_call_details, timestamp } = data;

            // Update queue gauge
            renderQueueGauge(pending_calls, active_calls, max_concurrency);

            // Update active calls
            renderActiveCalls(active_call_details);

            // Detect changes for activity feed
            const currentIds = new Set((active_call_details || []).map(c => c.call_id));

            // New active calls
            currentIds.forEach(id => {
                if (!lastActiveIds.has(id)) {
                    addFeedItem(`Call ${id} started`, 'success');
                }
            });

            // Calls that ended -> Sync DB history immediately!
            let endedCount = 0;
            lastActiveIds.forEach(id => {
                if (!currentIds.has(id)) {
                    addFeedItem(`Call ${id} ended`, 'info');
                    endedCount++;
                }
            });

            if (endedCount > 0) {
                loadCallHistory();
                loadMetrics();
            }

            // Queue changes
            const prevPending = parseInt(document.getElementById('queue-pending').textContent) || 0;
            if (pending_calls > prevPending) {
                addFeedItem(`${pending_calls - prevPending} call(s) queued`, 'info');
            }

            lastActiveIds = currentIds;
        } catch (e) {
            // ignore parse errors
        }
    };

    evtSource.onerror = () => {
        addFeedItem('Reconnecting to event stream...', 'warning');
        setTimeout(connectSSE, 3000);
    };
}

// ── Call History Table (Synced with DB) ───────────────────────────────────
let currentCallHistoryData = [];
let currentPage = 1;
let pageSize = 50;
let totalCallCount = 0;
let currentModalIndex = -1;

function updatePaginationControls() {
    const totalPages = Math.max(1, Math.ceil(totalCallCount / pageSize));
    if (currentPage > totalPages) currentPage = totalPages;

    const start = totalCallCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;
    const end = Math.min(totalCallCount, (currentPage - 1) * pageSize + currentCallHistoryData.length);

    const startEl = document.getElementById('pagination-start');
    const endEl = document.getElementById('pagination-end');
    const totalEl = document.getElementById('pagination-total');
    const pageInfoEl = document.getElementById('pagination-page-info');
    const prevBtn = document.getElementById('btn-prev-page');
    const nextBtn = document.getElementById('btn-next-page');

    if (startEl) startEl.textContent = start;
    if (endEl) endEl.textContent = end;
    if (totalEl) totalEl.textContent = totalCallCount;
    if (pageInfoEl) pageInfoEl.textContent = `Page ${currentPage} of ${totalPages}`;

    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
}

async function loadCallHistory(page = currentPage) {
    currentPage = Math.max(1, page);
    const searchInput = document.getElementById('call-search-input');
    const statusSelect = document.getElementById('call-status-filter');
    const searchVal = searchInput ? searchInput.value.trim() : '';
    const statusVal = statusSelect ? statusSelect.value : 'all';
    const offset = (currentPage - 1) * pageSize;

    let url = `/v1/dashboard/calls?limit=${pageSize}&offset=${offset}`;
    if (searchVal) url += `&search=${encodeURIComponent(searchVal)}`;
    if (statusVal && statusVal !== 'all') url += `&status=${encodeURIComponent(statusVal)}`;

    const data = await apiFetch(url);
    if (data.error) {
        document.getElementById('calls-table-body').innerHTML =
            `<tr><td colspan="7" class="empty-state">Could not load call history</td></tr>`;
        showToast('Could not load call history', 'error');
        return;
    }

    let calls = data.calls || [];

    // Client-side status filter guard
    if (statusVal && statusVal.toLowerCase() !== 'all') {
        const cleanTarget = statusVal.toLowerCase().replace(/[\s_]/g, '');
        calls = calls.filter(c => {
            const st = (c.status || '').toLowerCase().replace(/[\s_]/g, '');
            return st.includes(cleanTarget);
        });
    }

    currentCallHistoryData = calls;
    totalCallCount = typeof data.total === 'number' ? data.total : (data.total_count || calls.length);

    document.getElementById('calls-total').textContent = totalCallCount;
    updatePaginationControls();

    const tbody = document.getElementById('calls-table-body');
    if (currentCallHistoryData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No call logs found matching current filters</td></tr>`;
        return;
    }

    tbody.innerHTML = currentCallHistoryData.map((c, idx) => {
        const rawStatus = c.status || 'Unknown';
        const statusDisplay = (rawStatus.toLowerCase() === 'none' || rawStatus.toLowerCase() === 'null') ? 'Completed' : rawStatus;
        const statusClass = `status-${statusDisplay.toLowerCase().replace(/\s+/g, '-')}`;
        const duration = c.duration
            ? `${Math.floor(c.duration / 60)}m ${c.duration % 60}s`
            : '—';
        const time = c.created_at
            ? new Date(c.created_at).toLocaleString()
            : '—';
        const phone = c.caller_number || c.client_phone || c.client_name || '—';
        const trunk = c.trunk_id || '—';
        const attemptsCount = c.attempts_count || (Array.isArray(c.attempts) ? c.attempts.length : 1);
        let attemptBadge = '';
        if (attemptsCount > 1) {
            const retries = attemptsCount - 1;
            const retryLabel = retries === 1 ? '1 retry' : `${retries} retries`;
            attemptBadge = `<span class="badge-attempt" title="${attemptsCount} total attempts recorded (${retryLabel})">Attempt #${attemptsCount} (${retryLabel})</span>`;
        }

        return `
            <tr>
                <td class="col-status">
                    <div class="status-cell-wrap">
                        <span class="status-dot-sm ${statusClass}"></span>
                        <span class="status-text">${escapeHtml(statusDisplay)}</span>
                        ${attemptBadge}
                    </div>
                </td>
                <td class="cell-mono" style="font-weight:600;">${escapeHtml(c.call_id || '—')}</td>
                <td>${escapeHtml(phone)}</td>
                <td class="cell-mono">${escapeHtml(trunk)}</td>
                <td>${duration}</td>
                <td class="cell-time">${time}</td>
                <td>
                    <button class="btn-logout" style="padding:4px 8px; font-size:var(--text-xs);" onclick="openCallModalByIndex(${idx})">Inspect</button>
                </td>
            </tr>
        `;
    }).join('');
}

function openCallModalByIndex(index) {
    const call = currentCallHistoryData[index];
    if (!call) return;

    currentModalIndex = index;
    resetCopyButtons();

    document.getElementById('modal-call-id').textContent = `#${call.call_id}`;
    const modalRawStatus = call.status || 'Unknown';
    const modalStatus = (modalRawStatus.toLowerCase() === 'none' || modalRawStatus.toLowerCase() === 'null') ? 'Completed' : modalRawStatus;
    document.getElementById('modal-call-status').textContent = modalStatus;
    document.getElementById('modal-call-status').className = `status-badge status-${modalStatus.toLowerCase().replace(/\s+/g, '-')}`;

    const eventName = call.event || call.call_log_raw?.event || 'N/A';
    const eventEl = document.getElementById('modal-call-event');
    if (eventEl) {
        eventEl.textContent = eventName;
        if (eventName === 'CALL_DATA_UPDATE') {
            eventEl.style.color = 'var(--accent)';
            eventEl.style.borderColor = 'rgba(88, 101, 242, 0.4)';
        } else if (eventName === 'CALL_RETRY') {
            eventEl.style.color = 'var(--warning)';
            eventEl.style.borderColor = 'rgba(240, 178, 50, 0.4)';
        } else if (eventName === 'CALL_DATA_INBOUND_UPDATE') {
            eventEl.style.color = 'var(--success)';
            eventEl.style.borderColor = 'rgba(35, 165, 90, 0.4)';
        } else {
            eventEl.style.color = 'var(--text-primary)';
            eventEl.style.borderColor = 'var(--border-default)';
        }
    }

    const dur = call.duration ? `${Math.floor(call.duration / 60)}m ${call.duration % 60}s (${call.duration}s)` : 'N/A';
    document.getElementById('modal-call-duration').textContent = dur;

    document.getElementById('modal-caller-num').textContent = call.caller_number || call.client_phone || 'N/A';
    document.getElementById('modal-called-num').textContent = call.called_number || 'N/A';
    document.getElementById('modal-trunk-id').textContent = call.trunk_id || 'N/A';
    document.getElementById('modal-created-at').textContent = call.created_at ? new Date(call.created_at).toLocaleString() : 'N/A';

    const aiCallId = call.call_log_raw?.ai_call_id || call.call_log_raw?.job_id || 'N/A';
    document.getElementById('modal-ai-call-id').textContent = aiCallId;

    // Navigation and Index info
    const prevBtn = document.getElementById('btn-prev-modal-call');
    const nextBtn = document.getElementById('btn-next-modal-call');
    const indexInfo = document.getElementById('modal-call-index-info');
    if (prevBtn) prevBtn.disabled = index <= 0;
    if (nextBtn) nextBtn.disabled = index >= currentCallHistoryData.length - 1;
    if (indexInfo) indexInfo.textContent = `Call ${index + 1} of ${currentCallHistoryData.length}`;

    const audioContainer = document.getElementById('modal-recording-container');
    const audioPlayer = document.getElementById('modal-audio-player');
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.playbackRate = 1;
    }
    document.querySelectorAll('.speed-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.speed === '1');
    });

    if (call.recording_url) {
        audioPlayer.src = call.recording_url;
        audioPlayer.load();
        audioContainer.style.display = 'block';
    } else {
        audioPlayer.removeAttribute('src');
        audioContainer.style.display = 'none';
    }

    const summaryText = call.summary || call.call_log_raw?.ai_summary || call.call_log_raw?.summary || call.purpose || 'No AI summary generated for this call.';
    document.getElementById('modal-call-summary').textContent = summaryText;

    const rawTranscript = call.transcript || call.call_log_raw?.call_transcript || call.call_log_raw?.transcript || call.call_log_raw?.conversation;
    currentModalTranscriptText = formatTranscriptForCopy(rawTranscript);
    renderTranscriptInModal(rawTranscript);

    // Render Attempt History & Retries Timeline
    const attempts = Array.isArray(call.attempts) && call.attempts.length > 0
        ? call.attempts
        : [{
            attempted_at: call.created_at,
            status: call.status,
            ai_call_id: aiCallId,
            duration: call.duration,
            recording_url: call.recording_url,
            summary: call.summary,
        }];

    const attemptsCountElem = document.getElementById('modal-attempts-count');
    if (attemptsCountElem) {
        attemptsCountElem.textContent = `${attempts.length} ${attempts.length > 1 ? 'Attempts' : 'Attempt'}`;
    }
    const tabAttemptsBadge = document.getElementById('modal-tab-attempts-badge');
    if (tabAttemptsBadge) {
        tabAttemptsBadge.textContent = attempts.length;
    }

    const timelineContainer = document.getElementById('modal-attempts-timeline');
    if (timelineContainer) {
        timelineContainer.innerHTML = attempts.map((att, attIdx) => {
            const attNum = attIdx + 1;
            const attTime = att.attempted_at ? new Date(att.attempted_at).toLocaleString() : 'N/A';
            const attStatus = att.status || 'Unknown';
            const attStatusClass = `status-${attStatus.toLowerCase().replace(/\s+/g, '-')}`;
            const attDur = att.duration ? `${Math.floor(att.duration / 60)}m ${att.duration % 60}s` : '0s';
            const attAiId = att.ai_call_id ? escapeHtml(att.ai_call_id) : '—';
            const attEvent = att.event || att.payload?.event || '';
            const isLatest = attIdx === attempts.length - 1;

            return `
                <div style="background:var(--bg-surface); border:1px solid var(--border-default); border-radius:6px; padding:10px 12px; font-size:var(--text-xs); display:flex; flex-direction:column; gap:4px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="font-weight:700; color:var(--accent);">Attempt #${attNum} ${attNum > 1 ? '(Retry)' : ''}</span>
                            <span class="status-badge ${attStatusClass}" style="font-size:10px; padding:2px 8px;">${escapeHtml(attStatus)}</span>
                            ${attEvent ? `<span style="font-family:var(--font-mono); font-size:10px; background:var(--bg-input); padding:2px 6px; border-radius:4px; border:1px solid var(--border-default); color:var(--text-secondary);">${escapeHtml(attEvent)}</span>` : ''}
                            ${isLatest ? '<span style="font-size:9px; background:#22c55e; color:white; padding:1px 6px; border-radius:4px; font-weight:600;">LATEST</span>' : ''}
                        </div>
                        <span style="font-family:var(--font-mono); color:var(--text-tertiary); font-size:11px;">Time: ${escapeHtml(attTime)}</span>
                    </div>
                    <div style="display:flex; gap:16px; color:var(--text-secondary); font-size:11px; margin-top:2px;">
                        <span>Duration: <b style="color:var(--text-primary); font-family:var(--font-mono);">${attDur}</b></span>
                        <span>AI Job ID: <b style="color:var(--accent); font-family:var(--font-mono);">${attAiId}</b></span>
                    </div>
                    ${att.summary ? `<div style="color:var(--text-secondary); font-size:11px; font-style:italic; margin-top:2px;">Summary: "${escapeHtml(att.summary)}"</div>` : ''}
                </div>
            `;
        }).join('');
    }

    document.getElementById('modal-raw-json').textContent = JSON.stringify(call.call_log_raw || {}, null, 2);

    document.getElementById('call-detail-modal').classList.remove('hidden');
}

function renderTranscriptInModal(rawTranscript) {
    const container = document.getElementById('modal-call-transcript');
    if (!container) return;

    if (!rawTranscript) {
        container.innerHTML = `<div style="color:var(--text-tertiary); font-size:var(--text-xs); font-style:italic; text-align:center;">No transcript available for this call</div>`;
        return;
    }

    let items = [];
    if (typeof rawTranscript === 'string') {
        try {
            items = JSON.parse(rawTranscript);
        } catch (e) {
            items = rawTranscript;
        }
    } else {
        items = rawTranscript;
    }

    if (typeof items === 'string') {
        const safeText = escapeHtml(items);
        container.innerHTML = `<div style="font-size:var(--text-xs); line-height:1.5; white-space:pre-wrap; color:var(--text-primary);">${safeText}</div>`;
        return;
    }

    if (!Array.isArray(items) || items.length === 0) {
        container.innerHTML = `<div style="color:var(--text-tertiary); font-size:var(--text-xs); font-style:italic; text-align:center;">No transcript turns recorded</div>`;
        return;
    }

    let html = '';
    items.forEach((turn, turnIdx) => {
        if (typeof turn === 'object' && turn !== null) {
            const botText = turn.bot || turn.agent || turn.assistant;
            const userText = turn.user || turn.customer || turn.caller;
            const turnTime = turn.timestamp || turn.start_time || turn.start;
            const seekAttr = (turnTime !== undefined && !isNaN(Number(turnTime))) ? `onclick="seekAudioToTurn(${Number(turnTime)})" title="Click to jump audio"` : '';

            if (botText) {
                html += `
                    <div class="transcript-bubble" ${seekAttr} style="align-self:flex-start; max-width:85%; background:var(--bg-elevated); border:1px solid var(--border-default); border-radius:6px; border-bottom-left-radius:2px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4;">
                        <b style="color:var(--accent); font-size:11px; display:block; margin-bottom:2px;">AI Agent</b>
                        ${escapeHtml(botText)}
                    </div>
                `;
            }
            if (userText) {
                html += `
                    <div class="transcript-bubble" ${seekAttr} style="align-self:flex-end; max-width:85%; background:var(--accent); color:white; border-radius:6px; border-bottom-right-radius:2px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4;">
                        <b style="color:rgba(255,255,255,0.8); font-size:11px; display:block; margin-bottom:2px;">Caller</b>
                        ${escapeHtml(userText)}
                    </div>
                `;
            }
            if (turn.role && (turn.content || turn.text)) {
                const isAssistant = turn.role === 'assistant' || turn.role === 'agent' || turn.role === 'bot';
                const label = isAssistant ? 'AI Agent' : 'Caller';
                const bgStyle = isAssistant
                    ? 'background:var(--bg-elevated); border:1px solid var(--border-default); align-self:flex-start; border-bottom-left-radius:2px;'
                    : 'background:var(--accent); color:white; align-self:flex-end; border-bottom-right-radius:2px;';
                const labelColor = isAssistant ? 'color:var(--accent);' : 'color:rgba(255,255,255,0.8);';
                html += `
                    <div class="transcript-bubble" ${seekAttr} style="max-width:85%; border-radius:6px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4; ${bgStyle}">
                        <b style="${labelColor} font-size:11px; display:block; margin-bottom:2px;">${label}</b>
                        ${escapeHtml(turn.content || turn.text || '')}
                    </div>
                `;
            }
        } else if (typeof turn === 'string') {
            html += `<div style="font-size:var(--text-xs); color:var(--text-secondary); padding:4px 0;">${escapeHtml(turn)}</div>`;
        }
    });

    container.innerHTML = html || `<div style="color:var(--text-tertiary); font-size:var(--text-xs); font-style:italic; text-align:center;">No transcript turns formatted</div>`;
}

function escapeHtml(str) {
    return String(str || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function closeCallModal() {
    const modal = document.getElementById('call-detail-modal');
    const audioPlayer = document.getElementById('modal-audio-player');
    
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.removeAttribute('src');
        audioPlayer.load();
    }
    
    if (modal) {
        modal.classList.add('hidden');
    }

    resetCopyButtons();
}

document.getElementById('btn-close-call-modal')?.addEventListener('click', closeCallModal);

document.getElementById('call-detail-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'call-detail-modal') {
        closeCallModal();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('call-detail-modal');
        if (modal && !modal.classList.contains('hidden')) {
            closeCallModal();
        }
    }
});

// ── Copy to Clipboard Helpers ──────────────────────────────────────────────
let currentModalTranscriptText = '';

function resetCopyButtons() {
    ['btn-copy-summary', 'btn-copy-transcript', 'btn-copy-payload'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn && btn.dataset.copyTimeout) {
            clearTimeout(Number(btn.dataset.copyTimeout));
            btn.classList.remove('copied');
            if (btn.dataset.originalHtml) {
                btn.innerHTML = btn.dataset.originalHtml;
                delete btn.dataset.originalHtml;
            }
            delete btn.dataset.copyTimeout;
        }
    });
}

function formatTranscriptForCopy(rawTranscript) {
    if (!rawTranscript) return '';
    let items = rawTranscript;
    if (typeof rawTranscript === 'string') {
        try {
            items = JSON.parse(rawTranscript);
        } catch (e) {
            return rawTranscript;
        }
    }
    if (typeof items === 'string') {
        return items;
    }
    if (!Array.isArray(items)) {
        return typeof items === 'object' ? JSON.stringify(items, null, 2) : String(items);
    }
    const lines = [];
    items.forEach(turn => {
        if (typeof turn === 'object' && turn !== null) {
            const botText = turn.bot || turn.agent || turn.assistant;
            const userText = turn.user || turn.customer || turn.caller;
            if (botText) lines.push(`AI Agent: ${botText}`);
            if (userText) lines.push(`Caller: ${userText}`);
            if (turn.role && (turn.content || turn.text)) {
                const isAssistant = turn.role === 'assistant' || turn.role === 'agent' || turn.role === 'bot';
                const roleLabel = isAssistant ? 'AI Agent' : 'Caller';
                lines.push(`${roleLabel}: ${turn.content || turn.text}`);
            }
        } else if (typeof turn === 'string') {
            lines.push(turn);
        }
    });
    return lines.join('\n\n');
}

function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-6px)';
        setTimeout(() => toast.remove(), 250);
    }, duration);
}

function seekAudioToTurn(approxSeconds) {
    const audio = document.getElementById('modal-audio-player');
    if (audio && audio.src && !isNaN(approxSeconds) && approxSeconds >= 0) {
        audio.currentTime = approxSeconds;
        audio.play().catch(() => {});
        showToast(`Seeking audio to ${Math.floor(approxSeconds / 60)}m ${Math.floor(approxSeconds % 60)}s`, 'info');
    }
}

function setAudioSpeed(speed) {
    const audio = document.getElementById('modal-audio-player');
    if (audio) {
        audio.playbackRate = speed;
        document.querySelectorAll('.speed-btn').forEach(btn => {
            btn.classList.toggle('active', parseFloat(btn.dataset.speed) === speed);
        });
        showToast(`Playback speed set to ${speed}x`, 'info');
    }
}

function downloadModalAudio() {
    const audio = document.getElementById('modal-audio-player');
    const callId = document.getElementById('modal-call-id')?.textContent.replace(/^#/, '').trim() || 'recording';
    if (!audio || !audio.src) {
        showToast('No audio recording available for download', 'error');
        return;
    }
    const a = document.createElement('a');
    a.href = audio.src;
    a.download = `call_${callId}.mp3`;
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast('Starting audio recording download', 'success');
}

function switchModalTab(tabName) {
    document.querySelectorAll('.modal-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    document.querySelectorAll('.modal-tab-panel').forEach(panel => {
        if (tabName === 'all') {
            panel.classList.add('active');
        } else {
            panel.classList.toggle('active', panel.dataset.panel === tabName);
        }
    });
}

function navigateModalCall(direction) {
    const newIndex = currentModalIndex + direction;
    if (newIndex >= 0 && newIndex < currentCallHistoryData.length) {
        openCallModalByIndex(newIndex);
    }
}

async function redialCurrentCall() {
    if (currentModalIndex < 0 || !currentCallHistoryData[currentModalIndex]) return;
    const call = currentCallHistoryData[currentModalIndex];
    const phone = call.caller_number || call.client_phone || call.called_number;
    if (!phone) {
        showToast('No valid phone number found for redial', 'error');
        return;
    }
    const confirmed = confirm(`Initiate outbound redial to ${phone}?`);
    if (!confirmed) return;

    try {
        const payload = {
            client_phone: phone,
            client_name: call.client_name || call.call_log_raw?.client_name || 'Caller',
            prompt: call.call_log_raw?.metadata?.prompt || call.call_log_raw?.prompt || 'Outbound consultation follow-up',
            trunk_id: call.trunk_id || undefined
        };
        const res = await apiFetch('/v1/webhooks/telephony', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        if (res.error) {
            showToast(`Redial failed: ${res.error}`, 'error');
        } else {
            showToast(`Redial initiated for ${phone}`, 'success');
            setTimeout(() => loadCallHistory(1), 1500);
        }
    } catch (e) {
        showToast(`Redial request failed: ${e.message}`, 'error');
    }
}

function exportFilteredCalls(format = 'csv') {
    if (!currentCallHistoryData || currentCallHistoryData.length === 0) {
        showToast('No call records available to export', 'error');
        return;
    }
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    if (format === 'csv') {
        const headers = ['Call ID', 'Status', 'Phone / Client', 'Trunk', 'Duration Seconds', 'Created At', 'Summary'];
        const rows = currentCallHistoryData.map(c => [
            c.call_id || '',
            c.status || '',
            c.caller_number || c.client_phone || c.client_name || '',
            c.trunk_id || '',
            c.duration || 0,
            c.created_at || '',
            (c.summary || c.call_log_raw?.ai_summary || '').replace(/"/g, '""')
        ]);
        const csvContent = [headers.join(','), ...rows.map(r => r.map(f => `"${f}"`).join(','))].join('\n');
        downloadBlob(csvContent, `call_logs_${timestamp}.csv`, 'text/csv');
        showToast(`Exported ${currentCallHistoryData.length} records to CSV`, 'success');
    } else if (format === 'json') {
        const jsonContent = JSON.stringify(currentCallHistoryData, null, 2);
        downloadBlob(jsonContent, `call_logs_${timestamp}.json`, 'application/json');
        showToast(`Exported ${currentCallHistoryData.length} records to JSON`, 'success');
    }
}

function downloadBlob(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function copyToClipboard(text, btnElement) {
    if (typeof text !== 'string') {
        text = text ? String(text) : '';
    }
    if (!text.trim()) return;

    const onSuccess = () => {
        showToast('Copied to clipboard', 'success');
        if (!btnElement) return;
        if (btnElement.dataset.copyTimeout) {
            clearTimeout(Number(btnElement.dataset.copyTimeout));
        }
        if (!btnElement.dataset.originalHtml) {
            btnElement.dataset.originalHtml = btnElement.innerHTML;
        }
        btnElement.classList.add('copied');
        btnElement.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
            <span>Copied!</span>
        `;
        const timeoutId = setTimeout(() => {
            btnElement.classList.remove('copied');
            if (btnElement.dataset.originalHtml) {
                btnElement.innerHTML = btnElement.dataset.originalHtml;
                delete btnElement.dataset.originalHtml;
            }
            delete btnElement.dataset.copyTimeout;
        }, 2000);
        btnElement.dataset.copyTimeout = String(timeoutId);
    };

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => fallbackCopy(text, onSuccess));
    } else {
        fallbackCopy(text, onSuccess);
    }
}

function fallbackCopy(text, callback) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '-9999px';
    textarea.setAttribute('readonly', '');
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
        document.execCommand('copy');
        if (callback) callback();
    } catch (err) {
        console.error('Failed to copy text: ', err);
    }
    document.body.removeChild(textarea);
}

document.getElementById('btn-copy-summary')?.addEventListener('click', (e) => {
    const summaryElem = document.getElementById('modal-call-summary');
    const text = summaryElem ? summaryElem.textContent.trim() : '';
    copyToClipboard(text, e.currentTarget);
});

document.getElementById('btn-copy-transcript')?.addEventListener('click', (e) => {
    const transcriptElem = document.getElementById('modal-call-transcript');
    const text = currentModalTranscriptText || (transcriptElem ? transcriptElem.innerText.trim() : '');
    copyToClipboard(text, e.currentTarget);
});

document.getElementById('btn-copy-payload')?.addEventListener('click', (e) => {
    const jsonElem = document.getElementById('modal-raw-json');
    const text = jsonElem ? jsonElem.textContent.trim() : '';
    copyToClipboard(text, e.currentTarget);
});

// ── Knowledge Base ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // KB tab switching
    document.querySelectorAll('.kb-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.kb-tab').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.kb-pane').forEach(p => p.classList.remove('active'));
            
            btn.classList.add('active');
            document.getElementById(`kb-${btn.dataset.tab}`).classList.add('active');
            
            hideKbResult();
        });
    });

    // Upload file
    const btnKbUpload = document.getElementById('btn-kb-upload');
    const handleKbUpload = async () => {
        const kbId = document.getElementById('kb-id-upload').value.trim();
        const file = document.getElementById('kb-file').files[0];
        
        if (!kbId || !file) return showKbResult('KB ID and file are required', 'error');
        
        showKbResult('Uploading and indexing...', 'info');
        setBtnLoading('btn-kb-upload', true);
        
        try {
            const formData = new FormData();
            formData.append('file', file);
            
            const res = await fetch(`/v1/knowledge/upload?kb_id=${encodeURIComponent(kbId)}`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${TOKEN}` },
                body: formData
            });
            
            const data = await res.json();
            if (res.ok) {
                showKbResult(`Success! ${data.chunks_created} chunks created (${data.strategy_used})`, 'success');
            } else {
                showKbResult(data.error || 'Upload failed', 'error');
            }
        } catch (e) {
            showKbResult(e.message, 'error');
        } finally {
            setBtnLoading('btn-kb-upload', false);
        }
    };
    btnKbUpload.addEventListener('click', handleKbUpload);
    document.getElementById('kb-id-upload').addEventListener('keypress', (e) => { if (e.key === 'Enter') handleKbUpload(); });

    // Index text
    const btnKbText = document.getElementById('btn-kb-text');
    const handleKbText = async () => {
        const kbId = document.getElementById('kb-id-text').value.trim();
        const content = document.getElementById('kb-content').value.trim();
        const title = document.getElementById('kb-title').value.trim();
        
        if (!kbId || !content) return showKbResult('KB ID and content are required', 'error');
        
        showKbResult('Indexing...', 'info');
        setBtnLoading('btn-kb-text', true);
        
        try {
            const res = await fetch('/v1/knowledge/text', {
                method: 'POST',
                headers: { ...apiHeaders() },
                body: JSON.stringify({ kb_id: kbId, content, title: title || undefined })
            });
            
            const data = await res.json();
            if (res.ok) {
                showKbResult(`Success! ${data.chunks_created} chunks created (${data.strategy_used})`, 'success');
            } else {
                showKbResult(data.error || 'Indexing failed', 'error');
            }
        } catch (e) {
            showKbResult(e.message, 'error');
        } finally {
            setBtnLoading('btn-kb-text', false);
        }
    };
    btnKbText.addEventListener('click', handleKbText);
    document.getElementById('kb-id-text').addEventListener('keypress', (e) => { if (e.key === 'Enter') handleKbText(); });
    document.getElementById('kb-title').addEventListener('keypress', (e) => { if (e.key === 'Enter') handleKbText(); });

    // Fetch URL
    const btnKbUrl = document.getElementById('btn-kb-url');
    const handleKbUrl = async () => {
        const kbId = document.getElementById('kb-id-url').value.trim();
        const url = document.getElementById('kb-url-input').value.trim();
        
        if (!kbId || !url) return showKbResult('KB ID and URL are required', 'error');
        
        showKbResult('Fetching and indexing...', 'info');
        setBtnLoading('btn-kb-url', true);
        
        try {
            const res = await fetch('/v1/knowledge/url', {
                method: 'POST',
                headers: { ...apiHeaders() },
                body: JSON.stringify({ kb_id: kbId, url })
            });
            
            const data = await res.json();
            if (res.ok) {
                showKbResult(`Success! ${data.chunks_created} chunks created (${data.strategy_used})`, 'success');
            } else {
                showKbResult(data.error || 'URL indexing failed', 'error');
            }
        } catch (e) {
            showKbResult(e.message, 'error');
        } finally {
            setBtnLoading('btn-kb-url', false);
        }
    };
    btnKbUrl.addEventListener('click', handleKbUrl);
    document.getElementById('kb-id-url').addEventListener('keypress', (e) => { if (e.key === 'Enter') handleKbUrl(); });
    document.getElementById('kb-url-input').addEventListener('keypress', (e) => { if (e.key === 'Enter') handleKbUrl(); });

    // Test Chat
    let chatHistory = [];
    const chatInput = document.getElementById('chat-input');
    const btnKbChat = document.getElementById('btn-kb-chat');
    const chatArea = document.getElementById('chat-area');
    const kbIdChatSelect = document.getElementById('kb-id-chat');

    async function loadKbIdsForDashboard() {
        if (!kbIdChatSelect) return;
        try {
            const res = await fetch('/v1/knowledge/list', { headers: apiHeaders() });
            const data = await res.json();
            kbIdChatSelect.innerHTML = '';
            if (data.status === 'success' && data.kbs.length > 0) {
                data.kbs.forEach(kb => {
                    const opt = document.createElement('option');
                    opt.value = kb;
                    opt.textContent = kb;
                    kbIdChatSelect.appendChild(opt);
                });
            } else {
                kbIdChatSelect.innerHTML = '<option value="">No KBs found</option>';
            }
        } catch (e) {
            kbIdChatSelect.innerHTML = '<option value="">Error loading</option>';
        }
    }
    loadKbIdsForDashboard();

    function appendChatMessage(text, isUser, context = null) {
        const msgDiv = document.createElement('div');
        msgDiv.style.maxWidth = '85%';
        msgDiv.style.padding = '10px 14px';
        msgDiv.style.borderRadius = '8px';
        msgDiv.style.fontSize = 'var(--text-sm)';
        msgDiv.style.lineHeight = '1.4';
        
        let contentHtml = '';
        
        if (isUser) {
            msgDiv.style.background = 'var(--accent-primary)';
            msgDiv.style.color = 'white';
            msgDiv.style.alignSelf = 'flex-end';
            msgDiv.style.borderBottomRightRadius = '2px';
            // Basic escape for user input
            const safeText = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            contentHtml = `<div>${safeText}</div>`;
        } else {
            msgDiv.style.background = 'var(--bg-surface)';
            msgDiv.style.border = '1px solid var(--border-default)';
            msgDiv.style.alignSelf = 'flex-start';
            msgDiv.style.borderBottomLeftRadius = '2px';
            // Render markdown for AI response
            contentHtml = `<div class="markdown-body" style="font-size: 0.9rem; line-height: 1.5;">${marked.parse(text)}</div>`;
        }
        
        if (context && context.length > 0) {
            let contextHtml = '<div class="context-box" style="font-size: 0.8rem; background: rgba(218, 165, 32, 0.1); border-left: 3px solid #DAA520; padding: 8px; margin-bottom: 8px; border-radius: 4px;"><strong>Retrieved Context:</strong><br>';
            context.forEach((c, idx) => {
                const preview = c.preview ? c.preview.substring(0, 150) : "";
                contextHtml += `<div style="margin-top: 4px;">[${idx + 1}] <b style="color: #DAA520;">${c.title}</b>: <span style="color: var(--text-tertiary);">${preview}...</span></div>`;
            });
            contextHtml += '</div>';
            contentHtml = contextHtml + contentHtml;
        }
        
        msgDiv.innerHTML = contentHtml;
        chatArea.appendChild(msgDiv);
        chatArea.scrollTop = chatArea.scrollHeight;
    }

    async function handleChat() {
        const message = chatInput.value.trim();
        const kbId = document.getElementById('kb-id-chat').value.trim();
        
        if (!message || !kbId) return;
        
        appendChatMessage(message, true);
        chatInput.value = '';
        chatInput.disabled = true;
        btnKbChat.disabled = true;
        btnKbChat.textContent = '...';
        hideKbResult();
        
        try {
            const response = await fetch('/v1/kb/chat', {
                method: 'POST',
                headers: { ...apiHeaders() },
                body: JSON.stringify({
                    kb_id: kbId,
                    message: message,
                    history: chatHistory
                })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                appendChatMessage(data.reply, false, data.context);
                chatHistory.push({ role: "user", content: message });
                chatHistory.push({ role: "assistant", content: data.reply });
            } else {
                appendChatMessage("Error: " + (data.error || "Unknown error"), false);
            }
        } catch (err) {
            appendChatMessage("Failed to connect to server.", false);
        } finally {
            chatInput.disabled = false;
            btnKbChat.disabled = false;
            btnKbChat.textContent = 'Send';
            chatInput.focus();
        }
    }

    btnKbChat.addEventListener('click', handleChat);
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleChat();
    });

    function showKbResult(message, type) {
        const el = document.getElementById('kb-result');
        el.textContent = message;
        el.className = `kb-result ${type}`;
        el.style.display = 'block';
    }

    function hideKbResult() {
        document.getElementById('kb-result').style.display = 'none';
    }

    function setBtnLoading(btnId, loading) {
        const btn = document.getElementById(btnId);
        btn.disabled = loading;
        if (loading) {
            btn.dataset.label = btn.dataset.label || btn.textContent;
            btn.innerHTML = '<span class="loading-spinner"></span>Processing...';
        } else {
            btn.textContent = btn.dataset.label;
        }
    }
});

// ── Init ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const username = localStorage.getItem('username');
    if (username) document.getElementById('nav-username').textContent = username;

    loadMetrics();
    loadSystemStatus();
    loadCallHistory(1);
    connectSSE();
    addFeedItem('Dashboard connected', 'info');

    document.getElementById('system-status')?.addEventListener('click', toggleSystemStatusDetails);
    document.addEventListener('click', (event) => {
        const wrapper = document.querySelector('.system-status-wrap');
        const popover = document.getElementById('system-status-popover');
        const indicator = document.getElementById('system-status');
        if (wrapper && popover && indicator && !wrapper.contains(event.target)) {
            popover.hidden = true;
            indicator.setAttribute('aria-expanded', 'false');
        }
    });

    // Event listeners for DB search & filtering
    document.getElementById('call-search-input')?.addEventListener('input', () => {
        loadCallHistory(1);
    });

    document.getElementById('call-status-filter')?.addEventListener('change', () => {
        loadCallHistory(1);
    });

    document.getElementById('btn-refresh-history')?.addEventListener('click', () => {
        loadMetrics();
        loadCallHistory(currentPage);
        showToast('Refreshing metrics and call history', 'info');
    });

    // Pagination controls
    document.getElementById('btn-prev-page')?.addEventListener('click', () => {
        if (currentPage > 1) {
            loadCallHistory(currentPage - 1);
        }
    });

    document.getElementById('btn-next-page')?.addEventListener('click', () => {
        loadCallHistory(currentPage + 1);
    });

    document.getElementById('pagination-page-size')?.addEventListener('change', (e) => {
        pageSize = parseInt(e.target.value, 10) || 50;
        loadCallHistory(1);
    });

    // Export buttons
    document.getElementById('btn-export-csv')?.addEventListener('click', () => {
        exportFilteredCalls('csv');
    });

    document.getElementById('btn-export-json')?.addEventListener('click', () => {
        exportFilteredCalls('json');
    });

    // Modal tabs
    document.querySelectorAll('.modal-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchModalTab(btn.dataset.tab);
        });
    });

    // Audio player speed controls
    document.querySelectorAll('.speed-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            setAudioSpeed(parseFloat(btn.dataset.speed));
        });
    });

    // Audio download button
    document.getElementById('btn-download-audio')?.addEventListener('click', () => {
        downloadModalAudio();
    });

    // Modal call navigation
    document.getElementById('btn-prev-modal-call')?.addEventListener('click', () => {
        navigateModalCall(-1);
    });

    document.getElementById('btn-next-modal-call')?.addEventListener('click', () => {
        navigateModalCall(1);
    });

    document.getElementById('btn-redial-modal-call')?.addEventListener('click', () => {
        redialCurrentCall();
    });

    // Global keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('call-detail-modal');
        const isModalOpen = modal && !modal.classList.contains('hidden');
        const isInputActive = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);

        if (e.key === 'Escape' && isModalOpen) {
            closeCallModal();
        } else if (e.key === '/' && !isInputActive) {
            e.preventDefault();
            const searchInput = document.getElementById('call-search-input');
            if (searchInput) {
                searchInput.focus();
                searchInput.select();
            }
        } else if (isModalOpen && !isInputActive) {
            if (e.key === 'ArrowLeft' || e.key === 'k') {
                navigateModalCall(-1);
            } else if (e.key === 'ArrowRight' || e.key === 'j') {
                navigateModalCall(1);
            } else if (e.key === ' ') {
                const audio = document.getElementById('modal-audio-player');
                if (audio && audio.src) {
                    e.preventDefault();
                    if (audio.paused) {
                        audio.play().catch(() => {});
                    } else {
                        audio.pause();
                    }
                }
            }
        }
    });

    // Refresh metrics & sync call history every 15s
    setInterval(() => {
        loadMetrics();
        loadSystemStatus();
        loadCallHistory(currentPage);
    }, 15000);
});
