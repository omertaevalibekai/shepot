"""Язык интерфейса: русский или английский.

Строки в коде пишутся по-русски и оборачиваются в t(): по-русски они
показываются как есть, по-английски — берутся из словаря i18n_en.EN.
Строки, которой нет в словаре, показываются по-русски, поэтому забытый
перевод виден, но ничего не ломает (тест test_i18n ловит такие строки).

Язык выбирается в настройках; «auto» — по языку Windows: русский, казахский,
украинский и белорусский интерфейс → русский, всё остальное → английский.
"""

from __future__ import annotations

import ctypes

from .i18n_en import EN

LANGUAGES = (("auto", "Как в Windows"), ("ru", "Русский"), ("en", "English"))
RU_FAMILY = {0x19, 0x3F, 0x22, 0x23}      # русский, казахский, украинский, белорусский

_lang = "ru"


def system_language() -> str:
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except Exception:
        return "ru"
    return "ru" if (lang_id & 0x3FF) in RU_FAMILY else "en"


def set_language(choice: str) -> str:
    """choice: auto | ru | en. Возвращает язык, который стал активным."""
    global _lang
    _lang = system_language() if choice not in ("ru", "en") else choice
    return _lang


def language() -> str:
    return _lang


def t(text: str, **values) -> str:
    """Строка на языке интерфейса; {имя} в ней подставляются из values."""
    if _lang == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text


def plural(n: int, ru_forms: tuple[str, str, str], en_forms: tuple[str, str]) -> str:
    """«1 слово / 3 слова / 5 слов» или «1 word / 5 words» — только само слово."""
    if _lang == "en":
        return en_forms[0] if n == 1 else en_forms[1]
    if n % 10 == 1 and n % 100 != 11:
        return ru_forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return ru_forms[1]
    return ru_forms[2]
