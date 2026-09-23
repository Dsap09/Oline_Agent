"""
Modul integrasi GitHub API (PyGithub) untuk fitur Self-Improving Agent Oline.
Memungkinkan Oline membaca file, membuat branch, mengedit kode, dan membuat Pull Request.
"""

import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _get_github_credentials() -> tuple[str, str, str]:
    """Mengambil credentials GitHub dari environment variables."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    return token, owner, repo


def _get_repo():
    """Mengembalikan PyGithub Repository object."""
    token, owner, repo_name = _get_github_credentials()
    if not token or not owner or not repo_name:
        return None

    try:
        from github import Github
        g = Github(token)
        return g.get_repo(f"{owner}/{repo_name}")
    except Exception as e:
        logger.error("Error initializing Github repository client: %s", str(e))
        return None


async def read_github_file(path: str, branch: str = "main") -> str:
    """
    Membaca isi file dari repository GitHub Oline pada branch tertentu.
    """
    repo = _get_repo()
    if not repo:
        return "Credentials GITHUB_TOKEN, GITHUB_OWNER, atau GITHUB_REPO belum dikonfigurasi."

    try:
        file_content = repo.get_contents(path, ref=branch)
        if isinstance(file_content, list):
            return f"Path '{path}' adalah direktori, bukan file."
        return file_content.decoded_content.decode("utf-8")
    except Exception as e:
        logger.warning("Failed to read GitHub file '%s' on branch '%s': %s", path, branch, str(e))
        return f"File '{path}' tidak ditemukan atau gagal dibaca pada branch '{branch}': {str(e)}"


async def create_github_branch(branch_name: str, from_branch: str = "main") -> str:
    """
    Membuat branch baru dari branch sumber (default: main).
    """
    repo = _get_repo()
    if not repo:
        return "Credentials GITHUB_TOKEN, GITHUB_OWNER, atau GITHUB_REPO belum dikonfigurasi."

    clean_branch = branch_name.strip()
    if not clean_branch.startswith("oline-update/") and not clean_branch.startswith("oline-fix/"):
        clean_branch = f"oline-update/{clean_branch.lstrip('/')}"

    try:
        source_branch = repo.get_branch(from_branch)
        sha = source_branch.commit.sha
        repo.create_git_ref(ref=f"refs/heads/{clean_branch}", sha=sha)
        return f"Branch '{clean_branch}' berhasil dibuat dari '{from_branch}'."
    except Exception as e:
        err_str = str(e)
        if "Reference already exists" in err_str or "422" in err_str:
            return f"Branch '{clean_branch}' sudah ada sebelumnya."
        logger.error("Failed to create GitHub branch '%s': %s", clean_branch, err_str)
        return f"Gagal membuat branch '{clean_branch}': {err_str}"


async def update_github_file(
    branch: str, path: str, content: str, commit_message: str
) -> str:
    """
    Menambah atau memperbarui file pada branch tertentu di repository GitHub.
    """
    repo = _get_repo()
    if not repo:
        return "Credentials GITHUB_TOKEN, GITHUB_OWNER, atau GITHUB_REPO belum dikonfigurasi."

    clean_branch = branch.strip()
    try:
        # Cek apakah file sudah ada di branch tersebut
        try:
            contents = repo.get_contents(path, ref=clean_branch)
            if not isinstance(contents, list):
                repo.update_file(
                    path=contents.path,
                    message=commit_message,
                    content=content,
                    sha=contents.sha,
                    branch=clean_branch,
                )
                return f"File '{path}' berhasil diperbarui di branch '{clean_branch}'."
        except Exception:
            # File belum ada -> buat file baru
            repo.create_file(
                path=path,
                message=commit_message,
                content=content,
                branch=clean_branch,
            )
            return f"File '{path}' berhasil dibuat di branch '{clean_branch}'."

    except Exception as e:
        logger.error("Failed to update GitHub file '%s' on branch '%s': %s", path, clean_branch, str(e))
        return f"Gagal mengupdate file '{path}': {str(e)}"


async def create_pull_request(
    branch: str, title: str, body: str, base_branch: str = "main"
) -> str:
    """
    Membuat Pull Request (PR) dari branch fitur ke branch utama (main).
    """
    repo = _get_repo()
    if not repo:
        return "Credentials GITHUB_TOKEN, GITHUB_OWNER, atau GITHUB_REPO belum dikonfigurasi."

    clean_branch = branch.strip()
    try:
        pr = repo.create_pull(
            title=title,
            body=body,
            head=clean_branch,
            base=base_branch,
        )
        return f"Pull Request berhasil dibuat: {pr.html_url}\nSilakan review dan merge ya!"
    except Exception as e:
        err_str = str(e)
        logger.error("Failed to create Pull Request for branch '%s': %s", clean_branch, err_str)
        return f"Gagal membuat Pull Request: {err_str}"


async def merge_pull_request(pr_number) -> str:
    """
    Menggabungkan (merge) Pull Request ke branch dasarnya via GitHub API.
    pr_number bisa berupa nomor PR (int/str) atau URL PR.
    """
    repo = _get_repo()
    if not repo:
        return "Credentials GITHUB_TOKEN, GITHUB_OWNER, atau GITHUB_REPO belum dikonfigurasi."

    if not pr_number:
        return "Nomor PR tidak valid."

    try:
        num = str(pr_number).strip().rstrip("/").rsplit("/", 1)[-1]
        pr = repo.get_pull(int(num))
        result = pr.merge()
        if getattr(result, "merged", False):
            return f"PR #{num} berhasil di-merge: {result.message}"
        return f"PR #{num} gagal di-merge: {result.message}"
    except Exception as e:
        err_str = str(e)
        logger.error("Failed to merge Pull Request '%s': %s", pr_number, err_str)
        return f"Gagal merge PR: {err_str}"


def _sanitize_slug(name: str) -> str:
    """Mengubah nama menjadi slug aman untuk nama branch (huruf kecil, dash)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s or "preview"


