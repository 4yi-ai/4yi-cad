"""OpenAI-compatible client for the 4yi LLM gateway.

The platform injects OPENAI_BASE_URL (-> ${origin}/api/v1), OPENAI_API_KEY (the
per-install xclaw-bsl-* token) and TEXT_MODEL. We call ${base_url}/chat/completions
only — the /responses endpoint is rejected for marketplace apps. The gateway
forwards the whole body (including `tools`/`tool_choice`), so a tool-calling agent
loop works against any tool-capable upstream model.

Each call is capped at ~290s upstream; the self-correction loop must therefore be
multiple bounded calls, not one long call (handled in app/agent/loop.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 280.0  # stay under the ~290s gateway envelope


class GatewayError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class ChatCompletion:
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class GatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_config(cls, cfg, **kwargs) -> "GatewayClient":
        return cls(
            base_url=cfg.openai_base_url,
            api_key=cfg.openai_api_key,
            model=cfg.text_model,
            **kwargs,
        )

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
    ) -> ChatCompletion:
        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        try:
            resp = await self._http.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway request failed: {exc}") from exc

        if resp.status_code // 100 != 2:
            raise GatewayError(
                f"gateway returned {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_json(resp),
            )

        data = resp.json()
        message = (data.get("choices") or [{}])[0].get("message", {}) or {}
        return ChatCompletion(
            content=message.get("content"),
            tool_calls=message.get("tool_calls") or [],
            raw=data,
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - best-effort error body
        return resp.text
