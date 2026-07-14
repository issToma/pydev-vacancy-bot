from __future__ import annotations

import json
import logging

import aiohttp
from redis.asyncio import Redis

from core.config import settings

logger = logging.getLogger(__name__)

FX_CACHE_KEY = "cache:fx:cbr"


async def get_fx_rates(redis: Redis) -> dict:
    """Курсы ЦБ РФ (рублей за 1 единицу валюты), cache-aside в Redis.

    Возвращает словарь {"USD": 78.5, "EUR": 92.1, "RUB": 1.0, "RUR": 1.0, ...}
    плюс "_as_of" — дата курса (ISO) или None. Источник (ЦБ) не критичен: при
    недоступности деградируем к одним рублям — неконвертируемые валюты
    вызывающий пропустит, а неудачу не кэшируем (попробуем в следующий раз).
    """
    cached = await redis.get(FX_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)

    rates: dict = {"RUB": 1.0, "RUR": 1.0, "_as_of": None}
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(settings.cbr_fx_url) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        for code, v in data.get("Valute", {}).items():
            nominal = v.get("Nominal") or 1
            value = v.get("Value")
            if value:
                rates[code] = value / nominal
        rates["_as_of"] = data.get("Date")
    except Exception as e:  # noqa: BLE001 — курс не критичен, деградируем к рублям
        logger.warning("Не удалось получить курс ЦБ: %s", e)
        return rates

    await redis.set(FX_CACHE_KEY, json.dumps(rates), ex=settings.fx_cache_ttl)
    return rates
