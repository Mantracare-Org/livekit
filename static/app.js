const {
    Room,
    RoomEvent,
    Track,
} = LivekitClient;

let room;
let isMicOn = true;

// Elements
const micBtn = document.getElementById('mic-btn');
const micOnIcon = document.getElementById('mic-on');
const micOffIcon = document.getElementById('mic-off');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const chatContent = document.getElementById('chat-content');
const visualizer = document.getElementById('visualizer');

// Payload UI Elements
const testPanel = document.getElementById('test-panel');
const tabBtns = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');

// Structured Inputs
const inputClientName = document.getElementById('input-client-name');
const inputCallId = document.getElementById('input-call-id');
const inputLeadId = document.getElementById('input-lead-id');
const inputKbId = document.getElementById('input-kb-id');
const inputPrompt = document.getElementById('input-prompt');

// Load KB IDs
async function loadKbIdsForConsole() {
    if (!inputKbId) return;
    try {
        const res = await fetch('/api/v1/knowledge/list', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await res.json();
        
        inputKbId.innerHTML = '<option value="">None (No KB context)</option>';
        if (data.status === 'success' && data.kbs.length > 0) {
            data.kbs.forEach(kb => {
                const opt = document.createElement('option');
                opt.value = kb;
                opt.textContent = kb;
                inputKbId.appendChild(opt);
            });
        }
    } catch (e) {
        console.error("Failed to load KB IDs", e);
    }
}
loadKbIdsForConsole();

// Raw Inputs
const payloadRaw = document.getElementById('payload-raw');
const parseRawBtn = document.getElementById('parse-raw-btn');

// Control Buttons
const startSessionBtn = document.getElementById('start-session-btn');
const disconnectBtn = document.getElementById('disconnect-btn');

// Tab Switching Logic
tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        const tabId = btn.dataset.tab;
        tabBtns.forEach(b => b.classList.remove('active'));
        tabContents.forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(`tab-${tabId}`).classList.add('active');
    });
});

// Parse Raw JSON Logic
parseRawBtn.addEventListener('click', () => {
    try {
        const rawValue = payloadRaw.value.trim();
        if (!rawValue) return;
        
        const data = JSON.parse(rawValue);
        
        // Extract fields
        if (data.client_name) inputClientName.value = data.client_name;
        if (data.call_id) inputCallId.value = data.call_id;
        if (data.lead_id) inputLeadId.value = data.lead_id;
        if (data.kb_id) inputKbId.value = data.kb_id;
        if (data.prompt) inputPrompt.value = data.prompt;
        
        // Switch to structured tab
        tabBtns[0].click();
        addSystemMessage('✅ Payload parsed successfully into fields.');
    } catch (e) {
        alert('❌ Invalid JSON: ' + e.message);
    }
});

async function connect() {
    try {
        statusText.innerText = 'Initializing...';
        startSessionBtn.disabled = true;
        
        // Construct payload from structured fields
        const payload = {
            client_name: inputClientName.value || 'User',
            call_id: inputCallId.value || '99999',
            lead_id: inputLeadId.value || '12345',
            kb_id: inputKbId.value || undefined,
            prompt: inputPrompt.value || 'You are a helpful assistant.'
        };

        // 1. Post to dispatch-test
        const response = await fetch('/dispatch-test', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (data.error) throw new Error(data.error);
        const serverUrl = data.url;
        if (!serverUrl) throw new Error("LIVEKIT_URL not found on server");

        statusText.innerText = 'Connecting...';

        // 2. Initialize Room
        room = new Room({
            adaptiveStream: true,
            dynacast: true,
        });

        // 3. Set up listeners
        setupRoomListeners();

        // 4. Connect to room
        console.log('Connecting to', serverUrl, 'Room:', data.room);
        await room.connect(serverUrl, data.token);
        console.log('Room state:', room.state);

        // 5. Publish microphone
        await room.localParticipant.setMicrophoneEnabled(true);
        isMicOn = true;
        updateMicUI();

        // Hide test panel on success
        testPanel.classList.add('hidden');

    } catch (error) {
        console.error('Failed to trigger/connect:', error);
        statusText.innerText = 'Error';
        addSystemMessage(`Error: ${error.message}`);
    } finally {
        startSessionBtn.disabled = false;
    }
}


async function disconnect() {
    if (room) {
        await room.disconnect();
    }
}

function updateMicUI() {
    if (isMicOn) {
        micOnIcon.classList.remove('hidden');
        micOffIcon.classList.add('hidden');
        micBtn.classList.remove('active');
    } else {
        micOnIcon.classList.add('hidden');
        micOffIcon.classList.remove('hidden');
        micBtn.classList.add('active');
    }
}

function toggleMic() {
    isMicOn = !isMicOn;
    room.localParticipant.setMicrophoneEnabled(isMicOn);
    updateMicUI();
}

function addSystemMessage(text) {
    const div = document.createElement('div');
    div.className = 'message system';
    div.innerText = text;
    chatContent.appendChild(div);
    chatContent.scrollTop = chatContent.scrollHeight;
}

function addChatMessage(text, role) {
    const lastMsg = chatContent.lastElementChild;
    if (lastMsg && lastMsg.classList.contains(role) && lastMsg.dataset.interim === 'true') {
        lastMsg.innerText = text;
        if (!text.endsWith('...')) {
            lastMsg.dataset.interim = 'false';
        }
    } else {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        div.innerText = text;
        if (text.endsWith('...')) {
            div.dataset.interim = 'true';
        }
        chatContent.appendChild(div);
    }
    chatContent.scrollTop = chatContent.scrollHeight;
}

function handleIncomingData(data) {
    if (data.type === 'transcript') {
        const role = data.participant_type === 'agent' ? 'agent' : 'user';
        addChatMessage(data.text, role);
    }
}

// Event Listeners
startSessionBtn.addEventListener('click', connect);
disconnectBtn.addEventListener('click', disconnect);
micBtn.addEventListener('click', toggleMic);

function setupRoomListeners() {
    room.on(RoomEvent.Connected, () => {
        console.log('✅ Connected to room');
        statusDot.classList.add('active');
        statusText.innerText = 'Connected';
        addSystemMessage('Connected. Agent is being dispatched...');
        disconnectBtn.classList.remove('hidden');
        micBtn.classList.remove('hidden');
    });

    room.on(RoomEvent.Disconnected, () => {
        console.log('❌ Disconnected');
        statusDot.classList.remove('active');
        statusText.innerText = 'Disconnected';
        addSystemMessage('Session ended.');
        disconnectBtn.classList.add('hidden');
        micBtn.classList.add('hidden');
        testPanel.classList.remove('hidden');
        visualizer.classList.add('hidden');
    });


    room.on(RoomEvent.ParticipantConnected, (participant) => {
        console.log('👤 Participant joined:', participant.identity);
        addSystemMessage(`Agent "${participant.identity}" joined.`);
    });

    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        console.log(`🎵 Track subscribed: ${track.kind} from ${participant.identity}`);
        if (track.kind === Track.Kind.Audio) {
            const audioElement = track.attach();
            document.body.appendChild(audioElement);
        }
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach(el => el.remove());
    });

    room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const agentSpeaking = speakers.some(
            s => s.identity !== room.localParticipant.identity
        );
        visualizer.classList.toggle('hidden', !agentSpeaking);
    });

    room.on(RoomEvent.DataReceived, (payload, participant) => {
        try {
            const text = new TextDecoder().decode(payload);
            const data = JSON.parse(text);
            handleIncomingData(data);
        } catch (e) {
            // not JSON, ignore
        }
    });
}

