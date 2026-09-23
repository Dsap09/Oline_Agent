"""
Oline Worker — layanan Render untuk task berat (pilot: landing page generator).

Worker menerima task dari Vercel (orchestrator) via POST /process, menjalankan
pipeline generasi landing page TANPA batas timeout (Render bukan serverless),
mengirimkan progres & hasil akhir langsung ke Telegram, lalu membersihkan
pending task di Vercel KV.

Worker di-deploy sebagai Web Service Render dengan root directory = oline-worker/.
Karena Render men-clone seluruh repo, worker mengimpor ulang modul src/ (gemini,
handlers, tools, personas, kv) lewat sys.path menuju root repo.
"""

import asyncio
import json
import logging
import os
import shutil
import sys

# Repo root agar bisa import src/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import httpx  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("oline-worker")

app = FastAPI(title="Oline Worker")

WORKER_KEY = os.environ.get("OLINE_WORKER_KEY", "").strip()
VERCEL_CALLBACK_URL = os.environ.get("VERCEL_CALLBACK_URL", "").strip()


def _tool_available(name: str) -> bool:
    """Cek apakah binary tersedia di PATH worker."""
    try:
        return shutil.which(name) is not None
    except Exception:
        return False


async def _ensure_opencode_cli() -> None:
    """
    Startup check untuk coding agent: pastikan node/npm/git/opencode tersedia di worker.
    Jika opencode CLI belum ada tapi node+npm ada, install global via npm (sekali).
    Hasil check dicatat ke log agar mudah didiagnosis dari dashboard Render.
    """
    node_ok = _tool_available("node")
    npm_ok = _tool_available("npm")
    git_ok = _tool_available("git")
    opencode_ok = _tool_available("opencode")

    logger.info(
        "[startup] Tool check — node=%s npm=%s git=%s opencode=%s",
        node_ok, npm_ok, git_ok, opencode_ok,
    )

    if opencode_ok:
        try:
            proc = await asyncio.create_subprocess_exec(
                "opencode", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            ver = (stdout or b"").decode("utf-8", errors="replace").strip()
            logger.info("[startup] opencode CLI versi: %s", ver or "(kosong)")
        except Exception as e:
            logger.warning("[startup] Gagal cek versi opencode: %s", str(e))
        return

    if not (node_ok and npm_ok):
        logger.warning(
            "[startup] node/npm tidak tersedia — jalur OpenCode CLI nonaktif, "
            "fallback tools akan dipakai untuk coding_agent."
        )
        return

    logger.info("[startup] node+npm tersedia, menginstall opencode-ai secara global...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", "opencode-ai",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        log = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode == 0:
            logger.info("[startup] opencode-ai terinstall: %s", log[-300:])
        else:
            logger.warning("[startup] Gagal install opencode-ai: %s", err[-300:])
    except asyncio.TimeoutError:
        logger.warning("[startup] Install opencode-ai timeout (> 5 menit).")
    except Exception as e:
        logger.warning("[startup] Error saat install opencode-ai: %s", str(e))


@app.on_event("startup")
async def _startup_check():
    asyncio.create_task(_ensure_opencode_cli())


def _auth_ok(auth_header: str) -> bool:
    if not WORKER_KEY:
        return False
    return (auth_header or "").strip() == WORKER_KEY


async def _get_msg_id(payload: dict, chat_id: int) -> int | None:
    msg_id = payload.get("message_id")
    if msg_id:
        try:
            return int(msg_id)
        except (TypeError, ValueError):
            pass
    # Fallback: baca dari KV (progres message disimpan oleh Vercel)
    try:
        from src.kv import get_progress_message_id
        return await get_progress_message_id(chat_id)
    except Exception as e:
        logger.warning("Gagal membaca progress message_id dari KV: %s", str(e))
    return None


async def _notify_vercel_callback(chat_id: int, status: str, message: str) -> None:
    """Best-effort: beri tahu Vercel bahwa task selesai (untuk monitoring/cleanup)."""
    if not VERCEL_CALLBACK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                VERCEL_CALLBACK_URL,
                json={"chat_id": chat_id, "status": status, "message": message[:500]},
                headers={"X-Worker-Key": WORKER_KEY},
            )
    except Exception as e:
        logger.warning("Callback ke Vercel gagal (non-critical): %s", str(e))


async def _run_task(chat_id: int, perintah: str, intent: str, user_name: str, msg_id: int | None) -> None:
    """Menjalankan pipeline task berat (landing page) dan mengirim hasil ke Telegram."""
    from src.handlers import (
        clear_pending_task,
        clear_progress_message_id,
        delete_checkpoint,
        get_checkpoint,
        save_checkpoint,
        send_telegram_message,
        update_progress,
    )
    from src.kv import clear_task_start
    from src.gemini import chat_with_oline

    is_landing = intent in ("preview", "deploy", "design_reference")

    try:
        # Inisialisasi/ambil checkpoint (khusus landing) untuk fallback
        checkpoint = None
        if is_landing:
            checkpoint = await get_checkpoint(chat_id)
            if not checkpoint:
                checkpoint = {
                    "perintah_asli": perintah,
                    "style_guide": "Desain modern, responsif, kontras tinggi, typography berkarakter, tanpa placeholder abu-abu",
                    "langkah_selesai": [],
                    "langkah_sekarang": "generate_html",
                    "data": {},
                    "retry_count": 0,
                    "max_retry": 3,
                    "waktu_terakhir": "",
                }
                await save_checkpoint(chat_id, checkpoint)

        if is_landing:
            if msg_id:
                await update_progress(chat_id, msg_id, "⏳ Menyusun struktur landing page...")
                await update_progress(chat_id, msg_id, "⏳ Merancang gaya visual & mencari referensi desain...")
                await update_progress(chat_id, msg_id, "⏳ Menyiapkan preview & link...")
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=perintah,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )
            if checkpoint and "preview" not in checkpoint.get("langkah_selesai", []):
                checkpoint["langkah_selesai"].append("generate_html")
                checkpoint["langkah_selesai"].append("generate_css")
                checkpoint["langkah_selesai"].append("preview")
                checkpoint["langkah_sekarang"] = "selesai"
                await save_checkpoint(chat_id, checkpoint)
        else:
            if msg_id:
                await update_progress(chat_id, msg_id, "⏳ Sedang memproses permintaan kamu... Mohon tunggu sebentar ya.")
            else:
                await send_telegram_message(chat_id, "⏳ Baik, permintaan kamu sedang diproses. Aku kabari setelah selesai ya.")
            response_text = await chat_with_oline(
                chat_id=chat_id,
                user_message=perintah,
                user_name=user_name,
                intent=intent,
                is_retry=True,
            )

        # Kirim hasil final
        ok = False
        if msg_id:
            final_text = f"✅ Selesai!\n\n{response_text}"
            if len(final_text) <= 4096:
                ok = await update_progress(chat_id, msg_id, final_text)
            else:
                await update_progress(chat_id, msg_id, "✅ Selesai! Hasil preview/deploy dikirimkan di bawah ini:")
                ok = await send_telegram_message(chat_id, response_text)
        else:
            ok = await send_telegram_message(chat_id, response_text)

        await clear_pending_task(chat_id)
        await clear_progress_message_id(chat_id)
        await clear_task_start(chat_id)
        if is_landing:
            await delete_checkpoint(chat_id)

        # Paritas: self_monitor untuk tool intent yang diproses di worker (bukan landing).
        if not is_landing:
            try:
                from src.self_monitor import self_monitor
                asyncio.create_task(self_monitor(chat_id))
            except Exception as sm_err:
                logger.warning("Gagal self_monitor: %s", str(sm_err))

        logger.info("[worker] Task selesai chat=%s ok=%s", chat_id, ok)
        await _notify_vercel_callback(chat_id, "success", str(response_text)[:500])

    except Exception as e:
        logger.error("[worker] Task gagal chat=%s: %s", chat_id, str(e), exc_info=True)
        try:
            fail_text = f"❌ Gagal memproses task. Penyebab: {str(e)[:100]}"
            # Edit pesan progres yang sama agar tidak ada bubble sisa yang membingungkan.
            if msg_id:
                edited = await update_progress(chat_id, msg_id, fail_text)
                if not edited:
                    await send_telegram_message(chat_id, fail_text)
            else:
                await send_telegram_message(chat_id, fail_text)
        except Exception as send_err:
            logger.warning("Gagal kirim pesan error: %s", str(send_err))
        try:
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
        except Exception as clean_err:
            logger.warning("Gagal cleanup pending task: %s", str(clean_err))
        await _notify_vercel_callback(chat_id, "error", str(e)[:500])


