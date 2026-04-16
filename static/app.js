const {
    Room,
    RoomEvent,
    Track,
} = LivekitClient;

let room;
let isMicOn = true;

// Elements
const connectBtn = document.getElementById('connect-btn');
const disconnectBtn = document.getElementById('disconnect-btn');
const micBtn = document.getElementById('mic-btn');
const micOnIcon = document.getElementById('mic-on');
const micOffIcon = document.getElementById('mic-off');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const chatContent = document.getElementById('chat-content');
const visualizer = document.getElementById('visualizer');

async function connect() {
    try {
        statusText.innerText = 'Connecting...';
        connectBtn.disabled = true;

        // 1. Fetch token and config
        const [tokenRes, configRes] = await Promise.all([
            fetch('/token'),
            fetch('/config')
        ]);
        const tokenData = await tokenRes.json();
        const configData = await configRes.json();
        console.log('Token fetched:', !!tokenData.token);
        console.log('Server URL:', configData.url);

        if (tokenData.error) throw new Error(tokenData.error);
        const serverUrl = configData.url;
        if (!serverUrl) throw new Error("LIVEKIT_URL not found");

        // 2. Initialize Room
        room = new Room({
            adaptiveStream: true,
            dynacast: true,
        });

        // 3. Set up listeners BEFORE connecting
        room.on(RoomEvent.Connected, () => {
            console.log('✅ Connected to room');
            statusDot.classList.add('active');
            statusText.innerText = 'Connected';
            addSystemMessage('Connected to room. Waiting for agent...');
            connectBtn.classList.add('hidden');
            disconnectBtn.classList.remove('hidden');
            micBtn.classList.remove('hidden');
        });

        room.on(RoomEvent.Disconnected, () => {
            console.log('❌ Disconnected');
            statusDot.classList.remove('active');
            statusText.innerText = 'Disconnected';
            addSystemMessage('Disconnected from room.');
            connectBtn.classList.remove('hidden');
            disconnectBtn.classList.add('hidden');
            micBtn.classList.add('hidden');
            connectBtn.disabled = false;
            visualizer.classList.add('hidden');
        });

        room.on(RoomEvent.ParticipantConnected, (participant) => {
            console.log('👤 Participant joined:', participant.identity);
            addSystemMessage(`Agent "${participant.identity}" joined the room.`);
        });

        room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
            console.log(`🎵 Track subscribed: ${track.kind} from ${participant.identity} (${participant.isAgent ? 'Agent' : 'User'})`);
            if (track.kind === Track.Kind.Audio) {
                // attach() returns an <audio> element — it MUST be added to the DOM to play
                const audioElement = track.attach();
                document.body.appendChild(audioElement);
                addSystemMessage(`Agent "${participant.identity}" has started speaking.`);
            }
        });

        room.on(RoomEvent.TrackUnsubscribed, (track) => {
            // Clean up attached elements
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

        // 4. Connect to room
        console.log('Connecting to', serverUrl);
        await room.connect(serverUrl, tokenData.token);
        console.log('Room state:', room.state);

        // 5. Publish microphone
        await room.localParticipant.setMicrophoneEnabled(true);
        isMicOn = true;
        updateMicUI();

    } catch (error) {
        console.error('Failed to connect:', error);
        statusText.innerText = 'Error';
        addSystemMessage(`Error: ${error.message}`);
        connectBtn.disabled = false;
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
connectBtn.addEventListener('click', connect);
disconnectBtn.addEventListener('click', disconnect);
micBtn.addEventListener('click', toggleMic);
