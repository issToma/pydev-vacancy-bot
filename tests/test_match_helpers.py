from services.vacancy.match import _BRACKET_ID, _candidates_block


def test_bracket_id_stripped():
    assert _BRACKET_ID.sub("", "3. [134182839] Python Dev") == "3. Python Dev"


def test_link_with_id_survives():
    text = "Ссылка: https://hh.ru/vacancy/134182839"
    assert _BRACKET_ID.sub("", text) == text     # в ссылке нет скобок — не трогаем


def test_candidates_block_format():
    cands = [{
        "id": 42, "name": "Py Dev", "employer": "Acme", "area": "Москва",
        "experience": "От 1 года до 3 лет",
        "key_skills": ["Python", "FastAPI"], "techs": [],
    }]
    out = _candidates_block(cands)
    # без порядкового номера (чтобы модель не echo-ила его), но с id для ссылки и навыками
    assert "[42]" in out and "Py Dev" in out and "Python" in out