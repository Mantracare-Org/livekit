import logging
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    room_io,
)
from livekit.plugins import assemblyai, openai, cartesia, silero, ai_coustics, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Load environment variables from .env.local
load_dotenv(".env.local")

logger = logging.getLogger("agent")

class CallCenterAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are an elite, professional call center representative. 
            Your goal is to provide seamless, high-quality assistance. 
            - Speak naturally and conversationally.
            - Keep your responses concise and to the point.
            - Avoid list-like formatting, emojis, or complex punctuation.
            - If you don't know something, be honest and professional.
            - Be polite, patient, and proactive in solving the user's needs.
            """,
        )

server = AgentServer()

def prewarm(proc: JobProcess):
    """
    Load the VAD model into memory before the job starts to save latency.
    """
    proc.userdata["vad"] = silero.VAD.load()

server.setup_fnc = prewarm

@server.rtc_session(agent_name="pro-call-agent")
async def start_agent_session(ctx: JobContext):
    """
    Initializes the voice pipeline for a single caller session.
    """
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info(f"Starting agent session in room: {ctx.room.name}")

    # Set up the high-performance voice pipeline
    session = AgentSession(
        # STT: AssemblyAI Universal-3 Pro (Optimized for neural turn detection)
        stt=assemblyai.STT(model="universal-3-pro"),
        
        # LLM: GPT-4o-Mini (Fastest reasoning)
        llm=openai.LLM(model="gpt-4o-mini"),
        
        # TTS: Cartesia Sonic-3 (Extreme low-latency, human-like prosody)
        tts=cartesia.TTS(model="sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
        
        # Turn Detection: Multilingual model for tight response loops
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        
        # Preemptive Generation: Start thinking while the user is still finishing their sentence
        preemptive_generation=True,
    )

    # Enhance the audio quality with AI Coustics and Noise Cancellation
    await session.start(
        agent=CallCenterAssistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else ai_coustics.audio_enhancement(
                        model=ai_coustics.EnhancerModel.QUAIL_VF_L
                    )
                ),
            ),
        ),
    )

    logger.info("Agent session connected.")
    await ctx.connect()

if __name__ == "__main__":
    # Provides CLI commands like 'dev', 'start', 'console'
    cli.run_app(server)
