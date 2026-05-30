import threading
import time

import openai
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import logger
from .config import Config


class _RateLimiter:
    """최소 간격을 보장하는 간단한 rate limiter."""
    def __init__(self, rpm: int):
        self._interval = 60.0 / rpm
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            remaining = self._interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last = time.monotonic()


class LLMClient:
    def __init__(self, config: Config):
        self._client = OpenAI(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )
        self._model = config.llm.model
        self._temperature = config.llm.temperature
        self._max_completion_tokens = config.llm.max_completion_tokens

        self._fallback_client: OpenAI | None = None
        self._fallback_model: str | None = None
        self._fallback_limiter: _RateLimiter | None = None
        if config.llm.fallback:
            fb = config.llm.fallback
            self._fallback_client = OpenAI(base_url=fb.base_url, api_key=fb.api_key)
            self._fallback_model = fb.model
            self._fallback_limiter = _RateLimiter(fb.rpm_limit)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
    )
    def _chat_primary(self, messages: list[dict], **kwargs) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=kwargs.get('temperature', self._temperature),
            max_completion_tokens=kwargs.get('max_completion_tokens', self._max_completion_tokens),
        )
        return response.choices[0].message.content.strip()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=6, max=60),
        retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
    )
    def _chat_fallback(self, messages: list[dict], **kwargs) -> str:
        assert self._fallback_client and self._fallback_model and self._fallback_limiter
        self._fallback_limiter.wait()
        response = self._fallback_client.chat.completions.create(
            model=self._fallback_model,
            messages=messages,
            temperature=kwargs.get('temperature', self._temperature),
            max_completion_tokens=kwargs.get('max_completion_tokens', self._max_completion_tokens),
        )
        return response.choices[0].message.content.strip()

    def chat(self, messages: list[dict], **kwargs) -> str:
        try:
            return self._chat_primary(messages, **kwargs)
        except Exception as e:
            if self._fallback_client is None:
                raise
            log = logger.get()
            log.warning('[LLM] primary 실패 → fallback(%s) 사용: %s', self._fallback_model, e)
            print(f'[경고] primary LLM 실패 → fallback({self._fallback_model}) 사용')
            return self._chat_fallback(messages, **kwargs)
