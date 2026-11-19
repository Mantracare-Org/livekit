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
import unicodedata
from typing import Dict, List, Optional, Tuple, Set
import langdetect

from livekit.agents import stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.plugins import deepgram

logger = logging.getLogger("mantra.language_manager")

SUPPORTED_LANGUAGES: Set[str] = {
    "en", "hi", "es", "fr", "de", "it", "nl", "pl", "pt", "ar",
    "ja", "ko", "zh", "ms", "id", "th", "vi", "tl", "tr", "sv",
    "da", "no", "fi", "ru"
}

LANGUAGE_NAMES: Dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ar": "Arabic",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Mandarin Chinese",
    "ms": "Malay",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "tl": "Tagalog / Filipino",
    "tr": "Turkish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "ru": "Russian",
}

NATIVE_SCRIPTS: Dict[str, str] = {
    "en": "Latin",
    "hi": "Devanagari (हिन्दी)",
    "es": "Latin",
    "fr": "Latin",
    "de": "Latin",
    "it": "Latin",
    "nl": "Latin",
    "pl": "Latin",
    "pt": "Latin",
    "ar": "Arabic (العربية)",
    "ja": "Japanese (日本語)",
    "ko": "Hangul (한국어)",
    "zh": "Hanzi (中文)",
    "ms": "Latin",
    "id": "Latin",
    "th": "Thai (ไทย)",
    "vi": "Latin",
    "tl": "Latin",
    "tr": "Latin",
    "sv": "Latin",
    "da": "Latin",
    "no": "Latin",
    "fi": "Latin",
    "ru": "Cyrillic (Русский)",
}

