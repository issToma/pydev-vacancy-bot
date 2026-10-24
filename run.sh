#!/usr/bin/env bash
# Запуск всего проекта одной командой: инфраструктура (docker) + миграции + бот + воркер.
# Логи бота и воркера идут в этот же терминал. Ctrl+C останавливает бот и воркер;
# инфра-контейнеры (postgres/redis/rabbitmq/minio) продолжают жить.
#
# Использование:  ./run.sh
set -uo pipefail
cd "$(dirname "$0")"

# Запускать от обычного пользователя: uv стоит у него, а docker поднимется через sudo внутри.
if [ "$(id -u)" -eq 0 ]; then
    echo "!! Не запускай от root/sudo. Запусти от своего пользователя:  ./run.sh" >&2
    echo "   (docker внутри сам вызовет sudo и спросит пароль)" >&2
    exit 1
fi
command -v uv >/dev/null 2>&1 || { echo "!! uv не найден в PATH. Установи uv или запусти от пользователя, где он есть." >&2; exit 1; }

# Снимаем прокси из окружения: если запускаешь из терминала с включённым VPN,
# в env торчит ALL_PROXY=socks://… — httpx (OpenAI-клиент) не умеет socks и падает
# на импорте. Ручной запуск работал именно потому, что прокси там не было.
# (LLM пойдёт к Gemini напрямую — как в рабочем ручном сценарии.)
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy NO_PROXY no_proxy

echo ">> Инфраструктура (docker: postgres/redis/rabbitmq/minio)…"
# --wait: ждём healthcheck'и (postgres/rabbitmq), прежде чем стартовать бот/воркер.
# Иначе они коннектятся к ещё не готовому брокеру → на старте сыпался безобидный,
# но пугающий ERROR «Connection reset by peer» от notifier и celery beat.
sudo docker compose up -d --wait postgres redis rabbitmq minio

echo ">> Миграции БД…"
uv run alembic upgrade head

echo ">> Бот + воркер + раздача файлов (Ctrl+C — остановить всё)…"
trap 'kill 0' EXIT                                   # на выходе гасим все фоновые задачи
uv run celery -A core.celery_app:celery_app worker -B -l info &
uv run python -m bot.main &
# Раздача скачанных файлов: ссылки на то, что не влезает в чат (> 48 МБ).
# Отдельное приложение, а не api.main: у основного API есть POST /subscriptions
# без авторизации и Swagger — наружу его выставлять нельзя. Порт обязан совпадать
# с PUBLIC_BASE_URL в .env, иначе ссылки будут указывать не туда.
uv run uvicorn api.files:app --host 0.0.0.0 --port 8080 &
wait
