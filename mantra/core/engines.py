"""Engine building for the agent worker: post-call analysis LLM, live LLM/TTS/STT
engines, voice mapping, and model/voice selection.

Extracted verbatim from the former mantra/agent.py module.
"""
import logging
import os

import httpx
import openai as openai_client

from livekit.agents import inference, llm
from livekit.plugins import cartesia, deepgram, google, openai

from mantra.language_manager import (
    CallKeytermMemory,
    LanguageManager,
    MultilingualParallelSTT,  # noqa: F401 (re-exported for parity)
    resolve_stt_keyterms,
    resolve_stt_language,
)

logger = logging.getLogger("mantra.engines")

POST_CALL_LLM_MODEL = os.getenv("POST_CALL_LLM_MODEL", "deepseek-chat")

VOICE_MAPPING = {
    "gemma": "62ae83ad-4f6a-430b-af41-a9bede9286ca",
    "alistair": "c8f7835e-28a3-4f0c-80d7-c1302ac62aae",
    "sunny": "156fb8d2-335b-4950-9cb3-a2d33befec77",
    "tyler": "820a3788-2b37-4d21-847a-b65d8a68c99a",
    "vikas": "adf97b9d-905c-41de-9fe9-afb387116d06",
    "camila": "bef2ba57-5c10-433b-b215-3bef35110a81",
    "renata": "d3793b7b-4996-409c-9d59-96dd09f47717",
    "arushi": "95d51f79-c397-46f9-b49a-23763d3eaa2d",
    "sia": "4459a9a5-69d6-4680-b970-e13dc51845b6",
    "sneha": "6b02ffe5-e3cb-48c0-a023-c72f85953375",
    "kavita": "56e35e2d-6eb6-4226-ab8b-9776515a7094",
    "katie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "cathy": "e8e5fffb-252c-436d-b842-8879b84445b6",
}


def build_post_call_llm() -> "llm.LLM":
    """Build a dedicated LLM engine for post-call analysis.

    Uses the Pro-tier model so transcript analysis (stage transitions,
    next_call_on, user_intent) is more reliable than the live-agent flash model.
    Falls back to the Gemini flash model if DEEPSEEK_API_KEY is missing.
    """
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.warning("DEEPSEEK_API_KEY not set for post-call LLM, falling back to gemini-2.5-flash")
        return google.LLM(model="gemini-2.5-flash")

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5, keepalive_expiry=120),
    )
    client = openai_client.AsyncClient(
        api_key=deepseek_key,
        base_url="https://api.deepseek.com",
        http_client=http_client,
    )
    logger.info(f"Post-call LLM using model: {POST_CALL_LLM_MODEL}")
    return openai.LLM(
        model=POST_CALL_LLM_MODEL,
        client=client,
        timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0),
    )


def select_model_and_voice(payload: dict | None):
    """Select the LLM name, voice input, voice ID and speed from the call payload.

    Mirrors the original inline logic (ai_payload priority, then payload, then
    defaults of deepseek / arushi / 1.0).
    """
    if isinstance(payload, dict):
        ai_p = payload.get("ai_payload")
        if not isinstance(ai_p, dict):
            ai_p = {}

        model_name = ai_p.get("ai_model") or payload.get("model") or "deepseek"
        model_name = str(model_name).lower()

        _raw_voice = (
            ai_p.get("voice_id")
            or payload.get("voice_id")
            or payload.get("voice_name")
            or payload.get("voice")
            or "arushi"
        )
        voice_input = "arushi" if _raw_voice in (None, "null", "None") else _raw_voice
        voice_id = VOICE_MAPPING.get(str(voice_input).lower(), voice_input)

        voice_speed = ai_p.get("voice_speed") or payload.get("voice_speed") or 1
    else:
        model_name = "deepseek"
        voice_input = "arushi"
        voice_id = VOICE_MAPPING["arushi"]
        voice_speed = 1.0

    try:
        voice_speed = float(voice_speed)
        voice_speed = max(0.1, min(2.0, voice_speed))
    except (ValueError, TypeError):
        voice_speed = 1.0

    return model_name, voice_input, voice_id, voice_speed


