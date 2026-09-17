"""
Notion API Integration untuk Oline Bot.
Menyimpan catatan ke database Notion menggunakan REST API.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

NOTION_API_URL = "https://api.notion.com/v1/pages"


def extract_database_id(raw_id: str) -> str:
    """
    Ekstrak 32-karakter ID database Notion dari ID mentah atau URL link Notion.
    Contoh: 'https://app.notion.com/p/3ceec30101df806fa6ddf65ab5aa6e40?v=...' -> '3ceec30101df806fa6ddf65ab5aa6e40'
    """
    if not raw_id:
        return ""

    raw_clean = raw_id.strip()

    # Jika mengandung URL, ekstrak bagian hex id 32 karakter
    match = re.search(r"([a-fA-F0-9]{32})", raw_clean)
    if match:
        return match.group(1)

    # Cek format UUID dengan strip tanda hubung
    cleaned_uuid = raw_clean.replace("-", "")
    if len(cleaned_uuid) == 32:
        return cleaned_uuid

    return raw_clean


def _get_notion_memory_db_id() -> str:
    """
    Mengambil ID database memori Notion.
    Mendahulukan NOTION_MEMORY_DATABASE_ID, jika kosong fallback ke NOTION_DATABASE_ID.
    """
    raw_mem = (os.environ.get("NOTION_MEMORY_DATABASE_ID") or "").strip().strip('"').strip("'")
    if not raw_mem:
        raw_mem = (os.environ.get("NOTION_DATABASE_ID") or "").strip().strip('"').strip("'")
    db_id = extract_database_id(raw_mem)
    cleaned = db_id.replace("-", "")
    if len(cleaned) == 32:
        return cleaned
    return db_id


async def save_note_to_notion(
    title: str, content: str, category: str = "Umum"
) -> dict[str, Any]:
    """
    Menyimpan catatan baru ke Notion database dengan deteksi skema properti secara dinamis.
    Return dictionary berisi status dan pesan respons.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    raw_db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    database_id = extract_database_id(raw_db_id)

    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi di environment variables."}

    if not database_id:
        return {"error": "NOTION_DATABASE_ID belum dikonfigurasi atau format ID tidak valid."}

    if not title or not title.strip():
        return {"error": "Judul catatan tidak boleh kosong."}

    if not content or not content.strip():
        return {"error": "Isi catatan tidak boleh kosong."}

    wib = timezone(timedelta(hours=7))
    now_iso = datetime.now(wib).isoformat()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    title_key = "Title"
    date_key = None
    category_key = None
    text_key = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Inspeksi Skema Database Notion secara dinamis
            try:
                db_resp = await client.get(
                    f"https://api.notion.com/v1/databases/{database_id}", headers=headers
                )
                if db_resp.status_code == 200:
                    props_schema = db_resp.json().get("properties", {})
                    for p_name, p_info in props_schema.items():
                        p_type = p_info.get("type")
                        p_clean = p_name.strip().lower()

                        if p_type == "title":
                            title_key = p_name
                        elif p_type == "date" or "tanggal" in p_clean or "date" in p_clean:
                            if p_type == "date":
                                date_key = p_name
                        elif p_type in ("select", "status") or "kategori" in p_clean or "category" in p_clean:
                            if p_type in ("select", "status"):
                                category_key = p_name
                        elif p_type == "rich_text" or "isi" in p_clean or "content" in p_clean:
                            if p_type == "rich_text":
                                text_key = p_name
            except Exception as schema_err:
                logger.warning("Gagal membaca skema database Notion: %s", str(schema_err))

            # 2. Susun Payload Properti secara Otomatis
            properties_payload: dict[str, Any] = {
                title_key: {"title": [{"text": {"content": title.strip()}}]}
            }

            if category_key:
                properties_payload[category_key] = {"select": {"name": (category or "Umum").strip()}}
            if date_key:
                properties_payload[date_key] = {"date": {"start": now_iso}}
            if text_key:
                properties_payload[text_key] = {
                    "rich_text": [{"type": "text", "text": {"content": content.strip()}}]
                }

            payload = {
                "parent": {"database_id": database_id},
                "properties": properties_payload,
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content.strip()}}]
                        },
                    }
                ],
            }

            # 3. Kirim Pembuatan Halaman Baru
            resp = await client.post(NOTION_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                page_url = data.get("url", "")
                return {
                    "status": "success",
                    "title": title.strip(),
                    "category": category.strip() if category else "Umum",
                    "message": f"Catatan '{title.strip()}' berhasil disimpan ke Notion.",
                    "url": page_url,
                }
            else:
                err_body = resp.text[:200]
                logger.error("Notion API error (Status %d): %s", resp.status_code, err_body)
                return {
                    "error": f"Gagal menyimpan ke Notion (Status {resp.status_code}): {err_body}"
                }
    except httpx.TimeoutException:
        return {"error": "Koneksi ke Notion API mengalami timeout (10 detik). Coba lagi nanti ya."}
    except Exception as e:
        logger.error("Error saving note to Notion: %s", str(e))
        return {"error": f"Gagal menghubungi Notion API: {str(e)}"}


async def add_notion_property(
    name: str, property_type: str = "files"
) -> dict[str, Any]:
    """
    Menambahkan atau mengedit properti/kolom baru pada skema database Notion.
    Supported property_type: 'files', 'url', 'select', 'multi_select', 'date', 'checkbox', 'number', 'rich_text'.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    raw_db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    database_id = extract_database_id(raw_db_id)

    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi di environment variables."}

    if not database_id:
        return {"error": "NOTION_DATABASE_ID belum dikonfigurasi atau format ID tidak valid."}

    if not name or not name.strip():
        return {"error": "Nama properti/kolom tidak boleh kosong."}

    clean_name = name.strip()
    clean_type = property_type.strip().lower()

    valid_types = {
        "files": {"files": {}},
        "file": {"files": {}},
        "url": {"url": {}},
        "link": {"url": {}},
        "select": {"select": {}},
        "multi_select": {"multi_select": {}},
        "date": {"date": {}},
        "tanggal": {"date": {}},
        "checkbox": {"checkbox": {}},
        "number": {"number": {}},
        "rich_text": {"rich_text": {}},
        "text": {"rich_text": {}},
    }

    type_payload = valid_types.get(clean_type, {"files": {}})
    canonical_type = list(type_payload.keys())[0]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    payload = {
        "properties": {
            clean_name: type_payload
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"https://api.notion.com/v1/databases/{database_id}",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                return {
                    "status": "success",
                    "property_name": clean_name,
                    "property_type": canonical_type,
                    "message": f"Kolom '{clean_name}' (tipe {canonical_type}) berhasil ditambahkan ke database Notion.",
                }
            else:
                err_body = resp.text[:250]
                logger.error("Notion PATCH database error (Status %d): %s", resp.status_code, err_body)
                return {"error": f"Gagal menambahkan kolom ke Notion (Status {resp.status_code}): {err_body}"}
    except httpx.TimeoutException:
        return {"error": "Koneksi ke Notion API mengalami timeout (10 detik). Coba lagi nanti ya."}
    except Exception as e:
        logger.error("Error in add_notion_property: %s", str(e))
        return {"error": f"Gagal mengubah skema database Notion: {str(e)}"}


# --- Notion Hybrid Memory Functions ---

async def _inspect_and_ensure_memory_schema(
    client: httpx.AsyncClient, database_id: str, headers: dict
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """
    Inspeksi skema database Notion dan pastikan properti Title, Jenis (select), Tanggal (date), dan Isi (rich_text) ada.
    Returns: (title_key, category_key, date_key, isi_key)
    """
    title_key = "Title"
    category_key = None
    date_key = None
    isi_key = None

    try:
        db_resp = await client.get(
            f"https://api.notion.com/v1/databases/{database_id}", headers=headers
        )
        if db_resp.status_code == 200:
            props = db_resp.json().get("properties", {})
            for p_name, p_info in props.items():
                p_type = p_info.get("type")
                p_clean = p_name.strip().lower()
                if p_type == "title":
                    title_key = p_name
                elif p_type in ("select", "status"):
                    if p_clean in ("jenis", "kategori", "category", "type") or not category_key:
                        category_key = p_name
                elif p_type == "date":
                    if p_clean in ("tanggal", "date") or not date_key:
                        date_key = p_name
                elif p_type == "rich_text":
                    if p_clean in ("isi", "content", "detail", "text") or not isi_key:
                        isi_key = p_name

            # Patch database jika ada properti penting yang belum ada
            missing_props = {}
            if not category_key:
                missing_props["Jenis"] = {"select": {}}
            if not isi_key:
                missing_props["Isi"] = {"rich_text": {}}

            if missing_props:
                try:
                    patch_resp = await client.patch(
                        f"https://api.notion.com/v1/databases/{database_id}",
                        json={"properties": missing_props},
                        headers=headers,
                    )
                    if patch_resp.status_code == 200:
                        if not category_key and "Jenis" in missing_props:
                            category_key = "Jenis"
                        if not isi_key and "Isi" in missing_props:
                            isi_key = "Isi"
                except Exception as patch_err:
                    logger.warning("Gagal menambahkan missing properties ke Notion: %s", str(patch_err))
    except Exception as e:
        logger.warning("Gagal inspeksi skema Notion memory database: %s", str(e))

    return title_key, category_key or "Jenis", date_key or "Tanggal", isi_key or "Isi"


async def save_memory_to_notion(
    title: str, content: str, memory_type: str = "Aturan"
) -> str:
    """
    Menyimpan memori baru (Aturan, Preferensi, Ringkasan, Fakta) ke database Notion 'Memori Oline'.
    Gunakan NOTION_MEMORY_DATABASE_ID (fallback ke NOTION_DATABASE_ID jika belum diset).
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = _get_notion_memory_db_id()

    if not api_key:
        return "Gagal menyimpan memori: NOTION_API_KEY belum dikonfigurasi."
    if not database_id:
        return "Gagal menyimpan memori: Database ID Notion belum dikonfigurasi."

    if not title or not title.strip():
        title = f"Memori {memory_type}"
    if not content or not content.strip():
        return "Gagal menyimpan memori: Content kosong."

    # Ringkas judul maksimal 100 karakter
    clean_title = title.strip()[:100]

    wib = timezone(timedelta(hours=7))
    now_iso = datetime.now(wib).isoformat()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            title_key, category_key, date_key, isi_key = await _inspect_and_ensure_memory_schema(
                client, database_id, headers
            )

            properties_payload: dict[str, Any] = {
                title_key: {"title": [{"text": {"content": clean_title}}]}
            }
            if category_key:
                properties_payload[category_key] = {"select": {"name": (memory_type or "Aturan").strip()}}
            if date_key:
                properties_payload[date_key] = {"date": {"start": now_iso}}
            if isi_key:
                properties_payload[isi_key] = {"rich_text": [{"type": "text", "text": {"content": content.strip()}}]}

            payload = {
                "parent": {"database_id": database_id},
                "properties": properties_payload,
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content.strip()}}]
                        },
                    }
                ],
            }

            resp = await client.post("https://api.notion.com/v1/pages", json=payload, headers=headers)
            if resp.status_code == 200:
                # Write-through cache update: langsung refresh KV cache dari Notion dengan TTL 24 jam
                await read_memory_from_notion(memory_type, force_refresh=True)
                await read_memory_from_notion(None, force_refresh=True)
                return "Memori berhasil disimpan."
            elif resp.status_code == 404:
                logger.warning("Notion memory database 404 Not Found (object_not_found). Check integration permissions.")
                return "Gagal menyimpan memori: Database Notion tidak ditemukan (404). Pastikan database sudah dibagikan ke Integrasi Notion."
            else:
                err_text = resp.text[:200]
                logger.error("Notion save_memory error (Status %d): %s", resp.status_code, err_text)
                return f"Gagal menyimpan memori: Status {resp.status_code} - {err_text}"
    except Exception as e:
        logger.error("Error in save_memory_to_notion: %s", str(e))
        return f"Gagal menyimpan memori: {str(e)}"


