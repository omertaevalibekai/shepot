"""Подключение и отключение хуков Шёпота в настройках Claude Code (~/.claude/settings.json).

Трогаем только свои записи — их узнаём по адресу моста. Перед записью
кладём рядом резервную копию. Пока Шёпот выключен, Claude Code работает
как обычно, но пишет об ошибке хука; поэтому есть «Отключить».
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import claude_bridge

SETTINGS = Path.home() / ".claude" / "settings.json"
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
          "Notification", "Stop", "SessionEnd")
MARK = f"127.0.0.1:{claude_bridge.PORT}/claude/"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8") or "{}")


def _is_ours(group: dict) -> bool:
    return any(MARK in str(h.get("url", "")) for h in group.get("hooks", []))


def _strip(data: dict) -> dict:
    hooks = data.get("hooks") or {}
    for event in list(hooks):
        kept = [g for g in hooks[event] if not _is_ours(g)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(f"settings.json.shepot-backup-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def is_installed(path: Path = SETTINGS) -> bool:
    try:
        data = _load(path)
    except (OSError, ValueError):
        return False
    return any(_is_ours(g) for groups in (data.get("hooks") or {}).values() for g in groups)


def install(path: Path = SETTINGS) -> None:
    data = _strip(_load(path))
    secret = claude_bridge.token()
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        hooks.setdefault(event, []).append({"hooks": [{
            "type": "http",
            "url": f"http://{MARK}{event}",
            "headers": {"X-Shepot-Token": secret},
            # разрешение ждёт человека; остальное — мгновенный ответ
            "timeout": int(claude_bridge.DECISION_WAIT_S) + 15 if event == "PermissionRequest" else 5,
        }]})
    _save(path, data)


def uninstall(path: Path = SETTINGS) -> None:
    if path.exists():
        _save(path, _strip(_load(path)))
