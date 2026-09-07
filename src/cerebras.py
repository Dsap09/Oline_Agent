"""
Cerebras API client untuk Oline bot.
Menggunakan Cerebras Cloud (llama3.3-70b) via OpenAI-compatible API.
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

from src.utils import clean_tool_calls

logger = logging.getLogger(__name__)

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "llama3.3-70b").strip()


def _get_cerebras_client():
    """Mengembalikan instance OpenAI client yang dikonfigurasi untuk Cerebras Cloud."""
    api_key = os.environ.get("CEREBRAS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CEREBRAS_API_KEY environment variable is not set.")
    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url=CEREBRAS_BASE_URL,
    )


async def chat_cerebras(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    tool_declarations: Optional[list[dict]] = None,
    chat_id: int = 0,
) -> str:
    """
    Memanggil Cerebras Cloud dengan dukungan function calling & riwayat percakapan.

    Args:
        system_prompt: System prompt lengkap.
        history: Riwayat percakapan dari KV.
        user_message: Pesan pengguna saat ini.
        tool_declarations: Deklarasi tools.
        chat_id: ID chat Telegram.

    Returns:
        String respons dari model Cerebras.
    """
    from src.tools import convert_tools_to_openai_format, execute_tool

    logger.info("Mencoba Cerebras... (chat_id: %s)", chat_id)
    client = _get_cerebras_client()

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    if history:
        for h in history[-10:]:
            role = h.get("role", "user")
            openai_role = "assistant" if role in ("model", "assistant") else "user"
            text = h.get("text", "") or h.get("content", "")
            if text and text.strip():
                messages.append({"role": openai_role, "content": text.strip()})

    messages.append({"role": "user", "content": user_message})

    openai_tools = convert_tools_to_openai_format(tool_declarations) if tool_declarations else []

    kwargs: dict[str, Any] = {
        "model": CEREBRAS_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    if openai_tools:
        kwargs["tools"] = openai_tools
        kwargs["tool_choice"] = "auto"

    response = await asyncio.to_thread(
        client.chat.completions.create, **kwargs
    )
    response_message = response.choices[0].message

    total_tokens = 0
    if hasattr(response, "usage") and response.usage:
        total_tokens += getattr(response.usage, "total_tokens", 0)

    max_iterations = 3
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        tool_calls = getattr(response_message, "tool_calls", None)
        if not tool_calls:
            break

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": response_message.content or "",
            "tool_calls": [],
        }
        for tc in tool_calls:
            tc_id = getattr(tc, "id", "")
            func_obj = getattr(tc, "function", None)
            func_name = getattr(func_obj, "name", "") if func_obj else ""
            func_args_str = getattr(func_obj, "arguments", "{}") if func_obj else "{}"

            assistant_msg["tool_calls"].append({
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": func_args_str if isinstance(func_args_str, str) else json.dumps(func_args_str),
                },
            })

        messages.append(assistant_msg)

        for tc in tool_calls:
            tc_id = getattr(tc, "id", "")
            func_obj = getattr(tc, "function", None)
            func_name = getattr(func_obj, "name", "") if func_obj else ""
            func_args_str = getattr(func_obj, "arguments", "{}") if func_obj else "{}"

            if isinstance(func_args_str, str):
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                except json.JSONDecodeError:
                    func_args = {}
            else:
                func_args = func_args_str or {}

            try:
                tool_result = await execute_tool(func_name, func_args, chat_id=chat_id)
            except Exception as ex:
                logger.error("Error executing tool %s via Cerebras: %s", func_name, str(ex))
                tool_result = {"error": f"Error executing tool {func_name}: {str(ex)}"}

            tool_content = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_content,
            })

        follow_kwargs: dict[str, Any] = {
            "model": CEREBRAS_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        response = await asyncio.to_thread(
            client.chat.completions.create, **follow_kwargs
        )
        response_message = response.choices[0].message
        if hasattr(response, "usage") and response.usage:
            total_tokens += getattr(response.usage, "total_tokens", 0)

    try:
        from src.kv import increment_usage
        await increment_usage("cerebras", {"request": 1, "token": total_tokens})
    except Exception as kv_err:
        logger.warning("Failed to increment cerebras usage: %s", str(kv_err))

    final_text = response_message.content or ""
    return clean_tool_calls(final_text)