async def verify_notion_databases() -> dict[str, Any]:
    """
    Memverifikasi bahwa kedua database Notion (Catatan & Memori) masih ada dan
    masih dibagikan ke Integrasi Oline dengan men-query endpoint /v1/databases/{id}.
    Returns dict {"results": {label: {"configured", "status", "title"/"detail"}}}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi di environment variables."}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    dbs = {
        "catatan": (os.environ.get("NOTION_DATABASE_ID", "") or "").strip(),
        "memori": _get_notion_memory_db_id(),
    }

    results: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for label, raw in dbs.items():
                db_id = extract_database_id(raw)
                if not db_id:
                    results[label] = {"configured": False, "status": "unconfigured"}
                    continue
                try:
                    resp = await client.get(
                        f"https://api.notion.com/v1/databases/{db_id}", headers=headers
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        title = ""
                        raw_title = data.get("title") or []
                        if raw_title and isinstance(raw_title[0], dict):
                            title = raw_title[0].get("plain_text", "")
                        results[label] = {
                            "configured": True,
                            "status": "ok",
                            "database_id": db_id,
                            "title": title,
                        }
                    elif resp.status_code == 404:
                        results[label] = {
                            "configured": True,
                            "status": "not_shared",
                            "database_id": db_id,
                            "detail": "database tidak ditemukan / belum dibagikan ke Integrasi Oline",
                        }
                    else:
                        results[label] = {
                            "configured": True,
                            "status": "error",
                            "database_id": db_id,
                            "detail": f"status {resp.status_code}",
                        }
                except Exception as e:
                    results[label] = {
                        "configured": True,
                        "status": "error",
                        "database_id": db_id,
                        "detail": str(e),
                    }
    except Exception as e:
        logger.error("Error verifying Notion databases: %s", str(e))
        return {"error": f"Gagal menghubungi Notion API: {str(e)}"}

    return {"results": results}


async def read_memory_from_notion(
    memory_type: Optional[str] = None, force_refresh: bool = False
) -> str:
    """
    Membaca daftar memori dari database Notion.
    Menggunakan Vercel KV sebagai cache utama selama 24 jam (86400s) untuk latensi sangat cepat (<50ms).
    Notion hanya di-query saat cache miss atau force_refresh=True.
    Format pengembalian: - {title}: {isi_text}
    """
    cache_key = f"cache:notion_memory:{memory_type or 'all'}"
    from src.kv import get_cache, set_cache

    if not force_refresh:
        cached_val = await get_cache(cache_key)
        if cached_val is not None:
            return cached_val

    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = _get_notion_memory_db_id()

    if not api_key or not database_id:
        return ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            title_key, category_key, date_key, isi_key = await _inspect_and_ensure_memory_schema(
                client, database_id, headers
            )

            payload: dict[str, Any] = {}
            if memory_type and category_key:
                payload["filter"] = {
                    "property": category_key,
                    "select": {"equals": memory_type.strip()},
                }

            resp = await client.post(
                f"https://api.notion.com/v1/databases/{database_id}/query",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning("Notion query memory status %d: %s", resp.status_code, resp.text[:200])
                return ""

            data = resp.json()
            lines = []
            for page in data.get("results", []):
                props = page.get("properties", {})
                t_prop = props.get(title_key, {}).get("title", []) or props.get("Title", {}).get("title", [])
                title_text = t_prop[0].get("text", {}).get("content", "").strip() if t_prop else ""

                isi_prop = props.get(isi_key, {}).get("rich_text", []) if isi_key else []
                if not isi_prop and "Isi" in props:
                    isi_prop = props.get("Isi", {}).get("rich_text", [])
                isi_text = isi_prop[0].get("text", {}).get("content", "").strip() if isi_prop else ""

                if title_text and isi_text:
                    lines.append(f"- {title_text}: {isi_text}")
                elif title_text:
                    lines.append(f"- {title_text}")
                elif isi_text:
                    lines.append(f"- {isi_text}")

            result_str = "\n".join(lines)
            # Simpan ke Vercel KV dengan TTL 24 jam (86400s)
            await set_cache(cache_key, result_str, ttl_seconds=86400)
            return result_str
    except Exception as e:
        logger.error("Error in read_memory_from_notion: %s", str(e))
        return ""


async def clear_memory_cache(memory_type: Optional[str] = None) -> bool:
    """
    Mengosongkan cache KV untuk memori Notion.
    """
    from src.kv import del_cache
    if memory_type:
        await del_cache(f"cache:notion_memory:{memory_type}")
    await del_cache("cache:notion_memory:all")
    await del_cache("cache:notion_memory:Aturan")
    await del_cache("cache:notion_memory:Preferensi")
    return True

