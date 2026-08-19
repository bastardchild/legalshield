import logging
from openai import AsyncOpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_llm_client() -> AsyncOpenAI:
    """
    Returns an AsyncOpenAI client pointed at the configured LLM provider.
    Hermes uses an OpenAI-compatible API — swap base_url + api_key via .env
    to redirect to Ollama or any other OpenAI-compatible endpoint.
    """
    return AsyncOpenAI(
        base_url=f"{settings.hermes_base_url}/v1",
        api_key=settings.hermes_api_key,
    )


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    response_format: dict | None = None,
) -> str:
    """
    Generic chat completion call against any OpenAI-compatible provider.
    Returns the assistant message content as a string.
    """
    client = get_llm_client()
    model_name = model or settings.llm_model

    kwargs = dict(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if response_format:
        kwargs["response_format"] = response_format

    logger.info(f"LLM call → model={model_name}, messages={len(messages)}, max_tokens={max_tokens}")
    try:
        response = await client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        logger.info(f"LLM response received: {len(content or '')} chars")
        return content or ""
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise
