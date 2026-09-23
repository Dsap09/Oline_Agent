# AGENTS.md

Oline: a Telegram bot (Gen-Z persona). Python 3.10+, deployed to Vercel Serverless Functions, with a separate Render worker for heavy tasks.

## Language
All code, comments, docstrings, and user-facing strings are in **Indonesian**. Match this — do not write English comments or output.

## Architecture (important routing facts)
- Entrypoint: `api/index.py` (WSGI, function `app()`). Routes in `vercel.json`: `/api/keepalive`, `/api/process_pending`, `/api/token_check`, `/api/delegate`, `/api/callback`; everything else → `api/index.py`. A new `api/*.py` file must be added to BOTH `builds` and `routes` in `vercel.json`.
- The Telegram webhook **must return 200 fast**. Never do slow work synchronously in `index.py`.
- Two execution modes in `src/bot.py::handle_message`:
  - **Sync**: fast intent (`None`), plus quick imperatives `health`, `kelola_fitur`, `cek_token`, `renew_token`, `kuota`, `notion` note-save, and deploy list/delete.
  - **Background** (`HEAVY_BACKGROUND_INTENTS`, bot.py ~909): `rekomendasi`, `suara`, `jurnal`, `drive`, `search`, `gambar`, `neo4j`, `coding`, `github`, `vercel_logs`, `lokasi`. Landing intents (`preview`/`deploy`/`design_reference`) are also background. These go through `_route_heavy_task` (bot.py:1200): send a progress message, `save_pending_task(delegated=True)`, then `delegate_to_worker` (POST `RENDER_WORKER_URL/process` to the Render worker). If delegation fails, fall back to `save_pending_task(delegated=False)` + self-trigger `/api/process_pending`.
- Render worker (`oline-worker/main.py`, FastAPI) runs the landing-page pipeline without Vercel timeouts, edits the same progress message via `update_progress`, sends the final result, and calls back to `/api/callback` (`X-Worker-Key` auth) for KV cleanup.
- Message flow: `handle_message` → `detect_intent` (keyword map `HEAVY_KEYWORDS`; a standalone 4-letter alphabetic word = `saham` intent) → `src/gemini.py::chat_with_oline` → `src/handlers.py::call_model_with_fallback`.
- Three AI paths selected by `jalur` in `chat_with_oline` (gemini.py:635): `fast` (intent None, no tools), `landing` (`preview`/`deploy`/`design_reference` → DeepInfra), `tools` (everything else). Fast path must be instant; akademik/ERINE must route to slow path with tools.
- Every heavy intent passes a feature-flag check (`src/kv.py::is_feature_active`); a new intent must be registered in `src/config.py::FITUR_LIST` or the feature gate will block it.
- **Coding agent** (`coding_agent` intent, keywords "tambahkan fitur"/"edit dirimu"/"perbaiki bug"): Plan dulu, eksekusi belakangan. `handle_message` intercepts it and runs `_start_coding_agent_flow`: generate plan via `generate_coding_plan` (DeepInfra, fallback Gemini), send ONE bubble with `plan:approve`/`plan:fix`/`plan:cancel` buttons, save state via `save_plan_state` (KV key `coding_plan:<chat_id>`). On approve, `_execute_approved_plan` delegates to the Render worker; on fix, user feedback is handled through the `clarify_state` mechanism (`__plan_fix__:` prefix). Callback buttons `pr:merge`/`pr:delete`/`pr:review` (bot.py `handle_callback`) act on the finished PR via `merge_pull_request`/`delete_github_branch`.
- Progress bubbles support `reply_markup` via `update_progress(chat_id, message_id, text, reply_markup=...)` and `send_or_edit_progress` (handlers.py) — keep single-bubble UX intact; `append_elapsed_time` strips/caps text at 4096 chars.

## Adding a new tool (3 places, all in `src/tools.py`)
1. Add the declaration dict to `TOOL_DECLARATIONS`.
2. Map it under `TOOLS_BY_INTENT` (~line 887) — empty/missing intent yields no tools.
3. Register the executor in `TOOL_EXECUTORS` (~line 3243), plus any intent keywords in `src/bot.py::HEAVY_KEYWORDS` and (if background) `HEAVY_BACKGROUND_INTENTS`.

## Tests
- Framework is stdlib **`unittest`** (pytest is NOT installed). Run from repo root:
  `python -m unittest tests/test_<name>.py`
- Test files add the repo root to `sys.path` themselves. Many tests are **integration tests that call real APIs** and need a populated `.env` (loaded at import via `load_dotenv()` in `src/config.py`); some are mocked. A failing network/credential test is often an environment issue, not a code bug.

## Environment & deployment
- Copy `.env.example` → `.env`. `src/config.py` calls `load_dotenv()` at import time.
- `.gitignore` excludes `.env`, `*.json` (except `vercel.json`), `brief.md`, `prd.md`, `scratch/` — never commit these.
- Worker env: `RENDER_WORKER_URL`, `OLINE_WORKER_KEY` (shared key for `/api/delegate`/`/api/callback`/worker `/process`). Endpoint secrets: `DELEGATE_SECRET`, `PROCESS_PENDING_SECRET`, `KEEPALIVE_SECRET`, `WEBHOOK_SECRET`.
- Deploy: `vercel --prod`, then register webhook: `python scripts/set_webhook.py https://<project>.vercel.app` (inspect with `--info`).
- **Do NOT run `scripts/set_commands.py` without flags** — it registers the Telegram command list, which makes Telegram show the blue "Menu" button beside the input. To keep commands typed manually while hiding that button: `python scripts/set_commands.py --clear` (once) and never re-register. `--info` shows the current list.
- The GitHub self-improvement tools open PRs to `main` for manual review; auto-deploy happens on merge. Don't push directly to `main` unless asked.

## Layout (non-obvious)
- `src/tools.py` (~3450 lines) is the tool registry + executors. `src/kv.py` is Vercel KV/Redis REST. `src/personas.py` holds system prompts. `src/handlers.py` is the background pending-task processor + worker delegate, NOT Telegram handlers.
- `oline-worker/` is a separate Render Web Service (FastAPI) with its own `requirements.txt` and `Procfile` (`uvicorn main:app`); it imports `src/` via `sys.path` to the repo root. Landing-page and background tasks can run here. For `coding_agent` it runs hybrid: OpenCode CLI first (`opencode run`, config `opencode.json` root → DeepSeek via DeepInfra), fallback to PyGithub tools (branch → `ai_fix_code` per file → PR).
- `scratch/` is gitignored throwaway scripts — don't rely on it as source of truth.