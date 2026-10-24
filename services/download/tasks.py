from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis.asyncio as aioredis
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import NullPool, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.celery_app import celery_app
from core.config import settings
from core.models import Job
from services.download.formats import (
    AUDIO,
    FORMAT_SORT,
    QUALITIES,
    build_format,
    download_dir,
    human_size,
)
from services.vacancy.notifications import publish_notifications

logger = logging.getLogger(__name__)

# Ответы YouTube, означающие «нужны куки залогиненного браузера». Проверяем по
# точным фразам: проверка на подстроку "bot" ловила слово из пути проекта
# (vacancy_bot) и подсказка вылезала на посторонних ошибках.
ANTIBOT_MARKERS = (
    "Sign in to confirm",
    "confirm you're not a bot",
    "Sign in to view",
    "This video is only available to Music Premium members",
)

# Недокачанное: yt-dlp дописывает их по ходу загрузки, автоочистке трогать нельзя.
PARTIAL_SUFFIXES = {".part", ".ytdl"}
# Файл моложе этого возраста считаем «в работе» и не удаляем: промежуточные
# дорожки DASH (.f137.mp4) выглядят как обычные файлы, но нужны для склейки.
FRESH_SECONDS = 3600


def _lock_key(url: str, quality: str) -> str:
    """Ключ single-flight: одна ссылка в одном качестве качается только в одном экземпляре.

    Две параллельные задачи дали бы yt-dlp одно и то же имя временного файла:
    кто первый переименует .part в готовый, у второго файл исчезнет из-под ног
    и загрузка упадёт на rename. Воркер prefork-овый, задачи реально идут разом.
    Ссылка в ключ не кладётся целиком — она бывает длинной, берём её хеш.
    """
    digest = hashlib.sha256(f"{url}|{quality}".encode()).hexdigest()[:32]
    return f"lock:download:{digest}"


async def _publish(payload: dict) -> None:
    """Положить уведомление в очередь. Сбой доставки не должен ронять загрузку:
    файл уже на диске, и терять его из-за недоступного RabbitMQ незачем."""
    try:
        await publish_notifications([payload])
    except Exception:
        logger.exception("Не удалось опубликовать уведомление о загрузке")


async def _notify(user_id: int, text: str, edit_message_id: int | None = None) -> None:
    """Текстовый статус: новым сообщением или правкой уже отправленного."""
    payload: dict = {"user_id": user_id, "text": text}
    if edit_message_id:
        payload["edit_message_id"] = edit_message_id
    await _publish(payload)


async def _update_job(
    session_factory, job_id: uuid.UUID, *,
    status: str | None = None, result_ref: str | None = None, **params,
) -> None:
    """Обновить статус задачи и дописать поля в params."""
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if result_ref is not None:
            job.result_ref = result_ref
        if params:
            job.params = {**(job.params or {}), **params}
        await session.commit()


async def _find_done(session_factory, url: str, quality: str) -> Job | None:
    """Та же ссылка в том же качестве уже скачана — и файл ещё на месте?"""
    async with session_factory() as session:
        rows = (await session.execute(
            select(Job)
            .where(
                Job.service == "download",
                Job.status == "done",
                Job.params["url"].astext == url,
                Job.params["quality"].astext == quality,
            )
            .order_by(Job.created_at.desc())
            .limit(5)
        )).scalars().all()
    for job in rows:
        if job.result_ref and Path(job.result_ref).exists():
            return job
    return None


def _ydl_opts(quality: str, hook) -> dict:
    """Опции yt-dlp. Пользовательский выбор — только качество; остальное из настроек."""
    opts: dict = {
        "format": build_format(quality),
        "format_sort": FORMAT_SORT,      # при равном разрешении — H.264 + AAC
        "outtmpl": str(download_dir() / "%(title).120B [%(id)s].%(ext)s"),
        "noplaylist": True,          # ссылка на ролик внутри плейлиста = один ролик
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        # прогресс отдаём в чат через hook; в stdout он не нужен — иначе забивает
        # лог воркера сотнями строк «[download] 12.3% of ...» на каждую загрузку
        "noprogress": True,
    }
    if quality == AUDIO:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # контейнер для склейки video+audio. Список через «/»: mp4, пока кодеки
        # ему родные, иначе mkv. AV1/VP9 с Opus внутри mp4 — формально валидно,
        # но именно на нём плееры чаще всего дают чёрный экран; в mkv они играют.
        opts["merge_output_format"] = "mp4/mkv"

    if settings.ytdlp_proxy:
        # прокси ТОЛЬКО для скачивалки: скрапер hh.ru ходит напрямую
        opts["proxy"] = settings.ytdlp_proxy
    if settings.ytdlp_cookies_from_browser:
        # обход антибот-проверки YouTube («Sign in to confirm you're not a bot»)
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser,)
    return opts