def _merge_preview_files(html: str, css: str, js: str) -> str:
    """Menggabungkan HTML/CSS/JS menjadi satu file index.html untuk preview."""
    combined = html or ""
    if css:
        style = f"<style>{css}</style>"
        if "</head>" in combined:
            combined = combined.replace("</head>", f"{style}</head>")
        else:
            combined = f"{style}\n{combined}"
    if js:
        script = f"<script>{js}</script>"
        if "</body>" in combined:
            combined = combined.replace("</body>", f"{script}</body>")
        else:
            combined = f"{combined}\n{script}"
    return combined


async def create_github_preview(
    title: str, html: str, css: str = "", js: str = "", slug: str = "preview"
) -> dict:
    """
    Membuat preview landing page via GitHub (branch terpisah) dan mengembalikan
    URL preview yang bisa dibuka publik via htmlpreview.

    Preview diletakkan di branch 'preview/<slug>-<timestamp>' agar tidak
    mengganggu 'main', dan bisa dihapus bersih dengan delete_github_branch.
    """
    token, owner, repo_name = _get_github_credentials()
    if not token or not owner or not repo_name:
        return {
            "status": "error",
            "error": "ERROR: GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum dikonfigurasi.",
            "url": None,
        }

    try:
        from github import Github

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")

        branch = f"preview/{_sanitize_slug(slug)}-{int(time.time())}"
        combined = _merge_preview_files(html or "", css or "", js or "")
        if not combined.strip():
            return {"status": "error", "error": "ERROR: Kode HTML kosong.", "url": None}

        src = repo.get_branch("main")
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=src.commit.sha)
        repo.create_file(
            path="index.html",
            message=f"preview: {title}",
            content=combined,
            branch=branch,
        )

        preview_url = (
            f"https://htmlpreview.github.io/?https://github.com/"
            f"{owner}/{repo_name}/blob/{branch}/index.html"
        )
        return {
            "status": "success",
            "result_code": "SUKSES",
            "url": preview_url,
            "branch": branch,
            "message": f"SUKSES: Preview siap! Buka link ini untuk melihat: {preview_url}",
        }
    except Exception as e:
        logger.error("Error creating GitHub preview: %s", str(e))
        return {"status": "error", "error": f"ERROR: Gagal membuat preview GitHub: {str(e)}", "url": None}


