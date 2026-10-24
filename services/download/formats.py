from __future__ import annotations

from pathlib import Path

from core.config import settings

# Нейтральный модуль: его импортируют и бот (service.py), и воркер (tasks.py),
# поэтому здесь не должно быть ни aiogram, ни celery — только данные и чистые функции.

AUDIO = "audio"          # псевдо-«качество»: не видео, а дорожка в mp3

# Что пользователь видит на кнопках. Ключ уходит в callback_data, поэтому короткий.
QUALITIES: dict[str, str] = {
    "480": "480p",
    "720": "720p",
    "1080": "1080p",
    "2160": "4K",
    AUDIO: "🎵 MP3",
}


# Порядок предпочтения потоков. Разрешение остаётся главным критерием, а среди
# равных по разрешению выбираем H.264 + AAC: «лучший» по качеству на бит поток у
# YouTube это AV1 (следом VP9), и такое видео многие плееры показывают чёрным
# экраном при живом звуке. Прописывать кодек прямо в селекторе формата нельзя —
# тогда запрос 4K молча отдавал бы 1080p, потому что H.264 выше 1080p там нет.
FORMAT_SORT = ["res", "vcodec:h264", "acodec:aac"]


def build_format(quality: str) -> str:
    """Селектор формата yt-dlp по выбранному качеству.

    Отбирает только по разрешению; какой кодек взять среди подходящих — решает
    FORMAT_SORT. Порядок попыток: DASH (видео+аудио раздельно, склейка ffmpeg) →
    прогрессивный поток нужной высоты → что угодно. YouTube с 2025 года отдаёт
    только DASH, поэтому первый вариант — основной, остальные для других площадок.
    """
    if quality == AUDIO:
        return "bestaudio/best"
    return f"bv*[height<={quality}]+ba/b[height<={quality}]/bv*+ba/b"


def human_size(num: float | None) -> str:
    """Байты → человекочитаемая строка (640 МБ, 1.4 ГБ)."""
    if not num:
        return "?"
    value = float(num)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"

def download_dir() -> Path:
    """Папка готовых файлов (создаётся при первом обращении).

    resolve() обязателен: DOWNLOAD_DIR по умолчанию относительный, и без него
    файлы легли бы относительно рабочего каталога процесса — то есть в разные
    места в зависимости от того, откуда запущен воркер.
    """
    path = Path(settings.download_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
