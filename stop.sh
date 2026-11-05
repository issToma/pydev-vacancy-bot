#!/usr/bin/env bash
# Остановить весь проект: бот и воркер (если запущены) и инфра-контейнеры.
# Инфра останавливается через docker compose stop — контейнеры остаются выключенными
# (в т.ч. после перезагрузки), пока снова не запустишь ./run.sh
#
# Использование:  ./stop.sh
set -uo pipefail
cd "$(dirname "$0")"

echo ">> Останавливаю бот и воркер…"
pkill -f "python -m bot.main" 2>/dev/null && echo "   бот остановлен" || echo "   бот не запущен"
# Сужаем по своему имени узла: модуль core.celery_app называется так же
# у media-бота, и шаблон без -n гасил бы заодно его воркер.
pkill -f "celery -A core.celery_app.*-n vacancy@" 2>/dev/null && echo "   воркер остановлен" || echo "   воркер не запущен"

echo ">> Останавливаю инфру (docker)…"
sudo docker compose stop postgres redis rabbitmq

echo ">> Готово — всё остановлено."