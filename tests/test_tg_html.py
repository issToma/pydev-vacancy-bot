from core.tg_html import to_telegram_html


def test_bold():
    assert to_telegram_html("**жир**") == "<b>жир</b>"


def test_inline_code():
    assert to_telegram_html("`x = 1`") == "<code>x = 1</code>"


def test_code_block_with_lang():
    out = to_telegram_html("```python\nprint(1)\n```")
    assert '<pre><code class="language-python">' in out
    assert "print(1)" in out


def test_bullets_normalized():
    assert to_telegram_html("* пункт").startswith("• пункт")
    assert to_telegram_html("- пункт").startswith("• пункт")


def test_plain_text_escaped():
    out = to_telegram_html("a < b & c > d")
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out


def test_code_block_content_escaped():
    out = to_telegram_html("```\n<tag> & \"q\"\n```")
    assert "&lt;tag&gt;" in out          # содержимое блока экранировано
    assert "<pre><code>" in out          # но сами теги обёртки — реальные