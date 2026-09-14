"""Background call monitors and room/session event handlers for the agent worker.

Extracted verbatim from the former mantra/agent.py module, bound to a CallContext.
"""
import asyncio
import datetime
import logging
import os

from livekit import rtc

from mantra.call_duration import (
    BASE_FAREWELL_SECONDS as CALL_BASE_FAREWELL_SECONDS,
    BASE_HARD_LIMIT_SECONDS as CALL_BASE_HARD_LIMIT_SECONDS,
    EXTENDED_FAREWELL_SECONDS as CALL_EXTENDED_FAREWELL_SECONDS,
    EXTENDED_HARD_LIMIT_SECONDS as CALL_EXTENDED_HARD_LIMIT_SECONDS,
    current_limits,
    extend_call,
)
from mantra.core.common import CallContext, create_bg_task
from mantra.core.room_control import _force_disconnect_room
from mantra.email_alerts import send_crash_email
from mantra.positive_intent import should_extend_from_history
from mantra.utils import report_telemetry

logger = logging.getLogger("mantra.call_monitors")

_PIPELINE_ERROR_ALERT_COOLDOWN = 300.0  # seconds between alert emails per call

INBOUND_FAREWELL_PHRASES = [
    "goodbye",
    "good bye",
    "bye bye",
    "take care",
    "have a great day",
    "have a good day",
    "have a nice day",
    "talk to you later",
    "see you later",
]
OUTBOUND_FAREWELL_PHRASES = INBOUND_FAREWELL_PHRASES + [
    "thanks for calling",
    "thank you for calling",
]


async def positive_intent_monitor(cc: CallContext):
    logger.info("[INTENT] Positive-intent monitor started (outbound only, heuristic, auto-extend 3m to 5m)")
    await asyncio.sleep(5.0)
    while cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        try:
            if cc.call_state.get("is_inbound") or cc.call_state.get("duration_extended"):
                await asyncio.sleep(2.0)
                continue
            elapsed = asyncio.get_event_loop().time() - cc.entrypoint_start_time
            if elapsed < 15.0 or elapsed > CALL_EXTENDED_HARD_LIMIT_SECONDS - 10:
                await asyncio.sleep(2.0)
                continue
            if not (cc.session and hasattr(cc.session, "history") and cc.session.history):
                await asyncio.sleep(2.0)
                continue
            msgs = list(cc.session.history.messages())
            should, reason = should_extend_from_history(msgs)
            if should:
                logger.info(f"[INTENT] Positive intent detected: {reason} at t={elapsed:.1f}s")
                ok = await extend_call(cc.call_state, reason, elapsed)
                if ok:
                    create_bg_task(report_telemetry(tos_task_id=cc.call_state.get("tos_task_id"), message=f"[Agent Worker] Call auto-extended to 5m — {reason}", call_id=cc.call_state.get("call_id"), data={"reason": reason, "elapsed": round(elapsed, 1)}))
                break
        except Exception as e:
            logger.info(f"[INTENT] monitor error: {e}")
        await asyncio.sleep(2.0)


