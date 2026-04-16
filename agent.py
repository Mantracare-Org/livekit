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

# Load environment variables from .env.local
load_dotenv(".env.local")

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
            min_silence_duration=0.2,
        ),
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
    )

    agent = Agent(
        instructions="""You are an elite, professional call center representative. 
        Your goal is to provide seamless, high-quality assistance. 
        - Speak naturally and conversationally.
        - Keep your responses concise and to the point.
        - If you don't know something, be honest and professional.
        - Be polite, patient, and proactive in solving the user's needs.
        """,
        min_endpointing_delay=0.7,
        max_endpointing_delay=1.5,
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(instructions="greet the user and ask how you can help")


if __name__ == "__main__":
    cli.run_app(server)
