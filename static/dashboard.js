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
    const evtSource = new EventSource('/api/v1/dashboard/stream');

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
