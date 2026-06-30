from __future__ import annotations

import asyncio
import json
import logging
import random
from urllib.parse import urlencode

from dateutil import parser as dt_parser
from playwright.async_api import async_playwright, BrowserContext, Page
from tqdm import tqdm

from services.vacancy.scraper import config
from services.vacancy.scraper.keywords import extract_technologies
from services.vacancy.scraper.text_utils import strip_html_tags

logger = logging.getLogger(__name__)

SEARCH_URL = "https://hh.ru/search/vacancy"
ROLES_URL = "https://api.hh.ru/professional_roles"
# Во встроенный <template> hh.ru кладёт JSON со всем состоянием страницы
STATE_SELECTOR = "template#HH-Lux-InitialState"

# Маппинги кодов hh → русские названия (как в существующей таблице vacancies_scraped)
WORK_FORMAT_MAP = {
    "ON_SITE": "На месте работодателя",
    "REMOTE": "Удалённо",
    "HYBRID": "Гибрид",
    "FIELD_WORK": "Разъездной",
}
EXPERIENCE_MAP = {
    "noExperience": "Нет опыта",
    "between1And3": "От 1 года до 3 лет",
    "between3And6": "От 3 до 6 лет",
    "moreThan6": "Более 6 лет",
}
EMPLOYMENT_FORM_MAP = {
    "FULL": "Полная",
    "PART": "Частичная",
    "PROJECT": "Проект",
    "FLY_IN_FLY_OUT": "Вахта",
    "SIDE_JOB": "Подработка",
}


def build_search_url(page: int) -> str:
    """Собирает URL поиска из фильтров; списки → повторяющиеся параметры."""
    params: list[tuple[str, str]] = []
    for key, value in config.SEARCH_FILTERS.items():
        if isinstance(value, (list, tuple)):
            params.extend((key, str(v)) for v in value)
        else:
            params.append((key, str(value)))
    params.append(("page", str(page)))
    return f"{SEARCH_URL}?{urlencode(params)}"


async def _human_delay() -> None:
    await asyncio.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))


async def _read_state(page: Page) -> dict | None:
    el = await page.query_selector(STATE_SELECTOR)
    if not el:
        return None
    raw = await page.eval_on_selector(STATE_SELECTOR, "el => el.content.textContent")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


_NAV_FAILED = object()  # маркер «навигация сорвалась» (в отличие от успешного None)


async def _goto(page: Page, url: str, retries: int = 2):
    """Навигация с повтором при временных сбоях (NS_ERROR_ABORT, таймауты).
    Возвращает Response (может быть None при успехе) или _NAV_FAILED при сбое."""
    for attempt in range(1, retries + 1):
        try:
            return await page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
        except Exception as e:
            if attempt < retries:
                await asyncio.sleep(2)
                continue
            logger.warning("Навигация не удалась: %s (%r)", url, e)
            return _NAV_FAILED


async def _goto_and_get_state(page: Page, url: str) -> dict | None:
    """Открывает страницу и достаёт встроенный стейт. Если стейта нет —
    вероятно, показана капча; в headed-режиме ждём ручного прохождения."""
    if await _goto(page, url) is _NAV_FAILED:
        return None
    state = await _read_state(page)
    if state is not None:
        return state

    logger.warning("Стейт страницы не найден (возможно капча/блок): %s", page.url)
    if config.HEADLESS:
        logger.error("Headless-режим: пройти капчу нельзя. Запустите с HH_HEADLESS=0.")
        return None

    logger.warning("Пройдите проверку в открытом окне браузера — жду до %sс...", config.CAPTCHA_WAIT_SECONDS)
    waited = 0
    while waited < config.CAPTCHA_WAIT_SECONDS:
        await asyncio.sleep(3)
        waited += 3
        state = await _read_state(page)
        if state is not None:
            logger.info("Проверка пройдена, продолжаю")
            return state
    logger.error("Не дождались прохождения проверки за %sс", config.CAPTCHA_WAIT_SECONDS)
    return None


async def _load_role_map(context: BrowserContext) -> dict:
    """Справочник профессиональных ролей id→название (через контекст браузера)."""
    try:
        resp = await context.request.get(ROLES_URL)
        data = await resp.json()
        role_map = {}
        for cat in data.get("categories", []):
            for r in cat.get("roles", []):
                role_map[str(r["id"])] = r["name"]
        logger.info("Загружен справочник ролей: %s записей", len(role_map))
        return role_map
    except Exception as e:
        logger.warning("Не удалось загрузить справочник ролей (%r) — роли будут как id", e)
        return {}


def _parse_list_ids(state: dict) -> tuple[list[tuple[int, str]], int]:
    """Возвращает [(vacancy_id, url), ...] и общее число результатов по фильтру."""
    vsr = state.get("vacancySearchResult") or {}
    vacancies = vsr.get("vacancies") or []
    total = vsr.get("totalResults") or 0
    items: list[tuple[int, str]] = []
    for v in vacancies:
        vid = v.get("vacancyId")
        if vid is None:
            continue
        url = (v.get("links") or {}).get("desktop") or f"https://hh.ru/vacancy/{vid}"
        items.append((int(vid), url))
    return items, total


