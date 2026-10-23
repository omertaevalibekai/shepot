"""Словарь, который учится на исправлениях.

После вставки Шёпот помнит, что вставил. Позже он читает текст того же окна
(UI Automation, без нажатий) и сравнивает: если человек заменил слово на
название, бренд или термин, это слово уходит в словарь, и в следующий раз
распознавание напишет его правильно.

Запоминаем только «термины»: слова с латиницей или с заглавной буквы.
Обычные исправления грамматики словарь не засоряют.
"""

from __future__ import annotations

import difflib
import re

_WORD = re.compile(r"[\w][\w'’.+#-]*[\w+#]|[\w]", re.UNICODE)

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})

MAX_PHRASE_WORDS = 3          # «лендинг пейдж» → «landing page» — до трёх слов за раз
MIN_ANCHOR_SHARE = 0.6        # вставленный текст должен узнаваться в окне хотя бы на 60 %


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _latin(text: str) -> str:
    return text.lower().translate(_TRANSLIT)


def _looks_like(old: str, new: str) -> bool:
    """«фигму» и «Figma», «Алибеку» и «Алибек» — одно и то же слово, а не другая мысль."""
    a, b = _latin(old), _latin(new)
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.55


def _is_term(new: str, old: str) -> bool:
    if new == old:
        return False
    if re.search(r"[A-Za-z]", new):
        return True                                    # латиница: бренд, термин, имя
    return new[:1].isupper() and not old[:1].isupper()  # стало с заглавной: имя собственное


def find_corrections(inserted: str, document: str) -> list[str]:
    """Термины, которыми человек заменил слова из вставленного текста."""
    return [new for _old, new in find_correction_pairs(inserted, document)]


def find_correction_pairs(inserted: str, document: str) -> list[tuple[str, str]]:
    """Пары «как было вставлено → как исправил человек» (только термины).

    Ищем вставленный текст в документе по совпадающим словам; там, где
    между совпадениями слова отличаются и похожи по звучанию, — исправление.
    """
    ins = _words(inserted)
    doc = _words(document)
    if not ins or not doc:
        return []
    matcher = difflib.SequenceMatcher(None, [w.lower() for w in ins], [w.lower() for w in doc],
                                      autojunk=False)
    blocks = matcher.get_matching_blocks()
    matched = sum(b.size for b in blocks)
    if matched < MIN_ANCHOR_SHARE * len(ins):
        return []                                      # вставленного текста в окне уже нет
    learned: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            if tag == "equal":
                # «docker» → «Docker»: регистр сравнение выше не видит
                for a, b in zip(ins[i1:i2], doc[j1:j2]):
                    if a != b and _is_term(b, a):
                        learned.append((a, b))
            continue
        old, new = ins[i1:i2], doc[j1:j2]
        if len(old) > MAX_PHRASE_WORDS:
            continue                                   # переписал фразу целиком — это не опечатка
        old_text = " ".join(old)
        if i2 == len(ins) or i1 == 0:
            # на краю вставки опоры с одной стороны нет: в замену попадёт и чужой
            # текст документа — берём самый похожий кусок у границы
            spans = [new[:k] for k in range(1, min(len(new), MAX_PHRASE_WORDS) + 1)] if i2 == len(ins) \
                else [new[-k:] for k in range(1, min(len(new), MAX_PHRASE_WORDS) + 1)]
            new = max(spans, key=lambda s: difflib.SequenceMatcher(None, _latin(old_text),
                                                                    _latin(" ".join(s))).ratio())
        if len(new) > MAX_PHRASE_WORDS:
            continue
        new_text = " ".join(new)
        if _looks_like(old_text, new_text) and _is_term(new_text, old_text):
            learned.append((old_text, new_text))
    seen, result = set(), []
    for old, new in learned:
        if (old.lower(), new.lower()) not in seen:
            seen.add((old.lower(), new.lower()))
            result.append((old, new))
    return result


# ------------------------------------------------------- применение словаря

def remember_pairs(replacements: dict, pairs: list[tuple[str, str]], limit: int = 400) -> dict:
    """Запоминает «как распознал → как надо»; ключ — распознанное в нижнем регистре."""
    result = dict(replacements)
    for old, new in pairs:
        if old.lower() != new.lower():
            result.pop(old.lower(), None)
            result[old.lower()] = new
    return dict(list(result.items())[-limit:])


def apply(text: str, replacements: dict) -> str:
    """Чинит распознанный текст по выученным парам «как распознал → как надо».

    Подсказка модели распознавания — только совет, и термины всё равно иногда
    приходят кириллицей. Этот шаг локальный и детерминированный. Похожие слова
    сознательно не угадываем: «телеграмма» и «редиска» — не Telegram и не Redis.
    Каждую новую форму слова Шёпот выучит, когда её поправят в первый раз.
    """
    if not text:
        return text
    for old, new in sorted(replacements.items(), key=lambda p: -len(p[0])):
        pattern = r"(?<![\w-])" + re.escape(old).replace(r"\ ", r"[\s-]+") + r"(?![\w-])"
        text = re.sub(pattern, lambda _m, n=new: n, text, flags=re.IGNORECASE)
    return text


def merge(vocabulary: list[str], terms: list[str], limit: int = 200) -> tuple[list[str], list[str]]:
    """Добавляет новые термины в словарь; возвращает (словарь, что добавлено)."""
    known = {t.lower(): i for i, t in enumerate(vocabulary)}
    result = list(vocabulary)
    added = []
    for term in terms:
        index = known.get(term.lower())
        if index is None:
            result.append(term)
            added.append(term)
        elif result[index] != term:
            result[index] = term                       # «docker» в словаре → «Docker»
            added.append(term)
    return result[-limit:], added
