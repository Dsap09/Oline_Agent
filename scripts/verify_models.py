"""
Verifikasi function calling & kompatibilitas tools untuk semua model di MODEL_CATALOG.

Untuk tiap model (OpenCode Go & DeepInfra), script menguji beberapa tool NYATA yang
mewakili bentuk argumen berbeda:
  - preview_with_codepen : argumen besar (html/css/js) — kritis untuk landing page
  - get_weather_forecast : argumen string sederhana
  - save_memory_to_notion: enum + teks
  - check_ai_quota       : tanpa argumen

Catatan provider:
- Model "thinking" (mis. DeepSeek V4 Pro) menolak forced tool_choice
  ("Thinking mode does not support this tool_choice"), jadi otomatis diuji mode auto.

Jalankan manual dengan .env berisi API key asli:
    python scripts/verify_models.py

Exit code 0 bila semua model lolos semua probe; 1 bila ada yang gagal.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console Windows (cp1252) tidak bisa mencetak karakter box-drawing/emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()

from src.config import MODEL_CATALOG, get_model_label

PROVIDER_BASE_URLS = {
    "opencode_go": "https://opencode.ai/zen/go/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
}

PROVIDER_ENV_KEYS = {
    "opencode_go": "OPENCODE_GO_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
}

# Probe tool nyata dari registry Oline (lihat src/tools.py TOOL_DECLARATIONS).
TOOL_PROBES = [
    {
        "name": "preview_with_codepen",
        "prompt": (
            "Panggil tool preview_with_codepen untuk membuat landing page sederhana "
            "kafe bernama 'Kopi Senja'. Sertakan parameter title, html, css, dan js. "
            "Gunakan HTML/CSS/JS sederhana saja (masing-masing kurang dari 2000 karakter)."
        ),
        "required": ["title", "html", "css"],
        "forced": False,  # model thinking menolak forced tool_choice
    },
    {
        "name": "get_weather_forecast",
        "prompt": "Panggil tool get_weather_forecast untuk kota Bandung.",
        "required": ["city"],
        "forced": True,
    },
    {
        "name": "save_memory_to_notion",
        "prompt": (
            "Panggil tool save_memory_to_notion untuk menyimpan preferensi user "
            "'suka kopi hitam tanpa gula'. Gunakan memory_type='Preferensi'."
        ),
        "required": ["title", "content"],
        "forced": True,
    },
    {
        "name": "check_ai_quota",
        "prompt": "Panggil tool check_ai_quota untuk melihat kuota AI.",
        "required": [],
        "forced": True,
    },
]


def _client_for(provider: str):
    """Mengembalikan (client, error_message). client=None bila key belum diset."""
    from openai import OpenAI

    env_key = PROVIDER_ENV_KEYS.get(provider, "")
    api_key = os.environ.get(env_key, "").strip()
    if not api_key:
        return None, f"{env_key} belum diset"

    if provider == "opencode_go":
        # OpenCode Go mewajibkan User-Agent custom + header x-opencode-session.
        return OpenAI(
            api_key=api_key,
            base_url=PROVIDER_BASE_URLS[provider],
            default_headers={
                "User-Agent": "Oline-Agent/1.0",
                "x-opencode-session": "oline-verify",
            },
        ), ""

    return OpenAI(api_key=api_key, base_url=PROVIDER_BASE_URLS[provider]), ""


def _openai_tool(name: str):
    """Ambil deklarasi tool nyata Oline dalam format OpenAI."""
    from src.tools import TOOL_DECLARATIONS, convert_tools_to_openai_format

    for decl in TOOL_DECLARATIONS:
        if isinstance(decl, dict) and decl.get("name") == name:
            converted = convert_tools_to_openai_format([decl])
            return converted[0] if converted else None
    return None


def _verify_probe(client, model_id: str, probe: dict, temperature: float):
    """Uji satu model memanggil satu tool nyata. Returns (ok, detail)."""
    tool = _openai_tool(probe["name"])
    if not tool:
        return False, f"deklarasi tool {probe['name']} tidak ditemukan"

    messages = [{"role": "user", "content": probe["prompt"]}]
    tools = [tool]

    def _call(choice):
        return client.chat.completions.create(
            model=model_id,
            messages=messages,
            tools=tools,
            tool_choice=choice,
            temperature=temperature,
            # HTML/CSS/JS landing butuh budget besar; 4096 memotong JSON argumen.
            max_tokens=16384,
            timeout=180.0,
        )

    response = None
    if probe["forced"]:
        forced_choice = {"type": "function", "function": {"name": probe["name"]}}
        try:
            response = _call(forced_choice)
        except Exception as e:
            # Sebagian provider/model menolak forced tool_choice -> fallback auto.
            if "tool_choice" not in str(e).lower():
                return False, f"request gagal: {str(e)[:140]}"

    if response is None:
        try:
            response = _call("auto")
        except Exception as e:
            return False, f"request gagal (auto): {str(e)[:140]}"

    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        content = (message.content or "")[:80].replace("\n", " ")
        return False, f"tidak memanggil tool (jawab: {content!r})"

    func = getattr(tool_calls[0], "function", None)
    name = getattr(func, "name", "") if func else ""
    if name != probe["name"]:
        return False, f"memanggil tool salah: {name or '(kosong)'}"

    raw_args = getattr(func, "arguments", "") or ""
    finish_reason = response.choices[0].finish_reason
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError:
        return False, f"JSON argumen tidak valid/terpotong (finish_reason={finish_reason})"
    if not isinstance(args, dict):
        return False, "argumen bukan objek JSON"

    for field in probe.get("required", []):
        value = args.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return False, f"argumen '{field}' kosong"

    return True, "OK"


def main() -> int:
    total = 0
    passed = 0
    skipped = 0
    failed = 0

    print("Matriks kompatibilitas tool untuk model manual (/models)\n")
    for provider, group in MODEL_CATALOG.items():
        client, err = _client_for(provider)
        print(f"━━━ {group.get('label', provider)} ━━━")
        if client is None:
            skipped += len(group.get("models", []))
            print(f"  SKIP semua model: {err}\n")
            continue

        for entry in group.get("models", []):
            total += 1
            temperature = entry.get("temperature", 0.7)
            model_id = entry["model_id"]
            failures = []
            ok_count = 0
            for probe in TOOL_PROBES:
                try:
                    ok, detail = _verify_probe(client, model_id, probe, temperature)
                except Exception as e:
                    ok, detail = False, f"error tak terduga: {str(e)[:120]}"
                if ok:
                    ok_count += 1
                else:
                    failures.append(f"{probe['name']}: {detail}")

            status = "PASS" if not failures else "FAIL"
            if failures:
                failed += 1
                detail = " | ".join(failures)
            else:
                passed += 1
                detail = f"{ok_count}/{len(TOOL_PROBES)} tools OK"
            print(f"  [{status}] {get_model_label(entry['key'])} ({model_id}) — {detail}")
        print("")

    print(f"Ringkasan: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    if failed == 0 and passed == 0:
        print("Tidak ada model yang diuji (semua API key kosong).")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
