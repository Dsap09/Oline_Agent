"""
Verifikasi function calling untuk semua model di MODEL_CATALOG (Tahap 10 brief).

Mengirim satu permintaan tool-call sederhana ke tiap model (OpenCode Go & DeepInfra)
dan melaporkan PASS/FAIL. Jalankan manual dengan .env berisi API key asli:

    python scripts/verify_models.py

Exit code 0 bila semua model yang key-nya tersedia lolos; 1 bila ada yang gagal.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from src.config import MODEL_CATALOG, get_model_label

DUMMY_TOOL = {
    "type": "function",
    "function": {
        "name": "cek_cuaca",
        "description": "Mengambil cuaca terkini untuk sebuah kota.",
        "parameters": {
            "type": "object",
            "properties": {
                "kota": {"type": "string", "description": "Nama kota, misal Jakarta"},
            },
            "required": ["kota"],
        },
    },
}

PROMPT = "Panggil tool cek_cuaca untuk kota Jakarta."

PROVIDER_BASE_URLS = {
    "opencode_go": "https://opencode.ai/zen/go/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
}

PROVIDER_ENV_KEYS = {
    "opencode_go": "OPENCODE_GO_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
}


def _client_for(provider: str):
    """Mengembalikan (client, error_message). client=None bila key belum diset."""
    from openai import OpenAI

    env_key = PROVIDER_ENV_KEYS.get(provider, "")
    api_key = os.environ.get(env_key, "").strip()
    if not api_key:
        return None, f"{env_key} belum diset"
    return OpenAI(api_key=api_key, base_url=PROVIDER_BASE_URLS[provider]), ""


def _verify_one(client, model_id: str):
    """Uji satu model memanggil tool. Returns (ok, detail)."""
    messages = [{"role": "user", "content": PROMPT}]
    tools = [DUMMY_TOOL]
    forced_choice = {"type": "function", "function": {"name": "cek_cuaca"}}

    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            tools=tools,
            tool_choice=forced_choice,
            temperature=0.0,
            max_tokens=200,
            timeout=60.0,
        )
    except Exception as forced_err:
        # Sebagian provider menolak tool_choice terpaksa; coba mode auto.
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.0,
                max_tokens=200,
                timeout=60.0,
            )
        except Exception as e:
            return False, f"request gagal: {str(e)[:160]} (forced: {str(forced_err)[:80]})"

    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return False, "tidak ada tool_calls pada respons"

    func = getattr(tool_calls[0], "function", None)
    name = getattr(func, "name", "") if func else ""
    if name != "cek_cuaca":
        return False, f"memanggil tool yang salah: {name or '(kosong)'}"

    try:
        args = json.loads(getattr(func, "arguments", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    if not args.get("kota"):
        return False, "argumen 'kota' kosong"

    return True, f"tool_calls OK (kota={args.get('kota')})"


def main() -> int:
    total = 0
    passed = 0
    skipped = 0
    failed = 0

    print("Verifikasi function calling model manual (/models)\n")
    for provider, group in MODEL_CATALOG.items():
        client, err = _client_for(provider)
        print(f"━━━ {group.get('label', provider)} ━━━")
        if client is None:
            skipped += len(group.get("models", []))
            print(f"  SKIP semua model: {err}\n")
            continue
        for entry in group.get("models", []):
            total += 1
            key = entry["key"]
            try:
                ok, detail = _verify_one(client, entry["model_id"])
            except Exception as e:
                ok, detail = False, f"error tak terduga: {str(e)[:160]}"
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1
            print(f"  [{status}] {get_model_label(key)} ({entry['model_id']}) — {detail}")
        print("")

    print(f"Ringkasan: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    if failed == 0 and passed == 0:
        print("Tidak ada model yang diuji (semua API key kosong).")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
