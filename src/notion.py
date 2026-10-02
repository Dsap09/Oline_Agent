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
) -> tuple[str, str, str, str, Optional[str], Optional[str]]:
    """
    Inspeksi skema database Notion dan pastikan properti Title, Jenis (select),
    Tanggal (date), Isi (rich_text), Sumber (rich_text), dan Confidence (number) ada.
    Returns: (title_key, category_key, date_key, isi_key, sumber_key, confidence_key)
    sumber_key/confidence_key bisa None bila gagal ditambahkan (degradasi anggun).
    """
    title_key = "Title"
    category_key = None
    date_key = None
    isi_key = None
    sumber_key = None
    confidence_key = None

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
                    if p_clean in ("isi", "content", "detail", "text"):
                        isi_key = p_name
                    elif p_clean in ("sumber", "source"):
                        sumber_key = p_name
                    elif isi_key is None:
                        isi_key = p_name
                    elif sumber_key is None:
                        sumber_key = p_name
                elif p_type == "number":
                    if p_clean in ("confidence", "konfidensi", "skor", "score") or not confidence_key:
                        confidence_key = p_name

            # Patch database jika ada properti penting yang belum ada
            missing_props = {}
            if not category_key:
                missing_props["Jenis"] = {"select": {}}
            if not isi_key:
                missing_props["Isi"] = {"rich_text": {}}
            if not sumber_key:
                missing_props["Sumber"] = {"rich_text": {}}
            if not confidence_key:
                missing_props["Confidence"] = {"number": {}}

            if missing_props:
                try:
                    patch_resp = await client.patch(
                        f"https://api.notion.com/v1/databases/{database_id}",
                        json={"properties": missing_props},
                        headers=headers,
                    )
                    if patch_resp.status_code == 200:
                        if not category_key:
                            category_key = "Jenis"
                        if not isi_key:
                            isi_key = "Isi"
                        if not sumber_key:
                            sumber_key = "Sumber"
                        if not confidence_key:
                            confidence_key = "Confidence"
                except Exception as patch_err:
                    logger.warning("Gagal menambahkan missing properties ke Notion: %s", str(patch_err))
    except Exception as e:
        logger.warning("Gagal inspeksi skema Notion memory database: %s", str(e))

    return (
        title_key,
        category_key or "Jenis",
        date_key or "Tanggal",
        isi_key or "Isi",
        sumber_key,
        confidence_key,
    )


