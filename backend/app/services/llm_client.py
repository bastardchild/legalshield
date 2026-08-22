import json
import logging
import re

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMJSONError(ValueError):
    """Raised when the model cannot be coaxed into returning parseable JSON."""


def _normalize_base_url(raw: str) -> str:
    """
    Return the provider root without a trailing `/v1`.

    The OpenAI SDK expects a base_url that already includes the API version, which we
    append in get_llm_client(). Users commonly paste a full endpoint (the Ollama tip in
    the README does exactly this), which previously produced `.../v1/v1` and 404s.
    """
    url = raw.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url


def get_llm_client() -> AsyncOpenAI:
    """
    AsyncOpenAI client for the configured OpenAI-compatible provider.

    Swap base_url + api_key via .env to point at Hermes, Ollama, or anything else.
    An explicit timeout is essential: without it a hanging provider blocks the RQ job
    until job_timeout kills the worker, which leaves the contract stuck in `processing`.
    """
    settings = get_settings()
    return AsyncOpenAI(
        base_url=f"{_normalize_base_url(settings.hermes_base_url)}/v1",
        api_key=settings.hermes_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


async def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    response_format: dict | None = None,
) -> str:
    """Generic chat completion. Returns the assistant message content as a string."""
    settings = get_settings()
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
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise

    choice = response.choices[0]
    content = choice.message.content or ""
    if getattr(choice, "finish_reason", None) == "length":
        # The caller will usually fail to parse this; log it so the cause is obvious.
        logger.warning(
            f"LLM response truncated at max_tokens={max_tokens} (finish_reason=length). "
            "Consider raising max_tokens or shortening the input."
        )
    logger.info(f"LLM response received: {len(content)} chars")
    return content


def extract_json_object(raw: str) -> dict:
    """
    Pull a JSON object out of a model response.

    Handles the three failure modes seen in practice: markdown code fences, a
    conversational preamble ("Here is the JSON:"), and trailing commentary after the
    closing brace. Raises LLMJSONError if nothing parseable is found.
    """
    if not raw or not raw.strip():
        raise LLMJSONError("Model returned an empty response.")

    text = raw.strip()

    # Prefer a fenced block when present.
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    candidates = []
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            span = _outermost_object(candidate)
            if span is None:
                continue
            try:
                parsed = json.loads(span)
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            return parsed
        raise LLMJSONError(f"Expected a JSON object, got {type(parsed).__name__}.")

    raise LLMJSONError(f"No parseable JSON object found in response (first 200 chars): {text[:200]!r}")


def _outermost_object(text: str) -> str | None:
    """
    Return the substring spanning the first balanced {...} object, or None.

    Brace counting rather than a regex, because findings contain nested objects and
    braces inside string values.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text[start:], start):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


async def chat_completion_json(
    messages: list[dict],
    *,
    agent_label: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict:
    """
    Chat completion that must yield a JSON object.

    Requests native JSON mode first; providers that reject `response_format` are
    retried without it. Unparseable output is retried up to `llm_json_retries` times
    with the parse error fed back to the model.
    """
    settings = get_settings()
    attempts = max(1, settings.llm_json_retries + 1)
    convo = list(messages)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            raw = await chat_completion(
                convo,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            if _is_response_format_error(e):
                logger.info(f"[{agent_label}] Provider rejected response_format; retrying without it.")
                raw = await chat_completion(convo, temperature=temperature, max_tokens=max_tokens)
            else:
                raise

        try:
            return extract_json_object(raw)
        except LLMJSONError as e:
            last_error = e
            logger.warning(f"[{agent_label}] JSON parse failed on attempt {attempt}/{attempts}: {e}")
            if attempt == attempts:
                break
            convo = list(messages) + [
                {"role": "assistant", "content": raw[:2000]},
                {
                    "role": "user",
                    "content": (
                        f"That response could not be parsed as JSON ({e}). "
                        "Reply with the JSON object only — no prose, no markdown fences, "
                        "no trailing commentary."
                    ),
                },
            ]

    raise LLMJSONError(f"[{agent_label}] gave up after {attempts} attempts: {last_error}")


def _is_response_format_error(exc: Exception) -> bool:
    """Heuristic: did the provider reject the response_format parameter itself?"""
    msg = str(exc).lower()
    return "response_format" in msg or "json_object" in msg
