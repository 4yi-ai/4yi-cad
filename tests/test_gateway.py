"""Unit tests for the OpenAI-compatible gateway client.

We never hit the network: an httpx.MockTransport captures the outgoing request
and returns canned responses. Contract under test (plan gateway section):
  - calls ${base_url}/chat/completions with Bearer auth and the injected model
  - forwards `tools` for the tool-calling agent loop
  - parses assistant content and tool_calls
  - raises on non-2xx
"""

import httpx
import pytest

from app.gateway import ChatCompletion, GatewayClient, GatewayError


def _client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return GatewayClient(
        base_url="https://platform.example/api/v1",
        api_key="xclaw-bsl-token",
        model="anthropic.claude-sonnet-4-6",
        http_client=http,
    )


async def test_posts_to_chat_completions_with_bearer_and_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi", "role": "assistant"}}]},
        )

    gw = _client(handler)
    await gw.chat_completion([{"role": "user", "content": "make a box"}])

    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer xclaw-bsl-token"
    assert seen["json"]["model"] == "anthropic.claude-sonnet-4-6"
    assert seen["json"]["messages"][0]["content"] == "make a box"


async def test_forwards_tools_when_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["json"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok", "role": "assistant"}}]}
        )

    tools = [{"type": "function", "function": {"name": "run_cadquery"}}]
    gw = _client(handler)
    await gw.chat_completion([{"role": "user", "content": "x"}], tools=tools)

    assert seen["json"]["tools"] == tools


async def test_parses_content_and_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_cadquery",
                                        "arguments": '{"script": "box(1,1,1)"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    gw = _client(handler)
    out = await gw.chat_completion([{"role": "user", "content": "x"}])

    assert isinstance(out, ChatCompletion)
    assert out.content is None
    assert out.tool_calls[0]["function"]["name"] == "run_cadquery"


async def test_non_2xx_raises_gateway_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "insufficient balance"})

    gw = _client(handler)
    with pytest.raises(GatewayError) as exc:
        await gw.chat_completion([{"role": "user", "content": "x"}])

    assert exc.value.status_code == 402
