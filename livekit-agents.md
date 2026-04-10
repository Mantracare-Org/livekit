LiveKit Agents is a Python framework for building realtime, programmable voice AI agents that can see, hear, and understand. You can create conversational, multi-modal agents with customizable voices using various TTS providers.

## Custom Voice Creation

Yes, you can create custom voices in LiveKit Agents through several TTS providers that support voice cloning or custom voice selection:

### Zero-Shot Voice Cloning (MistralAI)

MistralAI's TTS supports zero-shot voice cloning using reference audio:

````python
from livekit.plugins import mistralai
import base64

# Using zero-shot voice cloning
ref_audio_b64 = base64.b64encode(open("sample.mp3", "rb").read()).decode()
tts = mistralai.TTS(ref_audio=ref_audio_b64)
``` [1](#0-0)

### Custom Cloned Voices (Inworld)
Inworld TTS supports custom cloned voices:

```python
from livekit.plugins import inworld

tts = inworld.TTS(
    voice="Hades",  # voice ID (default or custom cloned voice)
    model="inworld-tts-1",
)
``` [2](#0-1)

### Voice Selection (AWS Nova Sonic)
AWS Nova Sonic provides 18 expressive voices across multiple languages:

```python
from livekit.plugins.aws.experimental.realtime import SONIC2_VOICES

model = aws.realtime.RealtimeModel.with_nova_sonic_2(
    voice="carolina"  # Portuguese, feminine
)
``` [3](#0-2)

## What You Can Do With LiveKit

- **Voice Agents**: Build conversational AI agents with STT, LLM, and TTS capabilities
- **Multi-modal Support**: Agents can see, hear, and understand through video/audio streams
- **Realtime Processing**: Low-latency, full-duplex voice interactions
- **Function Calling**: Integrate tools and APIs for agentic workflows
- **Multi-language Support**: Support for multiple languages and voice options
- **Telephony Integration**: Connect agents to phone systems via SIP
- **Custom Voices**: Use voice cloning or select from provider-specific voice options

The framework supports 60+ provider integrations including OpenAI, Google, Anthropic, Deepgram, ElevenLabs, and others, allowing you to mix and match the right components for your use case.

## Notes

- Custom voice capabilities depend on the specific TTS provider you choose
- Zero-shot cloning requires a reference audio file from the target voice
- Some providers like Inworld require pre-trained custom voices on their platform
- Voice selection varies by provider - check provider documentation for available options

Wiki pages you might want to explore:
- [Overview (livekit/agents)](/wiki/livekit/agents#1)
- [Building Agents (livekit/agents)](/wiki/livekit/agents#3)
- [LiveKit Cloud Inference Gateway (livekit/agents)](/wiki/livekit/agents#5.7)

### Citations

**File:** livekit-plugins/livekit-plugins-mistralai/README.md (L35-39)
```markdown
# Using zero-shot voice cloning
import base64
ref_audio_b64 = base64.b64encode(open("sample.mp3", "rb").read()).decode()
tts = mistralai.TTS(ref_audio=ref_audio_b64)
````

````

**File:** livekit-plugins/livekit-plugins-inworld/README.md (L33-36)
```markdown
tts = inworld.TTS(
    voice="Hades",                 # voice ID (default or custom cloned voice)
    model="inworld-tts-1",         # or "inworld-tts-1-max"
    encoding="OGG_OPUS",           # LINEAR16, MP3, OGG_OPUS, ALAW, MULAW, FLAC
````

**File:** livekit-plugins/livekit-plugins-aws/README.md (L193-197)

```markdown
from livekit.plugins.aws.experimental.realtime import SONIC2_VOICES

model = aws.realtime.RealtimeModel.with_nova_sonic_2(
voice="carolina" # Portuguese, feminine
)
```