async def save_memory_entry(
    title: str,
    content: str,
    memory_type: str = "Aturan",
    sumber: str = "",
    confidence: Optional[float] = None,
    source_message: str = "",
) -> dict[str, Any]:
    """
    Menyimpan memori baru ke database Notion 'Memori Oline' (versi terstruktur).
    Returns dict: {"status": "success", "page_id", "url", ...} atau {"error": ...}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = _get_notion_memory_db_id()

    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi."}
    if not database_id:
        return {"error": "Database ID Notion belum dikonfigurasi."}

    if not title or not title.strip():
        title = f"Memori {memory_type}"
    if not content or not content.strip():
        return {"error": "Content kosong."}

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
            title_key, category_key, date_key, isi_key, sumber_key, confidence_key = (
                await _inspect_and_ensure_memory_schema(client, database_id, headers)
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
            if sumber_key and (sumber or source_message):
                properties_payload[sumber_key] = {
                    "rich_text": [{"type": "text", "text": {"content": (sumber or source_message).strip()[:1800]}}]
                }
            if confidence_key and confidence is not None:
                try:
                    properties_payload[confidence_key] = {"number": float(confidence)}
                except (TypeError, ValueError):
                    pass

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
                data = resp.json()
                # Write-through cache update: langsung refresh KV cache dari Notion dengan TTL 24 jam
                await read_memory_from_notion(memory_type, force_refresh=True)
                await read_memory_from_notion(None, force_refresh=True)
                return {
                    "status": "success",
                    "title": clean_title,
                    "category": (memory_type or "Aturan").strip(),
                    "page_id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "message": f"Catatan '{clean_title}' berhasil disimpan ke Notion.",
                }
            elif resp.status_code == 404:
                logger.warning("Notion memory database 404 Not Found (object_not_found). Check integration permissions.")
                return {"error": "Database Notion tidak ditemukan (404). Pastikan database sudah dibagikan ke Integrasi Notion."}
            else:
                err_text = resp.text[:200]
                logger.error("Notion save_memory error (Status %d): %s", resp.status_code, err_text)
                return {"error": f"Gagal menyimpan memori: Status {resp.status_code} - {err_text}"}
    except Exception as e:
        logger.error("Error in save_memory_entry: %s", str(e))
        return {"error": f"Gagal menyimpan memori: {str(e)}"}


async def save_memory_to_notion(
    title: str, content: str, memory_type: str = "Aturan"
) -> str:
    """
    Backward-compatible wrapper: menyimpan memori dan mengembalikan string status
    (dipakai tool/legacy callers). Untuk orchestration butuh page_id, pakai save_memory_entry.
    """
    res = await save_memory_entry(title=title, content=content, memory_type=memory_type)
    if isinstance(res, dict) and res.get("status") == "success":
        return "Memori berhasil disimpan."
    if isinstance(res, dict):
        return res.get("error", "Gagal menyimpan memori.")
    return str(res)


def _extract_rich_text(props: dict, key: Optional[str]) -> str:
    if not key or key not in props:
        return ""
    arr = props.get(key, {}).get("rich_text", []) or []
    if not arr:
        return ""
    return arr[0].get("text", {}).get("content", "").strip()


def _extract_select(props: dict, key: str) -> str:
    info = props.get(key) or {}
    select = info.get("select") or {}
    return (select.get("name") or "").strip()


async def query_memory_entries(memory_type: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Query langsung database Memori Notion (bukan cache) dan mengembalikan entri terstruktur.
    Returns list dict: {id, title, isi, jenis, url, sumber, confidence}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = _get_notion_memory_db_id()
    if not api_key or not database_id:
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            title_key, category_key, _date_key, isi_key, sumber_key, confidence_key = (
                await _inspect_and_ensure_memory_schema(client, database_id, headers)
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
                logger.warning("Notion query entries status %d: %s", resp.status_code, resp.text[:200])
                return []

            entries: list[dict[str, Any]] = []
            for page in resp.json().get("results", []):
                props = page.get("properties", {})
                t_prop = props.get(title_key, {}).get("title", []) or props.get("Title", {}).get("title", [])
                title_text = t_prop[0].get("text", {}).get("content", "").strip() if t_prop else ""
                isi_text = _extract_rich_text(props, isi_key) or _extract_rich_text(props, "Isi")
                sumber_text = _extract_rich_text(props, sumber_key) if sumber_key else ""
                confidence_val = None
                if confidence_key and confidence_key in props:
                    confidence_val = props.get(confidence_key, {}).get("number")
                entries.append({
                    "id": page.get("id", ""),
                    "title": title_text,
                    "isi": isi_text,
                    "jenis": _extract_select(props, category_key),
                    "url": page.get("url", ""),
                    "sumber": sumber_text,
                    "confidence": confidence_val,
                })
            return entries
    except Exception as e:
        logger.error("Error in query_memory_entries: %s", str(e))
        return []


async def update_memory_page(
    page_id: str,
    content: Optional[str] = None,
    title: Optional[str] = None,
    memory_type: Optional[str] = None,
    sumber: Optional[str] = None,
    confidence: Optional[float] = None,
) -> dict[str, Any]:
    """
    Memperbarui halaman memori Notion yang sudah ada (bukan membuat duplikat baru).
    Returns dict {"status": "success", ...} atau {"error": ...}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = _get_notion_memory_db_id()
    if not api_key or not database_id:
        return {"error": "Notion belum dikonfigurasi."}
    if not page_id:
        return {"error": "page_id kosong."}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            title_key, category_key, date_key, isi_key, sumber_key, confidence_key = (
                await _inspect_and_ensure_memory_schema(client, database_id, headers)
            )

            properties_payload: dict[str, Any] = {}
            if title and title.strip():
                properties_payload[title_key] = {"title": [{"text": {"content": title.strip()[:100]}}]}
            if memory_type and category_key:
                properties_payload[category_key] = {"select": {"name": memory_type.strip()}}
            if content and content.strip() and isi_key:
                properties_payload[isi_key] = {
                    "rich_text": [{"type": "text", "text": {"content": content.strip()[:1800]}}]
                }
            if sumber and sumber_key:
                properties_payload[sumber_key] = {
                    "rich_text": [{"type": "text", "text": {"content": sumber.strip()[:1800]}}]
                }
            if confidence is not None and confidence_key:
                try:
                    properties_payload[confidence_key] = {"number": float(confidence)}
                except (TypeError, ValueError):
                    pass

            if not properties_payload:
                return {"error": "Tidak ada perubahan properti."}

            resp = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"properties": properties_payload},
                headers=headers,
            )
            if resp.status_code == 200:
                await clear_memory_cache()
                return {"status": "success", "page_id": page_id}
            err_text = resp.text[:200]
            logger.error("Notion update page error (Status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal memperbarui memori: Status {resp.status_code} - {err_text}"}
    except Exception as e:
        logger.error("Error in update_memory_page: %s", str(e))
        return {"error": f"Gagal memperbarui memori: {str(e)}"}


