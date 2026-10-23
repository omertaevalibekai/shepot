"""Постобработка без обращения к API: python -m pytest tests/test_polish.py"""

import pytest

from shepot import polish


class Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_fences_and_quotes_are_stripped():
    assert polish._clean("```text\nПривет, мир.\n```") == "Привет, мир."
    assert polish._clean("«Готово.»") == "Готово."
    assert polish._clean('"Он сказал "да" и ушёл"') == '"Он сказал "да" и ушёл"'


def test_prompt_has_translation_vocabulary_and_custom_rule():
    prompt = polish.build_system_prompt(Cfg(
        mode="translate", target_language="kk", tone="formal",
        vocabulary=["AdaptAI", " "], custom_instruction="Обращайся на вы",
    ))
    assert "Translate the result into Kazakh" in prompt
    assert polish.TONE_RULES["formal"] in prompt
    assert "AdaptAI." in prompt and "Обращайся на вы" in prompt


def test_raw_mode_skips_the_model(monkeypatch):
    monkeypatch.setattr(polish, "_call", lambda *a: pytest.fail("модель не нужна"))
    assert polish.polish("  ну эээ привет  ", Cfg(mode="raw")) == "ну эээ привет"


def test_polish_falls_back_to_transcript_on_empty_answer(monkeypatch):
    monkeypatch.setattr(polish, "_call", lambda cfg, system, text: "  ")
    assert polish.polish("привет", Cfg(mode="clean", llm_provider="openai")) == "привет"


def test_edit_wraps_selection_and_request(monkeypatch):
    seen = {}

    def fake_call(cfg, system, message):
        seen["message"] = message
        return "«Короче.»"

    monkeypatch.setattr(polish, "_call", fake_call)
    result = polish.edit("Длинный текст", "Короче", Cfg(llm_provider="anthropic"))
    assert result == "Короче."
    assert "<selection>\nДлинный текст\n</selection>" in seen["message"]


def test_edit_without_provider_still_uses_openai(monkeypatch):
    monkeypatch.setattr(polish, "_openai", lambda cfg, system, message: "Новый")
    assert polish.edit("Старый", "Перепиши", Cfg(llm_provider="none")) == "Новый"


def test_edit_needs_a_request():
    with pytest.raises(polish.PolishError):
        polish.edit("текст", "  ", Cfg())