async def transcript_logger(cc: CallContext):
    """Transcript logging & dynamic language switching task."""
    _last_logged_history_size = 0
    await asyncio.sleep(2.0)  # brief startup delay
    while cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        try:
            if cc.session and hasattr(cc.session, 'history') and cc.session.history:
                msgs = list(cc.session.history.messages())
                if len(msgs) > _last_logged_history_size:
                    new_msgs = msgs[_last_logged_history_size:]
                    _last_logged_history_size = len(msgs)
                    for m in new_msgs:
                        role = m.role.name if hasattr(m.role, "name") else str(m.role)
                        content = " ".join([str(c) for c in m.content]) if isinstance(m.content, list) else str(m.content)
                        if content and not content.startswith("[System:"):
                            content_preview = content[:200] + ("..." if len(content) > 200 else "")
                            now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            logger.info(f"[DIAG] [{now_str}] TRANSCRIPT | {role}: {content_preview}")

                            # Learn explicit names and locations for later STT turns.
                            if str(role).lower() in ["user", "caller"]:
                                if cc.keyterm_memory.learn_from_text(content):
                                    updated_keyterms = cc.keyterm_memory.snapshot()
                                    cc.call_state["stt_keyterms"] = updated_keyterms
                                    try:
                                        cc.stt_engine.update_options(keyterm=updated_keyterms)
                                        logger.info(f"[STT] Updated temporary call keyterms ({len(updated_keyterms)} terms)")
                                    except Exception as keyterm_err:
                                        logger.warning(f"[STT] Temporary keyterm update unavailable: {keyterm_err}")

                            # Intercept caller/user utterances for dynamic language switching
                            if str(role).lower() in ["user", "caller"]:
                                new_lang, switched = cc.language_mgr.process_user_utterance(content)
                                if switched:
                                    old_lang = cc.call_state.get("current_language", "en")
                                    cc.call_state["current_language"] = new_lang
                                    logger.info(f"[LANG] Language switch triggered: {old_lang} -> {new_lang}")

                                    # Keep STT stable for bilingual calls; update response language only.
                                    # 1. Dynamically update TTS language options (preserving voice & speed)
                                    try:
                                        cc.tts_engine.update_options(language=new_lang, voice=cc.voice_id)
                                        logger.info(f"[LANG] TTS updated to language='{new_lang}' (voice={cc.voice_id})")
                                    except Exception as tts_err:
                                        logger.error(f"[LANG] Failed to update TTS language: {tts_err}")

                                    # 2. Dynamically update agent system instructions
                                    try:
                                        cur_inst = cc.agent.instructions
                                        if "<!-- LANGUAGE_DIRECTIVE_START -->" in cur_inst and "<!-- LANGUAGE_DIRECTIVE_END -->" in cur_inst:
                                            pref = cur_inst.split("<!-- LANGUAGE_DIRECTIVE_START -->")[0]
                                            suff = cur_inst.split("<!-- LANGUAGE_DIRECTIVE_END -->")[1]
                                            new_directive = cc.language_mgr.get_prompt_directive()
                                            updated_inst = f"{pref}<!-- LANGUAGE_DIRECTIVE_START -->\n{new_directive}\n<!-- LANGUAGE_DIRECTIVE_END -->{suff}"
                                            await cc.agent.update_instructions(updated_inst)
                                            logger.info(f"[LANG] Agent instructions updated to language='{new_lang}'")
                                    except Exception as inst_err:
                                        logger.error(f"[LANG] Failed to update agent prompt instructions: {inst_err}")

        except Exception as e:
            logger.info(f"[DIAG] Transcript logger error: {e}")
        await asyncio.sleep(0.4)


async def inactivity_monitor(cc: CallContext):
    logger.info("Inactivity monitor started.")
    while not cc.call_state.get("user_joined"):
        await asyncio.sleep(1.0)

    cc.call_state["last_activity"] = asyncio.get_event_loop().time()
    cc.call_state["prompted_inactivity"] = False

    while cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        await asyncio.sleep(1.0)
        # Only monitor inactivity AFTER initial greeting has completed speaking
        if not cc.call_state.get("initial_greeting_done"):
            cc.call_state["last_activity"] = asyncio.get_event_loop().time()
            continue

        now = asyncio.get_event_loop().time()
        agent_state = cc.call_state.get("agent_state", "initializing")
        last_activity = cc.call_state.get("last_activity", now)

        time_since_activity = now - last_activity

        if agent_state in ["listening", "idle"]:
            if time_since_activity > 30.0:
                logger.warning("[DIAG] No user response for 30s. Disconnecting room due to inactivity.")
                cc.call_state["timeline"].append(
                    {
                        "event": "Inactivity Timeout Disconnect",
                        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    }
                )
                create_bg_task(_force_disconnect_room(cc.ctx))
                break
            elif time_since_activity > 15.0 and not cc.call_state.get(
                "prompted_inactivity", False
            ):
                logger.info("No response for 15s. Prompting user...")
                cc.call_state["prompted_inactivity"] = True
                try:
                    cc.session.generate_reply(
                        user_input="[System: The user has been silent for a while. Politely ask if they are still there (e.g. 'Are you still there?' or 'Let me know if you need help.'). Keep it extremely short.]"
                    )
                except RuntimeError as e:
                    logger.warning(
                        f"Failed to generate inactivity reply (session may be closing): {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error generating inactivity reply: {e}"
                    )


async def farewell_safety_net(cc: CallContext):
    """Detect if the agent said goodbye without calling end_call, and force disconnect."""
    logger.info("[DIAG] farewell_safety_net: Started")
    await asyncio.sleep(10.0)  # Let the conversation warm up first
    farewell_phrases = INBOUND_FAREWELL_PHRASES if cc.is_inbound else OUTBOUND_FAREWELL_PHRASES
    while cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        await asyncio.sleep(3.0)
        if not (cc.session and hasattr(cc.session, "history") and cc.session.history):
            continue
        try:
            messages = list(cc.session.history.messages())
            if not messages:
                continue
            last_msg = messages[-1]
            role = getattr(last_msg, "role", "")
            content = str(getattr(last_msg, "content", "")).lower()
            if role == "assistant" and any(
                phrase in content for phrase in farewell_phrases
            ):
                logger.warning(
                    "[DIAG] farewell_safety_net: Agent said goodbye but end_call was never invoked. Force disconnecting."
                )
                cc.call_state["timeline"].append(
                    {
                        "event": "Farewell Safety Net Triggered",
                        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    }
                )
                await asyncio.sleep(3.0)  # Give TTS time to finish speaking
                await _force_disconnect_room(cc.ctx)
                break
        except Exception as e:
            logger.info(f"Farewell safety net error: {e}")