def _sync_download(opts: dict, url: str) -> dict:
    """Блокирующая загрузка. yt-dlp импортируем здесь, а не на уровне модуля:
    Celery импортирует модуль задач при старте, и отсутствие пакета иначе
    уронило бы весь воркер — вместе со скрапингом вакансий."""
    import yt_dlp

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


def _stage(d: dict) -> str:
    """Что качается прямо сейчас — «видео» или «звук».

    YouTube и прочий DASH отдают дорожки разными файлами: yt-dlp качает их
    по очереди, поэтому прогресс доходит до 100% и начинается заново. Без подписи
    это выглядит как сбой. Для прогрессивного потока (видео+звук одним файлом)
    подписывать нечего — возвращаем пустую строку.
    """
    info = d.get("info_dict") or {}
    vcodec = info.get("vcodec") or "none"
    acodec = info.get("acodec") or "none"
    if vcodec != "none" and acodec == "none":
        return "видео"
    if vcodec == "none" and acodec != "none":
        return "звук"
    return ""


async def _probe_video(path: Path) -> dict:
    """Размеры и длительность через ffprobe — для отправки видео в Telegram.

    Без width/height/duration Telegram показывает присланное как файл, а не как
    видео с плеером. Ошибку ffprobe не считаем фатальной: отправим без метаданных.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "default=noprint_wrappers=1:nokey=0",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return {}
        values: dict = {}
        for line in out.decode().splitlines():
            key, _, value = line.partition("=")
            if key in ("width", "height") and value.isdigit():
                values[key] = int(value)
            elif key == "duration":
                with contextlib.suppress(ValueError):
                    values["duration"] = int(float(value))
        return values
    except (OSError, asyncio.SubprocessError) as exc:
        logger.warning("ffprobe не смог прочитать %s: %s", path, exc)
        return {}


async def _make_thumb(path: Path, duration: int | None) -> Path | None:
    """Кадр из середины ролика как обложка. Нет обложки — Telegram рисует чёрный
    прямоугольник, по которому не понять, что прислали.

    Пишем во временный каталог, а не рядом с видео: это служебный файл на один
    раз, в папке загрузок он был бы мусором. Бот удаляет его после отправки.

    Имя берём у mkstemp, а не собираем из названия ролика: /tmp пишут все
    пользователи машины, и по предсказуемому пути можно заранее положить симлинк —
    тогда ffmpeg с -y перезапишет чужой файл правами воркера.
    """
    offset = max(1, (duration or 10) // 2)
    fd, tmp_name = tempfile.mkstemp(prefix="dl-thumb-", suffix=".jpg")
    os.close(fd)                      # ffmpeg откроет файл сам (-y перезапишет пустой)
    thumb = Path(tmp_name)
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-ss", str(offset), "-i", str(path),
        "-frames:v", "1", "-vf", "scale=320:-2", str(thumb),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if thumb.exists() and thumb.stat().st_size > 0:
            return thumb
    except (OSError, asyncio.SubprocessError) as exc:
        logger.warning("Не удалось сделать обложку для %s: %s", path, exc)
    # кадр не получился — за собой убираем сами: пустую заготовку от mkstemp
    # удалять больше некому, бот чистит только отправленные обложки
    with contextlib.suppress(OSError):
        thumb.unlink()
    return None


def _result_path(info: dict) -> Path | None:
    """Путь итогового файла после склейки/постобработки."""
    for entry in info.get("requested_downloads") or []:
        if entry.get("filepath"):
            return Path(entry["filepath"])
    return None


async def _enforce_dir_limit() -> list[tuple[str, int]]:
    """Держать папку загрузок в пределах DOWNLOAD_MAX_DIR_GB, удаляя самые старые.

    «Старый» — по времени изменения файла, то есть по моменту, когда загрузка
    закончилась. Самый свежий файл не удаляется никогда: иначе одна загрузка
    крупнее лимита стёрла бы саму себя сразу после завершения.

    Из кандидатов на удаление исключаем недокачанное и всё, к чему притрагивались
    в последний час: параллельная загрузка держит в этой же папке .part и
    промежуточные дорожки, и снести их означает уронить её на склейке. В общий
    размер папки они при этом считаются — место-то занято.

    Возвращает список (имя, размер) удалённого — для лога и уведомления.
    """
    limit = settings.download_max_dir_gb * 1024 ** 3
    now = time.time()
    everything = [p for p in download_dir().iterdir() if p.is_file()]
    files = sorted(
        (p for p in everything
         if p.suffix not in PARTIAL_SUFFIXES and now - p.stat().st_mtime > FRESH_SECONDS),
        key=lambda p: p.stat().st_mtime,
    )
    total = sum(p.stat().st_size for p in everything)
    removed: list[tuple[str, int]] = []
    # `files` уже без свежего: отдельная оговорка «самый новый не трогаем» больше
    # не нужна, иначе один старый файл сверх лимита остался бы висеть навсегда.
    while total > limit and files:
        victim = files.pop(0)
        try:
            size = victim.stat().st_size
            victim.unlink()
        except OSError as exc:
            logger.warning("Не удалось удалить %s: %s", victim.name, exc)
            continue
        total -= size
        removed.append((victim.name, size))
        logger.info("Автоочистка: удалён %s (%s)", victim.name, human_size(size))
    return removed


async def _notify_cleanup(session_factory, removed: list[tuple[str, int]]) -> None:
    """Сообщить владельцам, что их файлы удалены — иначе исчезновение выглядит
    как потеря. Адресата ищем по result_ref в истории задач."""
    for name, size in removed:
        async with session_factory() as session:
            job = (await session.execute(
                select(Job)
                .where(Job.service == "download", Job.result_ref.like(f"%/{name}"))
                .limit(1)
            )).scalar_one_or_none()
        if job is None or job.user_id is None:
            continue
        title = (job.params or {}).get("title") or name
        await _notify(
            job.user_id,
            f"🗑 Удалил старую загрузку, чтобы освободить место "
            f"({human_size(size)})\n{title}",
        )


async def _make_link(session_factory, job_id: uuid.UUID) -> str:
    """Ссылка на файл, не влезающий в чат, плюс строка для сообщения.

    PUBLIC_BASE_URL пуст → раздача не настроена, честно говорим, что файл на диске.
    Токен 256-битный: угадать нельзя, поэтому отдельной авторизации у ссылки нет.
    """
    if not settings.public_base_url:
        return (
            f"Больше {settings.download_inline_max_mb} МБ — "
            f"в чат не отправляю, файл в папке загрузок"
        )
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.download_link_ttl_hours)
    await _update_job(
        session_factory, job_id, token=token, expires_at=expires.isoformat(),
    )
    link = f"{settings.public_base_url.rstrip('/')}/d/{token}"
    return f"{link}\nСсылка живёт {settings.download_link_ttl_hours} ч"


async def _run_download(
    job_id: str, user_id: int, message_id: int, url: str, quality: str,
) -> str:
    jid = uuid.UUID(job_id)
    label = QUALITIES.get(quality, quality)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = aioredis.from_url(settings.redis_url)
    lock = _lock_key(url, quality)
    # SET NX EX: не взяли — эту же ссылку уже качает соседний процесс воркера.
    # TTL с запасом над лимитом задачи, чтобы упавший воркер не запер ключ навсегда.
    got_lock = await redis.set(lock, "1", nx=True, ex=settings.download_time_limit + 120)
    try:
        if not got_lock:
            await _update_job(session_factory, jid, status="skipped", error="уже качается")
            await _notify(user_id, f"⏭ Эта ссылка уже качается · {label}", message_id)
            return "locked"

        cached = await _find_done(session_factory, url, quality)
        if cached is not None:
            cached_title = (cached.params or {}).get("title") or Path(cached.result_ref).stem
            await _update_job(
                session_factory, jid, status="done", result_ref=cached.result_ref,
                title=cached_title,
            )
            await _notify(
                user_id, f"✅ Уже скачано ранее · {label}\n{cached_title}", message_id,
            )
            return "cached"

        free = shutil.disk_usage(download_dir()).free
        if free < settings.download_min_free_gb * 1024 ** 3:
            await _update_job(session_factory, jid, status="failed", error="мало места на диске")
            await _notify(
                user_id,
                f"❌ Мало места на диске — свободно {human_size(free)}",
                message_id,
            )
            return "no-space"

        await _update_job(session_factory, jid, status="running")

        loop = asyncio.get_running_loop()
        last_edit = 0.0
        last_stage = None

        def hook(d: dict) -> None:
            """Прогресс-колбэк yt-dlp. Вызывается в рабочем потоке десятки раз в
            секунду, поэтому троттлим: Telegram не любит частых правок сообщения."""
            nonlocal last_edit, last_stage
            if d.get("status") != "downloading":
                return
            stage = _stage(d)
            now = time.monotonic()
            # смену дорожки показываем сразу, не дожидаясь очередного тика:
            # иначе до 15 с висит «100%» от предыдущей части
            if stage == last_stage and now - last_edit < settings.download_progress_interval:
                return
            last_edit = now
            last_stage = stage

            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            progress = f"{done / total * 100:.0f}% из {human_size(total)}" if total else human_size(done)

            head = f"⬇️ Качаю · {label}"
            if stage:
                head += f" · {stage}"
            text = f"{head}\n{progress}"
            if speed := d.get("speed"):
                text += f" · {human_size(speed)}/с"
            # сегментные потоки (HLS/DASH-куски): видно, сколько частей из скольких
            index, count = d.get("fragment_index"), d.get("fragment_count")
            if index and count:
                text += f"\nсегмент {index} из {count}"
            asyncio.run_coroutine_threadsafe(_notify(user_id, text, message_id), loop)

        info = await asyncio.to_thread(_sync_download, _ydl_opts(quality, hook), url)

        path = _result_path(info)
        if path is None or not path.exists():
            await _update_job(session_factory, jid, status="failed", error="файл не найден после загрузки")
            await _notify(user_id, "❌ Загрузка завершилась, но файла нет — смотри логи воркера", message_id)
            return "no-file"

        title = info.get("title") or path.stem
        size = path.stat().st_size
        await _update_job(
            session_factory, jid, status="done", result_ref=str(path), title=title,
        )

        # в чат — человеческое название. Имя файла на диске отличается: там ещё
        # id ролика и замены запрещённых символов — это нужно файловой системе,
        # а не пользователю. Полный путь лежит в Job.result_ref.
        done = f"✅ Готово · {label} · {human_size(size)}\n{title}"
        inline_limit = settings.inline_max_bytes

        # папка только что выросла — сразу подрезаем её под лимит. Сейчас
        # скачанный файл самый свежий, поэтому под нож он не попадёт.
        await _notify_cleanup(session_factory, await _enforce_dir_limit())

        if size > inline_limit:
            await _notify(user_id, f"{done}\n{await _make_link(session_factory, jid)}", message_id)
            return "done"

        # влезает в лимит Bot API → досылаем файлом отдельным сообщением: так
        # видео смотрится в любой сети, а не только дома. Статус правим как обычно.
        await _notify(user_id, done, message_id)
        media: dict = {"user_id": user_id, "media_path": str(path), "caption": title}
        if quality == AUDIO:
            media["is_audio"] = True
        else:
            # без width/height/duration Telegram покажет документ вместо плеера,
            # без обложки — чёрный прямоугольник
            media.update(await _probe_video(path))
            thumb = await _make_thumb(path, media.get("duration"))
            if thumb is not None:
                media["thumb_path"] = str(thumb)
        await _publish(media)
        return "done"

    except SoftTimeLimitExceeded:
        await _update_job(session_factory, jid, status="failed", error="превышен лимит времени")
        await _notify(
            user_id,
            f"❌ Не уложился в {settings.download_time_limit // 60} мин — загрузка прервана",
            message_id,
        )
        return "timeout"
    except Exception as exc:
        logger.exception("Ошибка загрузки %s", url)
        reason = str(exc)
        hint = ""
        if any(marker in reason for marker in ANTIBOT_MARKERS):
            hint = "\nПохоже, нужна авторизация: задай YTDLP_COOKIES_FROM_BROWSER в .env"
        await _update_job(session_factory, jid, status="failed", error=reason[:500])
        await _notify(user_id, f"❌ Не получилось скачать\n{reason[:300]}{hint}", message_id)
        return "failed"
    finally:
        await engine.dispose()
        if got_lock:            # чужой замок не трогаем — снимаем только свой
            await redis.delete(lock)
        await redis.aclose()


# time_limit задаём на задаче: глобальные 600 с из core.celery_app убили бы
# любую длинную загрузку на середине. soft — чтобы успеть сообщить в чат.
@celery_app.task(
    name="services.download.tasks.download_task",
    soft_time_limit=settings.download_time_limit,
    time_limit=settings.download_time_limit + 60,
)
def download_task(job_id: str, user_id: int, message_id: int, url: str, quality: str) -> str:
    """Скачать ссылку в DOWNLOAD_DIR и отчитаться в чат о ходе и результате."""
    result = asyncio.run(_run_download(job_id, user_id, message_id, url, quality))
    logger.info("download_task %s: %s", job_id, result)
    return result


async def _run_cleanup() -> int:
    """Подрезать папку загрузок и разослать уведомления об удалённом."""
    removed = await _enforce_dir_limit()
    if not removed:
        return 0
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await _notify_cleanup(
            async_sessionmaker(engine, expire_on_commit=False), removed
        )
    finally:
        await engine.dispose()
    return len(removed)


@celery_app.task(name="services.download.tasks.cleanup_downloads")
def cleanup_downloads() -> int:
    """Страховка к очистке после загрузки: ловит файлы, попавшие в папку иначе
    (положили руками, загрузка упала после склейки). Возвращает число удалённых."""
    removed = asyncio.run(_run_cleanup())
    if removed:
        logger.info("cleanup_downloads: удалено файлов — %s", removed)
    return removed