async def list_github_previews() -> dict:
    """
    Mengambil daftar semua preview landing page yang aktif (branch GitHub berprefix 'preview/').
    Setiap preview dibangun URL publik htmlpreview dari nama branch-nya.
    """
    token, owner, repo_name = _get_github_credentials()
    if not token or not owner or not repo_name:
        return {"error": "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum dikonfigurasi."}

    try:
        from github import Github

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")
        previews = []
        for branch in repo.get_branches():
            name = branch.name
            if not name.startswith("preview/"):
                continue
            slug = name[len("preview/"):]
            url = (
                f"https://htmlpreview.github.io/?https://github.com/"
                f"{owner}/{repo_name}/blob/{name}/index.html"
            )
            previews.append({"branch": name, "slug": slug, "url": url})

        previews.sort(key=lambda p: p["slug"], reverse=True)
        if not previews:
            return {"message": "Belum ada preview yang tersimpan."}
        return {"status": "success", "total": len(previews), "previews": previews}
    except Exception as e:
        logger.error("Gagal mengambil daftar preview GitHub: %s", str(e))
        return {"error": f"Gagal mengambil daftar preview: {str(e)}"}


async def delete_github_branch(branch_name: str) -> str:
    """
    Menghapus branch preview di GitHub (refs/heads/<branch>) secara bersih.
    Dipakai untuk membersihkan preview setelah landing page di-deploy.
    """
    token, owner, repo_name = _get_github_credentials()
    if not token or not owner or not repo_name:
        return "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum dikonfigurasi."

    clean_branch = (branch_name or "").strip().lstrip("refs/heads/")
    if not clean_branch:
        return "Nama branch tidak valid."

    try:
        from github import Github

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")
        ref = repo.get_git_ref(f"heads/{clean_branch}")
        ref.delete()
        logger.info("Branch preview '%s' berhasil dihapus.", clean_branch)
        return f"Branch preview '{clean_branch}' berhasil dihapus."
    except Exception as e:
        err = str(e)
        if "404" in err or "Not Found" in err:
            return f"Branch preview '{clean_branch}' sudah tidak ada (sudah dihapus)."
        logger.error("Gagal menghapus branch preview '%s': %s", clean_branch, err)
        return f"Gagal menghapus branch preview '{clean_branch}': {err}"


async def cleanup_old_previews(days: int = 7) -> str:
    """
    Menghapus semua branch preview GitHub (prefix 'preview/') yang usianya
    sudah lebih dari `days` hari (berdasarkan timestamp di nama branch).
    Preview yang dihapus/di-deploy tidak terpengaruh.
    """
    token, owner, repo_name = _get_github_credentials()
    if not token or not owner or not repo_name:
        return "GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO belum dikonfigurasi."

    cutoff = time.time() - (max(1, int(days)) * 86400)
    deleted = 0
    try:
        from github import Github

        g = Github(token)
        repo = g.get_repo(f"{owner}/{repo_name}")
        for branch in repo.get_branches():
            name = branch.name
            if not name.startswith("preview/"):
                continue
            # Format: preview/<slug>-<epoch> ; ambil epoch di segmen terakhir
            try:
                epoch = int(name.rsplit("-", 1)[-1])
            except ValueError:
                continue
            if epoch < cutoff:
                try:
                    repo.get_git_ref(f"heads/{name}").delete()
                    deleted += 1
                    logger.info("Preview kedaluwarsa dihapus: %s", name)
                except Exception as e:
                    logger.warning("Gagal hapus preview kedaluwarsa '%s': %s", name, str(e))

        msg = f"Pembersihan preview selesai: {deleted} branch preview kedaluwarsa (> {days} hari) dihapus."
        logger.info(msg)
        return msg
    except Exception as e:
        logger.error("Gagal membersihkan preview GitHub: %s", str(e))
        return f"Gagal membersihkan preview GitHub: {str(e)}"


async def ai_fix_code(current_code: str, error_desc: str) -> str:
    """
    Meminta AI (Gemini) untuk memperbaiki kode berdasarkan deskripsi error/diagnosis.
    """
    prompt = (
        f"Berikut adalah kode sumber file yang mengalami error:\n\n"
        f"```python\n{current_code}\n```\n\n"
        f"Detail Error & Diagnosis:\n{error_desc}\n\n"
        f"Tolong perbaiki kode tersebut. Kembalikan HANYA kode Python lengkap yang sudah diperbaiki "
        f"tanpa penjelasan tambahan, tanpa tanda backtick markdown, atau teks ekstra."
    )

    try:
        from src.gemini import chat_with_oline
        fixed = await chat_with_oline(
            chat_id=0,
            user_message=prompt,
            user_name="System",
            use_gemini_only=True,
        )
        if fixed:
            cleaned = fixed.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
            return cleaned if cleaned else current_code
    except Exception as e:
        logger.error("Error in ai_fix_code: %s", str(e))

    return current_code
