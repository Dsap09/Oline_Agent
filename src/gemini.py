"""
Gemini AI client untuk Oline bot.
Mengelola percakapan dengan Gemini API (menggunakan google-genai SDK)
termasuk function calling, memori pengguna, dan riwayat percakapan.
"""

import asyncio
from datetime import datetime
import json
import logging
import os
from typing import Any, Optional

from google import genai
from google.genai import types

from src.kv import (
    clear_pending_task,
    get_history,
    get_memory,
    get_pending_task,
    save_history,
    save_memory,
    save_pending_task,
    save_usage,
    update_pending_task_retry_count,
)
from src.personas import (
    MEMORY_INJECTION_TEMPLATE,
    OLINE_SYSTEM_PROMPT,
)
from src.tools import TOOL_EXECUTORS, get_tools_for_intent
from src.utils import clean_tool_calls, format_date_indonesian, get_current_time_context

logger = logging.getLogger(__name__)

# Konfigurasi Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")


def _get_client() -> genai.Client:
    """Mengembalikan instance Client dari google-genai SDK."""
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY environment variable is not set. "
            "Cannot initialize Gemini client."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _convert_schema_types_to_uppercase(schema: Any) -> Any:
    """
    Mengonversi nilai 'type' dalam schema dictionary ke enum kapital (misal 'object' -> 'OBJECT', 'string' -> 'STRING').
    Google GenAI SDK & Gemini API membutuhkan tipe kapital pada deklarasi function parameters.
    """
    if isinstance(schema, dict):
        new_schema = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                new_schema[key] = value.upper()
            else:
                new_schema[key] = _convert_schema_types_to_uppercase(value)
        return new_schema
    elif isinstance(schema, list):
        return [_convert_schema_types_to_uppercase(item) for item in schema]
    return schema


def _build_tools(tool_declarations: list[dict]) -> Optional[list[types.Tool]]:
    """Build google-genai Tool objects dari daftar deklarasi terfilter."""
    if not tool_declarations:
        return None
    function_declarations = []
    for tool_decl in tool_declarations:
        params = _convert_schema_types_to_uppercase(tool_decl.get("parameters", {}))
        function_declarations.append(
            types.FunctionDeclaration(
                name=tool_decl["name"],
                description=tool_decl.get("description", ""),
                parameters=params,
            )
        )
    return [types.Tool(function_declarations=function_declarations)]


async def _build_system_prompt_async(memory: str, user_name: str = "Teman") -> str:
    """Build system prompt lengkap dengan nama pengguna, memori KV, dan memori Notion."""
    if user_name and user_name not in ("Anonim", "Teman"):
        user_info = f"- Nama Pengguna: {user_name} (Sapa pengguna secara ramah dan santai dengan nama {user_name})."
    else:
        user_info = "- Nama Pengguna belum diketahui secara pasti. Jika pengguna memberi tahu namanya (misal: 'namaku Doni'), ingat nama tersebut."

    time_context = get_current_time_context()
    user_info += f"\n- Tanggal & Waktu Saat Ini: {time_context} Gunakan konteks tanggal dan jam WIB ini saat menjawab pertanyaan seputar hari, tanggal, jam, waktu, berita, atau event."

    prompt = OLINE_SYSTEM_PROMPT.format(user_info_section=user_info)

    if memory:
        prompt += MEMORY_INJECTION_TEMPLATE.format(memory=memory)

    # Membaca memori aturan & preferensi dari Notion dengan cache Vercel KV
    try:
        from src.notion import read_memory_from_notion
        aturan = await read_memory_from_notion("Aturan")
        preferensi = await read_memory_from_notion("Preferensi")
        if aturan or preferensi:
            prompt += "\n\n## Memori Aturan & Preferensi dari Notion:\n"
            if aturan:
                prompt += f"Aturan Pengguna:\n{aturan}\n"
            if preferensi:
                prompt += f"Preferensi Pengguna:\n{preferensi}\n"
    except Exception as e:
        logger.warning("Error loading Notion memory for prompt: %s", str(e))

    return prompt


