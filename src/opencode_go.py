"""
OpenCode Go API client untuk Oline bot.
Menggunakan endpoint OpenAI-compatible OpenCode Zen Go
(https://opencode.ai/zen/go/v1) dengan dukungan function calling.
Dipakai untuk model pilihan manual user via command /models.
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

from src.utils import clean_tool_calls

logger = logging.getLogger(__name__)

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_DEFAULT_MODEL = "deepseek-v4-flash"

# Batas waktu per panggilan. Landing page butuh ruang lebih; caller (handlers)
# bisa menimpa lewat parameter timeout sesuai jalur.
OPENCODE_GO_TIMEOUT = 150.0


def _get_opencode_go_client(chat_id: int = 0):
    """
    Mengembalikan instance OpenAI client yang dikonfigurasi untuk OpenCode Go.

    OpenCode Go MEWAJIBKAN:
    - User-Agent custom (bukan nama SDK/HTTP library generik).
    - Header `x-opencode-session` berisi ID sesi stabil per percakapan
      (dipakai untuk routing & prompt caching). Tanpa ini, respons 400 MissingSessionID.
    """
    api_key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "OPENCODE_GO_API_KEY environment variable is not set. "
            "Cannot initialize OpenCode Go client."
        )
    from openai import OpenAI
    session_id = f"oline-{chat_id}" if chat_id else "oline-system"
    return OpenAI(
        api_key=api_key,
        base_url=OPENCODE_GO_BASE_URL,
        default_headers={
            "User-Agent": "Oline-Agent/1.0",
            "x-opencode-session": session_id,
        },
    )


async def _create_completion(client, timeout: float, **kwargs):
    """
    Membuat chat completion dengan penanganan khusus model yang hanya menerima
    temperature tertentu (mis. Kimi K2.7 Code: hanya temperature=1).
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(client.chat.completions.create, **kwargs),
            timeout=timeout,
        )
    except Exception as e:
        text = str(e).lower()
        if kwargs.get("temperature") != 1.0 and "temperature" in text and "1" in text:
            retry_kwargs = {**kwargs, "temperature": 1.0}
            return await asyncio.wait_for(
                asyncio.to_thread(client.chat.completions.create, **retry_kwargs),
                timeout=timeout,
            )
        raise


async def chat_opencode_go(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    tool_declarations: Optional[list[dict]] = None,
    chat_id: int = 0,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    temperature: float = 0.7,
) -> str:
    """
    Memanggil model OpenCode Go dengan dukungan function calling & riwayat percakapan.

    Args:
        system_prompt: System prompt lengkap.
        history: Riwayat percakapan dari KV (format [{role, text}, ...]).
        user_message: Pesan pengguna saat ini.
        tool_declarations: Deklarasi tools format Gemini/dict (dikonversi ke OpenAI).
        chat_id: ID chat Telegram untuk inject ke tool executor.
        model: ID model OpenCode Go (default deepseek-v4-flash).
        timeout: Batas waktu per panggilan API (detik).
        temperature: Temperatur generasi (beberapa model hanya menerima nilai tertentu).

    Returns:
        String respons dari model.
    """
    from src.tools import convert_tools_to_openai_format, execute_tool

    client = _get_opencode_go_client(chat_id)
    model_name = (model or OPENCODE_GO_DEFAULT_MODEL).strip()
    call_timeout = timeout or OPENCODE_GO_TIMEOUT

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
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
    }
    if openai_tools:
        kwargs["tools"] = openai_tools
        kwargs["tool_choice"] = "auto"

    response = await _create_completion(client, call_timeout, **kwargs)
    response_message = response.choices[0].message

    total_tokens = 0
    if hasattr(response, "usage") and response.usage:
        total_tokens += getattr(response.usage, "total_tokens", 0)

    max_iterations = 3
    iteration = 0

    is_landing = any(
        (t.get("function", {}) or {}).get("name") == "preview_with_codepen"
        for t in openai_tools
    )
    preview_called = False

    follow_kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
    }

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
                logger.error("Error executing tool %s via OpenCode Go: %s", func_name, str(ex))
                tool_result = {"error": f"Error executing tool {func_name}: {str(ex)}"}

            if func_name == "preview_with_codepen":
                preview_called = True

            tool_content = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_content,
            })

        response = await _create_completion(client, call_timeout, **follow_kwargs)
        response_message = response.choices[0].message
        if hasattr(response, "usage") and response.usage:
            total_tokens += getattr(response.usage, "total_tokens", 0)

    final_text = response_message.content or ""

    # Enforcement landing: paksa satu iterasi preview bila tool belum dipanggil.
    if is_landing and not preview_called:
        logger.warning("OpenCode Go tidak memanggil preview_with_codepen; memaksa iterasi preview.")
        try:
            messages.append({
                "role": "user",
                "content": (
                    "Instruksi sistem: kamu BELUM memanggil tool `preview_with_codepen`, jadi "
                    "TASK BELUM SELESAI. Sekarang WAJIB panggil tool `preview_with_codepen` dengan "
                    "parameter lengkap (title, html, css, js) berisi kode HTML/CSS/JS landing page "
                    "yang sudah kamu rancang. JANGAN hanya menulis teks atau berjanji membuat "
                    "preview. Setelah tool dipanggil, sampaikan ringkasan singkat beserta link "
                    "preview yang dihasilkan."
                ),
            })
            force_kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 8192,
                "tools": openai_tools,
                "tool_choice": "auto",
            }
            response = await _create_completion(client, call_timeout, **force_kwargs)
            response_message = response.choices[0].message
            if hasattr(response, "usage") and response.usage:
                total_tokens += getattr(response.usage, "total_tokens", 0)

            forced_calls = getattr(response_message, "tool_calls", None)
            if forced_calls:
                messages.append({
                    "role": "assistant",
                    "content": response_message.content or "",
                    "tool_calls": [
                        {
                            "id": getattr(tc, "id", ""),
                            "type": "function",
                            "function": {
                                "name": getattr(getattr(tc, "function", None), "name", ""),
                                "arguments": getattr(getattr(tc, "function", None), "arguments", "{}"),
                            },
                        }
                        for tc in forced_calls
                    ],
                })
                for tc in forced_calls:
                    func_name = getattr(getattr(tc, "function", None), "name", "")
                    raw_args = getattr(getattr(tc, "function", None), "arguments", "{}")
                    try:
                        func_args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        func_args = {}
                    try:
                        tool_result = await execute_tool(func_name, func_args, chat_id=chat_id)
                    except Exception as ex:
                        logger.error("Error executing tool %s via OpenCode Go: %s", func_name, str(ex))
                        tool_result = {"error": f"Error executing tool {func_name}: {str(ex)}"}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": getattr(tc, "id", ""),
                        "content": json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result,
                    })

                response = await _create_completion(client, call_timeout, **follow_kwargs)
                response_message = response.choices[0].message
                final_text = response_message.content or ""
                if hasattr(response, "usage") and response.usage:
                    total_tokens += getattr(response.usage, "total_tokens", 0)
        except Exception as e:
            logger.warning("Enforcement preview OpenCode Go gagal: %s", str(e))

    try:
        from src.kv import increment_usage
        await increment_usage("opencode_go", {"request": 1, "token": total_tokens})
    except Exception as kv_err:
        logger.warning("Failed to increment opencode_go usage: %s", str(kv_err))

    if total_tokens > 0:
        logger.info(
            "OpenCode Go (%s) completed. Total tokens: %d",
            model_name, total_tokens,
        )

    return clean_tool_calls(final_text)
