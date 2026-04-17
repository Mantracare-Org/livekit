import logging
from dotenv import load_dotenv
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

    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=0.1,
            min_silence_duration=0.3,
        ),
        stt=openai.STT(language="en", detect_language=True),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice="95d51f79-c397-46f9-b49a-23763d3eaa2d", speed=1.2, language=None),
    )

    agent = Agent(
        instructions="""You are a helpful voice AI assistant. The user is interacting with you via voice.
            Your responses are concise, to the point, and without any complex formatting or punctuation.
            
            LANGUAGE LOGIC:
            - By default, speak in English.
            - If and ONLY if the user speaks in Hindi, you must respond in Hindi.
            - Always match the user's language if they switch to Hindi, but revert to English if they switch back.
        """,
        min_endpointing_delay=1.2,
        max_endpointing_delay=2.5,
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(instructions="greet the user and ask how you can help")


if __name__ == "__main__":
    cli.run_app(server)