def _address_str(addr: dict | None) -> str | None:
    if not addr:
        return None
    display = addr.get("displayName")
    if display:
        return display[:256]
    parts = [addr.get("city"), addr.get("street"), addr.get("building")]
    joined = ", ".join(p for p in parts if p)
    return joined[:256] or None


def _languages(vv: dict) -> list[str] | None:
    langs = vv.get("languages")
    if not langs:
        return None
    out = []
    for lang in langs:
        name = lang.get("name") or ""
        level = (lang.get("level") or {}).get("name")
        out.append(f"{name} — {level}" if level else name)
    return out or None


def parse_detail(state: dict, role_map: dict) -> dict | None:
    """Извлекает полную строку (схема vacancies_scraped) из стейта детальной страницы."""
    vv = state.get("vacancyView") or {}
    vid = vv.get("vacancyId")
    if vid is None:
        return None
    vid = int(vid)

    company = vv.get("company") or {}
    comp = vv.get("compensation") or {}
    description_html = vv.get("description", "") or ""
    description_text = strip_html_tags(description_html)
    key_skills = ((vv.get("keySkills") or {}).get("keySkill")) or []
    role_ids = vv.get("professionalRoleIds") or []
    work_formats = vv.get("workFormats") or []
    pub = vv.get("publicationDate")

    return {
        "id": vid,
        "name": (vv.get("name") or "")[:256],
        "area": ((vv.get("area") or {}).get("name") or "")[:256],
        "salary_min": comp.get("from"),
        "salary_max": comp.get("to"),
        "currency": (comp.get("currencyCode") or None),
        "address": _address_str(vv.get("address")),
        "published_at": dt_parser.isoparse(pub) if pub else None,
        "url": f"https://api.hh.ru/vacancies/{vid}?host=hh.ru",
        "apply_alternate_url": f"https://hh.ru/applicant/vacancy_response?vacancyId={vid}",
        "employer": (company.get("visibleName") or company.get("name") or "")[:256],
        "accredited_it_employer": company.get("accreditedITEmployer"),
        "work_format": [WORK_FORMAT_MAP.get(c, c) for c in work_formats] or None,
        "professional_role": [role_map.get(str(r), str(r)) for r in role_ids] or None,
        "experience": EXPERIENCE_MAP.get(vv.get("workExperience"), vv.get("workExperience") or ""),
        "employment_format": EMPLOYMENT_FORM_MAP.get(vv.get("employmentForm")),
        "description": description_text or None,
        "description_key_words": extract_technologies(description_text) or None,
        "has_test": bool(vv.get("userTestId") or vv.get("userTestPresent")),
        "key_skills": list(key_skills) or None,
        "languages": _languages(vv),
        "archived": (vv.get("status") or {}).get("archived"),
        "status": "available",  # деталь получена → доступна
    }


async def scrape_new_vacancies(existing_ids: set, on_batch=None) -> list[dict]:
    """Открывает hh.ru, обходит страницы выдачи, по новым вакансиям заходит
    на детальную страницу и собирает полные данные (схема vacancies_scraped).

    Если передан on_batch (async-колбэк), собранное сбрасывается порциями по
    config.BATCH_SIZE прямо по ходу — прерывание не теряет уже собранное."""
    collected: list[dict] = []
    buffer: list[dict] = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=config.HEADLESS)
        context = await browser.new_context(locale="ru-RU")
        page = await context.new_page()

        role_map = await _load_role_map(context)

        # 1) Собираем id вакансий со страниц выдачи
        listed: list[tuple[int, str]] = []
        for page_num in range(config.MAX_PAGES):
            state = await _goto_and_get_state(page, build_search_url(page_num))
            if not state:
                break
            items, total = _parse_list_ids(state)
            if not items:
                logger.info("Страница %s пуста — останавливаюсь", page_num + 1)
                break
            logger.info("Страница %s: %s вакансий (всего по фильтру ~%s)", page_num + 1, len(items), total)
            listed.extend(items)
            await _human_delay()

        # 2) Отбрасываем уже сохранённые
        new_items = [(vid, url) for vid, url in listed if vid not in existing_ids]
        logger.info("Собрано %s вакансий, новых %s", len(listed), len(new_items))

        # 3) По каждой новой заходим на детальную страницу (с прогресс-баром)
        bar = tqdm(new_items, desc="Сбор вакансий", unit="вак", dynamic_ncols=True)
        for vid, url in bar:
            state = await _goto_and_get_state(page, url)
            if not state:
                bar.write(f"  ! {vid} — пропуск (нет данных)")
                continue
            detail = parse_detail(state, role_map)
            if detail:
                collected.append(detail)
                buffer.append(detail)
                bar.set_postfix_str(detail["name"][:40])
                # инкрементальный сброс батча в БД
                if on_batch and len(buffer) >= config.BATCH_SIZE:
                    await on_batch(buffer)
                    buffer = []
            await _human_delay()
        bar.close()

        # дозаписываем остаток
        if on_batch and buffer:
            await on_batch(buffer)

        await browser.close()

    return collected