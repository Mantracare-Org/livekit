const startBtn = document.getElementById('startBtn');
const statusText = document.getElementById('statusText');
const micIndicator = document.getElementById('micIndicator');

let currentRoom = null;

async function startConversation() {
    try {
        startBtn.disabled = true;
        statusText.textContent = "Requesting session...";

        // 1 & 2: Call backend to get room details and token
        const response = await fetch('/session/start', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            throw new Error(`Failed to start session: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        statusText.textContent = "Connecting to LiveKit...";

        // 3: Connect using LiveKit JS SDK
        const room = new LivekitClient.Room({
            adaptiveStream: true,
            dynacast: true,
        });
        currentRoom = room;

        // Setup event listener for when agent audio arrives
        room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
            if (track.kind === LivekitClient.Track.Kind.Audio) {
                console.log("Audio track received from:", participant.identity);
                const element = track.attach();
                document.body.appendChild(element);
                statusText.textContent = "Agent connected. Start speaking.";
            }
        });

        room.on(LivekitClient.RoomEvent.Disconnected, () => {
            handleDisconnect();
        });

        // Use standard WebRTC connect logic
        await room.connect(data.url, data.token);
        console.log("Connected to room:", room.name);
        
        // 4: Enable local microphone to stream to agent
        await room.localParticipant.setMicrophoneEnabled(true);
        micIndicator.style.display = 'inline-block';
        
        statusText.textContent = "Connected. Waiting for agent to join...";
        
        // Update button to act as disconnect
        startBtn.textContent = "Disconnect";
        startBtn.classList.add('disconnect');
        startBtn.disabled = false;
        
        // Change click behavior
        startBtn.onclick = () => {
             room.disconnect();
        };

    } catch (error) {
        console.error(error);
        statusText.textContent = `Error: ${error.message}`;
        startBtn.disabled = false;
    }
}

function handleDisconnect() {
    currentRoom = null;
    statusText.textContent = "Disconnected.";
    micIndicator.style.display = 'none';
    
    startBtn.textContent = "Start Conversation";
    startBtn.classList.remove('disconnect');
    startBtn.disabled = false;
    
    // Reset click behavior
    startBtn.onclick = startConversation;
}

// Initial binding
startBtn.onclick = startConversation;