async def archive_memory_page(page_id: str) -> dict[str, Any]:
    """Mengarsipkan (menghapus lembut) satu halaman memori Notion."""
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi."}
    if not page_id:
        return {"error": "page_id kosong."}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"archived": True},
                headers=headers,
            )
            if resp.status_code == 200:
                await clear_memory_cache()
                return {"status": "success", "page_id": page_id}
            err_text = resp.text[:200]
            logger.error("Notion archive page error (Status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal menghapus memori: Status {resp.status_code} - {err_text}"}
    except Exception as e:
        logger.error("Error in archive_memory_page: %s", str(e))
        return {"error": f"Gagal menghapus memori: {str(e)}"}


async def archive_all_memory_pages() -> dict[str, Any]:
    """Mengarsipkan semua entri database Memori Notion (dipakai /memory clear)."""
    entries = await query_memory_entries()
    if not entries:
        return {"status": "success", "count": 0, "message": "Tidak ada memori yang tersimpan."}
    count = 0
    for entry in entries:
        res = await archive_memory_page(entry.get("id", ""))
        if isinstance(res, dict) and res.get("status") == "success":
            count += 1
    return {"status": "success", "count": count, "message": f"{count} memori berhasil dihapus."}


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
            title_key, category_key, date_key, isi_key, _sumber_key, _confidence_key = (
                await _inspect_and_ensure_memory_schema(client, database_id, headers)
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


# --- Database Pengeluaran "Keuangan Oline" ---

async def _resolve_expense_db_id() -> str:
    """
    ID database pengeluaran: env NOTION_EXPENSE_DATABASE_ID, fallback KV
    'expense_db_id' (agar bisa dikonfigurasi via /pengeluaran config tanpa redeploy).
    """
    raw = (os.environ.get("NOTION_EXPENSE_DATABASE_ID") or "").strip().strip('"').strip("'")
    if not raw:
        try:
            from src.kv import get_cache
            raw = (await get_cache("expense_db_id") or "").strip()
        except Exception:
            raw = ""
    db_id = extract_database_id(raw)
    cleaned = db_id.replace("-", "")
    if len(cleaned) == 32:
        return cleaned
    return db_id


def _expense_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_API_KEY', '').strip()}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


async def _inspect_expense_schema(
    client: httpx.AsyncClient, database_id: str, headers: dict
) -> dict[str, Optional[str]]:
    """
    Deteksi nama properti database Keuangan Oline secara dinamis.
    Returns dict logical key -> nama properti (None bila tidak ada di DB).
    """
    schema: dict[str, Optional[str]] = {
        "title": None,
        "nominal": None,
        "kategori": None,
        "kategori_type": None,
        "sumber": None,
        "sumber_type": None,
        "tanggal": None,
        "toko": None,
        "catatan": None,
    }
    try:
        db_resp = await client.get(
            f"https://api.notion.com/v1/databases/{database_id}", headers=headers
        )
        if db_resp.status_code != 200:
            logger.warning(
                "Inspeksi schema pengeluaran gagal (status %d): %s",
                db_resp.status_code, db_resp.text[:200],
            )
            return schema

        props = db_resp.json().get("properties", {})
        # (nama, tipe, jumlah opsi) — jumlah opsi dipakai untuk memilih properti
        # kategori/sumber yang benar bila ada duplikat (mis. "kategori" vs "Kategori").
        selects: list[tuple[str, str, int]] = []
        rich_texts: list[str] = []
        for p_name, p_info in props.items():
            p_type = p_info.get("type")
            p_clean = p_name.strip().lower()

            if p_type == "title":
                schema["title"] = p_name
            elif p_type == "number":
                schema["nominal"] = p_name
            elif p_type == "date":
                schema["tanggal"] = p_name
            elif p_type in ("select", "status"):
                opts = (p_info.get("select") or p_info.get("status") or {}).get("options") or []
                selects.append((p_name, p_type, len(opts)))
            elif p_type == "rich_text":
                rich_texts.append(p_name)
                if "toko" in p_clean or "store" in p_clean or "merchant" in p_clean:
                    schema["toko"] = p_name
                elif "catatan" in p_clean or "note" in p_clean or "keterangan" in p_clean or "item" in p_clean:
                    schema["catatan"] = p_name

        def _pick_select(candidates: list[tuple[str, str, int]]) -> tuple[Optional[str], Optional[str]]:
            """Pilih kandidat dengan opsi terisi lebih dulu agar data konsisten."""
            with_opts = [c for c in candidates if c[2] > 0]
            pool = with_opts or candidates
            return (pool[0][0], pool[0][1]) if pool else (None, None)

        selected_names: set[str] = set()
        kategori_cands = [
            c for c in selects
            if "kategori" in c[0].lower() or "category" in c[0].lower()
        ]
        if kategori_cands:
            schema["kategori"], schema["kategori_type"] = _pick_select(kategori_cands)
            selected_names.add(schema["kategori"] or "")

        sumber_cands = [
            c for c in selects
            if "sumber" in c[0].lower() or "source" in c[0].lower()
        ]
        if sumber_cands:
            schema["sumber"], schema["sumber_type"] = _pick_select(sumber_cands)
            selected_names.add(schema["sumber"] or "")

        # Fallback: pilih select yang belum terpakai.
        remaining_selects = [c for c in selects if c[0] not in selected_names]
        if not schema["kategori"] and remaining_selects:
            schema["kategori"], schema["kategori_type"] = _pick_select(remaining_selects)
            remaining_selects = [c for c in remaining_selects if c[0] != schema["kategori"]]
        if not schema["sumber"] and remaining_selects:
            schema["sumber"], schema["sumber_type"] = _pick_select(remaining_selects)

        remaining_rt = [n for n in rich_texts if n not in (schema["toko"], schema["catatan"])]
        if not schema["toko"] and remaining_rt:
            schema["toko"] = remaining_rt.pop(0)
        if not schema["catatan"] and remaining_rt:
            schema["catatan"] = remaining_rt.pop(0)
    except Exception as e:
        logger.warning("Gagal inspeksi schema database pengeluaran: %s", str(e))
    return schema


def _select_payload(prop_type: Optional[str], value: str) -> dict:
    if prop_type == "status":
        return {"status": {"name": value}}
    return {"select": {"name": value}}


async def save_expense(
    deskripsi: str,
    nominal: int,
    kategori: str = "Lain-lain",
    tanggal: Optional[str] = None,
    sumber: str = "manual",
    toko: str = "",
    catatan: str = "",
) -> dict[str, Any]:
    """
    Menyimpan satu pengeluaran ke database Notion "Keuangan Oline".
    Returns {"status": "success", "page_id", "url", ...} atau {"error": ...}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi."}
    database_id = await _resolve_expense_db_id()
    if not database_id:
        return {"error": "Database pengeluaran belum dikonfigurasi (NOTION_EXPENSE_DATABASE_ID)."}

    if not deskripsi or not deskripsi.strip():
        return {"error": "Deskripsi pengeluaran kosong."}
    try:
        nominal_val = int(round(float(nominal)))
    except (TypeError, ValueError):
        return {"error": "Nominal tidak valid."}
    if nominal_val <= 0:
        return {"error": "Nominal harus lebih dari 0."}

    wib = timezone(timedelta(hours=7))
    tanggal_iso = (tanggal or datetime.now(wib).strftime("%Y-%m-%d")).strip()[:10]

    headers = _expense_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            schema = await _inspect_expense_schema(client, database_id, headers)
            if not schema.get("nominal"):
                return {"error": "Properti 'Nominal' (number) tidak ditemukan di database pengeluaran."}
            if not schema.get("title"):
                return {"error": "Properti judul (title) tidak ditemukan di database pengeluaran."}

            properties: dict[str, Any] = {
                schema["title"]: {"title": [{"text": {"content": deskripsi.strip()[:120]}}]},
                schema["nominal"]: {"number": nominal_val},
            }
            if schema.get("kategori"):
                properties[schema["kategori"]] = _select_payload(
                    schema.get("kategori_type"), (kategori or "Lain-lain").strip()
                )
            if schema.get("sumber"):
                properties[schema["sumber"]] = _select_payload(schema.get("sumber_type"), sumber)
            if schema.get("tanggal"):
                properties[schema["tanggal"]] = {"date": {"start": tanggal_iso}}
            if schema.get("toko") and toko:
                properties[schema["toko"]] = {
                    "rich_text": [{"type": "text", "text": {"content": toko.strip()[:200]}}]
                }
            if schema.get("catatan") and catatan:
                properties[schema["catatan"]] = {
                    "rich_text": [{"type": "text", "text": {"content": catatan.strip()[:1800]}}]
                }

            payload = {"parent": {"database_id": database_id}, "properties": properties}
            resp = await client.post("https://api.notion.com/v1/pages", json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "success",
                    "page_id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "deskripsi": deskripsi.strip()[:120],
                    "nominal": nominal_val,
                    "kategori": (kategori or "Lain-lain").strip(),
                    "tanggal": tanggal_iso,
                    "sumber": sumber,
                }
            if resp.status_code == 404:
                return {
                    "error": "Database pengeluaran tidak ditemukan (404). Pastikan database dibagikan ke Integrasi Notion."
                }
            err_text = resp.text[:200]
            logger.error("Notion save_expense error (status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal menyimpan pengeluaran (status {resp.status_code})."}
    except httpx.TimeoutException:
        return {"error": "Koneksi ke Notion timeout. Coba lagi ya."}
    except Exception as e:
        logger.error("Error in save_expense: %s", str(e))
        return {"error": f"Gagal menghubungi Notion: {str(e)}"}


def _extract_select_any(props: dict, key: Optional[str]) -> str:
    if not key or key not in props:
        return ""
    info = props.get(key) or {}
    select = info.get("select") or info.get("status") or {}
    return (select.get("name") or "").strip()


def _map_expense_page(page: dict, schema: dict) -> dict[str, Any]:
    props = page.get("properties", {}) or {}
    title_key = schema.get("title")
    title_arr = []
    if title_key:
        title_arr = props.get(title_key, {}).get("title", []) or []
    deskripsi = ""
    if title_arr:
        deskripsi = title_arr[0].get("text", {}).get("content", "").strip()

    nominal = None
    if schema.get("nominal"):
        nominal = props.get(schema["nominal"], {}).get("number")

    tanggal = ""
    if schema.get("tanggal"):
        date_info = props.get(schema["tanggal"], {}).get("date") or {}
        tanggal = (date_info.get("start") or "")[:10]

    return {
        "id": page.get("id", ""),
        "deskripsi": deskripsi,
        "nominal": nominal,
        "kategori": _extract_select_any(props, schema.get("kategori")),
        "sumber": _extract_select_any(props, schema.get("sumber")),
        "tanggal": tanggal,
        "toko": _extract_rich_text(props, schema.get("toko")),
        "catatan": _extract_rich_text(props, schema.get("catatan")),
        "url": page.get("url", ""),
    }


async def query_expenses(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """
    Query database pengeluaran Notion.
    - start_date/end_date format YYYY-MM-DD (filter properti Tanggal).
    - keyword difilter di sisi Python (deskripsi/kategori/toko/catatan).
    Returns list dict terurut tanggal terbaru lebih dulu.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = await _resolve_expense_db_id()
    if not api_key or not database_id:
        return []

    headers = _expense_headers()
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            schema = await _inspect_expense_schema(client, database_id, headers)

            filters = []
            if schema.get("tanggal"):
                if start_date:
                    filters.append({
                        "property": schema["tanggal"],
                        "date": {"on_or_after": start_date[:10]},
                    })
                if end_date:
                    filters.append({
                        "property": schema["tanggal"],
                        "date": {"on_or_before": end_date[:10]},
                    })

            payload: dict[str, Any] = {"page_size": 100}
            if len(filters) == 1:
                payload["filter"] = filters[0]
            elif len(filters) > 1:
                payload["filter"] = {"and": filters}
            if schema.get("tanggal"):
                payload["sorts"] = [{"property": schema["tanggal"], "direction": "descending"}]

            cursor = None
            for _ in range(max(1, max_pages)):
                if cursor:
                    payload["start_cursor"] = cursor
                resp = await client.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.warning("Notion query_expenses status %d: %s", resp.status_code, resp.text[:200])
                    break
                data = resp.json()
                for page in data.get("results", []):
                    results.append(_map_expense_page(page, schema))
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
                if not cursor:
                    break
    except Exception as e:
        logger.error("Error in query_expenses: %s", str(e))
        return results

    if keyword:
        kw = keyword.strip().lower()
        results = [
            r for r in results
            if kw in (r.get("deskripsi") or "").lower()
            or kw in (r.get("kategori") or "").lower()
            or kw in (r.get("toko") or "").lower()
            or kw in (r.get("catatan") or "").lower()
        ]
    return results


async def archive_expense(page_id: str) -> dict[str, Any]:
    """Mengarsipkan (menghapus lembut) satu halaman pengeluaran Notion."""
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not api_key:
        return {"error": "NOTION_API_KEY belum dikonfigurasi."}
    if not page_id:
        return {"error": "page_id kosong."}

    headers = _expense_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"archived": True},
                headers=headers,
            )
            if resp.status_code == 200:
                return {"status": "success", "page_id": page_id}
            err_text = resp.text[:200]
            logger.error("Notion archive_expense error (status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal menghapus pengeluaran (status {resp.status_code})."}
    except Exception as e:
        logger.error("Error in archive_expense: %s", str(e))
        return {"error": f"Gagal menghubungi Notion: {str(e)}"}