async def call_limiter(cc: CallContext):
    """Call duration limiter — supports 3m default to 5m extension on positive intent (outbound only)"""
    logger.info("[DIAG] call_limiter: Started — waiting for remote participant to join.")
    _force_disconnect_cancelled = cc.call_state.get("_force_disconnect_cancelled")
    if not isinstance(_force_disconnect_cancelled, asyncio.Event):
        _force_disconnect_cancelled = asyncio.Event()
        cc.call_state["_force_disconnect_cancelled"] = _force_disconnect_cancelled
    extension_event = cc.call_state.get("extension_event")
    try:
        while not list(cc.ctx.room.remote_participants.values()):
            await asyncio.sleep(1.0)
            if cc.ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                return

        def _targets():
            ext = bool(cc.call_state.get("duration_extended") and not cc.call_state.get("is_inbound"))
            return current_limits(ext)

        elapsed = asyncio.get_event_loop().time() - cc.entrypoint_start_time
        farewell_target, hard_target = _targets()
        logger.info(
            f"[DIAG] call_limiter: Participant joined at t={elapsed:.2f}s. "
            f"Farewell in {max(0.0, farewell_target - elapsed):.2f}s (target {farewell_target}s), "
            f"Hard kill in {max(0.0, hard_target - elapsed):.2f}s (target {hard_target}s)."
        )

        farewell_done = False
        while cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.0)
            if _force_disconnect_cancelled.is_set():
                logger.info("Call limiter exiting — force-disconnect cancelled.")
                return
            cur_farewell, cur_hard = _targets()
            if (cur_farewell, cur_hard) != (farewell_target, hard_target):
                logger.warning(f"[CALL_LIMITER] Targets updated: farewell {farewell_target}->{cur_farewell}s hard {hard_target}->{cur_hard}s")
                farewell_target, hard_target = cur_farewell, cur_hard
                if farewell_done and cur_farewell == CALL_EXTENDED_FAREWELL_SECONDS:
                    elapsed_now = asyncio.get_event_loop().time() - cc.entrypoint_start_time
                    if elapsed_now < cur_farewell:
                        farewell_done = False
                        cc.call_state["farewell_triggered"] = False

            elapsed = asyncio.get_event_loop().time() - cc.entrypoint_start_time
            if not farewell_done and elapsed >= farewell_target:
                farewell_done = True
                cc.call_state["farewell_triggered"] = True
                logger.info(f"Farewell stage hit at t={elapsed:.2f}s (target {farewell_target}s)")
                if cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                    logger.info("Updating agent instructions for farewell.")
                    current_inst = cc.agent.instructions
                    if isinstance(current_inst, str) and "call time is ending now" not in current_inst:
                        farewell_inst = (
                            "IMPORTANT: The call time is ending now. "
                            "On your next turn, say a quick, natural one-sentence goodbye "
                            "and do not continue the conversation. Do not ask questions."
                        )
                        await cc.agent.update_instructions(current_inst + "\n\n" + farewell_inst)
                    logger.info("Farewell instructions set.")
                    for _ in range(25):
                        if cc.call_state.get("duration_extended") and not cc.call_state.get("is_inbound"):
                            logger.info("[CALL_LIMITER] Farewell wait interrupted — call extended")
                            farewell_done = False
                            cc.call_state["farewell_triggered"] = False
                            break
                        if cc.ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED or _force_disconnect_cancelled.is_set():
                            break
                        if hasattr(cc.session, "wait_for_inactive") and callable(getattr(cc.session, "wait_for_inactive")):
                            try:
                                await asyncio.wait_for(cc.session.wait_for_inactive(), timeout=1.0)
                                logger.info("Session became inactive naturally.")
                                break
                            except asyncio.TimeoutError:
                                continue
                            except Exception:
                                await asyncio.sleep(1.0)
                        else:
                            await asyncio.sleep(1.0)
                    else:
                        logger.warning("Session did not go inactive within 25s — hard limit will handle it.")
                else:
                    logger.warning("Room already disconnected — skipping farewell.")

            elapsed = asyncio.get_event_loop().time() - cc.entrypoint_start_time
            if elapsed >= hard_target:
                if cc.ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                    logger.warning(f"HARD DISCONNECT: {hard_target}s limit reached. Force disconnecting room.")
                    cc.call_state["timeline"].append(
                        {
                            "event": "Max Call Duration Reached",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                            "limit": hard_target,
                            "extended": bool(cc.call_state.get("duration_extended")),
                        }
                    )
                    await _force_disconnect_room(cc.ctx)
                break
    except asyncio.CancelledError:
        logger.info("Call limiter cancelled (call ended naturally before limits).")
        try:
            _force_disconnect_cancelled.set()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in call limiter: {e}")


