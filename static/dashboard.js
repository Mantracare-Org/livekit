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
async function loadMetrics() {
    const data = await apiFetch('/api/v1/dashboard/metrics');
    if (data.error) return;

    document.getElementById('metric-total').textContent = data.total_calls;
    document.getElementById('metric-answer-rate').textContent = `${data.answer_rate}%`;
    document.getElementById('metric-avg-duration').textContent =
        data.avg_duration_seconds ? `${Math.floor(data.avg_duration_seconds / 60)}m ${data.avg_duration_seconds % 60}s` : '—';
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
    const evtSource = new EventSource(`/api/v1/dashboard/stream?token=${TOKEN}`);

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

            // Calls that ended
            lastActiveIds.forEach(id => {
                if (!currentIds.has(id)) {
                    addFeedItem(`Call ${id} ended`, 'info');
                }
            });

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

// ── Call History Table ───────────────────────────────────────────────────
async function loadCallHistory() {
    const data = await apiFetch('/api/v1/dashboard/calls?limit=15&offset=0');
    if (data.error) {
        document.getElementById('calls-table-body').innerHTML =
            `<tr><td colspan="5" class="empty-state">Could not load call history</td></tr>`;
        return;
    }

    document.getElementById('calls-total').textContent = data.total;

    const tbody = document.getElementById('calls-table-body');
    if (data.calls.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No calls recorded yet</td></tr>`;
        return;
    }

    tbody.innerHTML = data.calls.map(c => {
        const statusClass = `status-${(c.status || 'unknown').toLowerCase().replace(/\s+/g, '-')}`;
        const duration = c.duration
            ? `${Math.floor(c.duration / 60)}m ${c.duration % 60}s`
            : '—';
        const time = c.created_at
            ? new Date(c.created_at).toLocaleString()
            : '—';
        return `
            <tr>
                <td><span class="status-dot-sm ${statusClass}"></span>${c.status || 'Unknown'}</td>
                <td class="cell-mono">${c.call_id || '—'}</td>
                <td>${c.client_name || c.client_phone || '—'}</td>
                <td>${duration}</td>
                <td class="cell-time">${time}</td>
            </tr>
        `;
    }).join('');
}

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
    document.getElementById('btn-kb-upload').addEventListener('click', async () => {
        const kbId = document.getElementById('kb-id-upload').value.trim();
        const file = document.getElementById('kb-file').files[0];
        
        if (!kbId || !file) return showKbResult('KB ID and file are required', 'error');
        
        showKbResult('Uploading and indexing...', 'info');
        setBtnLoading('btn-kb-upload', true);
        
        try {
            const formData = new FormData();
            formData.append('file', file);
            
            const res = await fetch(`/api/v1/knowledge/upload?kb_id=${encodeURIComponent(kbId)}`, {
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
    });

    // Index text
    document.getElementById('btn-kb-text').addEventListener('click', async () => {
        const kbId = document.getElementById('kb-id-text').value.trim();
        const content = document.getElementById('kb-content').value.trim();
        const title = document.getElementById('kb-title').value.trim();
        
        if (!kbId || !content) return showKbResult('KB ID and content are required', 'error');
        
        showKbResult('Indexing...', 'info');
        setBtnLoading('btn-kb-text', true);
        
        try {
            const res = await fetch('/api/v1/knowledge/text', {
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
    });

    // Fetch URL
    document.getElementById('btn-kb-url').addEventListener('click', async () => {
        const kbId = document.getElementById('kb-id-url').value.trim();
        const url = document.getElementById('kb-url').value.trim();
        
        if (!kbId || !url) return showKbResult('KB ID and URL are required', 'error');
        
        showKbResult('Fetching and indexing...', 'info');
        setBtnLoading('btn-kb-url', true);
        
        try {
            const res = await fetch('/api/v1/knowledge/url', {
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
    });

    // Test Chat
    let chatHistory = [];
    const chatInput = document.getElementById('chat-input');
    const btnKbChat = document.getElementById('btn-kb-chat');
    const chatArea = document.getElementById('chat-area');
    const kbIdChatSelect = document.getElementById('kb-id-chat');

    async function loadKbIdsForDashboard() {
        if (!kbIdChatSelect) return;
        try {
            const res = await fetch('/api/v1/knowledge/list', { headers: apiHeaders() });
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
            const response = await fetch('/api/v1/kb/chat', {
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
        btn.textContent = loading ? 'Processing...' : btn.dataset.label || btn.textContent;
        if (!loading) btn.dataset.label = btn.textContent;
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

    // Refresh metrics every 30s
    setInterval(loadMetrics, 30000);
});
