<<<<<<< HEAD
"""Backward-compatible wrapper for core.language_manager."""
=======
"""
Production-Grade Multilingual Language Manager for Mantra Voice Agent.

Architecture:
- NativeLanguageDetector: Unicode script block inspector & statistical ML (langdetect) model.
- LanguageHysteresisTracker: Hysteresis state machine for smooth conversational transitions.
- LanguageManager: Coordinates dynamic language orchestration across STT, LLM prompt, and Cartesia TTS.
"""

import asyncio
import logging
import time
import os
from typing import Dict, List, Optional, Tuple, Set
import langdetect

from livekit.agents import stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.plugins import deepgram

logger = logging.getLogger("mantra.language_manager")

SUPPORTED_LANGUAGES: Set[str] = {"en", "hi"}

LANGUAGE_NAMES: Dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    # "kn": "Kannada",
    # "te": "Telugu",
    # "mr": "Marathi",
}

NATIVE_SCRIPTS: Dict[str, str] = {
    "en": "Latin",
    "hi": "Devanagari (हिन्दी)",
    # "kn": "Kannada (ಕನ್ನಡ)",
    # "te": "Telugu (తెలుగు)",
    # "mr": "Devanagari (मराठी)",
}

STOP_WORDS = {
    "You", "The", "This", "That", "Please", "Do", "Not", "If", "Always", "Never",
    "Call", "When", "Your", "Our", "Their", "From", "With", "About", "Have", "Has",
    "Will", "Would", "Should", "Could", "What", "Where", "Which", "Who", "How",
    "Core", "Behavior", "Knowledge", "Base", "Search", "Directives", "Ending", "Call",
    "Pronunciation", "Critical", "Prosody", "Tone", "Follow", "Specific", "Instructions"
}


