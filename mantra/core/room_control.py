import asyncio
import logging
import os

from livekit import api, rtc

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


async def graceful_disconnect_after_speech(
    ctx,
    session=None,
    call_state=None,
    timeout: float = 15.0,
    post_speech_silence: float = 1.2,
):
    """Wait until the agent has completely finished generating and speaking its last line,
    plus a short post-speech buffer, before force-disconnecting the room.

    Args:
        ctx: LiveKit JobContext
        session: AgentSession (optional, provides live agent_state and audio output)
        call_state: call_state dict (optional, tracks agent_state events)
        timeout: Maximum duration (seconds) to wait before forcing room teardown
        post_speech_silence: Silence buffer (seconds) after agent finishes speaking
                             to let telephony SIP/RTP jitter buffers play out completely.
    """
    if not ctx:
        return

    logger.info("[DIAG] graceful_disconnect_after_speech: Started waiting for agent to finish speaking.")
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    def _room_connected() -> bool:
        return bool(
            ctx
            and hasattr(ctx, "room")
            and ctx.room
            and ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED
        )

    def _agent_state() -> str:
        if session is not None and hasattr(session, "agent_state"):
            return str(session.agent_state)
        if call_state is not None:
            return str(call_state.get("agent_state", "unknown"))
        return "unknown"

    def _has_pending_audio() -> bool:
        if session is not None and hasattr(session, "output"):
            output = getattr(session, "output", None)
            if output is not None and hasattr(output, "audio"):
                audio = getattr(output, "audio", None)
                if audio is not None and hasattr(audio, "_pending_playback_count"):
                    try:
                        return audio._pending_playback_count > 0
                    except Exception:
                        pass
        return False

    def _new_user_speech_detected() -> bool:
        """Check if the caller started speaking a NEW utterance AFTER disconnect was scheduled."""
        if call_state is not None:
            user_speaking_ts = call_state.get("user_speaking_timestamp", 0.0)
            if user_speaking_ts > start_time:
                if call_state.get("user_state") == "speaking":
                    return True
                if session is not None and hasattr(session, "user_state") and str(session.user_state) == "speaking":
                    return True
        elif session is not None and hasattr(session, "user_state"):
            # Fallback if no call_state: check session user_state only after 1.0s grace period
            if (loop.time() - start_time) > 1.0 and str(session.user_state) == "speaking":
                return True
        return False

    def _is_speaking() -> bool:
        return _agent_state() == "speaking" or _has_pending_audio()

    def _is_thinking() -> bool:
        return _agent_state() in ("thinking", "initializing")

    # Phase A: Wait for speech to start OR thinking to settle.
    # Give the LLM/TTS pipeline a moment to transition into thinking/speaking if tool just returned.
    has_spoken = False
    while _room_connected() and (loop.time() - start_time) < timeout:
        if _new_user_speech_detected():
            logger.info("[DIAG] graceful_disconnect_after_speech: New caller speech detected during Phase A. Aborting disconnect.")
            if call_state is not None:
                call_state["end_call_triggered"] = False
            return

        if _is_speaking():
            has_spoken = True
            logger.info(f"[DIAG] graceful_disconnect_after_speech: Agent is actively speaking (state={_agent_state()}).")
            break

        # If LLM is generating response or TTS is synthesizing
        if _is_thinking():
            await asyncio.sleep(0.1)
            continue

        # Give a minimum grace window of 1.0s after tool invocation before concluding agent won't speak
        elapsed = loop.time() - start_time
        if elapsed < 1.0:
            await asyncio.sleep(0.1)
            continue

        # Neither speaking nor thinking after 1.0s: turn completed without further speech
        logger.info(f"[DIAG] graceful_disconnect_after_speech: Agent neither speaking nor thinking after {elapsed:.1f}s.")
        break

    # Phase B: If agent is speaking, wait until speech playback completely finishes
    if _is_speaking() or has_spoken:
        speaking_wait_start = loop.time()
        while _room_connected() and (loop.time() - start_time) < timeout:
            if _new_user_speech_detected():
                logger.info("[DIAG] graceful_disconnect_after_speech: Caller interrupted agent speech during Phase B. Aborting disconnect.")
                if call_state is not None:
                    call_state["end_call_triggered"] = False
                return

            if _is_speaking():
                await asyncio.sleep(0.1)
            else:
                # Speech state reports idle/listening, but debounce for 300ms to ensure
                # it's not a momentary inter-clause pause or TTS packet boundary
                await asyncio.sleep(0.3)
                if not _is_speaking() and not _is_thinking():
                    logger.info(
                        f"[DIAG] graceful_disconnect_after_speech: Agent finished speaking "
                        f"(spoke for ~{loop.time() - speaking_wait_start:.1f}s, state={_agent_state()})."
                    )
                    break

    # Phase C: Post-speech silence buffer (allows SIP jitter buffer and telephony audio playout to reach caller ear)
    if _room_connected():
        logger.info(f"[DIAG] graceful_disconnect_after_speech: Waiting {post_speech_silence:.1f}s post-speech silence buffer.")
        silence_waited = 0.0
        while _room_connected() and silence_waited < post_speech_silence:
            if _new_user_speech_detected():
                logger.info("[DIAG] graceful_disconnect_after_speech: Caller started speaking during Phase C. Aborting disconnect.")
                if call_state is not None:
                    call_state["end_call_triggered"] = False
                return

            # If agent unexpectedly resumed speaking or thinking during buffer, resume waiting
            if _is_speaking() or _is_thinking():
                logger.info("[DIAG] graceful_disconnect_after_speech: Agent resumed speech/thinking during buffer, resuming wait.")
                while _room_connected() and (_is_speaking() or _is_thinking()) and (loop.time() - start_time) < timeout:
                    if _new_user_speech_detected():
                        logger.info("[DIAG] graceful_disconnect_after_speech: Caller started speaking while agent resumed. Aborting.")
                        if call_state is not None:
                            call_state["end_call_triggered"] = False
                        return
                    await asyncio.sleep(0.1)
                silence_waited = 0.0
            await asyncio.sleep(0.1)
            silence_waited += 0.1

    # Phase D: Disconnect room
    if _room_connected():
        if _new_user_speech_detected():
            logger.info("[DIAG] graceful_disconnect_after_speech: Caller is speaking right before Phase D. Aborting disconnect.")
            if call_state is not None:
                call_state["end_call_triggered"] = False
            return
        total_waited = loop.time() - start_time
        logger.info(f"[DIAG] graceful_disconnect_after_speech: Disconnecting room after {total_waited:.1f}s total wait.")
        await _force_disconnect_room(ctx)
    else:
        logger.info("[DIAG] graceful_disconnect_after_speech: Room was already disconnected.")