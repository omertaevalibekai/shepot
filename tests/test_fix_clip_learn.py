"""Исправления: регистр в словаре, терминалы для скриншотов."""

from shepot import learn


def test_sentence_start_capitalization_not_learned():
    assert learn.find_correction_pairs("потом сделаем это", "Потом сделаем это") == []
    assert learn.find_correction_pairs("привет. потом сделаем", "привет. Потом сделаем") == []


def test_mid_sentence_latin_case_still_learned():
    assert learn.find_corrections("добавь сервис в docker compose",
                                  "добавь сервис в Docker compose") == ["Docker"]


def test_sentence_start_capital_is_not_learned():
    # «потом» → «Потом» в начале предложения — грамматика, а не термин
    assert learn._is_term("Потом", "потом", sentence_start=True) is False


def test_latin_term_pair_still_applied():
    assert learn.apply("запусти фигма", {"фигма": "Figma"}) == "запусти Figma"


def test_terminal_whitelist():
    from shepot.clipshot import TERMINAL_APPS
    assert "windowsterminal.exe" in TERMINAL_APPS and "code.exe" in TERMINAL_APPS
    assert "telegram.exe" not in TERMINAL_APPS and "winword.exe" not in TERMINAL_APPS
