from __future__ import annotations

import aioboto3

from core.config import settings

# aioboto3-сессия: клиент создаётся per-operation через async context manager
# (`async with session.client("s3", ...)`). Сессия дешёвая и потокобезопасная.
session = aioboto3.Session()


def _client():
    """Контекстный менеджер S3-клиента к MinIO (S3-совместимое API)."""
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


async def ensure_buckets() -> None:
    """Создать бакеты для Dewarp, если их ещё нет (idempotent)."""
    async with _client() as s3:
        existing = {b["Name"] for b in (await s3.list_buckets())["Buckets"]}
        for bucket in (settings.s3_bucket_input, settings.s3_bucket_output):
            if bucket not in existing:
                await s3.create_bucket(Bucket=bucket)


async def put_object(bucket: str, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
    """Загрузить байты в S3."""
    async with _client() as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


async def get_object(bucket: str, key: str) -> bytes:
    """Скачать объект из S3 как байты."""
    async with _client() as s3:
        resp = await s3.get_object(Bucket=bucket, Key=key)
        async with resp["Body"] as stream:
            return await stream.read()