def _build_system_prompt(memory: str, user_name: str = "Teman") -> str:
    """Fallback synchronous function untuk _build_system_prompt."""
    if user_name and user_name not in ("Anonim", "Teman"):
        user_info = f"- Nama Pengguna: {user_name} (Sapa pengguna secara ramah dan santai dengan nama {user_name})."
    else:
        user_info = "- Nama Pengguna belum diketahui secara pasti. Jika pengguna memberi tahu namanya (misal: 'namaku Doni'), ingat nama tersebut."

    time_context = get_current_time_context()
    user_info += f"\n- Tanggal & Waktu Saat Ini: {time_context} Gunakan konteks tanggal dan jam WIB ini saat menjawab pertanyaan seputar hari, tanggal, jam, waktu, berita, atau event."

    prompt = OLINE_SYSTEM_PROMPT.format(user_info_section=user_info)

    if memory:
        prompt += MEMORY_INJECTION_TEMPLATE.format(memory=memory)

    return prompt


def _format_history_for_gemini(history: list[dict]) -> list[dict]:
    """
    Convert dan bersihkan riwayat percakapan dari KV ke format Gemini contents.
    Dibatasi maksimal 10 pesan terakhir (5 putaran percakapan) untuk efisiensi prompt.
    """
    if not history:
        return []

    recent_history = history[-10:]

    cleaned = []
    for msg in recent_history:
        role = msg.get("role", "user")
        text = msg.get("text", "").strip()

        if not text:
            continue

        role = "user" if role != "model" else "model"

        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1]["parts"][0]["text"] += f"\n{text}"
        else:
            cleaned.append({
                "role": role,
                "parts": [{"text": text}],
            })

    if cleaned and cleaned[-1]["role"] == "user":
        cleaned.pop()

    return cleaned


async def _execute_function_call(
    func_name: str, func_args: dict, chat_id: int
) -> dict[str, Any]:
    """
    Execute sebuah function call dari Gemini dan return hasilnya.
    """
    from src.tools import execute_tool

    return await execute_tool(func_name, func_args, chat_id=chat_id)


async def _update_memory(
    chat_id: int, user_message: str, bot_response: str, current_memory: str
) -> None:
    """
    Update memori pengguna menggunakan Gemini.
    Hanya dipanggil sesekali untuk menjaga efisiensi.
    """
    try:
        client = _get_client()
        memory_prompt = f"""Kamu adalah asisten yang bertugas merangkum informasi penting dari percakapan.

Memori sebelumnya:
{current_memory if current_memory else "(Belum ada)"}

Percakapan baru:
User: {user_message}
Bot: {bot_response}

Tugas: Perbarui ringkasan memori dengan menambahkan informasi baru yang penting (seperti nama pengguna, hobi, kejadian penting, preferensi). Jaga ringkasan tetap singkat (maksimal 300 kata). Jika tidak ada informasi baru yang relevan, kembalikan memori sebelumnya tanpa perubahan.

Format output: langsung tuliskan ringkasan memori tanpa prefix atau label."""

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=memory_prompt,
        )
        if response and response.text:
            await save_memory(chat_id, response.text.strip())
    except Exception as e:
        logger.error("Failed to update memory: %s", str(e))


# Model kandidat untuk rotasi & fallback otomatis
DEFAULT_MODEL_CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]


def _get_model_candidates() -> list[str]:
    """Mengembalikan maksimal 2 model kandidat untuk efisiensi waktu."""
    configured = os.environ.get("GEMINI_MODEL", "").strip()
    candidates = list(DEFAULT_MODEL_CANDIDATES)
    if configured and configured in candidates:
        candidates.remove(configured)
        candidates.insert(0, configured)
    elif configured:
        candidates.insert(0, configured)
    return candidates[:2]


