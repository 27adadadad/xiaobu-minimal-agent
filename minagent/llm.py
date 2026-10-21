"""Real HTTP adapter; tests inject a transport instead of changing production routing."""
import asyncio
import httpx
from .config import Settings


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def complete(self, messages, tools):
        s = self.settings
        if not s.configured:
            raise LLMError("llm_not_configured")
        body = {"model": s.model, "messages": messages, "tools": tools, "tool_choice": "auto", "stream": False}
        async with httpx.AsyncClient(timeout=s.timeout, transport=self.transport) as client:
            for attempt in range(3):
                try:
                    response = await client.post(s.base_url + "/chat/completions", headers={"Authorization": "Bearer " + s.api_key}, json=body)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == 2:
                        raise LLMError("llm_network_error") from exc
                    await asyncio.sleep(0.2 * 2 ** attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.2 * 2 ** attempt)
                        continue
                if response.is_error:
                    # Do not propagate provider bodies: they can contain credentials or user data.
                    raise LLMError("llm_http_" + str(response.status_code))
                try:
                    return response.json()["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise LLMError("llm_invalid_response") from exc
        raise LLMError("llm_unavailable")
