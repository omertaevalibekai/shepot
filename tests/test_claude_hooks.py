"""Хуки Шёпота в настройках Claude Code: свои добавляем и убираем, чужие не трогаем."""

import json

from shepot import claude_hooks


def test_install_keeps_foreign_hooks_and_uninstall_restores(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_hooks.claude_bridge, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(claude_hooks.claude_bridge, "TOKEN_FILE", tmp_path / "claude_token")
    settings = tmp_path / "settings.json"
    foreign = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "block-rm.sh"}]}]}}
    settings.write_text(json.dumps(foreign), encoding="utf-8")

    claude_hooks.install(settings)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "block-rm.sh"
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert set(claude_hooks.EVENTS) <= set(data["hooks"])
    assert claude_hooks.is_installed(settings)

    claude_hooks.install(settings)                      # повторно — без дублей
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert len(data["hooks"]["PreToolUse"]) == 2

    claude_hooks.uninstall(settings)
    assert json.loads(settings.read_text(encoding="utf-8")) == foreign
    assert not claude_hooks.is_installed(settings)
    assert list(tmp_path.glob("settings.json.shepot-backup-*"))
