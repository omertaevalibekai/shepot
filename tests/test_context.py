"""Контекст окна: python -m pytest tests/test_context.py"""

from shepot import context, polish
from shepot.context import WindowContext


def test_terms_are_names_and_latin_words():
    ctx = WindowContext(title="Telegram — Алибек Омертаев",
                        text="Привет! Скинь макет в Figma. Завтра созвон с Маратом по AdaptAI.")
    found = context.terms(ctx)
    assert {"Telegram", "Алибек", "Омертаев", "Figma", "Маратом", "AdaptAI"} <= set(found)
    assert "Привет" not in found and "Завтра" not in found      # просто начало фразы


def test_terms_have_no_duplicates_and_respect_limit():
    ctx = WindowContext(text=" ".join(f"Term{i} Term{i}" for i in range(50)))
    found = context.terms(ctx, limit=10)
    assert len(found) == 10 and len(set(found)) == 10


def test_never_reads_terminals_or_password_managers(monkeypatch):
    monkeypatch.setattr(context.selection, "document_text",
                        lambda limit=0: (_ for _ in ()).throw(AssertionError("читать нельзя")))
    for app in ("windowsterminal.exe", "code.exe", "keepassxc.exe", ""):
        assert not context.capture(app, 123)


def test_capture_keeps_the_end_of_long_text(monkeypatch):
    monkeypatch.setattr(context, "window_title", lambda hwnd: "Gmail")
    monkeypatch.setattr(context.selection, "document_text", lambda limit=0: "а" * 3000 + "конец")
    ctx = context.capture("chrome.exe", 1)
    assert ctx.title == "Gmail"
    assert ctx.text.endswith("конец") and len(ctx.text) <= context.MAX_TEXT_CHARS + 1


def test_stt_hint_unchanged_without_context():
    assert context.stt_hint("база", WindowContext()) == "база"


def test_stt_hint_ends_with_previous_speech():
    hint = context.stt_hint("база", WindowContext(text="Пишу Марату"), previous="и ещё одно")
    assert "Марату" in hint and hint.endswith("и ещё одно")


def test_polish_note_reaches_system_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(polish, "_call", lambda cfg, system, text: seen.setdefault("s", system) and "ок")

    class Cfg(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    note = context.polish_note(WindowContext(title="Slack", text="Обсуждаем релиз"))
    polish.polish("текст", Cfg(mode="clean", llm_provider="openai"), note)
    assert "<window_title>Slack</window_title>" in seen["s"]
    assert "Never repeat it" in seen["s"]
    assert context.polish_note(WindowContext()) == ""
