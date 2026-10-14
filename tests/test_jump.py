import json
import subprocess
from pathlib import Path

from shepot import jump

ALL = {"claude": "claude.exe", "codex": r"C:\Program Files\WindowsApps\OpenAI.Codex\app\resources\codex.exe",
       "code": "code.cmd", "wt": "wt.exe", "git": "git.exe"}
NO_CLAUDE = {**ALL, "claude": None}


def make(tmp_path: Path, name: str, **files) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    for rel, text in files.items():
        target = folder / rel.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return folder


def load(folder: Path, found=ALL) -> jump.Project:
    return jump.load_project(folder, found)


def test_variants_cover_layout_and_sound():
    assert jump.variants("шеп") == ["шеп", "itg", "shep"]
    assert "stable" in jump.variants("ыефиду")          # набрал не в той раскладке
    assert jump.variants("Шёпот")[0] == "шепот"


def test_search_finds_latin_folder_by_russian_query(tmp_path):
    projects = [load(make(tmp_path, n)) for n in ("shepot", "Stable", "whoop-loop", "Mindsnap")]
    assert [p.name for p in jump.search(projects, "шеп")][:1] == ["shepot"]
    assert [p.name for p in jump.search(projects, "ыеф")][:1] == ["Stable"]
    assert [p.name for p in jump.search(projects, "loop")][:1] == ["whoop-loop"]
    assert jump.search(projects, "zzz") == []
    assert len(jump.search(projects, "")) == 4


def test_prefix_beats_subsequence(tmp_path):
    projects = [load(make(tmp_path, n)) for n in ("Satykov", "Stable")]
    assert jump.search(projects, "sta")[0].name == "Stable"


def test_defaults_prefer_claude_then_codex(tmp_path):
    folder = make(tmp_path, "p")
    assert load(folder).options() == {"claude": True, "codex": False, "code": True, "dev": False, "web": False}
    assert load(folder, NO_CLAUDE).options()["codex"] is True
    # сохранённый выбор человека важнее, недоступное не включить
    saved = {"claude": False, "codex": True, "dev": True}
    assert load(folder).options(saved) == {"claude": False, "codex": True, "code": True, "dev": False, "web": False}


def test_package_json_dev_and_url(tmp_path):
    folder = make(tmp_path, "site", **{"package.json": json.dumps(
        {"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}})})
    project = load(folder)
    assert project.dev == "npm run dev"
    assert project.url == "http://localhost:5173"
    assert project.options()["dev"] and project.options()["web"]


def test_jump_toml_overrides(tmp_path):
    folder = make(tmp_path, "shepot", **{".jump.toml": (
        'name = "Шёпот"\naliases = ["dictation"]\nopen = ["codex"]\napp = "dist/Shepot/Shepot.exe"\n')})
    project = load(folder)
    assert project.name == "Шёпот"
    assert project.options() == {"claude": False, "codex": True, "code": False, "dev": False, "web": False}
    assert jump.search([project], "dict") == [project]
    assert jump.match_span("Шёпот", "шеп") == (0, 3)


def test_branch_from_git_head(tmp_path):
    folder = make(tmp_path, "repo", **{".git__HEAD": "ref: refs/heads/island-and-rewrite\n"})
    assert load(folder).branch == "island-and-rewrite"


def test_terminal_tabs_and_args(tmp_path, monkeypatch):
    monkeypatch.setattr(jump.shutil, "which", lambda name: None)
    project = jump.Project(path=tmp_path, name="site", dev="npm run dev; echo")
    tabs = jump.terminal_tabs(project, {"claude": True, "codex": True, "dev": True}, ALL)
    assert [t[0] for t in tabs] == ["site", "Codex", "dev"]          # первая вкладка — имя проекта
    assert tabs[1][1].startswith("& '") and tabs[1][1].endswith("codex.exe'")
    args = jump.terminal_args(project, tabs, "wt.exe")
    assert args[:3] == ["wt.exe", "-w", "new"]
    assert args.count("new-tab") == 3 and args.count("--suppressApplicationTitle") == 3
    assert "npm run dev\\; echo" in args               # своя точка с запятой экранирована
    assert args[-3:] == ["focus-tab", "-t", "0"]
    assert jump.terminal_args(project, [], None) == ["powershell.exe", "-NoLogo", "-NoExit"]


def test_scan_orders_pinned_then_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(jump, "tools", lambda: ALL)
    for n in ("a", "b", "c"):
        make(tmp_path, n)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "file.txt").write_text("x")
    names = [p.name for p in jump.scan(tmp_path, {str(tmp_path / "b"): 9e12}, [str(tmp_path / "c")])]
    assert names[:2] == ["c", "b"] and sorted(names) == ["a", "b", "c"]


def test_git_status_counts(tmp_path):
    folder = make(tmp_path, "repo", **{"a.txt": "1"})
    git = lambda *a: subprocess.run(["git", "-C", str(folder), *a], capture_output=True, check=True)  # noqa: E731
    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first commit")
    (folder / "a.txt").write_text("2")
    (folder / "b.txt").write_text("new")
    status = jump.git_status(folder, "git")
    assert status.dirty == 2 and status.last == "first commit" and status.last_ts > 0
    assert status.upstream is False


def test_is_editor_and_terminal_title(tmp_path):
    assert jump.is_editor("Chrome_WidgetWin_1", "app.py - shepot - Visual Studio Code", "shepot")
    assert jump.is_editor("Chrome_WidgetWin_1", "shepot - Visual Studio Code", "shepot")
    assert not jump.is_editor("Chrome_WidgetWin_1", "shepot - Google Chrome", "shepot")
    assert not jump.is_editor("Chrome_WidgetWin_1", "x - Stable - Visual Studio Code", "shepot")
    project = jump.Project(path=tmp_path / "shepot", name="Шёпот")
    assert jump.is_terminal("CASCADIA_HOSTING_WINDOW_CLASS", "Шёпот", project)
    assert not jump.is_terminal("CASCADIA_HOSTING_WINDOW_CLASS", "Codex", project)


def test_port_open_only_local():
    assert jump.port_open("https://album-carousel-self.vercel.app") is False
