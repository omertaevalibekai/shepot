"""Исправления: Anthropic без beta-параметров, таймауты клиентов."""

from types import SimpleNamespace

from shepot import polish, providers


class _Messages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = SimpleNamespace(type="text", text="готово")
        return SimpleNamespace(content=[block], stop_reason="end_turn")


def test_anthropic_plain_messages(monkeypatch):
    messages = _Messages()
    client = SimpleNamespace(messages=messages)    # без .beta: старый SDK тоже подойдёт
    monkeypatch.setattr(polish, "anthropic_client", lambda: client)
    cfg = {"anthropic_model": "m"}
    assert polish._anthropic(SimpleNamespace(get=cfg.get), "sys", "text") == "готово"
    assert set(messages.kwargs) == {"model", "max_tokens", "system", "messages"}


def test_timeout_is_bounded():
    timeout = providers._timeout()
    assert timeout.connect == 10.0 and timeout.read == 120.0
