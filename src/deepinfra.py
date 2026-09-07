"""
DeepInfra API client untuk Oline bot.
Menggunakan DeepSeek V4 Flash via DeepInfra (OpenAI-compatible API)
untuk intent preview & deploy yang membutuhkan kualitas generasi tinggi.
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

from src.utils import clean_tool_calls

logger = logging.getLogger(__name__)

DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"


def _get_deepinfra_client():
    """Mengembalikan instance OpenAI client yang dikonfigurasi untuk DeepInfra."""
    api_key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "DEEPINFRA_API_KEY environment variable is not set. "
            "Cannot initialize DeepInfra client."
        )
    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url=DEEPINFRA_BASE_URL,
    )


async def chat_deepinfra(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    tool_declarations: list[dict],
    chat_id: int = 0,
) -> str:
    """
    Memanggil DeepSeek V4 Flash via DeepInfra dengan dukungan function calling.
    Menggunakan format OpenAI-compatible.

    Args:
        system_prompt: System prompt lengkap.
        history: Riwayat percakapan dari KV (format [{role, text}, ...]).
        user_message: Pesan pengguna saat ini.
        tool_declarations: Deklarasi tools format Gemini/dict (akan dikonversi ke OpenAI format).
        chat_id: ID chat Telegram untuk inject ke tool executor.

    Returns:
        String respons dari model.
    """
    from src.tools import convert_tools_to_openai_format, execute_tool

    client = _get_deepinfra_client()

    # Build messages array
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # Format history (dibatasi 10 pesan terakhir)
    if history:
        for h in history[-10:]:
            role = h.get("role", "user")
            openai_role = "assistant" if role in ("model", "assistant") else "user"
            text = h.get("text", "") or h.get("content", "")
            if text and text.strip():
                messages.append({"role": openai_role, "content": text.strip()})

    messages.append({"role": "user", "content": user_message})

    # Konversi tools ke format OpenAI
    openai_tools = convert_tools_to_openai_format(tool_declarations) if tool_declarations else []

    model_name = os.environ.get("DEEPINFRA_MODEL", "deepseek-ai/DeepSeek-V4-Flash-0731").strip()

    # Konfigurasi request
    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 4096,
    }
    if openai_tools:
        kwargs["tools"] = openai_tools
        kwargs["tool_choice"] = "auto"

    # Panggil pertama (dijalankan di thread terpisah agar tidak blocking)
    response = await asyncio.to_thread(
        client.chat.completions.create, **kwargs
    )
    response_message = response.choices[0].message

    # Token tracking
    total_tokens = 0
    if hasattr(response, "usage") and response.usage:
        total_tokens += getattr(response.usage, "total_tokens", 0)

    # Function calling loop (maks 3 iterasi)
    max_iterations = 3
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        tool_calls = getattr(response_message, "tool_calls", None)
        if not tool_calls:
            break

        # Append assistant message with tool calls
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

        # Execute tools dan append results
        for tc in tool_calls:
            tc_id = getattr(tc, "id", "")
            func_obj = getattr(tc, "function", None)
            func_name = getattr(func_obj, "name", "") if func_obj else ""
            func_args_str = getattr(func_obj, "arguments", "{}") if func_obj else "{}"

            if isinstance(func_args_str, str):
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON arguments for tool %s: %s", func_name, func_args_str[:100])
                    func_args = {}
            else:
                func_args = func_args_str or {}

            try:
                tool_result = await execute_tool(func_name, func_args, chat_id=chat_id)
            except Exception as ex:
                logger.error("Error executing tool %s via DeepInfra: %s", func_name, str(ex))
                tool_result = {"error": f"Error executing tool {func_name}: {str(ex)}"}

            tool_content = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_content,
            })

        # Panggil ulang untuk mendapatkan respons final dari tool results
        follow_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 4096,
        }

        response = await asyncio.to_thread(
            client.chat.completions.create, **follow_kwargs
        )
        response_message = response.choices[0].message

        if hasattr(response, "usage") and response.usage:
            total_tokens += getattr(response.usage, "total_tokens", 0)

    # Extract final text
    final_text = response_message.content or ""

    try:
        from src.kv import increment_usage
        await increment_usage("deepinfra", {"request": 1, "token": total_tokens})
    except Exception as kv_err:
        logger.warning("Failed to increment deepinfra usage: %s", str(kv_err))

    if total_tokens > 0:
        logger.info(
            "DeepInfra (%s) completed. Total tokens: %d",
            model_name, total_tokens,
        )

    return clean_tool_calls(final_text)