def register_room_handlers(cc: CallContext):
    """Attach recording + participant-disconnect handlers to the room."""

    @cc.ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info(f"[DIAG] Track subscribed: kind={track.kind} participant={participant.identity} sid={track.sid}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            cc.recorder.start_recording(track, f"participant_{participant.identity}")
            logger.info(f"[DIAG] Recording started for participant audio track: {participant.identity}")

    @cc.ctx.room.on("local_track_published")
    def on_local_track_published(
        publication: rtc.LocalTrackPublication, track: rtc.Track
    ):
        logger.info(f"[DIAG] Local track published: kind={track.kind} sid={track.sid}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            cc.recorder.start_recording(track, "agent")
            logger.info(f"[DIAG] Recording started for agent audio track")

    @cc.ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        cc.call_state["timeline"].append(
            {
                "event": "Remote Participant Disconnected",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
        )
        logger.info(
            f"[DIAG] Participant {participant.identity} disconnected. Force-ending call."
        )
        create_bg_task(_force_disconnect_room(cc.ctx))


def register_session_handlers(cc: CallContext):
    """Attach session state/error handlers for activity tracking and alerts."""

    @cc.session.on("agent_state_changed")
    def on_agent_state(ev):
        cc.call_state["agent_state"] = ev.new_state
        logger.info(f"[DIAG] Agent state change: {getattr(ev, 'old_state', 'None')} -> {ev.new_state}")

        if ev.new_state == "speaking":
            cc.call_state["greeting_started"] = True

        elif getattr(ev, "old_state", None) == "speaking" and ev.new_state != "speaking":
            cc.call_state["last_activity"] = asyncio.get_event_loop().time()
            if cc.call_state.get("greeting_started"):
                cc.call_state["initial_greeting_done"] = True

    @cc.session.on("user_state_changed")
    def on_user_state(ev):
        logger.info(f"[DIAG] User state change: {getattr(ev, 'old_state', 'None')} -> {ev.new_state}")
        if ev.new_state == "speaking":
            cc.call_state["last_activity"] = asyncio.get_event_loop().time()
            cc.call_state["prompted_inactivity"] = False
            cc.call_state["user_has_spoken"] = True
        elif getattr(ev, "old_state", None) == "speaking" and ev.new_state != "speaking":
            cc.call_state["user_finished_speaking_at"] = asyncio.get_event_loop().time()

    @cc.session.on("error")
    def on_session_error(ev):
        err = getattr(ev, "error", None)
        # Framework wraps provider errors (LLMError/STTError/TTSError carry .error)
        inner = err if isinstance(err, BaseException) else getattr(err, "error", None) or err
        source = getattr(ev, "source", None)
        source_label = (
            f"{getattr(source, 'provider', '')} {type(source).__name__}".strip()
            if source is not None
            else "unknown"
        )
        recoverable = getattr(err, "recoverable", None)
        logger.error(
            f"[DIAG] Pipeline error from {source_label}: {inner}",
            exc_info=inner if isinstance(inner, BaseException) else None,
        )

        cc.call_state["pipeline_error_count"] = cc.call_state.get("pipeline_error_count", 0) + 1
        now = asyncio.get_event_loop().time()
        last_alert = cc.call_state.get("last_pipeline_error_alert", 0.0)
        if now - last_alert < _PIPELINE_ERROR_ALERT_COOLDOWN:
            return
        cc.call_state["last_pipeline_error_alert"] = now
        error_count = cc.call_state["pipeline_error_count"]

        async def _alert():
            try:
                await send_crash_email(
                    service_name="Livekit Voice Agent pipeline",
                    error=inner if isinstance(inner, BaseException) else RuntimeError(str(inner)),
                    context_data={
                        "Room Name": getattr(cc.ctx.room, "name", "N/A"),
                        "Job ID": getattr(cc.ctx.job, "id", "N/A"),
                        "Process ID (PID)": os.getpid(),
                        "Component": source_label,
                        "Recoverable": recoverable,
                        "Errors This Call": error_count,
                        "Agent": cc.agent_name,
                    },
                )
            except Exception as email_err:
                logger.error(f"[DIAG] Failed to dispatch pipeline error email: {email_err}")

        asyncio.create_task(_alert())