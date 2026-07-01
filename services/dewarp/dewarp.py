from __future__ import annotations

import cv2
import numpy as np

from core.config import settings
from services.dewarp.algorithm.config import DewarpConfig
from services.dewarp.algorithm.engine import DewarpEngine

# Движок (с загруженной моделью YOLO) — ленивый синглтон на процесс-воркер:
# модель ~136МБ грузится один раз при первом вызове, а не на каждую задачу.
_engine: DewarpEngine | None = None


def _get_engine() -> DewarpEngine:
    global _engine
    if _engine is None:
        _engine = DewarpEngine(DewarpConfig(weights_path=settings.dewarp_weights_path))
    return _engine


def dewarp_bytes(image_bytes: bytes) -> bytes | None:
    """Деварпинг изображения в байтах: JPEG-вход → JPEG-выход (или None, если документ не найден)."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError("Не удалось декодировать изображение")

    result_bgr = _get_engine().dewarp(image_bgr)
    if result_bgr is None:
        return None

    ok, buf = cv2.imencode(".jpg", result_bgr)
    if not ok:
        raise ValueError("Не удалось закодировать результат")
    return buf.tobytes()