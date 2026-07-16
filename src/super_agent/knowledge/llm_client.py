"""共享 LLM 客户端：连接池 + 指数退避重试。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator

import httpx
from langsmith import traceable

from super_agent.config import settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class LLMClient:
    """OpenAI 兼容 API 的 HTTP 客户端。

    特性：
      - 连接池复用（keep-alive）
      - 指数退避重试（最多 retry_max 次）
      - 统一构造请求 URL 和鉴权头
    """

    def __init__(
        self,
        retry_max: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        cfg = settings.llm
        base_url = cfg.oneapi_base_url.rstrip("/")
        self.api_url = f"{base_url}/chat/completions"
        self.api_key = cfg.oneapi_api_key
        self.model = cfg.default_model
        self.default_temperature = cfg.default_temperature
        self.default_max_tokens = cfg.max_tokens
        self.timeout = cfg.request_timeout
        self.retry_max = retry_max
        self.retry_base_delay = retry_base_delay

        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.api_url.rsplit("/chat/completions", 1)[0],
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        return self._client

    @traceable(name="llm_client.chat", run_type="llm")
    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """调用 LLM chat/completions 接口，返回完整响应 JSON。"""
        body = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.retry_max + 1):
            try:
                resp = self.client.post("/chat/completions", json=body)
                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in _RETRYABLE_STATUSES and attempt < self.retry_max:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM API returned %d (attempt %d/%d), retrying in %.1fs",
                        resp.status_code, attempt, self.retry_max, delay,
                    )
                    time.sleep(delay)
                    continue

                resp.raise_for_status()
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                if attempt < self.retry_max:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM API %s (attempt %d/%d), retrying in %.1fs: %s",
                        type(e).__name__, attempt, self.retry_max, delay, e,
                    )
                    time.sleep(delay)
                    continue
                logger.error("LLM API failed after %d attempts: %s", self.retry_max, e)
                raise
            except Exception as e:
                last_exc = e
                logger.error("LLM API unexpected error: %s", e)
                raise

        raise last_exc or RuntimeError("LLM API call failed")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @traceable(name="llm_client.chat_stream", run_type="llm")
    def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Generator[str, None, None]:
        """流式调用 LLM chat/completions 接口，逐 token 产出内容。

        使用 SSE (Server-Sent Events) 协议逐个产出 token，
        适合需要实时展示 LLM 生成过程的前端场景。
        """
        body = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
            "stream": True,
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.retry_max + 1):
            try:
                with self.client.stream("POST", "/chat/completions", json=body) as resp:
                    if resp.status_code != 200:
                        if resp.status_code in _RETRYABLE_STATUSES and attempt < self.retry_max:
                            delay = self.retry_base_delay * (2 ** (attempt - 1))
                            logger.warning(
                                "LLM stream returned %d (attempt %d/%d), retrying in %.1fs",
                                resp.status_code, attempt, self.retry_max, delay,
                            )
                            time.sleep(delay)
                            continue
                        resp.raise_for_status()

                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            return
                        chunk = json.loads(payload)
                        choices = chunk.get("choices", [{}])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    return  # stream completed normally
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                if attempt < self.retry_max:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM stream %s (attempt %d/%d), retrying in %.1fs: %s",
                        type(e).__name__, attempt, self.retry_max, delay, e,
                    )
                    time.sleep(delay)
                    continue
                logger.error("LLM stream failed after %d attempts: %s", self.retry_max, e)
                raise
            except Exception as e:
                last_exc = e
                logger.error("LLM stream unexpected error: %s", e)
                raise

        if last_exc:
            raise last_exc
