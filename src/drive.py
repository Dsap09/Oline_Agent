"""
Google Drive API helper untuk Oline bot.
Mengelola autentikasi Service Account, pembuat folder, upload file/foto,
list file, pencarian, dan download file.
"""

import io
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]


def get_drive_folder_id() -> str:
    """Mengembalikan root folder ID Oline di Google Drive."""
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    return folder_id


def _get_refresh_token() -> str:
    """
    Mengambil refresh token Google Drive dengan prioritas: Vercel KV (token:drive) > env.
    Token di KV memungkinkan Oline memperbarui sendiri tanpa redeploy (Kelola Token via KV).
    """
    try:
        from src.kv import get_token_sync
        kv_token = get_token_sync("drive")
        if kv_token and kv_token.strip():
            return kv_token.strip()
    except Exception as e:
        logger.warning("Gagal membaca token Drive dari KV, fallback ke env: %s", str(e))
    return os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "").strip()


def get_drive_service():
    """
    Inisialisasi dan mengembalikan service Google Drive v3 menggunakan OAuth 2.0 User Refresh Token.
    Menggunakan akun pribadi pemilik bot sehingga file tersimpan menggunakan kuota 15 GB pribadi.
    Refresh token dibaca dari Vercel KV terlebih dahulu, fallback ke env.
    """
    refresh_token = _get_refresh_token()
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()

    if not (refresh_token and client_id and client_secret):
        # Fallback: Coba Service Account jika OAuth belum diisi
        creds_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS", "").strip()
        if creds_json:
            try:
                creds_dict = json.loads(creds_json)
                from google.oauth2.service_account import Credentials
                from googleapiclient.discovery import build

                creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
                return build("drive", "v3", credentials=creds)
            except Exception as e:
                logger.warning("Service Account fallback failed: %s", str(e))

        raise ValueError(
            "OAuth 2.0 credentials (GOOGLE_DRIVE_REFRESH_TOKEN, GOOGLE_DRIVE_CLIENT_ID, "
            "GOOGLE_DRIVE_CLIENT_SECRET) environment variables are not configured."
        )

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )

        creds.refresh(Request())


        return build("drive", "v3", credentials=creds)
    except Exception as e:
        err_str = str(e)
        logger.error("Failed to initialize Google Drive OAuth 2.0 service: %s", err_str)
        if "invalid_grant" in err_str:
            # Notifikasi token error: beri tahu user cara memperbarui via KV.
            try:
                from src.kv import log_error
                import asyncio
                asyncio.run(log_error(
                    "Google Drive refresh token invalid_grant (dicabut/kedaluwarsa). "
                    "Gunakan /set_token drive <refresh_token>."
                ))
            except Exception:
                pass
            raise RuntimeError(
                "Token Google Drive tidak valid (invalid_grant — sudah dicabut/kedaluwarsa). "
                "Perbarui dengan kirim: /set_token drive <refresh_token>"
            )
        raise RuntimeError(f"Gagal menginisialisasi Google Drive API via OAuth 2.0: {err_str}")



def find_folder(service, folder_name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """
    Mencari folder berdasarkan nama persis di bawah parent_id (default GOOGLE_DRIVE_FOLDER_ID).
    Returns folder_id atau None jika tidak ditemukan.
    """
    root_id = parent_id or get_drive_folder_id()
    folder_name_clean = folder_name.replace("'", "\\'")

    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{folder_name_clean}' "
        f"and trashed=false"
    )
    if root_id:
        query += f" and '{root_id}' in parents"

    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    return None


def create_folder(service, folder_name: str, parent_id: Optional[str] = None) -> tuple[str, bool]:
    """
    Membuat folder baru di bawah parent_id.
    Returns tuple: (folder_id, is_newly_created).
    """
    root_id = parent_id or get_drive_folder_id()

    existing_id = find_folder(service, folder_name, root_id)
    if existing_id:
        return existing_id, False

    metadata: dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if root_id:
        metadata["parents"] = [root_id]

    folder = service.files().create(body=metadata, fields="id, name").execute()
    return folder.get("id"), True


def upload_file(
    service,
    file_name: str,
    file_bytes: bytes,
    mime_type: str = "application/octet-stream",
    folder_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Upload file/dokumen/foto ke Google Drive.
    Jika folder_name diisi, file dimasukkan ke subfolder tersebut.
    """
    from googleapiclient.http import MediaIoBaseUpload

    clean_name = os.path.basename(file_name) or "file_oline"
    root_id = get_drive_folder_id()
    target_parent_id = root_id

    if folder_name:
        folder_id, _ = create_folder(service, folder_name, root_id)
        target_parent_id = folder_id

    file_metadata: dict[str, Any] = {"name": clean_name}
    if target_parent_id:
        file_metadata["parents"] = [target_parent_id]

    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink, mimeType, size",
    ).execute()

    return {
        "id": uploaded.get("id"),
        "name": uploaded.get("name"),
        "web_link": uploaded.get("webViewLink", ""),
        "mimeType": uploaded.get("mimeType", mime_type),
        "size": uploaded.get("size", len(file_bytes)),
    }


def list_files(service, folder_name: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Mengambil daftar file dan folder di dalam folder_name (atau root folder ID).
    """
    root_id = get_drive_folder_id()
    target_id = root_id

    if folder_name:
        found_id = find_folder(service, folder_name, root_id)
        if not found_id:
            return []
        target_id = found_id

    query = "trashed=false"
    if target_id:
        query += f" and '{target_id}' in parents"

    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, size, webViewLink)",
        orderBy="folder, name",
    ).execute()

    items = results.get("files", [])
    output = []
    for item in items:
        is_dir = item.get("mimeType") == "application/vnd.google-apps.folder"
        output.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "mimeType": item.get("mimeType"),
            "is_folder": is_dir,
            "size": item.get("size", 0),
            "web_link": item.get("webViewLink", ""),
        })
    return output


def search_files(service, query_name: str) -> list[dict[str, Any]]:
    """
    Mencari file berdasarkan substring nama file.
    """
    clean_query = query_name.replace("'", "\\'")
    query = f"name contains '{clean_query}' and trashed=false"
    root_id = get_drive_folder_id()
    if root_id:
        query += f" and '{root_id}' in parents"

    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, size, webViewLink)",
    ).execute()

    items = results.get("files", [])
    output = []
    for item in items:
        is_dir = item.get("mimeType") == "application/vnd.google-apps.folder"
        output.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "mimeType": item.get("mimeType"),
            "is_folder": is_dir,
            "size": item.get("size", 0),
            "web_link": item.get("webViewLink", ""),
        })
    return output


def download_file(service, file_id: str) -> tuple[bytes, str, str]:
    """
    Mendownload file dari Google Drive berdasarkan file_id.
    Returns tuple: (file_bytes, file_name, mime_type).
    """
    from googleapiclient.http import MediaIoBaseDownload

    meta = service.files().get(fileId=file_id, fields="name, mimeType").execute()
    file_name = meta.get("name", "file_oline")
    mime_type = meta.get("mimeType", "application/octet-stream")

    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)
    return buffer.getvalue(), file_name, mime_type
