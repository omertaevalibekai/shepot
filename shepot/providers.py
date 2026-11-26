"""Общие клиенты внешних API.

Клиент создаётся один раз на процесс: пул соединений переживает диктовки,
поэтому TLS-рукопожатие оплачивается только при первом обращении. Прогрев
на старте убирает и его — иначе первая диктовка занимает на несколько
секунд больше остальных.
"""

from __future__ import annotations

import threading

from . import secrets
from .i18n import t


class ProviderError(RuntimeError):
    pass


NO_OPENAI_KEY = (
    "Не указан ключ OpenAI. Откройте «Настройки → Ключи API» и вставьте его, "
    "либо переключите распознавание на локальное."
)
NO_ANTHROPIC_KEY = "Не указан ключ Anthropic. Откройте «Настройки → Ключи API»."

_openai = None
_openai_key = ""
_anthropic = None
_anthropic_key = ""
_lock = threading.RLock()
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 120.0


def _timeout(sdk=None):
    """Подключение — 10 с, ответ — 120 с (длинные WAV): зависшая сеть не держит диктовку минутами.

    httpx напрямую не импортируем: новые openai тянут httpx2. Берём Timeout из самого SDK,
    а если его нет — просто 120 с на всё.
    """
    try:
        return sdk.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    except Exception:
        return READ_TIMEOUT_S


def openai_client():
    """Клиент OpenAI. Пересоздаётся, если пользователь сменил ключ."""
    global _openai, _openai_key
    key = secrets.get(secrets.OPENAI)
    if not key:
        raise ProviderError(t(NO_OPENAI_KEY))
    with _lock:
        if _openai is None or key != _openai_key:
            import openai
            from openai import OpenAI

            _openai = OpenAI(api_key=key, max_retries=1, timeout=_timeout(openai))
            _openai_key = key
        return _openai


def anthropic_client():
    global _anthropic, _anthropic_key
    key = secrets.get(secrets.ANTHROPIC)
    if not key:
        raise ProviderError(t(NO_ANTHROPIC_KEY))
    with _lock:
        if _anthropic is None or key != _anthropic_key:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderError(
                    t("Не установлен пакет anthropic. Выполните: pip install anthropic")
                ) from exc
            _anthropic = anthropic.Anthropic(api_key=key, max_retries=1, timeout=_timeout(anthropic))
            _anthropic_key = key
        return _anthropic


def reset() -> None:
    """Забыть клиентов — вызывается после смены ключей в настройках."""
    global _openai, _openai_key, _anthropic, _anthropic_key
    with _lock:
        _openai, _openai_key = None, ""
        _anthropic, _anthropic_key = None, ""


def _shape_problem(key: str) -> str | None:
    """Очевидные проблемы ключа, которые видно без запроса к серверу."""
    if not key:
        return t("Ключ не заполнен.")
    if not key.isascii():
        return t("В ключе есть посторонние символы (кириллица или невидимые знаки) — "
                 "скопируйте его заново целиком.")
    if any(c.isspace() for c in key):
        return t("В ключе есть пробел или перенос строки — скопируйте его заново.")
    return None


def check_openai(key: str) -> tuple[bool, str]:
    """Проверяет ключ живым запросом. Возвращает (годен, сообщение)."""
    key = (key or "").strip()
    problem = _shape_problem(key)
    if problem:
        return False, problem
    try:
        from openai import OpenAI

        models = {m.id for m in OpenAI(api_key=key, max_retries=0).models.list()}
    except Exception as exc:
        return False, humanize(exc)
    missing = [m for m in ("gpt-4o-transcribe", "gpt-4o-mini") if m not in models]
    if missing:
        return True, t("Ключ рабочий, но этому проекту недоступны модели: {models}", models=", ".join(missing))
    return True, t("Ключ рабочий, нужные модели доступны.")


def check_anthropic(key: str) -> tuple[bool, str]:
    key = (key or "").strip()
    problem = _shape_problem(key)
    if problem:
        return False, problem
    try:
        import anthropic
    except ImportError:
        return False, t("Не установлен пакет anthropic (pip install anthropic).")
    try:
        anthropic.Anthropic(api_key=key, max_retries=0).models.list()
    except Exception as exc:
        return False, humanize(exc)
    return True, t("Ключ рабочий.")


def humanize(exc: Exception) -> str:
    """Короткое понятное сообщение вместо ответа SDK с JSON внутри.

    Исходная ошибка целиком уходит в журнал (errlog) там, где её поймали.
    """
    text = str(exc)
    lowered = text.lower()
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if "Timeout" in name:
        return t("Сервер не ответил вовремя — проверьте интернет и попробуйте ещё раз.")
    if "Connection" in name:
        return t("Нет связи с сервером — проверьте интернет.")
    if isinstance(status, int):
        if status == 401:
            return t("Ключ не принят — проверьте, что скопировали его целиком.")
        if status == 403:
            return t("Доступ запрещён: у ключа нет прав на эту модель или регион не поддерживается.")
        if status == 404:
            return t("Модель не найдена — проверьте её название в настройках.")
        if status == 429:
            if "insufficient_quota" in lowered or "quota" in lowered or "billing" in lowered:
                return t("Баланс API исчерпан — пополните счёт у провайдера.")
            return t("Слишком много запросов — подождите немного и повторите.")
        if status >= 500:
            return t("Сервер временно недоступен (код {code}) — попробуйте ещё раз.", code=status)
        body = getattr(exc, "body", None)
        reason = ""
        if isinstance(body, dict):
            error = body.get("error", body)
            reason = str(error.get("message", "")) if isinstance(error, dict) else str(error)
        return t("Запрос отклонён (код {code}): {reason}", code=status, reason=(reason or text)[:160])
    if "401" in text or "invalid_api_key" in lowered or "authentication" in lowered:
        return t("Ключ не принят — проверьте, что скопировали его целиком.")
    if "429" in text or "quota" in lowered:
        return t("Ключ верный, но лимит или баланс исчерпаны.")
    if "connect" in lowered or "timeout" in lowered or "network" in lowered:
        return t("Нет связи с сервером — проверьте интернет.")
    return text[:200]


def warm(cfg) -> None:
    """Открывает соединения заранее. Ошибки здесь не важны — молча глотаем."""
    if cfg.get("stt_backend") != "local" or cfg.get("llm_provider") == "openai":
        try:
            openai_client().models.list()
        except Exception:
            pass
    if cfg.get("llm_provider") == "anthropic":
        try:
            anthropic_client().models.list()
        except Exception:
            pass


def missing_key(cfg) -> str | None:
    """Какого ключа не хватает при текущих настройках (None — всё на месте)."""
    if cfg.get("stt_backend") != "local" and not secrets.get(secrets.OPENAI):
        return secrets.OPENAI
    if cfg.get("mode") != "raw":
        provider = cfg.get("llm_provider")
        if provider == "openai" and not secrets.get(secrets.OPENAI):
            return secrets.OPENAI
        if provider == "anthropic" and not secrets.get(secrets.ANTHROPIC):
            return secrets.ANTHROPIC
    return None