def resolve_stt_keyterms(
    payload: Optional[dict] = None,
    custom_keyterms: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """
    Dynamically extract keyterms for Deepgram Nova-3 Keyterm Prompting.

    Collects only runtime-provided terms from environment and call metadata.
    """
    keyterms_set = set()

    env_terms = os.getenv("DEEPGRAM_KEYTERMS")
    if env_terms:
        for t in env_terms.split(","):
            t = t.strip()
            if t:
                keyterms_set.add(t)

    if custom_keyterms:
        for t in custom_keyterms:
            if t and isinstance(t, str):
                keyterms_set.add(t.strip())

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

    sorted_list = sorted(list(keyterms_set))
    return sorted_list if sorted_list else None


class CallKeytermMemory:
    """Bounded vocabulary learned from one call and discarded with that call."""

    def __init__(self, initial_terms: Optional[List[str]] = None, max_terms: int = 80):
        self._terms: Dict[str, str] = {}
        self._max_terms = max_terms
        self.add(initial_terms or [])

    @staticmethod
    def _clean(term: str) -> str:
        punctuation = " \t\r\n.,!?;:()[]{}\""
        return " ".join(term.strip(punctuation).split())

    def add(self, terms: List[str]) -> bool:
        changed = False
        for raw_term in terms:
            if not isinstance(raw_term, str):
                continue
            term = self._clean(raw_term)
            if len(term) < 2 or len(term) > 64:
                continue
            key = term.casefold()
            if key not in self._terms and len(self._terms) < self._max_terms:
                self._terms[key] = term
                changed = True
        return changed

    def learn_from_text(self, text: str) -> bool:
        if not text:
            return False
        words = [self._clean(word) for word in text.split()]
        words = [word for word in words if word and any(char.isalnum() for char in word)]
        candidates: List[str] = []
        phrase: List[str] = []
        for word in words:
            has_non_latin_letter = any(
                char.isalpha() and "LATIN" not in unicodedata.name(char, "")
                for char in word
            )
            if has_non_latin_letter or word[:1].isupper() or (word.isupper() and word.isalpha()):
                phrase.append(word)
                candidates.append(word)
            elif phrase:
                candidates.append(" ".join(phrase))
                phrase = []
        if phrase:
            candidates.append(" ".join(phrase))
        return self.add(candidates)

    def snapshot(self) -> List[str]:
        terms: List[str] = []
        token_count = 0
        for term in self._terms.values():
            next_tokens = len(term.split())
            if token_count + next_tokens > 450:
                break
            terms.append(term)
            token_count += next_tokens
        return terms


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
    COUNTRY_TO_STT: Dict[str, str] = {
        "IN": "en-IN", "IND": "en-IN", "INDIA": "en-IN",
        "US": "en-US", "USA": "en-US", "CA": "en-US", "CAN": "en-US",
        "GB": "en-GB", "GBR": "en-GB", "UK": "en-GB",
        "AU": "en-AU", "AUS": "en-AU",
        "NZ": "en-NZ", "NZL": "en-NZ",
        "ZA": "en", "ZAF": "en",
        "SG": "en", "SGP": "en",
        "MX": "es-419", "MEX": "es-419",
        "ES": "es-ES", "ESP": "es-ES",
        "CO": "es-419", "COL": "es-419",
        "FR": "fr", "FRA": "fr",
        "DE": "de", "DEU": "de",
        "AT": "de", "AUT": "de",
        "IT": "it", "ITA": "it",
        "NL": "nl", "NLD": "nl",
        "PL": "pl", "POL": "pl",
        "PT": "pt-PT", "PRT": "pt-PT",
        "BR": "pt-BR", "BRA": "pt-BR",
        "AE": "ar", "ARE": "ar", "SA": "ar", "SAU": "ar", "QA": "ar", "QAT": "ar",
        "JP": "ja", "JPN": "ja",
        "KR": "ko", "KOR": "ko",
        "CN": "zh", "CHN": "zh", "TW": "zh", "TWN": "zh",
        "MY": "ms", "MYS": "ms",
        "ID": "id", "IDN": "id",
        "TH": "th", "THA": "th",
        "VN": "vi", "VNM": "vi",
        "PH": "tl", "PHL": "tl",
        "TR": "tr", "TUR": "tr",
        "SE": "sv", "SWE": "sv",
        "DK": "da", "DNK": "da",
        "NO": "no", "NOR": "no",
        "FI": "fi", "FIN": "fi",
        "RU": "ru", "RUS": "ru",
    }
    if cc in COUNTRY_TO_STT:
        return COUNTRY_TO_STT[cc]

    # Infer from E.164 phone number prefix
    phone = (phone_number or "").strip()
    if phone.startswith("sip_"):
        phone = phone[4:]
    if phone.startswith("+"):
        phone = phone[1:]

    # National Indian phone heuristics
    if phone.startswith("91") and len(phone) >= 12:
        return "en-IN"
    elif len(phone) == 10 and phone[0] in ("6", "7", "8", "9"):
        return "en-IN"
    elif phone.startswith("0") and len(phone) in (10, 11) and phone[1] in ("1", "2", "6", "7", "8", "9"):
        return "en-IN"

    # International prefixes (ordered by longest prefix first)
    PREFIX_TO_STT = [
        ("358", "fi"),    # Finland
        ("351", "pt-PT"), # Portugal
        ("971", "ar"),    # UAE
        ("966", "ar"),    # Saudi Arabia
        ("974", "ar"),    # Qatar
        ("886", "zh"),    # Taiwan
        ("52", "es-419"), # Mexico
        ("34", "es-ES"),  # Spain
        ("57", "es-419"), # Colombia
        ("55", "pt-BR"),  # Brazil
        ("33", "fr"),     # France
        ("49", "de"),     # Germany
        ("43", "de"),     # Austria
        ("39", "it"),     # Italy
        ("31", "nl"),     # Netherlands
        ("48", "pl"),     # Poland
        ("81", "ja"),     # Japan
        ("82", "ko"),     # South Korea
        ("86", "zh"),     # China
        ("65", "en"),     # Singapore
        ("60", "ms"),     # Malaysia
        ("62", "id"),     # Indonesia
        ("66", "th"),     # Thailand
        ("84", "vi"),     # Vietnam
        ("63", "tl"),     # Philippines
        ("90", "tr"),     # Turkey
        ("46", "sv"),     # Sweden
        ("45", "da"),     # Denmark
        ("47", "no"),     # Norway
        ("44", "en-GB"),  # UK
        ("61", "en-AU"),  # Australia
        ("64", "en-NZ"),  # New Zealand
        ("27", "en"),     # South Africa
        ("7", "ru"),      # Russia
        ("1", "en-US"),   # US / Canada
    ]
    for prefix, locale in PREFIX_TO_STT:
        if phone.startswith(prefix) and len(phone) > len(prefix) + 5:
            return locale

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

    def __init__(self, initial_language: str = "en", response_mode: Optional[str] = None):
        normalized_init = self.normalize_language_code(initial_language)
        self.detector = NativeLanguageDetector()
        self.tracker = LanguageHysteresisTracker(normalized_init)
        requested_mode = str(response_mode or normalized_init).lower().strip()
        if requested_mode in {"hi", "hindi", "hi-in"}:
            self.response_mode = "hi"
        elif requested_mode in {"en", "english", "en-us", "en-in", "en-gb", "en-au"}:
            self.response_mode = "en"
        elif requested_mode in SUPPORTED_LANGUAGES:
            self.response_mode = requested_mode
        elif requested_mode in {"multi", "multilingual", "bilingual", "hinglish", "en-hi", "hi-en"}:
            self.response_mode = "hinglish"
        else:
            self.response_mode = normalized_init
        logger.info(f"[LANG] LanguageManager active with language='{self.tracker.current_language}' (mode='{self.response_mode}')")

    @staticmethod
    def normalize_language_code(code: Optional[str]) -> str:
        """Normalizes raw input strings into supported 2-letter ISO codes."""
        if not code:
            return "en"
        raw = str(code).lower().strip()

        KNOWN_CODES = {
            "en": "en", "english": "en", "en-us": "en", "en-in": "en", "en-gb": "en", "en-au": "en",
            "hi": "hi", "hindi": "hi", "hi-in": "hi",
            "es": "es", "spanish": "es", "es-419": "es", "es-es": "es",
            "fr": "fr", "french": "fr", "fr-fr": "fr", "fr-ca": "fr",
            "de": "de", "german": "de", "de-de": "de",
            "it": "it", "italian": "it", "it-it": "it",
            "nl": "nl", "dutch": "nl", "nl-nl": "nl",
            "pl": "pl", "polish": "pl", "pl-pl": "pl",
            "pt": "pt", "portuguese": "pt", "pt-br": "pt", "pt-pt": "pt",
            "ar": "ar", "arabic": "ar", "ar-ae": "ar", "ar-sa": "ar",
            "ja": "ja", "japanese": "ja", "ja-jp": "ja",
            "ko": "ko", "korean": "ko", "ko-kr": "ko",
            "zh": "zh", "chinese": "zh", "mandarin": "zh", "zh-cn": "zh", "zh-tw": "zh",
            "ms": "ms", "malay": "ms", "ms-my": "ms",
            "id": "id", "indonesian": "id", "id-id": "id",
            "th": "th", "thai": "th", "th-th": "th",
            "vi": "vi", "vietnamese": "vi", "vi-vn": "vi",
            "tl": "tl", "tagalog": "tl", "filipino": "tl",
            "tr": "tr", "turkish": "tr", "tr-tr": "tr",
            "sv": "sv", "swedish": "sv", "sv-se": "sv",
            "da": "da", "danish": "da", "da-dk": "da",
            "no": "no", "norwegian": "no", "no-no": "no",
            "fi": "fi", "finnish": "fi", "fi-fi": "fi",
            "ru": "ru", "russian": "ru", "ru-ru": "ru",
        }
        if raw in KNOWN_CODES:
            return KNOWN_CODES[raw]
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

        language, switched = self.tracker.evaluate_transition(
            detected_lang=detected_lang,
            confidence=confidence,
        )
        if switched and language in SUPPORTED_LANGUAGES:
            self.response_mode = language
        return language, switched

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

        if self.response_mode == "en":
            return (
                f"LANGUAGE RULE (PROFESSIONAL ENGLISH PROSODY — CRITICAL):\n"
                f"- CURRENT DETECTED UTTERANCE LANGUAGE: {lang_name} ({lang_code}).\n"
                f"- Respond in clear, warm English with natural telephone cadence.\n"
                f"- Use natural speech punctuation: commas (,), ellipses (...), and hyphens (-) for natural breathing pauses so the voice sounds like a live human on a phone call, not a monotone robot.\n"
                f"- Natural English fillers: 'Got it...', 'Sure...', 'Right...', 'I see...'.\n"
                f"- Do NOT insert Hindi words or Hinglish fillers when the caller is speaking English.\n"
                f"- Keep the phone response concise, natural, and polite."
            )

        if self.response_mode == "hi":
            return (
                f"LANGUAGE RULE (HINDI PROSODY — CRITICAL):\n"
                f"- CURRENT DETECTED UTTERANCE LANGUAGE: {lang_name} ({lang_code}).\n"
                f"- Respond in natural, warm Hindi using Devanagari script.\n"
                f"- Incorporate warm Indian honorifics and speech pauses: 'हाँ जी...', 'अच्छा...', 'बिलकुल जी', 'जी'.\n"
                f"- Use commas and ellipses (...) for expressive, human phone cadence.\n"
                f"- Keep commonly understood English product or medical terms when natural.\n"
                f"- Keep the phone response concise, natural, and polite."
            )

        if self.response_mode in SUPPORTED_LANGUAGES and self.response_mode not in {"en", "hi"}:
            return (
                f"LANGUAGE RULE (FLUENT {lang_name.upper()} TELEPHONY — CRITICAL):\n"
                f"- CURRENT DETECTED UTTERANCE LANGUAGE: {lang_name} ({lang_code}).\n"
                f"- You MUST ALWAYS respond entirely and fluently in {lang_name}.\n"
                f"- Maintain natural, polite, and culturally appropriate conversational cadence for telephone calls.\n"
                f"- Use natural speech punctuation: commas (,), ellipses (...), and hyphens (-) for natural breathing pauses so the voice sounds like a live human on a phone call, not a monotone robot.\n"
                f"- Do NOT speak English, Hindi, or any other language unless explicitly requested by the caller.\n"
                f"- Keep the phone response concise, natural, and polite."
            )

        return (
            f"LANGUAGE RULE (HINGLISH INDIAN TELEPHONY — CRITICAL):\n"
            f"- CURRENT DETECTED UTTERANCE LANGUAGE: {lang_name} ({lang_code}).\n"
            f"- ALWAYS speak in natural, expressive Hinglish (Hindi + English mixed the way Indians speak on phone calls).\n"
            f"- Default style: Mix Hindi words + English words in the same sentence. Prefer Hindi sentence structure with English nouns/verbs where it feels natural.\n"
            f"- Good examples with natural Indian phone punctuation:\n"
            f'  - "Haan ji, main aapki madad karta hoon... aapko appointment book karni hai kya?"\n'
            f'  - "Theek hai ji... aap kis location ko prefer karenge — Paschim Vihar ya Noida?"\n'
            f'  - "Got it... aapka naam kya hai?"\n'
            f'  - "Sure ji, main check karta hoon... aapka preferred time morning hai ya evening?"\n'
            f"- Use warm Indian conversational markers & fillers: 'Haan ji', 'Theek hai ji', 'Achha...', 'Bilkul', 'Got it...', 'Sure ji', 'Waise...'.\n"
            f"- Use commas (,) and ellipses (...) for warm micro-pauses so the voice engine produces natural pitch variations and human cadence.\n"
            f"- Avoid pure English sentences and avoid pure Hindi (Devanagari-only) sentences.\n"
            f"- Use simple everyday words. Prefer Roman script for Hindi words so the TTS engine pronounces them naturally.\n"
            f"- STRICT: Never switch to any other language. Only Hinglish / Hindi-English mix."
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

