"""
OpenRouter API Integration dengan Rotasi Model Otomatis dan Usage Tracking untuk Oline Bot.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_OPENROUTER_MODELS = [
    "poolside/laguna-s-2.1",
    "thinkingmachines/inkling",
    "z-ai/glm-5.2",
    "minimax/minimax-m3",
    "minimax/minimax-m2.7",
    "nvidia/nemotron-3-super",
    "nvidia/nemotron-3-nano-omni",
    "google/gemma-4-31b",
    "google/gemma-4-26b-a4b",
    "cohere/north-mini-code",
]

_LIMITED_MODELS: set[str] = set()
_LOCAL_KV_STORE: dict[str, str] = {}


def get_model_list() -> list[str]:
    """
    Mengambil daftar model OpenRouter dari environment variable OPENROUTER_MODELS.
    Jika tidak diset, menggunakan DEFAULT_OPENROUTER_MODELS.
    """
    raw = os.environ.get("OPENROUTER_MODELS", "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models
    return DEFAULT_OPENROUTER_MODELS.copy()


def convert_gemini_tools_to_openai(tool_declarations: Optional[list[dict]]) -> Optional[list[dict]]:
    """
    Mengonversi deklarasi tool format Gemini/Oline ke format OpenAI/OpenRouter tools.
    """
    if not tool_declarations:
        return None

    openai_tools = []
    for tool in tool_declarations:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        description = tool.get("description", "")
        parameters = tool.get("parameters", {"type": "object", "properties": {}})

        if name:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            })

    return openai_tools if openai_tools else None


async def save_openrouter_rate_limits(model: str, headers: Any) -> None:
    """
    Menyimpan metadata rate-limit (remaining/limit requests & tokens) dari header HTTP OpenRouter ke Vercel KV & Local Cache.
    """
    try:
        from src.kv import set_cache
        rem_req = headers.get("x-ratelimit-remaining-requests") or headers.get("x-ratelimit-remaining")
        limit_req = headers.get("x-ratelimit-limit-requests") or headers.get("x-ratelimit-limit")
        rem_tok = headers.get("x-ratelimit-remaining-tokens")
        limit_tok = headers.get("x-ratelimit-limit-tokens")

        if rem_req is not None or rem_tok is not None or limit_req is not None:
            payload = json.dumps({
                "remaining_requests": rem_req,
                "limit_requests": limit_req,
                "remaining_tokens": rem_tok,
                "limit_tokens": limit_tok,
            })
            key = f"openrouter:ratelimit:{model}"
            _LOCAL_KV_STORE[key] = payload
            await set_cache(key, payload, ttl_seconds=86400)
    except Exception as e:
        logger.warning("Gagal menyimpan rate limit OpenRouter: %s", str(e))


async def record_openrouter_usage(chat_id: int, model: str, req_count: int = 1) -> None:
    """
    Mencatat jumlah penggunaan request per model dan total untuk hari ini ke Vercel KV & Local Cache.
    """
    try:
        from src.kv import set_cache, get_cache
        today = datetime.now().strftime("%Y-%m-%d")

        # Model specific usage
        key_model = f"usage:openrouter:{model}:{today}"
        cur_val = await get_cache(key_model) or _LOCAL_KV_STORE.get(key_model)
        count = int(cur_val or "0") + req_count
        _LOCAL_KV_STORE[key_model] = str(count)
        await set_cache(key_model, str(count), ttl_seconds=86400)

        # Total OpenRouter usage
        key_total = f"usage:openrouter:total:{today}"
        cur_tot = await get_cache(key_total) or _LOCAL_KV_STORE.get(key_total)
        tot_count = int(cur_tot or "0") + req_count
        _LOCAL_KV_STORE[key_total] = str(tot_count)
        await set_cache(key_total, str(tot_count), ttl_seconds=86400)
    except Exception as e:
        logger.warning("Gagal mencatat pemakaian OpenRouter: %s", str(e))


async def get_openrouter_quota_info(chat_id: int = 0) -> dict[str, Any]:
    """
    Mengambil rincian status rotasi model OpenRouter, model aktif, sisa request & token spesifik model aktif,
    model berikutnya pada rotasi, dan status antrean sebelum fallback.
    """
    models = get_model_list()
    today = datetime.now().strftime("%Y-%m-%d")

    from src.kv import get_cache
    active_model = None
    active_model_idx = -1
    next_model = None
    models_status = []
    remaining_in_rotation = 0

    for idx, m in enumerate(models, start=1):
        key_model = f"usage:openrouter:{m}:{today}"
        usage_val = await get_cache(key_model) or _LOCAL_KV_STORE.get(key_model)
        usage_count = int(usage_val or "0")

        is_limited = m in _LIMITED_MODELS
        if is_limited:
            status_str = "Rate Limited (429)"
        elif active_model is None:
            active_model = m
            active_model_idx = idx - 1
            status_str = "Aktif (Primary)"
            remaining_in_rotation += 1
        else:
            if next_model is None and active_model is not None:
                next_model = m
            status_str = f"Antrean ke-{idx}"
            remaining_in_rotation += 1

        models_status.append({
            "order": idx,
            "model": m,
            "status": status_str,
            "usage": usage_count,
        })

    key_total = f"usage:openrouter:total:{today}"
    total_val = await get_cache(key_total) or _LOCAL_KV_STORE.get(key_total)
    total_usage = int(total_val or "0")

    # Ambil rincian sisa kuota spesifik model aktif dari KV atau Local Cache
    active_remaining_info = "Kuota Penuh / Belum Terpakai (Akan beralih jika limit)"
    active_token_info = "Tidak terbatas / Sesuai rate limit OpenRouter"

    if active_model:
        rl_val = await get_cache(f"openrouter:ratelimit:{active_model}") or _LOCAL_KV_STORE.get(f"openrouter:ratelimit:{active_model}")
        if rl_val:
            try:
                rl_data = json.loads(rl_val)
                rem_req = rl_data.get("remaining_requests")
                lim_req = rl_data.get("limit_requests")
                rem_tok = rl_data.get("remaining_tokens")
                lim_tok = rl_data.get("limit_tokens")

                next_name = next_model if next_model else "model berikutnya"
                if rem_req is not None and lim_req is not None:
                    active_remaining_info = f"{rem_req} / {lim_req} request (Sisa {rem_req} req sebelum beralih ke {next_name})"
                elif rem_req is not None:
                    active_remaining_info = f"Sisa {rem_req} request (sebelum beralih ke {next_name})"

                if rem_tok is not None and lim_tok is not None:
                    active_token_info = f"{rem_tok} / {lim_tok} token"
                elif rem_tok is not None:
                    active_token_info = f"Sisa {rem_tok} token"
            except Exception:
                pass

    return {
        "active_model": active_model or (models[0] if models else "Tidak ada"),
        "active_model_order": active_model_idx + 1 if active_model_idx != -1 else 1,
        "next_model": next_model or "Tidak ada (Model terakhir sebelum fallback Groq/Gemini)",
        "active_model_remaining_requests": active_remaining_info,
        "active_model_remaining_tokens": active_token_info,
        "total_models": len(models),
        "remaining_in_rotation": remaining_in_rotation,
        "models_status": models_status,
        "total_requests_today": total_usage,
        "fallback_queue": ["Groq API (Fast Path)", "Gemini API (Slow Path)"],
    }


async def chat_openrouter(
    system_prompt: str,
    history: list[dict],
    user_message: str,
    tool_declarations: Optional[list[dict]] = None,
    chat_id: int = 0,
) -> str:
    """
    Melakukan panggilan chat ke OpenRouter API dengan rotasi model otomatis.
    Jika satu model gagal/limit (429/400/500/timeout), otomatis beralih ke model berikutnya.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY belum dikonfigurasi.")

    model_list = get_model_list()
    openai_tools = convert_gemini_tools_to_openai(tool_declarations)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Dsap09/Oline_Agent",
        "X-Title": "Oline Agent",
    }

    from src.tools import execute_tool

    for model in model_list:
        try:
            logger.info("Mencoba OpenRouter model: %s (chat_id: %s)", model, chat_id)

            messages = [{"role": "system", "content": system_prompt}]
            # Sertakan maksimal 10 pesan terakhir dari riwayat
            for h in history[-10:]:
                role = "assistant" if h.get("role") in ("model", "assistant") else "user"
                text = h.get("text", "").strip()
                if text:
                    messages.append({"role": role, "content": text})

            messages.append({"role": "user", "content": user_message})

            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            }

            if openai_tools:
                payload["tools"] = openai_tools
                payload["tool_choice"] = "auto"

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(OPENROUTER_API_URL, json=payload, headers=headers)

                if resp.status_code != 200:
                    err_msg = resp.text[:200]
                    logger.warning(
                        "OpenRouter model %s gagal (HTTP %d): %s. Beralih ke model berikutnya...",
                        model, resp.status_code, err_msg
                    )
                    _LIMITED_MODELS.add(model)
                    continue

                # Tangkap header rate limit
                await save_openrouter_rate_limits(model, resp.headers)

                res_json = resp.json()
                choices = res_json.get("choices", [])
                if not choices:
                    logger.warning("OpenRouter model %s mengembalikan choices kosong. Mencoba model berikutnya...", model)
                    continue

                msg_obj = choices[0].get("message", {})
                tool_calls = msg_obj.get("tool_calls")

                # 1. Jika tidak ada tool calls, kembalikan teks jawaban langsung
                if not tool_calls:
                    content = msg_obj.get("content", "") or ""
                    if content.strip():
                        await record_openrouter_usage(chat_id, model, 1)
                        # Jika berhasil, hapus dari _LIMITED_MODELS jika sebelumnya ada
                        _LIMITED_MODELS.discard(model)
                        return content.strip()
                    else:
                        logger.warning("OpenRouter model %s mengembalikan content kosong. Mencoba model berikutnya...", model)
                        continue

                # 2. Jika ada tool calls, eksekusi tool call dan kirim hasilnya balik ke OpenRouter
                messages.append(msg_obj)
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name")
                    fn_args_str = tc.get("function", {}).get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_str) if isinstance(fn_args_str, str) else fn_args_str
                    except Exception:
                        fn_args = {}

                    logger.info("OpenRouter tool call: %s(args=%s)", fn_name, fn_args)
                    tool_result = await execute_tool(fn_name, fn_args, chat_id=chat_id)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(tool_result, ensure_ascii=False) if isinstance(tool_result, dict) else str(tool_result),
                    })

                # Kirim permintaan kedua setelah tool execution
                followup_payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                }
                followup_resp = await client.post(OPENROUTER_API_URL, json=followup_payload, headers=headers)
                if followup_resp.status_code == 200:
                    await save_openrouter_rate_limits(model, followup_resp.headers)
                    followup_json = followup_resp.json()
                    followup_choices = followup_json.get("choices", [])
                    if followup_choices:
                        final_text = followup_choices[0].get("message", {}).get("content", "") or ""
                        if final_text.strip():
                            await record_openrouter_usage(chat_id, model, 1)
                            _LIMITED_MODELS.discard(model)
                            return final_text.strip()

                logger.warning("OpenRouter followup response gagal untuk model %s. Mencoba model berikutnya...", model)
                continue

        except httpx.TimeoutException:
            logger.warning("OpenRouter model %s timeout. Beralih ke model berikutnya...", model)
            _LIMITED_MODELS.add(model)
            continue
        except Exception as e:
            logger.warning("OpenRouter model %s error: %s. Beralih ke model berikutnya...", model, str(e))
            _LIMITED_MODELS.add(model)
            continue

    raise RuntimeError("Semua model OpenRouter sedang limit atau mengalami kendala.")
