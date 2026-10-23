"""Прыжок в проект: горячая клавиша → поиск по папкам проектов → рабочее место.

Проекты — подпапки корня (по умолчанию Рабочий стол\\AI Projects). Что
открывать, выбирается галочками в панели (запоминается для проекта) и
угадывается по содержимому папки. Уточнить можно файлом .jump.toml:

    name    = "Шёпот"               # как показывать
    aliases = ["шепот", "dictation"]
    open    = ["claude", "code"]    # что отмечено по умолчанию
    dev     = "npm run dev"         # сервер — вкладка терминала
    url     = "http://localhost:5173"
    app     = "dist/Shepot/Shepot.exe"

Терминал — одно окно Windows Terminal с вкладками: Claude, Codex, сервер.
Окна редактора и терминала встают рядом: редактор слева, терминал справа.
Если они уже открыты, прыжок переключается на них, а не открывает новые.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import tomllib
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .i18n import t

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
RU = "йцукенгшщзхъфывапролджэячсмитьбю"
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv"}

# что можно открыть; порядок — как в панели и в клавишах Alt+1…5
PARTS = ("claude", "codex", "code", "dev", "web")
PART_LABELS = {"claude": "Claude", "codex": "Codex", "code": "VS Code", "dev": "Сервер", "web": "Браузер"}   # t() при показе

TERM_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"}
EDITOR_RATIO = 0.58               # редактор слева занимает 58 % ширины


def default_root() -> Path:
    return Path.home() / "Desktop" / "AI Projects"


# ------------------------------------------------------------------ инструменты

_tools: dict[str, str | None] = {}


def _codex_path() -> str | None:
    """Codex: команда в PATH или консольный codex.exe внутри приложения из Microsoft Store."""
    found = shutil.which("codex")
    if found:
        return found
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "(Get-AppxPackage OpenAI.Codex).InstallLocation"],
            capture_output=True, text=True, timeout=8, creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    exe = Path(out) / "app" / "resources" / "codex.exe" if out else None
    return str(exe) if exe and exe.exists() else None


def tools() -> dict[str, str | None]:
    """Где лежат claude, codex, code, wt, git. Ищется один раз (codex — около секунды)."""
    if not _tools:
        _tools.update(claude=shutil.which("claude"), code=shutil.which("code"),
                      wt=shutil.which("wt"), git=shutil.which("git"), codex=_codex_path())
    return _tools


def warm_tools() -> None:
    threading.Thread(target=tools, name="jump-tools", daemon=True).start()


# ------------------------------------------------------------------ проекты

@dataclass
class Project:
    path: Path
    name: str
    aliases: list[str] = field(default_factory=list)
    branch: str = ""
    dev: str = ""
    url: str = ""
    app: str = ""
    touched: float = 0.0              # когда в проекте последний раз что-то меняли
    defaults: dict = field(default_factory=dict)    # что отмечено, пока человек не выбрал сам
    available: set = field(default_factory=set)     # что вообще можно открыть

    @property
    def key(self) -> str:
        return str(self.path)

    def options(self, saved: dict | None = None) -> dict[str, bool]:
        opts = dict(self.defaults)
        for part, value in (saved or {}).items():
            if part in self.available:
                opts[part] = bool(value)
        return {part: opts.get(part, False) and part in self.available for part in PARTS}


def _branch(path: Path) -> str:
    try:
        head = (path / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return head.rsplit("/", 1)[-1] if head.startswith("ref:") else head[:7]


def _touched(path: Path) -> float:
    times = [path.stat().st_mtime]
    for probe in (".git/index", ".git/HEAD"):
        try:
            times.append((path / probe).stat().st_mtime)
        except OSError:
            pass
    return max(times)


def load_project(path: Path, found: dict | None = None) -> Project:
    found = tools() if found is None else found
    project = Project(path=path, name=path.name, branch=_branch(path), touched=_touched(path))
    package = path / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        scripts = data.get("scripts") or {}
        if "dev" in scripts:
            project.dev = "npm run dev"
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            if "vite" in deps:
                project.url = "http://localhost:5173"
            elif "next" in deps:
                project.url = "http://localhost:3000"
    extra: dict = {}
    config = path / ".jump.toml"
    if config.is_file():
        try:
            extra = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            extra = {}
        project.name = str(extra.get("name") or project.name)
        project.aliases = [str(a) for a in extra.get("aliases") or []]
        for key in ("dev", "url", "app"):
            if key in extra:
                setattr(project, key, str(extra[key] or ""))

    project.available = {part for part, ok in (
        ("claude", found.get("claude")), ("codex", found.get("codex")), ("code", found.get("code")),
        ("dev", project.dev), ("web", project.url),
    ) if ok}
    if "open" in extra:
        wanted = {str(p) for p in extra["open"]}
    else:
        wanted = {"code", "dev", "web"} | ({"claude"} if found.get("claude") else {"codex"})
        if extra.get("editor") is False:
            wanted.discard("code")
        if extra.get("terminal") == "":
            wanted -= {"claude", "codex"}
    project.defaults = {part: part in wanted for part in PARTS}
    return project


def scan(root: Path, recent: dict | None = None, pinned: list | None = None) -> list[Project]:
    """Подпапки корня: закреплённые, потом открытые прыжком недавно, потом недавно тронутые."""
    found = tools()
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                   and p.name not in SKIP_DIRS]
    except OSError:
        return []
    projects = []
    for folder in folders:
        try:
            projects.append(load_project(folder, found))
        except OSError:
            continue
    recent, pinned = recent or {}, set(pinned or [])
    projects.sort(key=lambda p: (p.key in pinned, recent.get(p.key, 0.0), p.touched), reverse=True)
    return projects


# ------------------------------------------------------------------ состояние

@dataclass
class Status:
    dirty: int = 0                 # файлов с изменениями
    ahead: int = 0                 # коммитов не отправлено
    behind: int = 0
    upstream: bool = False
    last: str = ""                 # последний коммит
    last_ts: float = 0.0
    running: bool = False          # сервер уже отвечает на своём порту
    editor: int = 0                # уже открытые окна
    terminal: int = 0

    @property
    def is_open(self) -> bool:
        return bool(self.editor or self.terminal)


def git_status(path: Path, git: str | None = None) -> Status:
    status = Status()
    git = git or tools().get("git")
    if not git or not (path / ".git").exists():
        return status
    run = lambda *a: subprocess.run([git, "-C", str(path), *a], capture_output=True, text=True,  # noqa: E731
                                    encoding="utf-8", errors="replace", timeout=4,
                                    creationflags=CREATE_NO_WINDOW).stdout
    try:
        lines = run("status", "--porcelain=v1", "-b").splitlines()
        if lines and lines[0].startswith("## "):
            head = lines.pop(0)
            status.upstream = "..." in head
            if m := re.search(r"ahead (\d+)", head):
                status.ahead = int(m.group(1))
            if m := re.search(r"behind (\d+)", head):
                status.behind = int(m.group(1))
        status.dirty = len([line for line in lines if line.strip()])
        log = run("log", "-1", "--format=%s%x1f%ct").strip()
        if "\x1f" in log:
            subject, stamp = log.rsplit("\x1f", 1)
            status.last, status.last_ts = subject, float(stamp or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return status


def port_open(url: str) -> bool:
    parsed = urlparse(url if "://" in url else "http://" + url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", parsed.port), timeout=0.15):
            return True
    except OSError:
        return False


def ago(ts: float) -> str:
    if not ts:
        return ""
    minutes = (time.time() - ts) / 60
    if minutes < 2:
        return t("сейчас")
    if minutes < 60:
        return t("{n} мин назад", n=int(minutes))
    hours = minutes / 60
    if hours < 24:
        return t("{n} ч назад", n=int(hours))
    days = int(hours / 24)
    if days == 1:
        return t("вчера")
    if days < 30:
        return t("{n} дн. назад", n=days)
    return t("{n} мес. назад", n=days // 30)


# ------------------------------------------------------------------ поиск

def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def swap_layout(text: str) -> str:
    out = []
    for ch in text:
        if ch in EN:
            out.append(RU[EN.index(ch)])
        elif ch in RU:
            out.append(EN[RU.index(ch)])
        else:
            out.append(ch)
    return "".join(out)


def translit(text: str) -> str:
    return "".join(TRANSLIT.get(ch, ch) for ch in text)


def variants(query: str) -> list[str]:
    """«шеп» → шеп, itg (раскладка), shep (как звучит)."""
    q = norm(query.strip())
    out = [q, swap_layout(q), translit(q), translit(swap_layout(q))]
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def score(project: Project, query: str) -> float:
    if not query.strip():
        return 1.0
    names = [norm(project.name), norm(project.path.name)] + [norm(a) for a in project.aliases]
    words = [w for n in names for w in n.replace("-", " ").replace("_", " ").split()]
    best = 0.0
    for q in variants(query):
        if any(n.startswith(q) for n in names):
            best = max(best, 3.0)
        elif any(w.startswith(q) for w in words):
            best = max(best, 2.5)
        elif any(q in n for n in names):
            best = max(best, 2.0)
        elif len(q) >= 3 and any(_subsequence(q, w) for w in words):
            best = max(best, 1.0)
    return best


def search(projects: list[Project], query: str) -> list[Project]:
    scored = [(score(p, query), i, p) for i, p in enumerate(projects)]
    return [p for s, i, p in sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))]


def match_span(name: str, query: str) -> tuple[int, int] | None:
    """Где в названии подсветить совпадение."""
    low = norm(name)
    for q in variants(query):
        at = low.find(q)
        if at >= 0:
            return at, at + len(q)
    return None


# ------------------------------------------------------------------ окна

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_RESTORE = 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]


def _text(fn, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    fn(hwnd, buf, 512)
    return buf.value


def top_windows() -> dict[int, tuple[str, str]]:
    """Видимые окна верхнего уровня: hwnd → (класс, заголовок)."""
    found: dict[int, tuple[str, str]] = {}

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            found[int(hwnd)] = (_text(user32.GetClassNameW, hwnd), _text(user32.GetWindowTextW, hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def is_editor(cls: str, title: str, folder: str) -> bool:
    if cls != "Chrome_WidgetWin_1" or not title.endswith("Visual Studio Code"):
        return False
    parts = [p.strip() for p in title.split(" - ")]
    return folder.lower() in (p.lower() for p in parts[:-1])


def is_terminal(cls: str, title: str, project: Project) -> bool:
    """Окно терминала прыжка: заголовок первой вкладки — имя проекта (программы его не меняют)."""
    return cls in TERM_CLASSES and title.strip() in (project.name, project.path.name)


def find_open(project: Project, windows: dict | None = None) -> tuple[int, int]:
    windows = top_windows() if windows is None else windows
    editor = term = 0
    for hwnd, (cls, title) in windows.items():
        if not editor and is_editor(cls, title, project.path.name):
            editor = hwnd
        if not term and is_terminal(cls, title, project):
            term = hwnd
    return editor, term


def work_area() -> tuple[int, int, int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def place(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Поставить окно так, чтобы видимая рамка совпала с прямоугольником
    (у Windows 11 вокруг окна невидимая полоса для захвата мышью)."""
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    outer, inner = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(inner), ctypes.sizeof(inner)) != 0:
        inner = outer
    dl, dt = inner.left - outer.left, inner.top - outer.top
    dr, db = outer.right - inner.right, outer.bottom - inner.bottom
    user32.SetWindowPos(hwnd, None, x - dl, y - dt, w + dl + dr, h + dt + db, SWP_NOZORDER | SWP_NOACTIVATE)