async def update_expense(
    page_id: str,
    deskripsi: Optional[str] = None,
    nominal: Optional[int] = None,
    kategori: Optional[str] = None,
    tanggal: Optional[str] = None,
    toko: Optional[str] = None,
    catatan: Optional[str] = None,
) -> dict[str, Any]:
    """Memperbarui properti halaman pengeluaran yang sudah ada (tanpa duplikat)."""
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = await _resolve_expense_db_id()
    if not api_key or not database_id:
        return {"error": "Notion pengeluaran belum dikonfigurasi."}
    if not page_id:
        return {"error": "page_id kosong."}

    headers = _expense_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            schema = await _inspect_expense_schema(client, database_id, headers)
            properties: dict[str, Any] = {}
            if deskripsi and schema.get("title"):
                properties[schema["title"]] = {"title": [{"text": {"content": deskripsi.strip()[:120]}}]}
            if nominal is not None and schema.get("nominal"):
                try:
                    properties[schema["nominal"]] = {"number": int(round(float(nominal)))}
                except (TypeError, ValueError):
                    pass
            if kategori and schema.get("kategori"):
                properties[schema["kategori"]] = _select_payload(schema.get("kategori_type"), kategori.strip())
            if tanggal and schema.get("tanggal"):
                properties[schema["tanggal"]] = {"date": {"start": tanggal[:10]}}
            if toko is not None and schema.get("toko"):
                properties[schema["toko"]] = {"rich_text": [{"type": "text", "text": {"content": toko.strip()[:200]}}]}
            if catatan is not None and schema.get("catatan"):
                properties[schema["catatan"]] = {"rich_text": [{"type": "text", "text": {"content": catatan.strip()[:1800]}}]}

            if not properties:
                return {"error": "Tidak ada perubahan properti."}

            resp = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                json={"properties": properties},
                headers=headers,
            )
            if resp.status_code == 200:
                return {"status": "success", "page_id": page_id}
            err_text = resp.text[:200]
            logger.error("Notion update_expense error (status %d): %s", resp.status_code, err_text)
            return {"error": f"Gagal memperbarui pengeluaran (status {resp.status_code})."}
    except Exception as e:
        logger.error("Error in update_expense: %s", str(e))
        return {"error": f"Gagal menghubungi Notion: {str(e)}"}


