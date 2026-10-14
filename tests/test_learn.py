"""Обучение словаря на исправлениях: python -m pytest tests/test_learn.py"""

from shepot.learn import apply, find_correction_pairs, find_corrections, merge, remember_pairs


def test_transliterated_brand_is_learned():
    ins = "Скинь мне ссылку на фигму, я поправлю онбординг и лендинг пейдж."
    doc = "Привет!\nСкинь мне ссылку на Figma, я поправлю onboarding и landing page.\nСпасибо"
    assert find_corrections(ins, doc) == ["Figma", "onboarding", "landing page"]


def test_name_capitalised_is_learned():
    assert find_corrections("передай алибеку что созвон в три", "передай Алибеку что созвон в три") == ["Алибеку"]


def test_case_only_change_is_learned():
    assert find_corrections("добавь сервис в docker compose", "добавь сервис в Docker compose") == ["Docker"]


def test_correction_at_start_of_insert():
    assert find_corrections("фигму открой и проверь макет", "Итак. Figma открой и проверь макет") == ["Figma"]


def test_grammar_fix_is_not_learned():
    ins = "он пошол домой и сказал что завтро будет"
    doc = "он пошёл домой и сказал, что завтра будет"
    assert find_corrections(ins, doc) == []


def test_rewritten_phrase_is_not_learned():
    ins = "давай встретимся завтра утром в офисе у Марата"
    doc = "давай лучше созвонимся в Zoom после обеда в офисе у Марата"
    assert find_corrections(ins, doc) == []


def test_text_gone_from_window_learns_nothing():
    assert find_corrections("скинь ссылку на фигму", "совсем другой текст про погоду") == []


def test_unchanged_text_learns_nothing():
    text = "Задеплой фронтенд на Vercel"
    assert find_corrections(text, "Заголовок\n" + text + "\nподпись") == []


def test_merge_adds_and_fixes_case():
    vocab, added = merge(["docker", "Vercel"], ["Docker", "Figma", "Vercel"])
    assert vocab == ["Docker", "Vercel", "Figma"]
    assert added == ["Docker", "Figma"]


def test_learned_pairs_fix_next_dictation():
    ins = "Скинь мне ссылку на фигму, я поправлю онбординг и лендинг пейдж."
    doc = "Скинь мне ссылку на Figma, я поправлю onboarding и landing page."
    pairs = find_correction_pairs(ins, doc)
    assert pairs == [("фигму", "Figma"), ("онбординг", "onboarding"), ("лендинг пейдж", "landing page")]
    reps = remember_pairs({}, pairs)
    fresh = "Открой фигму, онбординг и Лендинг-пейдж для мобилки."
    assert apply(fresh, reps) == "Открой Figma, onboarding и landing page для мобилки."


def test_unlearned_form_and_ordinary_words_untouched():
    reps = remember_pairs({}, [("фигму", "Figma"), ("редис", "Redis"), ("телеграм", "Telegram")])
    text = "Фигма в фигме, телеграмма пришла, редиска выросла, редисочка тоже."
    assert apply(text, reps) == text
