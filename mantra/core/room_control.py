"""Room teardown helper for the agent worker."""
import logging
import os

from livekit import api

logger = logging.getLogger("mantra.room_control")


async def _force_disconnect_room(ctx):
    """Delete the room via LiveKit API. Falls back to local disconnect."""
    lk_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    try:
        await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        # logger.info(f"{Fore.RED}➖ Room Destroyed via API: {ctx.room.name}{Style.RESET_ALL}")
    except Exception as e:
        logger.error(f"Failed to delete room via API: {e}")
        try:
            await ctx.room.disconnect()
            # logger.info(f"{Fore.RED}➖ Room Disconnected locally: {ctx.room.name}{Style.RESET_ALL}")
        except Exception as e2:
            logger.error(f"Local disconnect also failed: {e2}")
    finally:
        await lk_api.aclose()