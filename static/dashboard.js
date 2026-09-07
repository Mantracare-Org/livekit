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

// ── Active Calls + Queue ─────────────────────────────────────────────────
function renderActiveCalls(activeDetails) {
    const container = document.getElementById('active-calls-list');
    if (!activeDetails || activeDetails.length === 0) {
        container.innerHTML = `<div class="empty-state">No active calls</div>`;
        return;
    }

    container.innerHTML = activeDetails.map(c => `
        <div class="call-card">
            <div class="call-card-header">
                <span class="call-id">${c.call_id}</span>
                <span class="status-badge status-${c.status}">${c.status}</span>
            </div>
            <div class="call-card-body">
                <span class="call-room">${c.room_name}</span>
                <span class="call-time" data-call-start="${c.call_id}">—</span>
            </div>
        </div>
    `).join('');

    // Update elapsed time every second
    setInterval(() => {
        document.querySelectorAll('[data-call-start]').forEach(el => {
            el.textContent = 'active';
        });
    }, 1000);
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

async function loadCallHistory() {
    const searchInput = document.getElementById('call-search-input');
    const statusSelect = document.getElementById('call-status-filter');
    const searchVal = searchInput ? searchInput.value.trim() : '';
    const statusVal = statusSelect ? statusSelect.value : 'all';

    let url = `/v1/dashboard/calls?limit=50&offset=0`;
    if (searchVal) url += `&search=${encodeURIComponent(searchVal)}`;
    if (statusVal && statusVal !== 'all') url += `&status=${encodeURIComponent(statusVal)}`;

    const data = await apiFetch(url);
    if (data.error) {
        document.getElementById('calls-table-body').innerHTML =
            `<tr><td colspan="7" class="empty-state">Could not load call history</td></tr>`;
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
    document.getElementById('calls-total').textContent = data.total || calls.length;

    const tbody = document.getElementById('calls-table-body');
    if (currentCallHistoryData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No call logs found matching "${statusVal}"</td></tr>`;
        return;
    }

    tbody.innerHTML = currentCallHistoryData.map((c, idx) => {
        const statusClass = `status-${(c.status || 'unknown').toLowerCase().replace(/\s+/g, '-')}`;
        const duration = c.duration
            ? `${Math.floor(c.duration / 60)}m ${c.duration % 60}s`
            : '—';
        const time = c.created_at
            ? new Date(c.created_at).toLocaleString()
            : '—';
        const phone = c.caller_number || c.client_phone || c.client_name || '—';
        const trunk = c.trunk_id || '—';
        const attemptsCount = c.attempts_count || (Array.isArray(c.attempts) ? c.attempts.length : 1);
        const attemptBadge = attemptsCount > 1
            ? `<span class="badge-count" style="margin-left:6px; font-size:10px; padding:2px 6px; background:var(--accent); color:white; border-radius:10px;" title="${attemptsCount} attempts recorded">Attempt #${attemptsCount} (${attemptsCount - 1} Retries)</span>`
            : '';

        return `
            <tr>
                <td><span class="status-dot-sm ${statusClass}"></span>${c.status || 'Unknown'}${attemptBadge}</td>
                <td class="cell-mono" style="font-weight:600;">${c.call_id || '—'}</td>
                <td>${phone}</td>
                <td class="cell-mono">${trunk}</td>
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

    document.getElementById('modal-call-id').textContent = `#${call.call_id}`;
    document.getElementById('modal-call-status').textContent = call.status || 'Unknown';
    document.getElementById('modal-call-status').className = `status-badge status-${(call.status || 'unknown').toLowerCase().replace(/\s+/g, '-')}`;

    const dur = call.duration ? `${Math.floor(call.duration / 60)}m ${call.duration % 60}s (${call.duration}s)` : 'N/A';
    document.getElementById('modal-call-duration').textContent = dur;

    document.getElementById('modal-caller-num').textContent = call.caller_number || call.client_phone || 'N/A';
    document.getElementById('modal-called-num').textContent = call.called_number || 'N/A';
    document.getElementById('modal-trunk-id').textContent = call.trunk_id || 'N/A';
    document.getElementById('modal-created-at').textContent = call.created_at ? new Date(call.created_at).toLocaleString() : 'N/A';

    const aiCallId = call.call_log_raw?.ai_call_id || call.call_log_raw?.job_id || 'N/A';
    document.getElementById('modal-ai-call-id').textContent = aiCallId;

    const audioContainer = document.getElementById('modal-recording-container');
    const audioPlayer = document.getElementById('modal-audio-player');
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
    }
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

    const timelineContainer = document.getElementById('modal-attempts-timeline');
    if (timelineContainer) {
        timelineContainer.innerHTML = attempts.map((att, attIdx) => {
            const attNum = attIdx + 1;
            const attTime = att.attempted_at ? new Date(att.attempted_at).toLocaleString() : 'N/A';
            const attStatus = att.status || 'Unknown';
            const attStatusClass = `status-${attStatus.toLowerCase().replace(/\s+/g, '-')}`;
            const attDur = att.duration ? `${Math.floor(att.duration / 60)}m ${att.duration % 60}s` : '0s';
            const attAiId = att.ai_call_id ? escapeHtml(att.ai_call_id) : '—';
            const isLatest = attIdx === attempts.length - 1;

            return `
                <div style="background:var(--bg-surface); border:1px solid var(--border-default); border-radius:6px; padding:10px 12px; font-size:var(--text-xs); display:flex; flex-direction:column; gap:4px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="font-weight:700; color:var(--accent);">Attempt #${attNum} ${attNum > 1 ? '(Retry)' : ''}</span>
                            <span class="status-badge ${attStatusClass}" style="font-size:10px; padding:2px 8px;">${escapeHtml(attStatus)}</span>
                            ${isLatest ? '<span style="font-size:9px; background:#22c55e; color:white; padding:1px 6px; border-radius:4px; font-weight:600;">LATEST</span>' : ''}
                        </div>
                        <span style="font-family:var(--font-mono); color:var(--text-tertiary); font-size:11px;">⏰ ${escapeHtml(attTime)}</span>
                    </div>
                    <div style="display:flex; gap:16px; color:var(--text-secondary); font-size:11px; margin-top:2px;">
                        <span>⏱️ Duration: <b style="color:var(--text-primary); font-family:var(--font-mono);">${attDur}</b></span>
                        <span>🤖 AI Job ID: <b style="color:var(--accent); font-family:var(--font-mono);">${attAiId}</b></span>
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
    items.forEach(turn => {
        if (typeof turn === 'object' && turn !== null) {
            const botText = turn.bot || turn.agent || turn.assistant;
            const userText = turn.user || turn.customer || turn.caller;

            if (botText) {
                html += `
                    <div style="align-self:flex-start; max-width:85%; background:var(--bg-elevated); border:1px solid var(--border-default); border-radius:6px; border-bottom-left-radius:2px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4;">
                        <b style="color:var(--accent); font-size:11px; display:block; margin-bottom:2px;">🤖 AI Agent</b>
                        ${escapeHtml(botText)}
                    </div>
                `;
            }
            if (userText) {
                html += `
                    <div style="align-self:flex-end; max-width:85%; background:var(--accent); color:white; border-radius:6px; border-bottom-right-radius:2px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4;">
                        <b style="color:rgba(255,255,255,0.8); font-size:11px; display:block; margin-bottom:2px;">👤 Caller</b>
                        ${escapeHtml(userText)}
                    </div>
                `;
            }
            if (turn.role && (turn.content || turn.text)) {
                const isAssistant = turn.role === 'assistant' || turn.role === 'agent' || turn.role === 'bot';
                const label = isAssistant ? '🤖 AI Agent' : '👤 Caller';
                const bgStyle = isAssistant
                    ? 'background:var(--bg-elevated); border:1px solid var(--border-default); align-self:flex-start; border-bottom-left-radius:2px;'
                    : 'background:var(--accent); color:white; align-self:flex-end; border-bottom-right-radius:2px;';
                const labelColor = isAssistant ? 'color:var(--accent);' : 'color:rgba(255,255,255,0.8);';
                html += `
                    <div style="max-width:85%; border-radius:6px; padding:8px 12px; font-size:var(--text-xs); line-height:1.4; ${bgStyle}">
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
    loadCallHistory();
    connectSSE();
    addFeedItem('Dashboard connected', 'info');

    // Event listeners for DB search & filtering
    document.getElementById('call-search-input')?.addEventListener('input', () => {
        loadCallHistory();
    });

    document.getElementById('call-status-filter')?.addEventListener('change', () => {
        loadCallHistory();
    });

    document.getElementById('btn-refresh-history')?.addEventListener('click', () => {
        loadMetrics();
        loadCallHistory();
    });

    // Refresh metrics & sync call history every 15s
    setInterval(() => {
        loadMetrics();
        loadCallHistory();
    }, 15000);
});
