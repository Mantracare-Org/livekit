import logging
import asyncio
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

# Set up VERBOSE logging to catch everything
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("agent-diagnostic")
logger.setLevel(logging.DEBUG)

# Force standard libraries to debug mode too
logging.getLogger("livekit").setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.INFO)

server = AgentServer()

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """Main entrypoint with heavy logging."""
    logger.info("==========================================")
    logger.info(f"RECEIVED JOB: {ctx.job.id}")
    logger.info(f"ROOM NAME: {ctx.room.name}")
    logger.info(f"PARTICIPANT: {ctx.participant.identity if ctx.participant else 'None'}")
    logger.info("==========================================")

    try:
        session = AgentSession(
            vad=silero.VAD.load(),
            stt=assemblyai.STT(model="universal-3-pro"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=cartesia.TTS(model="sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
        )

        agent = Agent(
            instructions="You are a diagnostic assistant. Say 'Hello, I am connected' to verify connection.",
        )

        logger.info("Starting session...")
        await session.start(agent=agent, room=ctx.room)
        
        logger.info("Generating greeting...")
        await session.generate_reply(instructions="greet the user briefly and state your version as 1.5.4")
        
        # Keep the session alive for a bit to see if logs appear
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN ENTRYPOINT: {e}", exc_info=True)

if __name__ == "__main__":
    # Add a custom pre-run check
    logger.info("Worker starting with Express API...")
    cli.run_app(server)
