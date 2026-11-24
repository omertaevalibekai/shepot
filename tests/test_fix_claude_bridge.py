"""Мост Claude Code: выключенный остров, wait не получает ответ, залипшие сессии."""

import os
import time

from shepot import claude_bridge, claude_reply
from shepot.claude_bridge import Bridge, Session


def test_permission_returns_immediately_when_island_off():
    b = Bridge()
    b.ask_permissions = False
    t0 = time.monotonic()
    assert b.handle("PermissionRequest", {"session_id": "a", "tool_name": "Bash"}) == {}
    assert time.monotonic() - t0 < 1 and not b.pending


def test_reply_target_skips_wait():
    b = Bridge()
    b.sessions["a"] = Session("a", "a", state="wait", pid=1)
    assert b.reply_target() is None
    b.sessions["b"] = Session("b", "b", state="think", pid=1)
    assert b.reply_target().id == "b"


def test_snapshot_cleans_stuck_and_dead(monkeypatch):
    monkeypatch.setattr(claude_reply, "pid_alive", lambda pid: pid != 999)
    b = Bridge()
    old = time.monotonic() - claude_bridge.STUCK_S - 5
    b.sessions["t"] = Session("t", "t", state="think", pid=1, updated=old)
    b.sessions["w"] = Session("w", "w", state="wait", pid=1, updated=old)
    b.sessions["fresh"] = Session("fresh", "f", state="think", pid=1)
    b.sessions["dead"] = Session("dead", "d", state="think", pid=999)
    snap = {s.id: s.state for s in b.snapshot()}
    assert snap == {"t": "idle", "w": "idle", "fresh": "think"}


def test_pid_alive():
    assert claude_reply.pid_alive(os.getpid())
    assert not claude_reply.pid_alive(0)
    assert not claude_reply.pid_alive(4000000)
