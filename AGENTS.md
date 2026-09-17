# AGENTS.md

Oline: a Telegram bot (Gen-Z persona) deployed as Vercel Serverless Functions. Python 3.10+.

## Language
All code, comments, docstrings, and user-facing strings are in **Indonesian**. Match this — do not write English comments or output.

## Architecture (important routing facts)
- Entrypoint: `api/index.py` (WSGI). Routes defined in `vercel.json`: `/api/keepalive`, `/api/process_pending`, everything else → `api/index.py`.
- The Telegram webhook **must return 200 fast**. Never do slow work synchronously in `index.py`. Long tasks (e.g. landing page generation) go through `save_pending_task` + `asyncio.create_task(process_pending_task(...))` and are completed by the background `/api/process_pending` endpoint. Do not make these synchronous.
- Message flow: `src/bot.py::handle_message` → `detect_intent_async` (keyword map `HEAVY_KEYWORDS`; a standalone 4-letter alphabetic word = `saham` intent) → `src/gemini.py::chat_with_oline` → `src/handlers.py::call_model_with_fallback`.
- Two AI paths selected by `jalur` in `chat_with_oline`: `fast` (intent None, no tools), `landing` (`preview`/`deploy`/`design_reference` → DeepInfra), `tools` (everything else). Fast path must be instant; akademik/ERINE must route to slow path with tools.
- Every heavy intent passes a feature-flag check (`src/kv.py::is_feature_active`); a new intent must be registered in `src/config.py::FITUR_LIST` or the feature gate will block it.

## Adding a new tool (3 places, all in `src/tools.py`)
1. Add the declaration dict to `TOOL_DECLARATIONS`.
2. Map it under `TOOLS_BY_INTENT` (~line 820) — empty/missing intent yields no tools.
3. Register the executor in `TOOL_EXECUTORS` (~line 2988), plus any intent keywords in `src/bot.py::HEAVY_KEYWORDS`.

## Tests
- Framework is stdlib **`unittest`** (pytest is NOT installed). Run from repo root:
  `python -m unittest tests/test_<name>.py`
- Test files add the repo root to `sys.path` themselves. Many tests are **integration tests that call real APIs** and need a populated `.env` (loaded at import via `load_dotenv()` in `src/config.py`); some are mocked. A failing network/credential test is often an environment issue, not a code bug.

## Environment & deployment
- Copy `.env.example` → `.env`. `src/config.py` calls `load_dotenv()` at import time.
- `.env` and `api/*.json` (Google Drive service-account creds) are gitignored — never commit them.
- Deploy: `vercel --prod`, then register webhook: `python scripts/set_webhook.py https://<project>.vercel.app` (inspect with `--info`).
- The GitHub self-improvement tools open PRs to `main` for manual review; auto-deploy happens on merge. Don't push directly to `main` unless asked.

## Layout (non-obvious)
- `src/tools.py` (~3200 lines) is the tool registry + executors. `src/kv.py` is Vercel KV/Redis REST. `src/personas.py` holds system prompts. `src/handlers.py` is the background pending-task processor, not Telegram handlers.
- `scratch/` is gitignored throwaway scripts — don't rely on it as source of truth.
