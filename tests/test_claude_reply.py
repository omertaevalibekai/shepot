"""Голосовой ответ Claude Code: python -m pytest tests/test_claude_reply.py

Доставка проверяется на настоящей скрытой консоли: без нажатий и без буфера обмена.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

from shepot import claude_bridge, claude_reply


def test_records_flatten_newlines_and_end_with_enter():
    recs = claude_reply.records("да\nделай")
    chars = [r.Event.KeyEvent.UnicodeChar for r in recs if r.Event.KeyEvent.bKeyDown]
    assert "".join(chars) == "да делай\r"
    assert recs[-1].Event.KeyEvent.wVirtualKeyCode == claude_reply.VK_RETURN
    assert len(recs) == 2 * len("да делай\r")                  # нажатие и отпускание


def test_records_without_submit_have_no_enter():
    recs = claude_reply.records("ok", submit=False)
    assert all(r.Event.KeyEvent.UnicodeChar != "\r" for r in recs)


def test_send_reaches_a_real_console(tmp_path):
    out = tmp_path / "line.txt"
    reader = f"line=input();open(r'{out}','w',encoding='utf-8').write(line)"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0                                          # скрытое окно
    proc = subprocess.Popen([sys.executable, "-c", reader],
                            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    try:
        time.sleep(0.8)
        claude_reply.send(proc.pid, "Проверь тесты,\nпотом закоммить")
        proc.wait(10)
    finally:
        proc.kill()
    assert out.read_text(encoding="utf-8") == "Проверь тесты, потом закоммить"
    # своя консоль (если была) не потеряна: подпроцессы запускаются как раньше
    assert subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True).returncode == 0


def test_send_to_missing_process_fails_clearly():
    with pytest.raises(claude_reply.SendError):
        claude_reply.send(0, "текст")
    with pytest.raises(claude_reply.SendError):
        claude_reply.send(999_999, "текст")


def test_owner_pid_finds_the_client_process():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, (_host, client_port) = server.accept()
    try:
        assert claude_reply.owner_pid(client_port, port) == os.getpid()
    finally:
        for s in (conn, client, server):
            s.close()


# ------------------------------------------------------------- выбор сессии

def bridge_with(*sessions):
    bridge = claude_bridge.Bridge()
    for sid, event, pid in sessions:
        bridge.handle(event, {"session_id": sid, "cwd": f"C:/work/{sid}",
                              "notification_type": "idle_prompt"})
        bridge.sessions[sid].pid = pid
    return bridge


def test_waiting_session_beats_finished_one():
    bridge = bridge_with(("a", "Notification", 11), ("b", "Stop", 22))
    assert bridge.reply_target().id == "a"


def test_latest_finished_session_wins():
    bridge = bridge_with(("a", "Stop", 11), ("b", "Stop", 22))
    bridge.sessions["a"].updated -= 5                  # часы Windows шагают по ~15 мс
    assert bridge.reply_target().id == "b"


def test_session_without_pid_or_stale_is_skipped():
    bridge = bridge_with(("a", "Stop", 0), ("b", "Stop", 22))
    assert bridge.reply_target().id == "b"
    bridge.sessions["b"].updated -= claude_bridge.REPLY_FRESH_S + 1
    assert bridge.reply_target() is None


def test_replied_session_is_thinking_again():
    bridge = bridge_with(("a", "Stop", 11))
    bridge.replied(bridge.sessions["a"])
    assert bridge.sessions["a"].state == "think"