async def _generate_content_with_fallback(
    system_prompt: str,
    tools: Optional[list[types.Tool]],
    contents: Any,
    timeout_seconds: float = 8.0,
) -> tuple[Any, str, int]:
    """
    Memanggil client.models.generate_content dengan rotasi & fallback otomatis antar model kandidat.
    Dilengkapi timeout (default 8 detik) menggunakan asyncio.wait_for.
    Returns: (response, used_model_name, total_tokens)
    """
    candidates = _get_model_candidates()
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools if tools else None,
        temperature=0.7,
    )
    last_exception = None

    for model_name in candidates:
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=timeout_seconds,
            )
            logger.info("Successfully generated content using model: %s", model_name)

            total_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                meta = response.usage_metadata
                total_tokens = getattr(meta, "total_token_count", 0) or (
                    getattr(meta, "prompt_token_count", 0)
                    + getattr(meta, "candidates_token_count", 0)
                )

            if total_tokens == 0:
                prompt_len = sum(len(str(c)) for c in contents) if isinstance(contents, list) else len(str(contents))
                resp_len = len(response.text) if hasattr(response, "text") and response.text else 100
                total_tokens = max(15, (prompt_len + resp_len) // 4)

            return response, model_name, total_tokens

        except asyncio.TimeoutError:
            logger.warning(
                "Model %s timed out after %s seconds. Falling back to next model...",
                model_name,
                timeout_seconds,
            )
            last_exception = TimeoutError(f"Model {model_name} timed out")
            continue
        except Exception as e:
            logger.warning(
                "Model %s failed (%s). Falling back to next candidate model...",
                model_name,
                str(e),
            )
            last_exception = e
            continue

    if last_exception:
        raise last_exception
    raise RuntimeError("All Gemini model candidates failed.")


async def save_daily_summary(chat_id: int) -> str:
    """
    Mengumpulkan riwayat percakapan hari ini dari KV, merangkumnya menggunakan Gemini,
    dan menyimpan hasilnya ke Notion database 'Memori Oline' dengan Jenis = 'Ringkasan'.
    """
    try:
        history = await get_history(chat_id)
        if not history:
            return "Belum ada riwayat percakapan untuk dirangkum hari ini."

        lines = []
        for item in history:
            role = "User" if item.get("role") == "user" else "Oline"
            lines.append(f"{role}: {item.get('text', '')}")
        raw_chat = "\n".join(lines)

        summary_prompt = f"""Rangkum percakapan berikut secara singkat, padat, dan rapi (maksimal 150 kata):
{raw_chat}

Tuliskan poin-poin utama yang dibicarakan atau diputuskan."""

        client = _get_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=summary_prompt,
        )

        summary_text = response.text.strip() if (response and response.text) else raw_chat[:200]

        from src.notion import save_memory_to_notion
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"Ringkasan Percakapan {date_str}"
        res = await save_memory_to_notion(title=title, content=summary_text, memory_type="Ringkasan")
        return f"Ringkasan percakapan hari ini berhasil disimpan ke Notion! 📝\n\nRingkasan:\n{summary_text}"
    except Exception as e:
        logger.error("Error saving daily summary: %s", str(e))
        return f"Gagal membuat ringkasan harian: {str(e)}"


async def retry_pending_task(chat_id: int) -> Optional[str]:
    """
    Mengecek dan mencoba ulang pending task yang gagal sebelumnya.
    Returns respons hasil retry jika berhasil, None jika tidak ada pending task atau masih gagal.
    """
    task = await get_pending_task(chat_id)
    if not task:
        return None

    message = task.get("message", "")
    intent = task.get("intent")
    user_name = task.get("user_name", "Teman")
    retry_count = task.get("retry_count", 0)

    if not message:
        await clear_pending_task(chat_id)
        return None

    logger.info(
        "Retrying pending task for chat_id %s (attempt %d): %s",
        chat_id, retry_count + 1, message[:80],
    )

    try:
        # Panggil chat_with_oline dengan is_retry=True untuk mencegah infinite loop
        result = await chat_with_oline(
            chat_id=chat_id,
            user_message=message,
            user_name=user_name,
            intent=intent,
            is_retry=True,
        )

        # Cek apakah hasilnya bukan pesan error
        error_indicators = [
            "lagi error", "lagi ngelag", "coba lagi nanti",
            "trafik server", "error dua-duanya",
            "perintah kamu udah Oline simpan",
        ]
        is_error = any(indicator in result for indicator in error_indicators)

        if not is_error:
            # Berhasil! Hapus pending task
            await clear_pending_task(chat_id)
            return result
        else:
            # Masih gagal, increment retry count
            await update_pending_task_retry_count(chat_id, task)
            return None

    except Exception as e:
        logger.warning("Retry pending task failed for chat_id %s: %s", chat_id, str(e))
        await update_pending_task_retry_count(chat_id, task)
        return None


