#!/usr/bin/env bash
# Остановить бот и воркер (если запущены).
#
# Инфра-контейнеры НЕ трогаем намеренно: compose-проект infra общий с media-ботом,
# и остановка Postgres отсюда уронила бы его. Когда инфра точно никому не нужна:
#   sudo docker compose stop postgres redis rabbitmq
#
# Использование:  ./stop.sh
set -uo pipefail
cd "$(dirname "$0")"

echo ">> Останавливаю бот и воркер…"
pkill -f "python -m bot.main" 2>/dev/null && echo "   бот остановлен" || echo "   бот не запущен"
# Сужаем по своему имени узла: модуль core.celery_app называется так же
# у media-бота, и шаблон без -n гасил бы заодно его воркер.
pkill -f "celery -A core.celery_app.*-n vacancy@" 2>/dev/null && echo "   воркер остановлен" || echo "   воркер не запущен"

echo ">> Готово. Инфраструктура продолжает работать (она общая)."