def bring_front(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


# ------------------------------------------------------------------ запуск

def _ps(command: str) -> list[str]:
    return ["powershell.exe", "-NoLogo", "-NoExit", "-Command", command] if command else \
           ["powershell.exe", "-NoLogo", "-NoExit"]


def _quoted(path: str) -> str:
    return "& '" + path.replace("'", "''") + "'"


def terminal_tabs(project: Project, opts: dict, found: dict) -> list[tuple[str, str]]:
    """Вкладки терминала: (заголовок, команда). Первая называется именем проекта —
    по нему прыжок потом узнаёт уже открытое окно."""
    tabs = []
    if opts.get("claude") and found.get("claude"):
        tabs.append(("Claude", "claude"))
    if opts.get("codex") and found.get("codex"):
        # codex в PATH — по имени; консольный codex.exe из приложения — полным путём
        tabs.append(("Codex", "codex" if shutil.which("codex") else _quoted(found["codex"])))
    if opts.get("dev") and project.dev:
        tabs.append(("dev", project.dev))
    if tabs:
        tabs[0] = (project.name, tabs[0][1])
    return tabs


def terminal_args(project: Project, tabs: list[tuple[str, str]], wt: str | None) -> list[str]:
    """Команда Windows Terminal: новое окно, по вкладке на каждую команду."""
    path = str(project.path)
    if not tabs:
        tabs = [(project.name, "")]
    if not wt:
        return _ps(tabs[0][1])
    # в аргументах wt точка с запятой разделяет команды — внутри команды её экранируют
    esc = lambda c: c.replace(";", "\\;")                       # noqa: E731
    args = [wt, "-w", "new"]
    for index, (title, command) in enumerate(tabs):
        if index:
            args.append(";")
        args += ["new-tab", "-d", path, "--title", title, "--suppressApplicationTitle", *_ps(esc(command))]
    if len(tabs) > 1:
        args += [";", "focus-tab", "-t", "0"]
    return args


@dataclass
class Plan:
    """Что делает прыжок: новое окно, уже открытое или ничего."""
    editor: str = ""               # "" | new | reuse
    terminal: str = ""
    editor_hwnd: int = 0
    terminal_hwnd: int = 0
    web: bool = False
    app: bool = False

    @property
    def parts(self) -> list[str]:
        out = []
        if self.editor:
            out.append("code")
        if self.terminal:
            out.append("terminal")
        return out


def launch(project: Project, opts: dict, terminal_only: bool = False) -> Plan:
    """Запустить недостающее и запомнить, что уже было открыто."""
    found = tools()
    plan = Plan()
    editor_hwnd, term_hwnd = find_open(project)
    if terminal_only:
        opts = {**{p: False for p in PARTS}, "claude": opts.get("claude"), "codex": opts.get("codex")}
        if not (opts["claude"] or opts["codex"]):
            opts["claude" if found.get("claude") else "codex"] = True
    tabs = terminal_tabs(project, opts, found)
    want_term = bool(tabs) or terminal_only
    dev_running = bool(project.url) and port_open(project.url)
    if want_term:
        if term_hwnd:
            plan.terminal, plan.terminal_hwnd = "reuse", term_hwnd
        else:
            if dev_running:                       # сервер уже работает — второй не поднимаем
                tabs = [t for t in tabs if t[1] != project.dev] or tabs
            wt = found.get("wt")
            subprocess.Popen(terminal_args(project, tabs, wt), cwd=str(project.path),
                             creationflags=CREATE_NO_WINDOW if wt else CREATE_NEW_CONSOLE)
            plan.terminal = "new"
    if opts.get("code") and found.get("code"):
        if editor_hwnd:
            plan.editor, plan.editor_hwnd = "reuse", editor_hwnd
        else:
            subprocess.Popen([found["code"], str(project.path)], cwd=str(project.path),
                             creationflags=CREATE_NO_WINDOW)
            plan.editor = "new"
    if terminal_only:
        return plan
    if project.app and not plan.editor == "reuse":
        app = Path(project.app) if Path(project.app).is_absolute() else project.path / project.app
        if app.exists():
            os.startfile(str(app))
            plan.app = True
    if opts.get("web") and project.url:
        starting = opts.get("dev") and project.dev and not dev_running
        threading.Timer(4.0 if starting else 0.0, lambda: os.startfile(project.url)).start()
        plan.web = True
    return plan


def arrange(project: Project, plan: Plan, before: set[int], on_step=None, timeout: float = 12.0) -> int:
    """Дождаться окон, вывести вперёд и поставить рядом. Возвращает, сколько окон нашлось.
    on_step(part) зовётся, когда окно части появилось."""
    editor, term = plan.editor_hwnd, plan.terminal_hwnd
    for part, hwnd in (("code", editor), ("terminal", term)):
        if hwnd and on_step:
            on_step(part)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and ((plan.editor and not editor) or (plan.terminal and not term)):
        time.sleep(0.25)
        for hwnd, (cls, title) in top_windows().items():
            if plan.terminal and not term and hwnd not in before and cls in TERM_CLASSES:
                term = hwnd
                if on_step:
                    on_step("terminal")
            if plan.editor and not editor and is_editor(cls, title, project.path.name):
                editor = hwnd             # VS Code мог и переиспользовать уже открытое окно
                if on_step:
                    on_step("code")
    for hwnd in (editor, term):
        if hwnd:
            bring_front(hwnd)
    if editor and term:
        left, top, right, bottom = work_area()
        gap = 8
        split = left + int((right - left) * EDITOR_RATIO)
        for _ in range(2):                     # VS Code после показа ещё раз ставит свой размер
            place(editor, left + gap, top + gap, split - left - gap - gap // 2, bottom - top - gap * 2)
            place(term, split + gap // 2, top + gap, right - split - gap - gap // 2, bottom - top - gap * 2)
            time.sleep(0.8)
    return int(bool(editor)) + int(bool(term))


def start(project: Project, opts: dict, terminal_only: bool = False, on_step=None, on_done=None) -> Plan:
    """Запуск и раскладка в фоне."""
    before = set(top_windows())
    plan = launch(project, opts, terminal_only)

    def work() -> None:
        count = arrange(project, plan, before, on_step)
        if on_done:
            on_done(count)

    threading.Thread(target=work, name="jump-arrange", daemon=True).start()
    return plan
