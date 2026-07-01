from __future__ import annotations

from openai import AsyncOpenAI

from core.config import settings

# Провайдер-агностичный LLM-клиент: OpenAI SDK, направленный на любой
# OpenAI-совместимый endpoint (по умолчанию — Google Gemini). Смена провайдера
# (Groq/OpenAI/Anthropic) = только настройки llm_base_url/llm_api_key/llm_model,
# код /ask не меняется. Если ключ пуст — клиент создаётся, но вызов упадёт;
# /ask обрабатывает это отдельно («LLM не настроен»).
llm_client: AsyncOpenAI = AsyncOpenAI(
    api_key=settings.llm_api_key or "not-set",
    base_url=settings.llm_base_url,
)
