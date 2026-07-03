#!/usr/bin/env bash
# Запуск всего проекта одной командой: инфраструктура (docker) + миграции + бот + воркер.
# Логи бота и воркера идут в этот же терминал. Ctrl+C останавливает бот и воркер;
# инфра-контейнеры (postgres/redis/rabbitmq/minio) продолжают жить.
#
# Использование:  ./run.sh
set -uo pipefail
cd "$(dirname "$0")"

echo ">> Инфраструктура (docker: postgres/redis/rabbitmq/minio)…"
sudo docker compose up -d postgres redis rabbitmq minio

echo ">> Миграции БД…"
uv run alembic upgrade head

echo ">> Бот + воркер (Ctrl+C — остановить оба)…"
trap 'kill 0' EXIT                                   # на выходе гасим обе фоновые задачи
uv run celery -A core.celery_app:celery_app worker -B -l info &
uv run python -m bot.main &
wait