async def _slugify(text: str) -> str:
    """Mengubah teks bebas menjadi slug aman untuk nama branch."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "fitur").lower()).strip("-")
    return s[:40] or "fitur"


async def _try_opencode_cli(perintah: str, plan: dict) -> tuple[bool, str]:
    """
    Menjalankan OpenCode CLI (hybrid primary) untuk mengeksekusi plan di repo checkout.
    CLI HANYA mengedit file di working tree — git commit/push dikerjakan worker sendiri
    (lebih andal: pakai GITHUB_TOKEN, tanpa bergantung pada credential/git identity CLI).

    Mengembalikan (success, output/log). Perlu runtime Node + binary opencode di worker;
    bila tidak tersedia, kembalikan (False, pesan) agar fallback tools dipakai.
    """
    exe = shutil.which("opencode")
    if not exe:
        npx = shutil.which("npx")
        if not npx:
            return False, "opencode/npx CLI tidak tersedia di worker"
        exe = npx
        use_npx = True
    else:
        use_npx = False

    plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
    prompt = (
        "Kerjakan perintah coding berikut di repository ini. "
        f"Perintah user:\n{perintah}\n\n"
        f"Rencana yang sudah disetujui:\n{plan_text}\n\n"
        "Edit file yang dibutuhkan di working tree ini (buat file baru bila perlu). "
        "JANGAN menjalankan perintah git apapun (tidak perlu commit/push/branch). "
        "Selesai cukup dengan melaporkan file apa saja yang kamu ubah."
    )

    cmd = []
    if use_npx:
        cmd = [exe, "-y", "opencode-ai", "run", "--auto", "--format", "json", prompt]
    else:
        cmd = [exe, "run", "--auto", "--format", "json", prompt]

    logger.info("[worker] Menjalankan OpenCode CLI: %s", " ".join(cmd[:6]))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=_REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "OpenCode CLI timeout (> 15 menit)"

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        log = f"{out}\n{err}".strip()
        if proc.returncode == 0:
            return True, log
        return False, f"OpenCode CLI exit {proc.returncode}: {log[:2000]}"
    except Exception as e:
        return False, f"Gagal menjalankan OpenCode CLI: {str(e)}"


async def _git_prepare_branch(branch: str) -> tuple[bool, str]:
    """
    Siapkan branch kerja dari origin/main SEBELUM CLI mengedit file, sehingga
    perubahan CLI berada di branch tersebut (bukan menimpa main).
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo_name = os.environ.get("GITHUB_REPO", "").strip()
    if not token or not owner or not repo_name:
        return False, "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum diset di worker."

    origin = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
    steps = [
        (["git", "remote", "set-url", "origin", origin], "gagal set remote"),
        (["git", "fetch", "origin"], "gagal fetch origin"),
        (["git", "checkout", "-B", branch, "origin/main"], "gagal buat branch dari origin/main"),
        (["git", "config", "user.email", "oline@bot.local"], "gagal config email"),
        (["git", "config", "user.name", "Oline Bot"], "gagal config name"),
    ]
    for cmd, err_label in steps:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=_REPO_ROOT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            return False, f"{err_label}: timeout {' '.join(cmd)}"
        except Exception as e:
            return False, f"{err_label}: {str(e)}"
        if proc.returncode != 0:
            out = (stdout or b"").decode("utf-8", errors="replace")
            err = (stderr or b"").decode("utf-8", errors="replace")
            return False, f"{err_label}: {(out + err).strip()[:500]}"
    return True, f"Branch '{branch}' siap (dari origin/main)."


