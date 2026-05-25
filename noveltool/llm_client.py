import openai
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import Config


class LLMClient:
    def __init__(self, config: Config):
        self._client = OpenAI(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )
        self._model = config.llm.model
        self._temperature = config.llm.temperature
        self._max_completion_tokens = config.llm.max_completion_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((openai.RateLimitError, openai.APIConnectionError)),
    )
    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=kwargs.get('temperature', self._temperature),
            max_completion_tokens=kwargs.get('max_completion_tokens', self._max_completion_tokens),
        )
        return response.choices[0].message.content.strip()
