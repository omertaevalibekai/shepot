"""Перевод интерфейса: python -m pytest tests/test_i18n.py

Каждая русская строка, которую код показывает через t(), должна быть в
английском словаре, и подстановки {…} в переводе — те же, что в оригинале.
"""

import ast
import re
import string
from pathlib import Path

import pytest

from shepot import i18n
from shepot.i18n_en import EN

ROOT = Path(__file__).resolve().parent.parent / "shepot"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# словари, чьи значения показываются через t() в момент показа
SHOWN_DICTS = {"LABELS", "STATE_TEXT", "MODE_TITLES", "PART_LABELS", "APP_NAMES", "_RU", "names",
               "APP_TITLE", "NO_OPENAI_KEY", "NO_ANTHROPIC_KEY"}


def shown_strings() -> set[str]:
    found = set()
    for path in ROOT.glob("*.py"):
        if path.name == "i18n_en.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in SHOWN_DICTS for t in node.targets):
                for const in ast.walk(node.value):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str):
                        found.add(const.value)
    return {s for s in found if CYRILLIC.search(s)}


def fields(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_every_shown_string_has_a_translation():
    missing = sorted(shown_strings() - EN.keys())
    assert not missing, "нет перевода:\n" + "\n".join(missing)


@pytest.mark.parametrize("ru", sorted(EN))
def test_placeholders_match(ru):
    assert fields(ru) == fields(EN[ru])


def test_switching_language():
    try:
        assert i18n.set_language("en") == "en"
        assert i18n.t("Ответ ушёл: {session}", session="shepot") == "Reply sent: shepot"
        assert i18n.plural(5, ("слово", "слова", "слов"), ("word", "words")) == "words"
        assert i18n.set_language("ru") == "ru"
        assert i18n.t("Ответ ушёл: {session}", session="shepot") == "Ответ ушёл: shepot"
        assert i18n.plural(22, ("слово", "слова", "слов"), ("word", "words")) == "слова"
        assert i18n.set_language("auto") in ("ru", "en")
    finally:
        i18n.set_language("ru")


def test_untranslated_string_falls_back_to_russian():
    try:
        i18n.set_language("en")
        assert i18n.t("Такой строки нет") == "Такой строки нет"
    finally:
        i18n.set_language("ru")
