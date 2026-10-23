"""Окно настроек без показа на экране: python -m pytest tests/test_settings_ui.py

Конфиг пишется во временную папку; автозапуск, ключи и пробная регистрация
горячих клавиш подменены — настоящие настройки Windows тест не трогает.
"""

import json

import pytest
from PySide6.QtWidgets import QApplication

from shepot import config, i18n, settings_ui


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_ui.startup, "set_enabled", lambda on: None)
    monkeypatch.setattr(settings_ui.startup, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_ui.secrets, "set_key", lambda name, value: None)
    monkeypatch.setattr(settings_ui.secrets, "stored", lambda name: "")
    monkeypatch.setattr(settings_ui.hotkeys, "try_register", lambda text: None)
    monkeypatch.setattr(settings_ui.claude_hooks, "is_installed", lambda: False)
    monkeypatch.setattr(settings_ui.audio, "list_input_devices", lambda: [(3, "USB mic")])
    i18n.set_language("ru")
    cfg = config.Config()
    cfg.set("vocabulary", ["Figma", "AdaptAI"])
    cfg.set("replacements", {"фигму": "Figma", "эдапт": "AdaptAI"})
    dlg = settings_ui.SettingsDialog(cfg)
    yield dlg, cfg, tmp_path / "config.json"
    i18n.set_language("ru")


def saved(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_values_are_saved_on_done(dialog):
    dlg, _cfg, path = dialog
    dlg.mode.set_value("translate")
    dlg.insert_method.set_value("type")
    dlg.window_context.setChecked(True)
    dlg.custom_instruction.setText("  без эмодзи ")
    dlg.device.setCurrentIndex(dlg.device.findData(3))
    dlg._accept()
    data = saved(path)
    assert data["mode"] == "translate" and data["insert_method"] == "type"
    assert data["window_context"] is True and data["custom_instruction"] == "без эмодзи"
    assert data["input_device"] == 3 and data["hotkey"] == "ctrl+space"


def test_removed_term_forgets_its_learned_replacement(dialog):
    dlg, _cfg, path = dialog
    dlg.vocabulary._remove("AdaptAI")
    dlg._accept()
    data = saved(path)
    assert data["vocabulary"] == ["Figma"]
    assert data["replacements"] == {"фигму": "Figma"}


def test_rows_follow_choices(dialog):
    dlg, _cfg, _path = dialog
    dlg.mode.set_value("translate")
    dlg._sync()
    assert not dlg.target_row.isHidden()
    dlg.mode.set_value("raw")
    dlg._sync()
    assert dlg.target_row.isHidden() and dlg.tone_row.isHidden()
    dlg.hotkey_mode.set_value("pair")
    dlg._sync()
    assert not dlg.stop_row.isHidden()


def test_bad_hotkey_keeps_window_open(dialog, monkeypatch):
    dlg, _cfg, path = dialog
    monkeypatch.setattr(settings_ui.QMessageBox, "warning", lambda *a, **k: None)
    dlg.hotkey_capture.set_value("a")                     # без модификатора — мешала бы печатать
    dlg._accept()
    assert not path.exists()
    assert dlg._section == "keys"


def test_language_switch_saves_and_rebuilds_in_english(dialog):
    dlg, cfg, path = dialog
    dlg._open("text")
    dlg.mode.set_value("raw")
    dlg._language_changed("en")
    assert saved(path)["ui_language"] == "en" and saved(path)["mode"] == "raw"
    assert i18n.language() == "en"
    assert dlg.nav["general"].text() == "General"
    assert dlg._section == "text"
    assert dlg.mode.value() == "raw"                      # пересобранное окно читает сохранённое


def test_search_filters_sections(dialog):
    dlg, _cfg, _path = dialog
    dlg.search.setText("прыжок")
    visible = [k for k, b in dlg.nav.items() if not b.isHidden()]
    assert visible == ["windows"]
    dlg.search.setText("zzz")
    assert not dlg.nothing.isHidden()
