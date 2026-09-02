import re

POSITIVE_PATTERNS = [
    r"\b(interested|interest|tell me more|know more|more details|more info)\b",
    r"\b(book|booking|schedule|scheduling|appointment|slot|reschedule)\b",
    r"\b(price|pricing|cost|charges|fees|discount|offer|plan)\b",
    r"\b(available|availability|timing|when.*available|kab.*milega)\b",
    r"\b(how.*work|how.*process|what.*include|benefits|features)\b",
    r"\b(yes.*interested|haan.*batao|bataiye|samjhao|explain)\b",
    r"\b(send.*details|share.*details|whatsapp|message me)\b",
    r"\b(continue|go ahead|aage batao|boliye|proceed)\b",
]

NEGATIVE_PATTERNS = [
    r"\b(not interested|no interest|no need|not needed)\b",
    r"\b(no thanks|no thank you|not now|not required)\b",
    r"\b(don't call|do not call|call.*later|busy now)\b",
    r"\b(wrong number|galat number)\b",
    r"\b(stop calling|remove.*number)\b",
]

POSITIVE_RE = re.compile("|".join(POSITIVE_PATTERNS), re.IGNORECASE)
NEGATIVE_RE = re.compile("|".join(NEGATIVE_PATTERNS), re.IGNORECASE)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_positive_intent(text: str) -> tuple[bool, str]:
    norm = _normalize(text)
    if not norm or len(norm) < 8:
        return False, ""
    if NEGATIVE_RE.search(norm):
        return False, "negative veto"
    m = POSITIVE_RE.search(norm)
    if m:
        return True, f"keyword:{m.group(0).strip()}"
    if "?" in text and len(norm) > 20:
        return True, "engagement:question"
    return False, ""


def should_extend_from_history(messages: list, min_user_turns: int = 1) -> tuple[bool, str]:
    user_texts = []
    for msg in messages:
        role = getattr(msg, "role", "")
        r = str(role).lower() if not hasattr(role, "name") else str(role.name).lower()
        if r not in ("user", "caller"):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        content = str(content).strip()
        if not content or content.startswith("[System:"):
            continue
        user_texts.append(content)
    if len(user_texts) < min_user_turns:
        return False, ""
    recent = " ".join(user_texts[-3:])
    if NEGATIVE_RE.search(_normalize(recent)):
        return False, ""
    is_pos, reason = is_positive_intent(recent)
    if is_pos:
        return True, reason
    combined = " ".join(user_texts)
    if len(combined.split()) >= 12 and len(user_texts) >= 2:
        if POSITIVE_RE.search(_normalize(combined)):
            return True, "aggregated_positive"
    return False, ""
