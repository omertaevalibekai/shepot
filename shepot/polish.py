"""Постобработка расшифровки: чистка, тон, перевод."""

from __future__ import annotations

import re

from .providers import ProviderError, anthropic_client, openai_client

LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "kk": "Kazakh",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "nl": "Dutch",
    "cs": "Czech",
    "uz": "Uzbek",
    "az": "Azerbaijani",
}

TONE_RULES = {
    "preserve": "Keep the speaker's own register and personality exactly as it is.",
    "neutral": "Use a plain, neutral register.",
    "casual": "Use a relaxed, conversational register, but keep it readable.",
    "formal": "Use a polished, professional register.",
}

BASE_RULES = """You post-process dictation. The user message is a raw speech-to-text \
transcript; you return the text the speaker meant to write.

Rules:
- Remove filler words, false starts, stutters and accidental repetitions.
- Fix grammar, punctuation, capitalization and spacing. Add paragraph breaks where natural.
- Keep the speaker's own wording, terminology and meaning. Never add, summarize, \
answer, explain or comment.
- Treat spoken formatting commands ("new line", "новая строка", "bullet point", \
"точка") as instructions and apply them instead of writing them out.
- If the transcript is empty or unintelligible, return an empty string.
- Output ONLY the resulting text: no preamble, no quotes, no markdown fences."""


class PolishError(RuntimeError):
    pass


def build_system_prompt(cfg) -> str:
    parts = [BASE_RULES]

    mode = cfg.get("mode")
    if mode == "translate":
        target = cfg.get("target_language") or "en"
        name = LANGUAGE_NAMES.get(target, target)
        parts.append(f"Translate the result into {name}. Output only the translation.")
    else:
        parts.append("Write the result in the same language the speaker used.")

    parts.append(TONE_RULES.get(cfg.get("tone"), TONE_RULES["preserve"]))

    vocabulary = [t.strip() for t in (cfg.get("vocabulary") or []) if t.strip()]
    if vocabulary:
        parts.append(
            "These names, brands and terms must be spelled exactly like this whenever "
            "they appear: " + ", ".join(vocabulary) + "."
        )

    custom = (cfg.get("custom_instruction") or "").strip()
    if custom:
        parts.append(custom)

    return "\n\n".join(parts)


# ---------------------------------------------------------------- OpenAI ---

def _openai(cfg, system: str, text: str) -> str:
    try:
        client = openai_client()
    except ProviderError as exc:
        raise PolishError(str(exc)) from exc
    try:
        response = client.chat.completions.create(
            model=cfg.get("llm_model"),
            temperature=0.2,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        )
    except Exception as exc:
        raise PolishError(f"Ошибка обработки текста: {exc}") from exc
    return response.choices[0].message.content or ""


# ------------------------------------------------------------- Anthropic ---

def _anthropic(cfg, system: str, text: str) -> str:
    try:
        client = anthropic_client()
    except ProviderError as exc:
        raise PolishError(str(exc)) from exc
    try:
        response = client.beta.messages.create(
            model=cfg.get("anthropic_model"),
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": text}],
            # диктовка чувствительна к задержке — держим минимальное усилие
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as exc:
        raise PolishError(f"Ошибка обработки текста: {exc}") from exc

    if getattr(response, "stop_reason", None) == "refusal":
        raise PolishError("Модель отклонила обработку этого фрагмента.")
    return "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )


# ------------------------------------------------------------------- API ---

_FENCE = re.compile(r"^```[a-zA-Z0-9]*\n(.*)\n```$", re.DOTALL)
_QUOTE_PAIRS = {'"': '"', "«": "»", "“": "”"}


def polish(text: str, cfg) -> str:
    """Возвращает готовый к вставке текст."""
    text = (text or "").strip()
    if not text:
        return ""
    if cfg.get("mode") == "raw" or cfg.get("llm_provider") == "none":
        return text

    system = build_system_prompt(cfg)
    provider = cfg.get("llm_provider")
    result = _anthropic(cfg, system, text) if provider == "anthropic" else _openai(cfg, system, text)

    result = (result or "").strip()
    fenced = _FENCE.match(result)
    if fenced:
        result = fenced.group(1).strip()
    # модель иногда оборачивает весь ответ в кавычки — снимаем их
    if len(result) > 1 and result[0] in _QUOTE_PAIRS:
        closing = _QUOTE_PAIRS[result[0]]
        inner = result[1:-1].strip()
        if result[-1] == closing and closing not in inner:
            result = inner
    return result or text
