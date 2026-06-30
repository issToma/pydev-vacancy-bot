import re


def strip_html_tags(text: str) -> str:
    """Убирает HTML-теги из строки (описание вакансии приходит в HTML)."""
    return re.sub(r"<[^>]+>", "", text).strip()