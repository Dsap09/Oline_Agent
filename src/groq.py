"""
Groq API client untuk Oline bot (Fast Path).
Mengelola percakapan cepat (sapaan, obrolan umum) menggunakan model Groq openai/gpt-oss-20b.
Dilengkapi retry mechanism (exponential backoff) dan fallback exception.
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


async def chat_groq(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    max_retries: int = 3,
    chat_id: Optional[int] = None,
) -> str:
    """
    Panggil Groq API untuk Fast Path.
    Return teks respons atau raise exception jika terjadi kesalahan/rate limit.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set.")

    try:
        from groq import AsyncGroq
    except ImportError:
        raise ImportError(
            "Package 'groq' belum terinstall. Jalankan 'pip install groq'."
        )

    client = AsyncGroq(api_key=api_key)

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Format riwayat percakapan (dibatasi 10 pesan terakhir / 5 putaran)
    if history:
        for h in history[-10:]:
            role = h.get("role", "user")
            groq_role = "assistant" if role in ("model", "assistant") else "user"
            text = h.get("text", "") or h.get("content", "")
            if text and text.strip():
                messages.append({"role": groq_role, "content": text.strip()})

    messages.append({"role": "user", "content": user_message})

    candidate_models = [GROQ_MODEL]
    for alt_model in ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]:
        if alt_model not in candidate_models:
            candidate_models.append(alt_model)
    candidate_models = candidate_models[:2]

    last_exception = None
    for model_name in candidate_models:
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.9,
                    max_tokens=1024,
                )

                if response.choices and len(response.choices) > 0:
                    content = response.choices[0].message.content
                    if content and content.strip():
                        logger.info("Successfully received response from Groq (%s)", model_name)
                        
                        # Track Groq token usage
                        if chat_id:
                            try:
                                from src.kv import save_groq_usage

                                tokens_used = 0
                                if hasattr(response, "usage") and response.usage:
                                    usage = response.usage
                                    if isinstance(usage, dict):
                                        tokens_used = usage.get("total_tokens", 0) or (
                                            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                                        )
                                    else:
                                        tokens_used = getattr(usage, "total_tokens", 0) or (
                                            getattr(usage, "prompt_tokens", 0) + getattr(usage, "completion_tokens", 0)
                                        )

                                if tokens_used == 0:
                                    prompt_len = sum(
                                        len(str(m.get("content", "")))
                                        for m in messages
                                        if isinstance(m, dict)
                                    )
                                    resp_len = len(content) if content else 100
                                    tokens_used = max(15, (prompt_len + resp_len) // 4)

                                logger.info(
                                    "Saving Groq token usage for chat_id %s: %d tokens",
                                    chat_id,
                                    tokens_used,
                                )
                                await save_groq_usage(chat_id, tokens_used)
                            except Exception as kv_err:
                                logger.warning("Failed to save Groq token usage: %s", str(kv_err))

                        try:
                            from src.kv import increment_usage
                            await increment_usage("groq", {"request": 1, "token": tokens_used})
                        except Exception as kv_err:
                            logger.warning("Failed to increment groq usage: %s", str(kv_err))

                        return content.strip()

                raise ValueError("Groq returned empty response choices.")

            except Exception as e:
                last_exception = e
                logger.warning(
                    "Groq API call (%s) attempt %d/%d failed: %s",
                    model_name,
                    attempt + 1,
                    max_retries,
                    str(e),
                )
                if attempt < max_retries - 1:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    await asyncio.sleep(backoff)

    if last_exception:
        raise last_exception
    raise RuntimeError("Groq API request failed after retries.")


async def chat_groq_with_tools(
    system_prompt: str,
    history: list[dict[str, Any]],
    user_message: str,
    tools: list[dict[str, Any]],
    max_retries: int = 3,
    chat_id: Optional[int] = None,
) -> str:
    """
    Panggil Groq API untuk Slow Path Fallback dengan Function Calling.
    Jika Groq memutuskan memanggil fungsi (tool call), eksekusi, lalu kirim ulang hasilnya ke Groq.
    Jika model pilihan gagal atau function calling error, coba model alternatif yang mendukung function calling.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set.")

    try:
        from groq import AsyncGroq
    except ImportError:
        raise ImportError(
            "Package 'groq' belum terinstall. Jalankan 'pip install groq'."
        )

    from src.tools import convert_tools_to_openai_format, execute_tool

    client = AsyncGroq(api_key=api_key)
    openai_tools = convert_tools_to_openai_format(tools)

    candidate_models = [GROQ_MODEL]
    for alt_model in ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]:
        if alt_model not in candidate_models:
            candidate_models.append(alt_model)
    candidate_models = candidate_models[:2]

    last_exception = None
    for model_name in candidate_models:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if history:
            for h in history[-10:]:
                role = h.get("role", "user")
                groq_role = "assistant" if role in ("model", "assistant") else "user"
                text = h.get("text", "") or h.get("content", "")
                if text and text.strip():
                    messages.append({"role": groq_role, "content": text.strip()})

        messages.append({"role": "user", "content": user_message})

        for attempt in range(max_retries):
            try:
                # First call: determine if Groq wants to invoke tools
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    tools=openai_tools if openai_tools else None,
                    tool_choice="auto" if openai_tools else None,
                    temperature=0.7,
                    max_tokens=1024,
                )

                if not response.choices:
                    raise ValueError("Groq returned empty response choices.")

                response_message = response.choices[0].message
                total_tokens = 0

                if hasattr(response, "usage") and response.usage:
                    usage = response.usage
                    if isinstance(usage, dict):
                        total_tokens += usage.get("total_tokens", 0)
                    else:
                        total_tokens += getattr(usage, "total_tokens", 0)

                tool_calls = getattr(response_message, "tool_calls", None)

                if not tool_calls:
                    content = response_message.content or ""
                    try:
                        from src.kv import increment_usage
                        await increment_usage("groq", {"request": 1, "token": total_tokens})
                    except Exception as kv_err:
                        logger.warning("Failed to increment groq usage: %s", str(kv_err))

                    if chat_id and total_tokens > 0:
                        try:
                            from src.kv import save_groq_usage
                            await save_groq_usage(chat_id, total_tokens)
                        except Exception as kv_err:
                            logger.warning("Failed to save Groq token usage: %s", str(kv_err))
                    return content.strip()

                # Copy messages list for tool call roundtrip
                messages_second = list(messages)

                # Append assistant message with tool calls
                assistant_msg_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": response_message.content or "",
                    "tool_calls": [],
                }
                for tc in tool_calls:
                    tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "")
                    func_obj = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
                    func_name = getattr(func_obj, "name", None) or (func_obj.get("name") if isinstance(func_obj, dict) else "")
                    func_args_str = getattr(func_obj, "arguments", None) or (func_obj.get("arguments") if isinstance(func_obj, dict) else "{}")

                    assistant_msg_dict["tool_calls"].append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": func_args_str if isinstance(func_args_str, str) else json.dumps(func_args_str),
                        },
                    })

                messages_second.append(assistant_msg_dict)

                # Execute tools and append results
                for tc in tool_calls:
                    tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "")
                    func_obj = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
                    func_name = getattr(func_obj, "name", None) or (func_obj.get("name") if isinstance(func_obj, dict) else "")
                    func_args_str = getattr(func_obj, "arguments", None) or (func_obj.get("arguments") if isinstance(func_obj, dict) else "{}")

                    if isinstance(func_args_str, str):
                        try:
                            func_args = json.loads(func_args_str) if func_args_str else {}
                        except Exception:
                            func_args = {}
                    else:
                        func_args = func_args_str or {}

                    try:
                        tool_result = await execute_tool(func_name, func_args, chat_id=chat_id or 0)
                    except Exception as ex:
                        logger.error("Error executing tool %s: %s", func_name, str(ex))
                        tool_result = {"error": f"Error executing tool {func_name}: {str(ex)}"}

                    tool_content = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result

                    messages_second.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_content,
                    })

                # Second call: generate final text response based on tool results
                final_response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages_second,
                    temperature=0.7,
                    max_tokens=1024,
                )

                if hasattr(final_response, "usage") and final_response.usage:
                    usage = final_response.usage
                    if isinstance(usage, dict):
                        total_tokens += usage.get("total_tokens", 0)
                    else:
                        total_tokens += getattr(usage, "total_tokens", 0)

                try:
                    from src.kv import increment_usage
                    await increment_usage("groq", {"request": 1, "token": total_tokens})
                except Exception as kv_err:
                    logger.warning("Failed to increment groq usage: %s", str(kv_err))

                if chat_id and total_tokens > 0:
                    try:
                        from src.kv import save_groq_usage
                        await save_groq_usage(chat_id, total_tokens)
                    except Exception as kv_err:
                        logger.warning("Failed to save Groq token usage: %s", str(kv_err))

                if final_response.choices and len(final_response.choices) > 0:
                    final_content = final_response.choices[0].message.content or ""
                    return final_content.strip()

                return ""

            except Exception as e:
                last_exception = e
                logger.warning(
                    "Groq Slow Path (%s) attempt %d/%d failed: %s",
                    model_name,
                    attempt + 1,
                    max_retries,
                    str(e),
                )
                if attempt < max_retries - 1:
                    backoff = 2**attempt
                    await asyncio.sleep(backoff)

    if last_exception:
        raise last_exception
    raise RuntimeError("Groq Slow Path API request failed after retries.")