// ──────────────────────────────────────────
// INBOUND MANAGEMENT
// ──────────────────────────────────────────

const inboundResult = document.getElementById('inbound-result');

function displayInboundResult(data) {
    inboundResult.value = JSON.stringify(data, null, 2);
}

async function apiPost(url, body) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();
        displayInboundResult(data);
        return data;
    } catch (e) {
        inboundResult.value = `Error: ${e.message}`;
    }
}

async function apiGet(url) {
    try {
        const res = await fetch(url);
        const data = await res.json();
        displayInboundResult(data);
        return data;
    } catch (e) {
        inboundResult.value = `Error: ${e.message}`;
    }
}

async function apiDelete(url) {
    try {
        const res = await fetch(url, { method: 'DELETE' });
        const data = await res.json();
        displayInboundResult(data);
        return data;
    } catch (e) {
        inboundResult.value = `Error: ${e.message}`;
    }
}

// Inbound Trunk Handlers
document.getElementById('create-trunk-btn').addEventListener('click', async () => {
    const headersRaw = document.getElementById('trunk-headers').value.trim();
    let headersToAttributes = {};
    if (headersRaw) {
        try { headersToAttributes = JSON.parse(headersRaw); } catch (e) {
            inboundResult.value = `Error: Invalid JSON in Headers to Attributes: ${e.message}`;
            return;
        }
    }

    await apiPost('/api/v1/sip/trunks/inbound', {
        name: document.getElementById('trunk-name').value || 'ui-inbound-trunk',
        numbers: document.getElementById('trunk-numbers').value.split(',').map(s => s.trim()).filter(Boolean),
        auth_username: document.getElementById('trunk-auth-user').value,
        auth_password: document.getElementById('trunk-auth-pass').value,
        headers_to_attributes: headersToAttributes,
    });
});

document.getElementById('list-trunks-btn').addEventListener('click', () => {
    apiGet('/api/v1/sip/trunks/inbound');
});

document.getElementById('delete-trunk-btn').addEventListener('click', () => {
    const id = document.getElementById('trunk-delete-id').value.trim();
    if (!id) { inboundResult.value = 'Error: Trunk ID is required'; return; }
    apiDelete(`/api/v1/sip/trunks/inbound/${id}`);
});

// Dispatch Rule Handlers
document.getElementById('create-rule-btn').addEventListener('click', async () => {
    await apiPost('/api/v1/sip/dispatch-rules', {
        name: document.getElementById('rule-name').value || 'ui-inbound-rule',
        trunk_id: document.getElementById('rule-trunk-id').value,
        room_prefix: document.getElementById('rule-prefix').value || 'inbound_',
        prompt: document.getElementById('rule-prompt').value || 'You are a healthcare assistant. Greet the caller warmly.',
    });
});

document.getElementById('list-rules-btn').addEventListener('click', () => {
    apiGet('/api/v1/sip/dispatch-rules');
});

document.getElementById('delete-rule-btn').addEventListener('click', () => {
    const id = document.getElementById('rule-delete-id').value.trim();
    if (!id) { inboundResult.value = 'Error: Rule ID is required'; return; }
    apiDelete(`/api/v1/sip/dispatch-rules/${id}`);
});


