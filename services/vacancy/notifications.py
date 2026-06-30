from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message

from core.config import settings

logger = logging.getLogger(__name__)

NOTIFICATIONS_QUEUE = "notifications"
DLX_EXCHANGE = "notifications.dlx"      # dead-letter exchange
DLQ_QUEUE = "notifications.dlq"         # dead-letter queue (необработанные сообщения)


async def _declare_topology(channel: aio_pika.abc.AbstractChannel) -> aio_pika.abc.AbstractQueue:
    """Объявить очередь уведомлений с привязкой к DLQ (идемпотентно).

    Основная очередь durable; сообщения, отклонённые без requeue, уходят в DLX → DLQ.
    """
    dlx = await channel.declare_exchange(DLX_EXCHANGE, ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(DLQ_QUEUE, durable=True)
    await dlq.bind(dlx)

    queue = await channel.declare_queue(
        NOTIFICATIONS_QUEUE,
        durable=True,
        arguments={"x-dead-letter-exchange": DLX_EXCHANGE},
    )
    return queue


async def publish_notifications(payloads: list[dict]) -> None:
    """Опубликовать уведомления в очередь (persistent). Открывает соединение на вызов."""
    if not payloads:
        return
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await _declare_topology(channel)
        for payload in payloads:
            await channel.default_exchange.publish(
                Message(
                    json.dumps(payload).encode(),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key=NOTIFICATIONS_QUEUE,
            )
    logger.info("Опубликовано уведомлений: %s", len(payloads))


async def run_notifier(send: Callable[[int, str], Awaitable[None]]) -> None:
    """Consumer уведомлений: читает очередь и доставляет сообщения через send().

    Фоновая asyncio-задача внутри бота. При ошибке доставки сообщение
    отклоняется без requeue → уходит в DLQ (не теряется, не зацикливается).
    `send(user_id, text)` — обычно bot.send_message.
    """
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    queue = await _declare_topology(channel)

    logger.info("Notifier запущен, слушаю очередь %s", NOTIFICATIONS_QUEUE)
    async with queue.iterator() as it:
        async for message in it:
            # process(requeue=False): ack при успехе; при исключении — nack без
            # requeue → сообщение dead-letter'ится в DLQ.
            async with message.process(requeue=False):
                payload = json.loads(message.body)
                await send(payload["user_id"], payload["text"])