async def check_expense_database() -> dict[str, Any]:
    """
    Cek kesiapan database pengeluaran (dipakai /pengeluaran config).
    Returns {"configured": bool, "status": "ok|unconfigured|not_shared|error", ...}.
    """
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    database_id = await _resolve_expense_db_id()
    if not database_id:
        return {"configured": False, "status": "unconfigured", "detail": "NOTION_EXPENSE_DATABASE_ID belum diset."}
    if not api_key:
        return {"configured": False, "status": "error", "detail": "NOTION_API_KEY belum diset."}

    headers = _expense_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.notion.com/v1/databases/{database_id}", headers=headers
            )
            if resp.status_code == 200:
                data = resp.json()
                title = ""
                raw_title = data.get("title") or []
                if raw_title and isinstance(raw_title[0], dict):
                    title = raw_title[0].get("plain_text", "")
                return {
                    "configured": True,
                    "status": "ok",
                    "database_id": database_id,
                    "title": title or "Keuangan Oline",
                    "properties": list((data.get("properties") or {}).keys()),
                }
            if resp.status_code == 404:
                return {
                    "configured": True,
                    "status": "not_shared",
                    "database_id": database_id,
                    "detail": "Database tidak ditemukan / belum dibagikan ke Integrasi Oline.",
                }
            return {
                "configured": True,
                "status": "error",
                "database_id": database_id,
                "detail": f"Status {resp.status_code}: {resp.text[:150]}",
            }
    except Exception as e:
        logger.error("Error in check_expense_database: %s", str(e))
        return {"configured": True, "status": "error", "detail": str(e)}

