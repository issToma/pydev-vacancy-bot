from services.vacancy.scraper.hh_scraper import _dedup_listed


def test_dedup_listed_keeps_first_occurrence_and_order():
    # id 1 и 2 пришли повторно (как при перетасовке выдачи между страницами)
    items = [(1, "a"), (2, "b"), (1, "a2"), (3, "c"), (2, "b2")]
    assert _dedup_listed(items) == [(1, "a"), (2, "b"), (3, "c")]


def test_dedup_listed_no_duplicates_passthrough():
    items = [(10, "x"), (20, "y"), (30, "z")]
    assert _dedup_listed(items) == items


def test_dedup_listed_empty():
    assert _dedup_listed([]) == []