async def chat_with_oline(
    chat_id: int,
    user_message: str,
    user_name: str = "Teman",
    intent: Optional[str] = None,
    is_retry: bool = False,
    use_gemini_only: bool = False,
) -> str:
    """
    Main function untuk chat dengan Oline.
    Mengelola alur: intent tool filter, memori, riwayat, function calling, dan timeout fast/slow path.
    Intent preview/deploy diarahkan ke DeepInfra (DeepSeek V4 Flash) dengan fallback ke Gemini.
    """
    try:
        # Cek apakah pengguna meminta ringkasan percakapan harian
        msg_clean = user_message.strip().lower()
        if msg_clean in ("rekap percakapan hari ini", "ringkasan percakapan hari ini", "rekap percakapan", "ringkasan percakapan"):
            return await save_daily_summary(chat_id)

        # 1. Ambil memori dan riwayat
        memory = await get_memory(chat_id)
        history = await get_history(chat_id)

        # 2. Build system prompt
        system_prompt = await _build_system_prompt_async(memory, user_name=user_name)

        # Tentukan jalur fallback optimal sesuai brief.md
        if intent is None:
            jalur = "fast"
        elif intent in ("preview", "deploy", "design_reference"):
            jalur = "landing"
        else:
            jalur = "tools"

        tool_declarations = get_tools_for_intent(intent)

        try:
            from src.handlers import call_model_with_fallback
            fallback_response = await call_model_with_fallback(
                jalur=jalur,
                system_prompt=system_prompt,
                history=history,
                user_message=user_message,
                tools=tool_declarations,
                chat_id=chat_id,
            )
            if fallback_response and "Semua model AI sedang error" not in fallback_response:
                fallback_response = clean_tool_calls(fallback_response)
                history.append({"role": "user", "text": user_message})
                history.append({"role": "model", "text": fallback_response})
                await save_history(chat_id, history)
                return fallback_response
        except Exception as fallback_err:
            logger.warning("Fallback chain '%s' failed: %s. Falling back to Gemini...", jalur, str(fallback_err))

        # 3. Buat tools yang terfilter sesuai intent (Fast Path Gemini fallback: tools = None)
        tool_declarations = get_tools_for_intent(intent)
        tools = _build_tools(tool_declarations)

        # 4. Siapkan contents (riwayat dibatasi maks 10 pesan)
        contents = _format_history_for_gemini(history)
        contents.append({
            "role": "user",
            "parts": [{"text": user_message}],
        })

        try:
            # 5. Generate response (dengan timeout 8 detik & automatic fallback)
            response, used_model, tokens_used = await _generate_content_with_fallback(
                system_prompt, tools, contents, timeout_seconds=8.0
            )
            total_tokens_session = tokens_used

            # 6. Handle function calling loop jika ada
            max_iterations = 3
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                if not hasattr(response, "function_calls") or not response.function_calls:
                    break

                if response.candidates and len(response.candidates) > 0:
                    contents.append(response.candidates[0].content)

                function_responses = []
                for fc in response.function_calls:
                    func_name = fc.name
                    func_args = dict(fc.args) if fc.args else {}
                    result = await _execute_function_call(func_name, func_args, chat_id)

                    # Khusus tool deploy_to_vercel: verifikasi hasil SUKSES vs ERROR
                    if func_name == "deploy_to_vercel":
                        is_success = (
                            isinstance(result, dict)
                            and result.get("status") == "success"
                            and result.get("result_code") == "SUKSES"
                            and bool(result.get("url"))
                        )
                        if not is_success and not is_retry:
                            err_reason = result.get("error") if isinstance(result, dict) else str(result)
                            await save_pending_task(
                                chat_id=chat_id,
                                user_message=user_message,
                                intent=intent,
                                user_name=user_name,
                                error_reason=str(err_reason)[:200],
                            )

                    function_responses.append(
                        types.Part.from_function_response(
                            name=func_name,
                            response=result,
                        )
                    )

                contents.append(
                    types.Content(
                        role="user",
                        parts=function_responses,
                    )
                )

                # Generate lagi dengan function results
                response, used_model, tokens_used = await _generate_content_with_fallback(
                    system_prompt, tools, contents, timeout_seconds=8.0
                )
                total_tokens_session += tokens_used

            # 7. Extract final text response
            bot_response = ""
            if hasattr(response, "text") and response.text:
                bot_response = response.text
            elif response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        bot_response += part.text

            if not bot_response:
                bot_response = "hmm, aku lagi agak bingung nih. coba lagi nanti ya 😅"

            bot_response = clean_tool_calls(bot_response)

            # 9. Simpan pemakaian token ke KV
            try:
                from src.kv import increment_usage
                await increment_usage("gemini", {"request": 1, "token": total_tokens_session})
            except Exception as kv_err:
                logger.warning("Failed to increment gemini usage: %s", str(kv_err))

            if total_tokens_session > 0:
                await save_usage(chat_id, total_tokens_session)

        except Exception as gemini_err:
            if intent is not None and intent not in ("deploy", "preview", "design_reference") and os.environ.get("GROQ_API_KEY", "").strip():
                logger.warning(
                    "Gemini Slow Path gagal (%s). Mencoba fallback ke Groq Slow Path...",
                    str(gemini_err),
                )
                try:
                    from src.groq import chat_groq_with_tools

                    groq_response = await chat_groq_with_tools(
                        system_prompt=system_prompt,
                        history=history,
                        user_message=user_message,
                        tools=tool_declarations,
                        chat_id=chat_id,
                    )
                    if groq_response:
                        bot_response = (
                            groq_response
                            + "\n\n(⚠️ Oline pakai otak cadangan nih, Gemini lagi istirahat~)"
                        )
                    else:
                        raise ValueError("Groq returned empty response")
                except Exception as groq_err:
                    logger.error("Groq Slow Path fallback juga gagal: %s", str(groq_err))
                    # Simpan pending task agar bisa dicoba ulang nanti
                    if not is_retry:
                        await save_pending_task(
                            chat_id=chat_id,
                            user_message=user_message,
                            intent=intent,
                            user_name=user_name,
                            error_reason=f"Gemini: {str(gemini_err)[:100]} | Groq: {str(groq_err)[:100]}",
                        )
                    return (
                        "aduh, Oline lagi error dua-duanya nih 😢 "
                        "tapi tenang, perintah kamu udah Oline simpan — "
                        "nanti otomatis dicoba lagi ya, bestie~"
                    )
            else:
                raise gemini_err

        # 8. Update riwayat percakapan
        if "masalah teknis" not in bot_response and "error nih" not in bot_response:
            history.append({"role": "user", "text": user_message})
            history.append({"role": "model", "text": bot_response})
            await save_history(chat_id, history)

        return bot_response

    except Exception as e:
        err_msg = str(e)
        logger.error("Error in chat_with_oline: %s", err_msg, exc_info=True)

        # Simpan pending task agar perintah tidak hilang (kecuali sedang retry)
        if not is_retry:
            await save_pending_task(
                chat_id=chat_id,
                user_message=user_message,
                intent=intent,
                user_name=user_name,
                error_reason=err_msg[:200],
            )
            # Coba ulang otomatis 1x secara instan per brief.md
            try:
                auto_retry_res = await retry_pending_task(chat_id)
                if auto_retry_res:
                    return auto_retry_res
            except Exception as auto_err:
                logger.warning("Immediate 1x auto-retry failed: %s", str(auto_err))

            # Jika 1x retry otomatis masih gagal -> Tanya konfirmasi ke pengguna
            return "Task ini masih gagal nih. Mau dicoba lagi atau skip? 😢"

        return "Task ini masih gagal nih. Mau dicoba lagi atau skip? 😢"