def build_live_llm_engine(model_name: str):
    """Build the live LLM engine and matching openai client (client is None
    unless a DeepSeek HTTP client was created, needed for KV-cache pre-warming)."""
    if model_name == "gemini":
        logger.info("Using Gemini (Google) LLM")
        return google.LLM(model="gemini-2.5-flash"), None
    elif model_name == "deepseek":
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if not deepseek_key:
            logger.warning("DEEPSEEK_API_KEY not set, falling back to OpenAI")
            return openai.LLM(model="gpt-4o-mini"), None
        else:
            logger.info("Using DeepSeek LLM (Optimized Low-Latency HTTP/2)")

            try:
                http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=45.0, write=15.0, pool=15.0),
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=300.0),
                    http2=True,
                )
            except Exception:
                http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=45.0, write=15.0, pool=15.0),
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=300.0),
                )

            deepseek_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            client = openai_client.AsyncClient(
                api_key=deepseek_key,
                base_url=deepseek_base,
                http_client=http_client,
            )
            llm_engine = openai.LLM(
                model="deepseek-v4-flash",
                client=client,
                timeout=httpx.Timeout(connect=10.0, read=45.0, write=15.0, pool=15.0),
            )
            return llm_engine, client
    else:
        logger.info("Using OpenAI LLM")
        return openai.LLM(model="gpt-4o-mini"), None


def build_language_manager(payload: dict | None):
    """Build the LanguageManager from the payload's language hints.

    Returns (language_mgr, raw_lang, requested_lang, response_mode).
    """
    raw_lang = None
    if isinstance(payload, dict):
        ai_p = payload.get("ai_payload") if isinstance(payload.get("ai_payload"), dict) else {}
        raw_lang = (
            payload.get("language")
            or payload.get("lang")
            or ai_p.get("language")
            or ai_p.get("lang")
        )

    requested_lang = str(raw_lang or "").strip().lower()
    response_mode = (
        "hi"
        if requested_lang in {"hi", "hindi", "hi-in"}
        else "en"
        if requested_lang in {"en", "english", "en-us", "en-in", "en-gb"}
        else "en"
    )
    language_mgr = LanguageManager(initial_language=raw_lang, response_mode=response_mode)
    return language_mgr, raw_lang, requested_lang, response_mode


def build_tts_engine(voice_id: str, language: str, voice_speed: float):
    """Direct Cartesia TTS plugin (self-host compatible)."""
    return cartesia.TTS(
        model="sonic-3",
        voice=voice_id,
        language=language,
        speed=voice_speed,
    )


def build_stt_engine(payload: dict | None, call_state: dict, is_inbound: bool, language: str, requested_lang: str):
    """Deepgram Nova-3 STT engine with international locale resolution.

    Fully compatible with both INBOUND and OUTBOUND calls. Returns
    (stt_engine, stt_lang, dynamic_keyterms, keyterm_memory).
    """
    call_phone = (
        call_state.get("caller_phone_number")
        or (payload.get("phone_number") if isinstance(payload, dict) else None)
        or (payload.get("client_phone_number") if isinstance(payload, dict) else None)
        or (payload.get("client_phone") if isinstance(payload, dict) else None)
        or (payload.get("caller_phone") if isinstance(payload, dict) else None)
        or (payload.get("to_phone") if isinstance(payload, dict) else None)
    )
    country_val = (
        (payload.get("country") or payload.get("country_code") or payload.get("client_country"))
        if isinstance(payload, dict)
        else None
    )

    bilingual_stt = not requested_lang or requested_lang in {
        "multi", "multilingual", "bilingual", "hinglish", "en-hi", "hi-en"
    }
    stt_lang = (
        "multi"
        if bilingual_stt
        else resolve_stt_language(language=language, phone_number=call_phone, country_code=country_val)
    )
    logger.info(f"[STT] Deepgram Nova-3 configured with language/locale: '{stt_lang}' (Direction: {'inbound' if is_inbound else 'outbound'} | Phone: {call_phone})")

    dynamic_keyterms = resolve_stt_keyterms(payload=payload if isinstance(payload, dict) else None)
    keyterm_memory = CallKeytermMemory(dynamic_keyterms)
    dynamic_keyterms = keyterm_memory.snapshot()
    call_state["stt_keyterms"] = dynamic_keyterms
    stt_kwargs = {
        "model": "nova-3",
        "language": stt_lang,
        "smart_format": True,
        "punctuate": True,
        "numerals": True,
        "endpointing_ms": 100,
        "no_delay": True,
    }
    if dynamic_keyterms:
        stt_kwargs["keyterm"] = dynamic_keyterms
        logger.info(f"[STT] Deepgram Nova-3 keyterm prompting enabled ({len(dynamic_keyterms)} terms): {dynamic_keyterms[:10]}...")

    stt_engine = deepgram.STT(**stt_kwargs)
    return stt_engine, stt_lang, dynamic_keyterms, keyterm_memory