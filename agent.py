import logging
from dotenv import load_dotenv
import json

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
)
from livekit.plugins import assemblyai, openai, cartesia, silero

# Load environment variables
load_dotenv()          # Load .env (OpenAI, etc.)
load_dotenv(".env.local", override=True)  # Load .env.local (LiveKit, etc.) and override if needed

logger = logging.getLogger("agent")

server = AgentServer()


@server.rtc_session(agent_name="mantra-agent")
async def entrypoint(ctx: JobContext):
    """Main entrypoint — matches the official README pattern exactly."""

    logger.info(f"--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # Parse payload from metadata if available
    initial_instructions = """You are a helpful voice AI assistant. The user is interacting with you via voice.
        Your responses are concise, to the point, and without any complex formatting or punctuation.
    """
    client_name = "User"
    
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            if "prompt" in payload:
                initial_instructions = payload["prompt"]
            if "client_name" in payload:
                client_name = payload["client_name"]
            logger.info(f"Loaded custom prompt for {client_name}")
        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")


    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=0.1,
            min_silence_duration=0.3,
        ),
        stt=openai.STT(language="en", detect_language=True),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice="95d51f79-c397-46f9-b49a-23763d3eaa2d", speed=1.0, language=None),
    )

    agent = Agent(
        instructions=initial_instructions,
        min_endpointing_delay=1.2,
        max_endpointing_delay=2.5,
    )

    await session.start(agent=agent, room=ctx.room)
    
    # The prompt usually contains the opening script, but we can also trigger it explicitly
    # If the prompt says "Hello, this is a follow-up...", the LLM should know what to say.
    # However, generate_reply can take instructions on how to start.
    await session.generate_reply(instructions=f"Greet the user named {client_name} and follow the opening script in your instructions.")


if __name__ == "__main__":
    cli.run_app(server)
