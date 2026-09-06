"""
Modul integrasi GitHub API (PyGithub) untuk fitur Self-Improving Agent Oline.
Memungkinkan Oline membaca file, membuat branch, mengedit kode, dan membuat Pull Request.
"""

import logging
import os
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
    if not clean_branch.startswith("oline-update/"):
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
