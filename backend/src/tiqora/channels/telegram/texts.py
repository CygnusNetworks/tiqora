"""Customer-facing Telegram texts: built-in English/German defaults plus the
admin override from the channel settings.

Every text the bot sends on its own (greeting, consent prompt, the identity
check's "not found"/handoff messages) used to be hard-coded German, which
only fits a German deployment and a German-writing customer. Now:

* an admin-configured value (``channel.telegram.<key>``) always wins — it is
  one text for every customer, exactly as before;
* otherwise the built-in default in the customer's language is used: German
  for German, English for everything else (the neutral default).

The customer's language comes from :func:`customer_language` — what they
wrote if that is clear, else their Telegram client's ``language_code``.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.reply_language import LANGUAGE_PROFILES, detect_reply_language_detailed
from tiqora.channels.common import channel_setting

CHANNEL_NAME: Final = "telegram"

LANG_DE: Final = "de"
LANG_EN: Final = "en"

DEFAULT_TEXTS: Final[dict[str, dict[str, str]]] = {
    "start_text": {
        LANG_DE: (
            "Hallo {first_name}! 👋 Schildere mir bitte kurz dein Anliegen – ich lege "
            "dafür einen neuen Vorgang an."
        ),
        LANG_EN: (
            "Hi {first_name}! 👋 Please briefly describe your request – I'll open a new "
            "case for it."
        ),
    },
    "consent_text": {
        LANG_DE: (
            "Bevor wir Ihr Anliegen bearbeiten können, benötigen wir Ihre Zustimmung zur "
            "Verarbeitung Ihrer Daten (Chat-ID, Name, Nachrichteninhalt) zur Bearbeitung "
            "Ihrer Anfrage. Bitte bestätigen Sie über den Button unten. Senden Sie Ihr "
            "Anliegen danach bitte erneut.\n\n✅ Zustimmen"
        ),
        LANG_EN: (
            "Before we can handle your request, we need your consent to process your "
            "data (chat ID, name, message content) for this purpose. Please confirm "
            "with the button below, then send your request again.\n\n✅ Agree"
        ),
    },
    "consent_confirmed_text": {
        LANG_DE: (
            "Vielen Dank! Ihre Zustimmung wurde gespeichert. Bitte senden Sie jetzt Ihr Anliegen."
        ),
        LANG_EN: "Thank you! Your consent has been saved. Please send your request now.",
    },
    "consent_button_label": {
        LANG_DE: "✅ Zustimmen",
        LANG_EN: "✅ Agree",
    },
    "identity_no_match_text": {
        LANG_DE: (
            "Mit diesen Angaben ({fields}) konnte ich dich leider nicht finden. "
            "Bitte prüf sie noch einmal und schick sie mir erneut."
        ),
        LANG_EN: (
            "I couldn't find you with these details ({fields}). "
            "Please check them and send them again."
        ),
    },
    "identity_handoff_text": {
        LANG_DE: (
            "Ich konnte dich leider nicht zuordnen. Ich gebe dein Anliegen an unser Team "
            "weiter, jemand meldet sich bei dir."
        ),
        LANG_EN: (
            "I'm sorry, I couldn't match you to an account. I'm passing your request on "
            "to our team and someone will get back to you."
        ),
    },
}

# System-prompt addendum for AI replies on this channel — a chat reads as a
# conversation, not a letter. One text for every language: it only describes
# style, the reply language is set separately (tiqora.ai.reply_language).
DEFAULT_TONE_PROMPT: Final = (
    "This is a Telegram chat: address the customer informally and by first name "
    "(in German, consistently use 'du'). Keep it short, friendly and chat-like – "
    "no formal letter phrases and no salutations such as 'Dear Sir or Madam' or "
    "'Sehr geehrte…'."
)


def normalize_language(code: str | None) -> str | None:
    """``"de-AT"``/``"de"`` → ``"de"``; any other non-empty code → its primary
    subtag, lower-case; empty → ``None``."""
    if not code:
        return None
    primary = code.strip().replace("_", "-").split("-", 1)[0].lower()
    return primary or None


def customer_language(text: str | None, language_code: str | None) -> str | None:
    """What the customer most likely reads: the language of ``text`` when the
    stopword detector is confident, else the Telegram client language, else
    ``None`` (callers fall back to English)."""
    detection = detect_reply_language_detailed(
        None, text, candidates=list(LANGUAGE_PROFILES), default=""
    )
    if not detection.used_fallback:
        return detection.language
    return normalize_language(language_code)


def default_text(key: str, language: str | None) -> str:
    variants = DEFAULT_TEXTS[key]
    return variants[LANG_DE] if language == LANG_DE else variants[LANG_EN]


async def telegram_text(session: AsyncSession, key: str, language: str | None) -> str:
    """The admin-configured ``key`` if set, else the built-in default for
    ``language`` (German for ``"de"``, English otherwise)."""
    configured = await channel_setting(session, CHANNEL_NAME, key)
    return configured or default_text(key, language)


async def telegram_tone_prompt(session: AsyncSession) -> str:
    configured = await channel_setting(session, CHANNEL_NAME, "tone_prompt")
    return configured or DEFAULT_TONE_PROMPT


__all__ = [
    "DEFAULT_TEXTS",
    "DEFAULT_TONE_PROMPT",
    "LANG_DE",
    "LANG_EN",
    "customer_language",
    "default_text",
    "normalize_language",
    "telegram_text",
    "telegram_tone_prompt",
]