def resolve_stt_keyterms(
    payload: Optional[dict] = None,
    custom_keyterms: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """
    Dynamically extract keyterms for Deepgram Nova-3 Keyterm Prompting.

    Eliminates hardcoded location lists by dynamically combining:
    1. Base brand terms ('MantraCare', 'MantraAssist')
    2. Environment variable overrides (DEEPGRAM_KEYTERMS="term1,term2")
    3. Explicit webhook payload fields ('keyterms' or 'keywords')
    4. Capitalized proper noun phrases (locations, doctor names, clinics) extracted dynamically from campaign prompts
    """
    keyterms_set = set()

    # 1. Base brand terms
    keyterms_set.add("MantraCare")
    keyterms_set.add("MantraAssist")

    # 2. Environment variable override
    env_terms = os.getenv("DEEPGRAM_KEYTERMS")
    if env_terms:
        for t in env_terms.split(","):
            t = t.strip()
            if t:
                keyterms_set.add(t)

    # 3. Custom keyterms passed directly
    if custom_keyterms:
        for t in custom_keyterms:
            if t and isinstance(t, str):
                keyterms_set.add(t.strip())

    # 4. Dynamic extraction from call payload
    if payload and isinstance(payload, dict):
        p_terms = payload.get("keyterms") or payload.get("keywords")
        if isinstance(p_terms, list):
            for t in p_terms:
                if t and isinstance(t, str):
                    keyterms_set.add(t.strip())
        elif isinstance(p_terms, str):
            for t in p_terms.split(","):
                t = t.strip()
                if t:
                    keyterms_set.add(t)

        # Extract capitalized proper nouns dynamically from campaign prompt text
        prompt = payload.get("prompt")
        if prompt and isinstance(prompt, str):
            import re
            matches = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b', prompt)
            for m in matches:
                m_clean = m.strip()
                if len(m_clean) > 2 and m_clean not in STOP_WORDS:
                    keyterms_set.add(m_clean)

    sorted_list = sorted(list(keyterms_set))
    return sorted_list if sorted_list else None


def resolve_stt_language(
    language: Optional[str] = None,
    phone_number: Optional[str] = None,
    country_code: Optional[str] = None,
) -> str:
    """
    Resolve the optimal Deepgram STT language/locale model.

    - Explicit 'hi' returns 'hi' (Deepgram Nova-3 Hindi speech model).
    - Explicit 'multi' returns 'multi'.
    - Indian English calls (+91 prefix, 10-digit mobile, country code IN, or 'en') map to 'en-IN'.
    - Explicit non-Indian regional locales ('en-US', 'en-GB', 'en-AU', 'es', 'fr', etc.) map directly.
    """
    lang = (language or "en").strip().lower()

    if lang in ("hi", "hindi"):
        return "hi"
    if lang == "multi":
        return "multi"

    # If it's already a non-English locale (e.g. 'en-US', 'en-GB', 'es', 'fr'), use it
    if "-" in lang and lang != "en-in":
        return lang
    elif lang not in ("en", "en-in"):
        return lang

    # Check country code if provided
    cc = (country_code or "").strip().upper()
    if cc in ("IN", "IND", "INDIA"):
        return "en-IN"
    elif cc in ("US", "USA", "CA", "CAN", "UNITED STATES", "CANADA"):
        return "en-US"
    elif cc in ("GB", "GBR", "UK", "UNITED KINGDOM"):
        return "en-GB"
    elif cc in ("AU", "AUS", "AUSTRALIA"):
        return "en-AU"
    elif cc in ("NZ", "NZL", "NEW ZEALAND"):
        return "en-NZ"

    # Infer from E.164 phone number prefix
    phone = (phone_number or "").strip()
    if phone.startswith("sip_"):
        phone = phone[4:]
    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("91") and len(phone) >= 12:
        return "en-IN"
    elif len(phone) == 10 and phone[0] in ("6", "7", "8", "9"):
        return "en-IN"
    elif phone.startswith("0") and len(phone) in (10, 11) and phone[1] in ("1", "2", "6", "7", "8", "9"):
        return "en-IN"
    elif phone.startswith("1") and len(phone) >= 11:
        return "en-US"
    elif phone.startswith("44") and len(phone) >= 11:
        return "en-GB"
    elif phone.startswith("61") and len(phone) >= 10:
        return "en-AU"
    elif phone.startswith("64") and len(phone) >= 10:
        return "en-NZ"

    # Default Indian English locale
    return "en-IN"


# ── 1. Unicode Script & Statistical ML Language Detector ─────────────────

class NativeLanguageDetector:
    """
    Deterministic Unicode script block profiling & statistical ML language detection.
    Zero hardcoded keyword lists or brittle phonetic lookup dictionaries.
    """

    def detect(self, text: str, current_lang: str = "en") -> Tuple[Optional[str], float]:
        """Detects language code and confidence for an utterance."""
        counts = {"devanagari": 0, "kannada": 0, "telugu": 0, "latin": 0}
        for ch in text:
            code = ord(ch)
            if 0x0900 <= code <= 0x097F:
                counts["devanagari"] += 1
            elif 0x0C80 <= code <= 0x0CFF:
                counts["kannada"] += 1
            elif 0x0C00 <= code <= 0x0C7F:
                counts["telugu"] += 1
            elif (0x0041 <= code <= 0x005A) or (0x0061 <= code <= 0x007A):
                counts["latin"] += 1

        total = sum(counts.values())
        if total == 0:
            return None, 0.0

        # 1. Kannada Unicode script block (commented out for now)
        # if counts["kannada"] > 0 and counts["kannada"] >= max(counts["devanagari"], counts["telugu"], counts["latin"]):
        #     return "kn", counts["kannada"] / total

        # 2. Telugu Unicode script block (commented out for now)
        # if counts["telugu"] > 0 and counts["telugu"] >= max(counts["devanagari"], counts["kannada"], counts["latin"]):
        #     return "te", counts["telugu"] / total

        # 3. Devanagari script block -> Hindi
        if counts["devanagari"] > 0:
            ratio = counts["devanagari"] / total
            return "hi", max(ratio, 0.95)

        # 4. Latin script block -> Statistical ML detection
        if counts["latin"] > 0:
            ratio = counts["latin"] / total
            try:
                detected = langdetect.detect(text)
                if detected in SUPPORTED_LANGUAGES:
                    return detected, 0.95
            except Exception:
                pass
            return "en", ratio

        return None, 0.0


# ── 2. Language Hysteresis Tracker ───────────────────────────────────────

class LanguageHysteresisTracker:
    """
    Hysteresis state machine tracking language transitions and stability.
    """

    def __init__(self, initial_lang: str):
        self.current_language: str = initial_lang
        self.pending_candidate: Optional[str] = None
        self.pending_count: int = 0

    def evaluate_transition(
        self,
        detected_lang: str,
        confidence: float,
    ) -> Tuple[str, bool]:
        """Evaluates state transition based on confidence and hysteresis."""
        if not detected_lang or detected_lang not in SUPPORTED_LANGUAGES or detected_lang == self.current_language:
            self._reset()
            return self.current_language, False

        if confidence >= 0.75:
            old = self.current_language
            self.current_language = detected_lang
            self._reset()
            logger.info(f"[LANG] Confirmed switch: {old} -> {self.current_language} (conf={confidence:.2f})")
            return self.current_language, True

        if self.pending_candidate == detected_lang:
            self.pending_count += 1
            if self.pending_count >= 2:
                old = self.current_language
                self.current_language = detected_lang
                self._reset()
                logger.info(f"[LANG] Multi-turn confirmed switch: {old} -> {self.current_language}")
                return self.current_language, True
        else:
            self.pending_candidate = detected_lang
            self.pending_count = 1

        return self.current_language, False

    def _reset(self):
        self.pending_candidate = None
        self.pending_count = 0


# ── 3. Main Language Manager ─────────────────────────────────────────────

class LanguageManager:
    """
    Coordinating manager for multilingual speech-to-text, text-to-speech, and prompt orchestration.
    Zero hardcoded keyword dictionaries.
    """

    def __init__(self, initial_language: str = "en"):
        normalized_init = self.normalize_language_code(initial_language)
        self.detector = NativeLanguageDetector()
        self.tracker = LanguageHysteresisTracker(normalized_init)
        logger.info(f"[LANG] LanguageManager active with language='{self.tracker.current_language}'")

    @staticmethod
    def normalize_language_code(code: Optional[str]) -> str:
        """Normalizes raw input strings into supported 2-letter ISO codes."""
        if not code:
            return "en"
        raw = str(code).lower().strip()
        # if raw in ["kn", "kannada", "kn-in"]:
        #     return "kn"
        if raw in ["hi", "hindi", "hi-in"]:
            return "hi"
        # elif raw in ["te", "telugu", "te-in"]:
        #     return "te"
        # elif raw in ["mr", "marathi", "mr-in"]:
        #     return "mr"
        elif raw in ["en", "english", "en-us", "en-in", "en-gb"]:
            return "en"
        return "en"

    def process_user_utterance(self, text: str) -> Tuple[str, bool]:
        """Processes a user utterance and updates active language state using pure ML and script detection."""
        cleaned = text.strip()
        if not cleaned:
            return self.tracker.current_language, False

        detected_lang, confidence = self.detector.detect(cleaned, self.tracker.current_language)
        if not detected_lang or detected_lang not in SUPPORTED_LANGUAGES:
            detected_lang = self.tracker.current_language
            confidence = 0.5

        return self.tracker.evaluate_transition(
            detected_lang=detected_lang,
            confidence=confidence,
        )

    @property
    def current_language(self) -> str:
        """Returns the active language code."""
        return self.tracker.current_language

    def get_current_language(self) -> str:
        """Returns the active language code."""
        return self.tracker.current_language

    def get_prompt_directive(self) -> str:
        """Returns the dynamic prompt instruction matching the current language state."""
        lang_code = self.tracker.current_language
        lang_name = LANGUAGE_NAMES.get(lang_code, "English")

        return (
            f"LANGUAGE RULE (HINGLISH — CRITICAL):\n"
            f"- CURRENT DETECTED UTTERANCE LANGUAGE: {lang_name} ({lang_code}).\n"
            f"- ALWAYS speak in natural Hinglish (Hindi + English mixed the way Indians speak on phone calls).\n"
            f"- Default style: Mix Hindi words + English words in the same sentence. Prefer Hindi sentence structure with English nouns/verbs where it feels natural.\n"
            f"- Good examples:\n"
            f'  - "Haan ji, main aapki madad kar sakta hoon. Aapko appointment book karni hai kya?"\n'
            f'  - "Theek hai, aapko kis location pe prefer karenge — Paschim Vihar ya Noida?"\n'
            f'  - "Got it. Aapka naam kya hai?"\n'
            f'  - "Sure, main check karta hoon... aapka preferred time morning hai ya evening?"\n'
            f"- Avoid pure English sentences and avoid pure Hindi (Devanagari-only) sentences.\n"
            f"- Use simple everyday words. Prefer Roman script for Hindi words (Hinglish style) so the TTS sounds natural.\n"
            f'- Fillers that sound natural in Hinglish: "Haan", "Theek hai", "Achha", "Bilkul", "Got it", "Sure", "Okay ji".\n'
            f"- STRICT: Never switch to any other language (no Marathi, Kannada, Telugu, etc.). Only Hinglish / Hindi-English mix.\n"
            f"- If the caller speaks pure English, still reply in light Hinglish (do not switch to pure English).\n"
            f"- If the caller speaks pure Hindi, reply in Hinglish (do not go full Devanagari)."
        )


# ── 6. All-Ears Multilingual Parallel STT Engine ────────────────────────

def _score_transcript(lang: str, text: str, confidence: float) -> float:
    """Scores a candidate transcript based on confidence, native script match, utterance length, and ML detection."""
    text = text.strip()
    if not text:
        return 0.0

    score = confidence
    word_count = len(text.split())
    length_bonus = min(word_count * 0.05, 0.3)

    # has_kannada = any(0x0C80 <= ord(c) <= 0x0CFF for c in text)
    # has_telugu = any(0x0C00 <= ord(c) <= 0x0C7F for c in text)
    has_devanagari = any(0x0900 <= ord(c) <= 0x097F for c in text)

    # if lang == "kn" and has_kannada:
    #     score += 0.5 + length_bonus
    # elif lang == "te" and has_telugu:
    #     score += 0.5 + length_bonus
    # elif lang in ("mr", "hi") and has_devanagari:
    if lang == "hi" and has_devanagari:
        score += 0.5 + length_bonus
        try:
            detected = langdetect.detect(text)
            if detected == lang:
                score += 0.2
        except Exception:
            pass
    elif lang == "en" and not has_devanagari:
        score += 0.4 + length_bonus

    return score


class MultilingualParallelStream(stt.RecognizeStream):
    """
    Broadcasts audio frames across multiple language-specific Deepgram streams
    in real time and arbitrates incoming transcripts with debounced turn scoring.
    """

    def __init__(
        self,
        *,
        stt_instance: "MultilingualParallelSTT",
        languages: List[str],
        conn_options: APIConnectOptions,
    ):
        super().__init__(stt=stt_instance, conn_options=conn_options)
        valid_langs = [l for l in dict.fromkeys(languages) if l in SUPPORTED_LANGUAGES]
        self._languages: List[str] = valid_langs if valid_langs else ["en", "hi"]
        self._child_streams: Dict[str, stt.RecognizeStream] = {}
        self._child_tasks: List[asyncio.Task] = []
        self._pending_finals: List[Tuple[str, stt.SpeechEvent]] = []
        self._debounce_task: Optional[asyncio.Task] = None
        self._speaking: bool = False
        self._last_final_emitted_time: float = 0.0
        self._last_speech_started_time: float = 0.0
        self._lock = asyncio.Lock()

    async def _run(self) -> None:
        for lang in self._languages:
            try:
                stt_lang = "en-IN" if lang == "en" else lang
                stt_kwargs = {
                    "model": "nova-3",
                    "language": stt_lang,
                    "smart_format": True,
                    "numerals": True,
                    "endpointing_ms": 150,
                    "utterance_end_ms": 600,
                }
                k_terms = resolve_stt_keyterms()
                if k_terms:
                    stt_kwargs["keyterm"] = k_terms
                child_stt = deepgram.STT(**stt_kwargs)
                stream = child_stt.stream()
                self._child_streams[lang] = stream
                task = asyncio.create_task(self._listen_child(lang, stream))
                self._child_tasks.append(task)
            except Exception as e:
                logger.warning(f"[LANG] Failed to initialize Deepgram stream for '{lang}': {e}")

        async def _forward_input_frames():
            while not self._input_ch.closed:
                try:
                    frame_or_sentinel = await self._input_ch.recv()
                except utils.aio.ChanClosed:
                    break
                if isinstance(frame_or_sentinel, self._FlushSentinel):
                    for s in list(self._child_streams.values()):
                        try:
                            s.flush()
                        except Exception:
                            pass
                else:
                    for s in list(self._child_streams.values()):
                        try:
                            s.push_frame(frame_or_sentinel)
                        except Exception:
                            pass

        input_task = asyncio.create_task(_forward_input_frames())
        try:
            await asyncio.gather(*self._child_tasks, input_task)
        finally:
            await utils.aio.cancel_and_wait(input_task)
            if self._debounce_task and not self._debounce_task.done():
                self._debounce_task.cancel()
            for s in list(self._child_streams.values()):
                try:
                    await s.aclose()
                except Exception:
                    pass

    async def _flush_finals(self, delay: float = 0.08) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            if not self._pending_finals:
                return
            best_lang, best_ev = max(
                self._pending_finals,
                key=lambda item: _score_transcript(
                    item[0],
                    item[1].alternatives[0].text if item[1].alternatives else "",
                    item[1].alternatives[0].confidence if item[1].alternatives else 0.0,
                ),
            )
            self._pending_finals.clear()
            self._debounce_task = None
            self._last_final_emitted_time = time.time()

        if best_ev.alternatives:
            best_ev.alternatives[0].language = LanguageCode(best_lang)
            self._event_ch.send_nowait(best_ev)

    async def _listen_child(self, lang: str, stream: stt.RecognizeStream) -> None:
        try:
            async for ev in stream:
                if ev.type == stt.SpeechEventType.START_OF_SPEECH:
                    self._last_speech_started_time = time.time()
                    if not self._speaking:
                        self._speaking = True
                        self._event_ch.send_nowait(ev)
                elif ev.type == stt.SpeechEventType.END_OF_SPEECH:
                    if self._speaking:
                        self._speaking = False
                        self._event_ch.send_nowait(ev)
                elif ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    if not ev.alternatives or not ev.alternatives[0].text.strip():
                        continue
                    now = time.time()
                    # Drop late-arriving residual transcripts from lagging streams for an already resolved turn
                    if (now - self._last_final_emitted_time < 1.0) and (self._last_speech_started_time <= self._last_final_emitted_time):
                        continue

                    async with self._lock:
                        self._pending_finals.append((lang, ev))
                        if self._debounce_task is None or self._debounce_task.done():
                            self._debounce_task = asyncio.create_task(self._flush_finals(0.08))
                elif ev.type == stt.SpeechEventType.RECOGNITION_USAGE:
                    self._event_ch.send_nowait(ev)
        except Exception:
            pass


class MultilingualParallelSTT(stt.STT):
    """
    All-Ears Multilingual STT engine.
    Runs parallel Deepgram STT streams across English and Hindi
    simultaneously so the agent captures either language the caller speaks in real time.
    """

    def __init__(self, languages: Optional[List[str]] = None):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
            )
        )
        filtered = [l for l in (languages or ["en", "hi"]) if l in SUPPORTED_LANGUAGES]
        # Default active: en, hi (commented regional: mr, kn, te)
        self._languages: List[str] = filtered if filtered else ["en", "hi"]  # ["en", "mr", "kn", "te", "hi"]

    @property
    def model(self) -> str:
        return "nova-3-multilingual"

    @property
    def provider(self) -> str:
        return "deepgram"

    def update_options(self, **kwargs) -> None:
        if "languages" in kwargs and kwargs["languages"]:
            filtered = [l for l in kwargs["languages"] if l in SUPPORTED_LANGUAGES]
            self._languages = filtered if filtered else ["en", "hi"]
        elif "language" in kwargs and kwargs["language"]:
            lang = kwargs["language"]
            if lang in SUPPORTED_LANGUAGES and lang not in self._languages:
                self._languages.insert(0, lang)

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        langs = [l for l in self._languages if l in SUPPORTED_LANGUAGES]
        if not langs:
            langs = ["en", "hi"]
        if language is not NOT_GIVEN and language and language in SUPPORTED_LANGUAGES and language not in langs:
            langs.insert(0, language)
        return MultilingualParallelStream(
            stt_instance=self,
            languages=langs,
            conn_options=conn_options,
        )

    async def _recognize_impl(
        self,
        buffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("Use streaming mode with MultilingualParallelSTT")
>>>>>>> 1f4cdd1 (feat: update language manager to use Deepgram Nova-3 multilingual locale for Indian region and phone calls)

from core.language_manager import *