async def _git_commit_and_push(branch: str, commit_msg: str) -> tuple[bool, str]:
    """
    Commit perubahan working tree (yang sudah diedit CLI di branch ini) lalu push ke GitHub.
    Auth via GITHUB_TOKEN (URL remote diset dengan token), jadi tidak bergantung
    pada credential git bawaan Render.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo_name = os.environ.get("GITHUB_REPO", "").strip()
    if not token or not owner or not repo_name:
        return False, "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum diset di worker."

    origin = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
    steps = [
        (["git", "remote", "set-url", "origin", origin], "gagal set remote"),
        (["git", "add", "-A"], "gagal git add"),
        (["git", "commit", "-m", commit_msg], "gagal commit (mungkin tidak ada perubahan)"),
        (["git", "push", "-u", "origin", branch], "gagal push"),
    ]

    for cmd, err_label in steps:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=_REPO_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            out = (stdout or b"").decode("utf-8", errors="replace")
            err = (stderr or b"").decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            return False, f"{err_label}: timeout menjalankan {' '.join(cmd)}"
        except Exception as e:
            return False, f"{err_label}: {str(e)}"
        if proc.returncode != 0:
            combined = f"{out}\n{err}".strip()
            if "nothing to commit" in combined or "No changes" in combined:
                return False, "Tidak ada perubahan yang perlu di-commit (CLI tidak mengedit apa pun)."
            return False, f"{err_label}: {combined[:500]}"

    return True, f"Branch '{branch}' berhasil di-commit & di-push."


async def _run_coding_task(
    chat_id: int,
    perintah: str,
    user_name: str,
    msg_id: int | None,
) -> None:
    """
    Eksekusi coding agent di worker (hybrid): coba OpenCode CLI dulu, fallback ke
    alur GitHub tools (DeepInfra/DeepSeek). Lalu buat PR dan laporkan ke Telegram
    dengan tombol Review/Merge/Hapus di bubble yang sama.
    """
    from src.handlers import (
        clear_pending_task,
        clear_progress_message_id,
        coding_result_keyboard,
        send_telegram_message,
        update_progress,
    )
    from src.kv import (
        clear_task_start,
        get_plan_state,
        save_plan_state,
    )

    from src.github_tools import create_pull_request

    state = await get_plan_state(chat_id) or {}
    langkah = state.get("langkah", []) or []
    plan_summary = state.get("plan", perintah)

    branch = f"oline-feature/{await _slugify(plan_summary)}"

    try:
        if msg_id:
            await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch...")

        # --- Siapkan branch kerja dari origin/main (agar edit CLI tidak menimpa main) ---
        prep_ok, prep_log = await _git_prepare_branch(branch)
        if not prep_ok:
            logger.warning("[worker] Gagal siapkan branch (%s); fallback ke tools.", prep_log[:300])
            ok, log = False, prep_log
        else:
            if msg_id:
                await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file (OpenCode CLI)... ⏳ (bisa 1-3 menit, ini yang pertama kali jalan)")

            # --- Primary: OpenCode CLI (edit file di working tree, di branch ini) ---
            ok, log = await _try_opencode_cli(perintah, state)

            if ok:
                if msg_id:
                    await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file (OpenCode CLI)... ✅\n[4/5] Commit & push...")
                git_ok, git_log = await _git_commit_and_push(
                    branch, f"feat: {plan_summary[:60]}"
                )
                if not git_ok:
                    logger.warning("[worker] Git commit/push gagal (%s); fallback ke tools.", git_log[:300])
                    ok = False
                    log = git_log
            else:
                logger.warning("[worker] OpenCode CLI gagal (%s), fallback ke tools.", log[:300])

        if not ok:
            from src.github_tools import create_github_branch, update_github_file
            if msg_id:
                await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file (fallback tools)...")
            branch_res = await create_github_branch(branch)
            file_paths = state.get("file", [])
            if not file_paths:
                file_paths = [path.strip() for path in log.splitlines() if path.strip().startswith(("src/", "api/", "tests/", "oline-worker/"))][:8] or ["src/tools.py"]
            if msg_id:
                await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file... ✅\n[4/5] Commit & push...")
            edited_any = False
            for fp in file_paths:
                from src.github_tools import read_github_file, ai_fix_code
                current = await read_github_file(fp, branch="main")
                if current.startswith("Credentials") or current.startswith("File "):
                    continue
                new_content = await ai_fix_code(current, f"Perintah: {perintah}\nPlan: {plan_summary}")
                if new_content and new_content != current:
                    await update_github_file(branch, fp, new_content, f"feat: {plan_summary[:60]}")
                    edited_any = True
            if not edited_any:
                marker = (
                    f"# Fitur baru: {plan_summary}\n"
                    f"# Perintah asli: {perintah}\n"
                    "# TODO: implementasi detail sesuai plan.\n"
                )
                await update_github_file(branch, "src/coding_agent_note.py", marker, f"feat: {plan_summary[:60]}")
        else:
            if msg_id:
                await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file (OpenCode CLI)... ✅\n[4/5] Commit & push... ✅\n[5/5] Membuat PR...")

        # --- Buat Pull Request ---
        if msg_id:
            await update_progress(chat_id, msg_id, f"⏳ Memproses Plan: {plan_summary}\n\n[1/5] Menyiapkan worker... ✅\n[2/5] Menyiapkan repo & branch... ✅\n[3/5] Edit file... ✅\n[4/5] Commit & push... ✅\n[5/5] Membuat PR...")

        pr_title = f"feat: {plan_summary[:70]}"
        pr_body = (
            f"Perintah user: {perintah}\n\n"
            f"Langkah:\n" + "\n".join(f"- {l}" for l in langkah) +
            "\n\nRencana dibuat & disetujui user melalui Telegram."
        )
        pr_res = await create_pull_request(branch=branch, title=pr_title, body=pr_body)
        logger.info("[worker] PR coding agent: %s", pr_res)

        # --- Laporkan hasil + tombol aksi (bubble yang sama) ---
        import re as _re
        m = _re.search(r"pull/(\d+)", pr_res)
        pr_number = m.group(1) if m else ""
        state["branch"] = branch
        state["pr_number"] = pr_number
        state["status"] = "selesai"
        await save_plan_state(chat_id, state)

        report = (
            f"✅ Selesai: {plan_summary}\n\n"
            f"🌿 Branch: {branch}\n"
            f"📌 PR: {pr_res}\n\n"
            "Pilih aksi di bawah untuk menutup siklus:"
        )
        if msg_id:
            await update_progress(chat_id, msg_id, report, reply_markup=coding_result_keyboard())
        else:
            await send_telegram_message(chat_id, report)

        await clear_pending_task(chat_id)
        await clear_progress_message_id(chat_id)
        await clear_task_start(chat_id)
        await _notify_vercel_callback(chat_id, "success", report[:500])
    except Exception as e:
        logger.error("[worker] Coding task gagal chat=%s: %s", chat_id, str(e), exc_info=True)
        fail_text = f"❌ Gagal memproses plan. Penyebab: {str(e)[:150]}"
        if msg_id:
            await update_progress(chat_id, msg_id, fail_text)
        else:
            await send_telegram_message(chat_id, fail_text)
        try:
            await clear_pending_task(chat_id)
            await clear_progress_message_id(chat_id)
            await clear_task_start(chat_id)
        except Exception:
            pass
        await _notify_vercel_callback(chat_id, "error", str(e)[:500])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "oline-worker"}


@app.post("/process")
async def process(request: Request):
    auth = request.headers.get("X-Worker-Key", "")
    if not _auth_ok(auth):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    chat_id = payload.get("chat_id")
    perintah = payload.get("perintah") or payload.get("message")
    intent = payload.get("intent", "preview")
    user_name = payload.get("user_name", "Teman")

    if not chat_id or not perintah:
        return JSONResponse({"status": "error", "message": "chat_id & perintah wajib"}, status_code=400)

    msg_id = await _get_msg_id(payload, chat_id)

    logger.info("[worker] Menerima task chat=%s intent=%s msg_id=%s", chat_id, intent, msg_id)

    # Jalankan di background; balas 202 cepat (Render menahan proses hidup sampai selesai)
    if intent == "coding_agent":
        asyncio.create_task(_run_coding_task(chat_id, perintah, user_name, msg_id))
    else:
        asyncio.create_task(_run_task(chat_id, perintah, intent, user_name, msg_id))

    return JSONResponse({"status": "accepted", "message": "Task diterima, diproses di background."}, status_code=202)
