from services.vacancy.subs import subscription_matches


def test_single_word():
    assert subscription_matches({"text": "python"}, {"name": "Python Developer"})
    assert not subscription_matches({"text": "python"}, {"name": "Java Developer"})


def test_multi_word_is_and():
    # каждое слово фильтра должно встречаться в названии
    assert subscription_matches({"text": "middle-python"}, {"name": "Middle Python Developer"})
    assert not subscription_matches({"text": "middle-python"}, {"name": "Python Developer"})


def test_empty_filter_matches_all():
    assert subscription_matches({"text": ""}, {"name": "что угодно"})
    assert subscription_matches({}, {"name": "что угодно"})