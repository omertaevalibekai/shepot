"""Мост к Claude Code: локальный HTTP-сервер для хуков и список сессий.

Claude Code шлёт события хуков (type "http") на 127.0.0.1. Сервер ведёт
список сессий — кто думает, кто ждёт разрешения, кто закончил — и сообщает
острову через сигналы Qt. На PermissionRequest он ждёт решения из острова
и возвращает его Claude Code; не дождался — возвращает пустой ответ, и
Claude Code спрашивает в терминале как обычно.

Слушаем только 127.0.0.1 и требуем секретный заголовок: иначе любая
страница в браузере могла бы слать на localhost поддельные события.
"""

from __future__ import annotations

import json
import os
import secrets as pysecrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

from . import claude_reply
from .config import CONFIG_DIR

PORT = 47821
TOKEN_FILE = CONFIG_DIR / "claude_token"
DECISION_WAIT_S = 60.0         # дольше не держим: иначе терминал ждёт хук вместо обычного вопроса
REPLY_FRESH_S = 30 * 60        # отвечать голосом можно сессии, которая отзывалась не позже этого


def token() -> str:
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = pysecrets.token_urlsafe(24)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(value, encoding="utf-8")
    return value


@dataclass
class Session:
    id: str
    name: str
    state: str = "idle"            # think | wait | ask | done | idle
    action: str = ""
    started: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    pid: int = 0                   # процесс claude: в его консоль уходит голосовой ответ


@dataclass
class Permission:
    session_id: str
    session_name: str
    tool: str
    summary: str                   # что именно хочет сделать: команда, файл
    suggestions: list
    done: threading.Event = field(default_factory=threading.Event)
    decision: dict | None = None


def describe(tool: str, data: dict) -> str:
    """Коротко, что делает инструмент: «git push origin main», «hud.py»."""
    data = data or {}
    if tool == "Bash":
        return " ".join(str(data.get("command", "")).split())
    for key in ("file_path", "notebook_path", "path"):
        if data.get(key):
            return os.path.basename(str(data[key]).rstrip("\\/")) or str(data[key])
    for key in ("pattern", "url", "query", "description", "prompt"):
        if data.get(key):
            return " ".join(str(data[key]).split())
    return ""


def session_name(payload: dict) -> str:
    title = (payload.get("session_title") or "").strip()
    if title:
        return title
    cwd = (payload.get("cwd") or "").rstrip("\\/")
    if not cwd:
        return "Claude"
    if os.path.normcase(cwd) == os.path.normcase(os.path.expanduser("~")):
        return "~"
    return os.path.basename(cwd)


