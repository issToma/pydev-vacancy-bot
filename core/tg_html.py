from __future__ import annotations

import html
import re

TELEGRAM_TEXT_LIMIT = 4096      # максимум символов в сообщении Telegram
HTML_SOURCE_LIMIT = 3800        # запас под HTML-теги (чтобы итог влез в лимит)

_CODE_BLOCK = re.compile(r"```(\w+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_BULLET = re.compile(r"(?m)^\s*[*\-]\s+")


def to_telegram_html(text: str) -> str:
    """Грубый Markdown → безопасный Telegram-HTML (жирный, инлайн-код, блоки кода).

    Telegram-HTML поддерживает лишь подмножество тегов; всё остальное экранируем.
    Блоки кода вырезаем первыми (плейсхолдеры), чтобы не трогать их содержимое.
    """
    blocks: list[str] = []

    def _stash(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = html.escape(m.group(2))
        cls = f' class="language-{lang}"' if lang else ""
        blocks.append(f"<pre><code{cls}>{code}</code></pre>")
        return f"\x00{len(blocks) - 1}\x00"

    text = _CODE_BLOCK.sub(_stash, text)
    text = html.escape(text)                                  # экранируем обычный текст
    text = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BULLET.sub("• ", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], text)
    return text