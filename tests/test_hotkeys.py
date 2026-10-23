"""Разбор привязок горячих клавиш: python -m pytest tests/test_hotkeys.py"""

import pytest

from shepot.hotkeys import parse_binding


@pytest.mark.parametrize("text, kind, mods, vk", [
    ("ctrl+space", "reg", 0x2, 0x20),
    ("win+j", "reg", 0x8, 0x4A),
    ("ctrl+;", "reg", 0x2, 0xBA),
    ("f9", "reg", 0, 0x78),
    ("alt+num lock", "reg", 0x1, 0x90),
    ("vk 7c", "reg", 0, 0x7C),
    ("right ctrl", "key", 0, 0xA3),
    ("mouse forward", "mouse", 0, 0x06),
])
def test_known_bindings(text, kind, mods, vk):
    binding = parse_binding(text)
    assert (binding.kind, binding.mods, binding.vk) == (kind, mods, vk)


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        parse_binding("ctrl+нет такой")
