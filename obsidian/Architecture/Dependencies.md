# Dependencies

## Python Runtime

### LiveKit Ecosystem
| Package | Purpose |
|---------|---------|
| `livekit-api>=1.1.0` | LiveKit Cloud API client |
| `livekit-agents[silero,turn-detector]~=1.4` | Voice agent framework, Silero VAD, multilingual turn detection |
| `livekit-plugins-openai` | LLM (GPT-4o-mini) |
| `livekit-plugins-google` | LLM (Gemini 2.5 Flash) |
| `livekit-plugins-deepgram` | STT (Nova-3) |
| `livekit-plugins-assemblyai` | Alternative STT |
| `livekit-plugins-cartesia` | TTS (Sonic-3) |
| `livekit-plugins-ai-coustics` | Audio enhancement |
| `livekit-plugins-noise-cancellation~=0.2` | Background noise removal |

### Web Server
| Package | Purpose |
|---------|---------|
| `fastapi>=0.115.0` | Async web framework |
| `uvicorn[standard]>=0.34.0` | ASGI server |

### Infrastructure
| Package | Purpose |
|---------|---------|
| `asyncpg` | Async PostgreSQL client |
| `redis>=8.0.0` | Async Redis client |
| `boto3>=1.35.0` | AWS SDK (S3 uploads) |
| `mcp[cli]` | Model Context Protocol server |

### AI & Data Processing
| Package | Purpose |
|---------|---------|
| `torch` | PyTorch (CPU, from pytorch-cpu index) — required for Silero VAD |
| `httpx>=0.27.0` | Async HTTP client |
| `pydub>=0.25.1` | Audio processing (MP3, silence detection) |
| `pyjwt>=2.8.0` | JWT authentication |
| `python-dotenv` | Environment loading |

## Frontend

- **LiveKit Client SDK** — WebRTC room connection (loaded from CDN)
- **No build step** — Vanilla HTML/CSS/JS
- **Design System:** "OpsCraft" — Discord/Linear-inspired dark theme

## System (Docker)

- Python 3.12 (bookworm-slim)
- `libgomp1`, `libglib2.0-0`, `libasound2`, `libatomic1`, `libportaudio2`, `ffmpeg`
