# -*- coding: utf-8 -*-
"""
Codex Responses API adapter.

Bypasses LiteLLM for the Codex backend (chatgpt.com/backend-api/codex)
which requires store=false and stream=true — params that LiteLLM's
responses bridge does not forward correctly.
"""

import json
import logging
import os
from typing import Any, Dict, List

import httpx
import litellm
from litellm.types.utils import ChatCompletionMessageToolCall, Function

logger = logging.getLogger(__name__)


def is_codex_backend(config: Any) -> bool:
    base_url = getattr(config, "openai_base_url", "") or ""
    return "chatgpt.com/backend-api/codex" in base_url


def codex_completion(call_kwargs: Dict[str, Any], config: Any) -> Any:
    """Call the Codex Responses API directly (streaming SSE)."""
    messages = call_kwargs.get("messages", [])
    instructions = ""
    input_msgs: List[Dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            instructions = m["content"]
        elif m["role"] == "tool":
            input_msgs.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": str(m.get("content", "")),
            })
        elif m["role"] == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {"name": getattr(tc.function, "name", ""), "arguments": getattr(tc.function, "arguments", "")}
                input_msgs.append({
                    "type": "function_call",
                    "call_id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", ""),
                })
        else:
            input_msgs.append({"role": m["role"], "content": str(m.get("content", "") or "")})

    model_name = call_kwargs["model"]
    if "/" in model_name:
        model_name = model_name.split("/")[-1]

    api_key = call_kwargs.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    base_url = (getattr(config, "openai_base_url", "") or "").rstrip("/")

    body: Dict[str, Any] = {
        "model": model_name,
        "instructions": instructions or "You are a helpful assistant.",
        "input": input_msgs,
        "store": False,
        "stream": True,
    }

    if call_kwargs.get("tools"):
        converted_tools = []
        for tool in call_kwargs["tools"]:
            if tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                converted_tools.append({
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
            else:
                converted_tools.append(tool)
        body["tools"] = converted_tools

    full_text = ""
    usage_data: Dict[str, int] = {}
    tool_calls: List[ChatCompletionMessageToolCall] = []

    with httpx.stream(
        "POST",
        f"{base_url}/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=httpx.Timeout(120.0),
    ) as resp:
        if resp.status_code != 200:
            error_body = resp.read().decode()
            raise litellm.BadRequestError(
                message=f"Codex API error ({resp.status_code}): {error_body}",
                model=model_name,
                llm_provider="openai",
            )
        for line in resp.iter_lines():
            if not line.strip():
                continue
            if line.startswith("data: "):
                line = line[6:]
            if line == "[DONE]":
                break
            try:
                evt = json.loads(line)
                etype = evt.get("type", "")
                if etype == "response.output_text.delta":
                    full_text += evt.get("delta", "")
                elif etype == "response.output_item.done":
                    item = evt.get("item", {})
                    if item.get("type") == "function_call":
                        tool_calls.append(ChatCompletionMessageToolCall(
                            id=item.get("call_id", ""),
                            type="function",
                            function=Function(
                                name=item.get("name", ""),
                                arguments=item.get("arguments", ""),
                            ),
                        ))
                elif etype == "response.completed":
                    r = evt.get("response", {})
                    u = r.get("usage", {})
                    usage_data = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("total_tokens", 0),
                    }
            except (json.JSONDecodeError, KeyError):
                pass

    response = litellm.ModelResponse()
    response.choices[0].message.content = full_text or None
    if tool_calls:
        response.choices[0].message.tool_calls = tool_calls
    response.usage = litellm.Usage(**usage_data) if usage_data else None
    logger.info("[Codex] result: content_len=%s, tool_calls=%d", len(full_text), len(tool_calls))
    return response