class Bridge(QObject):
    """Список сессий и очередь разрешений; сигналы приходят в поток интерфейса."""

    changed = Signal()                    # список сессий изменился
    finished = Signal(str, str)           # имя сессии, первая строка ответа
    permissionAsked = Signal(object)      # Permission
    permissionClosed = Signal(object)     # Permission: решили в терминале или истекло

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self.pending: list[Permission] = []
        self._server: ThreadingHTTPServer | None = None
        self.error = ""

    # ---------------------------------------------------------------- сервер
    def start(self, port: int = PORT) -> bool:
        secret = token()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):          # без вывода в консоль
                pass

            def do_POST(self):
                if self.headers.get("X-Shepot-Token") != secret or not self.path.startswith("/claude/"):
                    self.send_response(403)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    payload = {}
                event = self.path.rsplit("/", 1)[-1]
                try:
                    reply = bridge.handle(event, payload, self.client_address[1])
                except Exception:
                    reply = {}                       # сломаться должен остров, а не Claude Code
                body = json.dumps(reply).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            self.error = f"Порт {port} занят: {exc}"
            return False
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="shepot-claude-bridge").start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    # -------------------------------------------------------------- события
    def handle(self, event: str, p: dict, client_port: int = 0) -> dict:
        sid = p.get("session_id") or "?"
        if p.get("agent_id"):
            return {}                                # субагенты не меняют состояние сессии
        if event == "SessionEnd":
            with self._lock:
                self.sessions.pop(sid, None)
            self._close_pending(sid)
            self.changed.emit()
            return {}
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                s = self.sessions[sid] = Session(sid, session_name(p))
            elif p.get("session_title"):
                s.name = p["session_title"]
            s.updated = time.monotonic()
            need_pid = not s.pid
        if need_pid and client_port:
            # хук шлёт сам процесс claude: по порту соединения узнаём, кто это
            try:
                s.pid = claude_reply.owner_pid(client_port, PORT)
            except Exception:
                pass

        if event == "SessionStart":
            pass
        elif event == "UserPromptSubmit":
            self._set(s, "think", "думает", restart=True)
            self._close_pending(sid)
        elif event == "PreToolUse":
            self._set(s, "think", _action(p.get("tool_name", ""), p.get("tool_input")))
        elif event == "PostToolUse":
            self._close_pending(sid)                 # инструмент выполнился — вопрос решён
            if s.state != "think":
                self._set(s, "think", s.action)
        elif event == "Notification":
            kind = p.get("notification_type")
            if kind == "idle_prompt":
                self._set(s, "ask", "ждёт ответа")
            elif kind == "permission_prompt" and s.state != "wait":
                self._set(s, "wait", "ждёт разрешения в терминале")
        elif event == "Stop":
            self._set(s, "done", "готово")
            self._close_pending(sid)
            line = " ".join((p.get("last_assistant_message") or "").split())
            self.finished.emit(s.name, line)
        elif event == "PermissionRequest":
            return self._ask_permission(s, p)
        self.changed.emit()
        return {}

    def _set(self, s: Session, state: str, action: str, restart: bool = False) -> None:
        if restart or (state == "think" and s.state != "think"):
            s.started = time.monotonic()
        s.state, s.action = state, action

    def _ask_permission(self, s: Session, p: dict) -> dict:
        tool = p.get("tool_name", "")
        request = Permission(s.id, s.name, tool, describe(tool, p.get("tool_input")),
                             p.get("permission_suggestions") or [])
        with self._lock:
            self.pending.append(request)
        self._set(s, "wait", "ждёт разрешения")
        self.changed.emit()
        self.permissionAsked.emit(request)
        request.done.wait(DECISION_WAIT_S)
        with self._lock:
            if request in self.pending:
                self.pending.remove(request)
        if request.decision is None:
            self.permissionClosed.emit(request)      # не успели — спросит терминал
            return {}
        self._set(s, "think", s.action)
        self.changed.emit()
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": request.decision}}

    def decide(self, request: Permission, allow: bool, always: bool = False) -> None:
        """Ответ из острова."""
        if allow:
            decision: dict = {"behavior": "allow"}
            if always and request.suggestions:
                decision["updatedPermissions"] = request.suggestions
        else:
            decision = {"behavior": "deny", "message": "Пользователь отказал в острове Шёпота."}
        request.decision = decision
        request.done.set()

    def _close_pending(self, sid: str) -> None:
        with self._lock:
            stale = [r for r in self.pending if r.session_id == sid]
        for request in stale:
            request.done.set()                       # решение без нас: терминал уже ответил

    def reply_target(self) -> Session | None:
        """Кому уйдёт голосовой ответ: сессия, которая ждёт ответа или только что закончила.

        Ждущая ответа важнее закончившей; среди равных — та, что отозвалась последней.
        Думающей сессии тоже можно ответить: Claude Code поставит сообщение в очередь.
        """
        now = time.monotonic()
        rank = {"ask": 0, "done": 1, "wait": 2, "think": 3}
        with self._lock:
            alive = [s for s in self.sessions.values()
                     if s.pid and s.state in rank and now - s.updated <= REPLY_FRESH_S]
        if not alive:
            return None
        return min(alive, key=lambda s: (rank[s.state], -s.updated))

    def replied(self, s: Session) -> None:
        """Ответ ушёл в терминал: сессия снова думает (UserPromptSubmit подтвердит)."""
        self._set(s, "think", "думает", restart=True)
        s.updated = time.monotonic()
        self.changed.emit()

    def snapshot(self) -> list[Session]:
        with self._lock:
            now = time.monotonic()
            for s in self.sessions.values():
                if s.state == "done" and now - s.updated > 600:
                    s.state, s.action = "idle", ""
            return sorted(self.sessions.values(), key=lambda x: x.started)


def _action(tool: str, data) -> str:
    detail = describe(tool, data or {})
    names = {"Read": "читает", "Edit": "правит", "Write": "пишет", "Bash": "запускает",
             "Grep": "ищет", "Glob": "ищет файлы", "WebFetch": "читает страницу",
             "WebSearch": "ищет в сети", "Task": "запустил агента", "Agent": "запустил агента"}
    verb = names.get(tool, tool)
    return f"{verb} {detail}".strip() if detail else verb
