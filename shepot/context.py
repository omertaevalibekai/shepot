"""Контекст окна: что уже написано там, куда человек диктует.

Перед диктовкой Шёпот читает заголовок активного окна и текст поля с фокусом
(UI Automation, без нажатий). Из этого получаются две подсказки:

* модели распознавания — имена и термины из окна («Алибек», «AdaptAI», «Figma»),
  чтобы они с первого раза писались как в переписке;
* модели чистки — сам текст поля, чтобы продолжение совпадало по стилю и
  написанию. Модель предупреждена: этот текст не повторять и не отвечать на него.

Текст окна уходит в облако вместе с диктовкой, поэтому есть выключатель
(«window_context») и окна, из которых не читаем никогда: терминалы (там
бывают ключи и пароли) и всё из списка исключений.
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass

from . import selection

MAX_TEXT_CHARS = 1500                  # хватает на абзац-два перед курсором
MAX_TERMS = 30
NEVER_READ = selection.UIA_APPS + selection.DEFAULT_SKIP_APPS + (
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
)

_WORD = re.compile(r"[\w][\w'’.+#-]*[\w+#]|[\w]", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?…]\s*$")


@dataclass(frozen=True)
class WindowContext:
    title: str = ""
    text: str = ""

    def __bool__(self) -> bool:
        return bool(self.title or self.text)


def window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def capture(app: str, hwnd: int) -> WindowContext:
    """Заголовок окна и конец текста в поле с фокусом; пустой контекст, если читать нельзя."""
    if not app or app in NEVER_READ or not hwnd:
        return WindowContext()
    title = window_title(hwnd)
    try:
        text = selection.document_text(limit=50_000) or ""
    except Exception:
        text = ""
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = "…" + text[-MAX_TEXT_CHARS:]
    return WindowContext(title=title[:200], text=text)


def terms(ctx: WindowContext, limit: int = MAX_TERMS) -> list[str]:
    """Имена и термины из окна: слова с латиницей и слова с заглавной не в начале фразы."""
    found: list[str] = []
    seen: set[str] = set()
    for source in (ctx.title, ctx.text):
        previous = ""
        for match in _WORD.finditer(source):
            word = match.group(0).strip("'’.-")
            starts_sentence = not previous or bool(_SENTENCE_END.search(
                source[max(0, match.start() - 3):match.start()]))
            previous = word
            if len(word) < 2 or word.isdigit():
                continue
            latin = re.search(r"[A-Za-z]", word) is not None
            proper = word[:1].isupper() and not starts_sentence and not word.isupper()
            if (latin or proper) and word.lower() not in seen:
                seen.add(word.lower())
                found.append(word)
    return found[-limit:]


def stt_hint(base: str, ctx: WindowContext, previous: str = "") -> str:
    """Подсказка распознаванию: общая + термины окна + конец уже распознанного."""
    extra = []
    names = terms(ctx)
    if names:
        extra.append("В тексте встречаются: " + ", ".join(names) + ".")
    if previous:
        extra.append(previous)
    if not extra:
        return base
    # модель смотрит на конец подсказки: там продолжение речи, его не обрезаем
    return " ".join([base, *extra] if base else extra)[-900:]


def polish_note(ctx: WindowContext) -> str:
    """Добавка к системному промпту чистки (пустая строка, если контекста нет)."""
    if not ctx:
        return ""
    lines = ["The user is dictating into a window. Use the context below only to spell "
             "names and terms the same way and to keep the same style. Never repeat it, "
             "answer it, translate it or include it in the output."]
    if ctx.title:
        lines.append(f"<window_title>{ctx.title}</window_title>")
    if ctx.text:
        lines.append(f"<text_already_in_field>\n{ctx.text}\n</text_already_in_field>")
    return "\n".join(lines)
