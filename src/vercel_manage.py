"""
Helper manajemen Vercel via REST API untuk fitur Token Renewal Assistant.
Memungkinkan Oline meng-update Environment Variable (token) dan trigger redeploy
agar perubahan langsung aktif tanpa commit secret ke git dan tanpa menyimpan di KV.
"""

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

VERCEL_API_TOKEN = (os.environ.get("VERCEL_API_TOKEN", "") or os.environ.get("VERCEL_TOKEN", "")).strip()
VERCEL_PROJECTS_URL = "https://api.vercel.com/v9/projects"
VERCEL_ENV_URL = "https://api.vercel.com/v9/projects/{project_id}/env"
VERCEL_DEPLOYMENTS_URL = "https://api.vercel.com/v13/deployments"
VERCEL_DEPLOYMENTS_LIST_URL = "https://api.vercel.com/v6/deployments"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {VERCEL_API_TOKEN}",
        "Content-Type": "application/json",
    }


async def _get_latest_deployment() -> Optional[dict]:
    """Mengambil detail deployment terbaru (termasuk gitSource & project)."""
    if not VERCEL_API_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                VERCEL_DEPLOYMENTS_LIST_URL, headers=_headers(), params={"limit": 1}
            )
            if resp.status_code == 200:
                deployments = resp.json().get("deployments", [])
                return deployments[0] if deployments else None
    except Exception as e:
        logger.warning("Gagal mengambil deployment terbaru Vercel: %s", str(e))
    return None


async def get_project_id() -> dict[str, Any]:
    """
    Mendapatkan projectId Vercel untuk project Oline.
    Returns dict dengan 'project_id' & 'project_name', atau 'error'.
    """
    if not VERCEL_API_TOKEN:
        return {"error": "VERCEL_API_TOKEN belum dikonfigurasi di environment variables."}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(VERCEL_PROJECTS_URL, headers=_headers(), params={"limit": 1})
            if resp.status_code == 200:
                projects = resp.json().get("projects", [])
                if not projects:
                    return {"error": "Tidak ditemukan project Vercel untuk akun ini."}
                first = projects[0]
                return {
                    "project_id": first.get("id"),
                    "project_name": first.get("name"),
                }
            return {"error": f"Gagal mengambil project Vercel (Status {resp.status_code})."}
    except Exception as e:
        logger.error("Error get_project_id: %s", str(e))
        return {"error": f"Error mengambil project Vercel: {str(e)}"}


async def set_env_var(key: str, value: str) -> dict[str, Any]:
    """
    Membuat/update (upsert) sebuah Environment Variable di Vercel.
    Returns dict dengan 'status', atau 'error'.
    """
    if not VERCEL_API_TOKEN:
        return {"error": "VERCEL_API_TOKEN belum dikonfigurasi di environment variables."}
    if not key or not value:
        return {"error": "key dan value token wajib diisi."}

    project = await get_project_id()
    if "error" in project:
        return project
    project_id = project["project_id"]

    payload = {
        "key": key,
        "value": value,
        "type": "encrypted",
        "target": ["production"],
    }
    url = VERCEL_ENV_URL.format(project_id=project_id)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=_headers(), params={"upsert": "true"}, json=payload)
            if resp.status_code in (200, 201, 200):
                return {"status": "success", "key": key}
            err_text = resp.text[:250]
            logger.error("Vercel env API error (Status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal mengupdate env var {key} (Status {resp.status_code}): {err_text}"}
    except httpx.TimeoutException:
        return {"error": "Gagal mengupdate env var: Timeout (15 detik)."}
    except Exception as e:
        logger.error("Error set_env_var: %s", str(e))
        return {"error": f"Error mengupdate env var: {str(e)}"}


async def trigger_redeploy() -> dict[str, Any]:
    """
    Memicu redeploy agar Environment Variable baru aktif.
    Memakai gitSource dari deployment terbaru (GitHub).
    """
    if not VERCEL_API_TOKEN:
        return {"error": "VERCEL_API_TOKEN belum dikonfigurasi di environment variables."}

    deployment = await _get_latest_deployment()
    if not deployment:
        return {"error": "Tidak dapat menemukan deployment terbaru untuk redeploy."}

    git_source = deployment.get("gitSource")
    project_name = deployment.get("project") or deployment.get("name")

    payload: dict = {"name": project_name}
    if isinstance(git_source, dict):
        git_payload = {
            "type": git_source.get("type", "github"),
            "ref": git_source.get("ref") or "main",
        }
        if git_source.get("repoId"):
            git_payload["repoId"] = git_source["repoId"]
        payload["gitSource"] = git_payload
    else:
        # Tanpa gitSource, redeploy deployment terakhir via id
        payload["deploymentId"] = deployment.get("uid") or deployment.get("id")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(VERCEL_DEPLOYMENTS_URL, headers=_headers(), json=payload)
            if resp.status_code in (200, 201):
                return {"status": "success", "message": "Redeploy dipicu. Token baru aktif dalam beberapa menit."}
            return {"error": f"Gagal trigger redeploy (Status {resp.status_code}): {resp.text[:200]}"}
    except httpx.TimeoutException:
        return {"error": "Gagal trigger redeploy: Timeout (20 detik)."}
    except Exception as e:
        logger.error("Error trigger_redeploy: %s", str(e))
        return {"error": f"Error trigger redeploy: {str(e)}"}


async def update_token_and_redeploy(key: str, value: str) -> dict[str, Any]:
    """
    Alur lengkap: update Environment Variable token di Vercel lalu trigger redeploy.
    """
    env_res = await set_env_var(key, value)
    if "error" in env_res:
        return env_res

    redeploy_res = await trigger_redeploy()
    if "error" in redeploy_res:
        return {
            "status": "success",
            "message": f"Env var {key} berhasil diperbarui, tapi gagal trigger redeploy otomatis: {redeploy_res['error']}. Silakan redeploy manual di dashboard Vercel.",
        }
    return redeploy_res
