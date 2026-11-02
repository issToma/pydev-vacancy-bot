#!/usr/bin/env bash
# Запуск ТОЛЬКО скрапера (Celery worker + beat), без Telegram-бота.
# Для работы с ВЫКЛЮЧЕННЫМ VPN: hh.ru с VPN блокирует, а боту VPN, наоборот, нужен.
# Бот тут не поднимается — когда он понадобится, просто запусти ./run.sh (оба сразу).
# Останавливается как обычно: Ctrl+C в этом терминале или ./stop.sh (гасит и воркер, и бота).
#
# Использование:  ./run-scraper.sh
#
# ⚠️ Не держи одновременно ./run-scraper.sh и ./run.sh — у обоих свой beat, расписание
#    задвоится (сам скрап не наложится — защищён single-flight локом, но это лишняя работа).
#    Перед переключением на бота: ./stop.sh (или Ctrl+C здесь).
set -uo pipefail
cd "$(dirname "$0")"

# Запускать от обычного пользователя: uv стоит у него, а docker поднимется через sudo внутри.
if [ "$(id -u)" -eq 0 ]; then
    echo "!! Не запускай от root/sudo. Запусти от своего пользователя:  ./run-scraper.sh" >&2
    echo "   (docker внутри сам вызовет sudo и спросит пароль)" >&2
    exit 1
fi
command -v uv >/dev/null 2>&1 || { echo "!! uv не найден в PATH. Установи uv или запусти от пользователя, где он есть." >&2; exit 1; }

# Снимаем прокси из окружения: если в env торчит ALL_PROXY=socks://… (от VPN-клиента),
# httpx (OpenAI-клиент для /match) не умеет socks и падает на импорте.
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy NO_PROXY no_proxy

echo ">> Инфраструктура (docker: postgres/redis/rabbitmq)…"
# --wait: ждём healthcheck'и (postgres/rabbitmq), прежде чем стартовать воркер.
sudo docker compose up -d --wait postgres redis rabbitmq

echo ">> Миграции БД…"
uv run alembic upgrade head

echo ">> Скрапер (Celery worker + beat). Ctrl+C — остановить…"
exec uv run celery -A core.celery_app:celery_app worker -B -l info
