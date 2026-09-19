"""
Neo4j AuraDB client untuk Oline bot.
Menyimpan dan membaca data aktivitas pengguna dalam bentuk graph (node & relasi).
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _create_driver():
    """Membuat koneksi Neo4j driver baru. Returns None jika env vars belum diset."""
    uri = os.environ.get("NEO4J_URI", "").strip()
    user = os.environ.get("NEO4J_USER", "neo4j").strip()
    password = os.environ.get("NEO4J_PASSWORD", "").strip()

    if not uri or not user or not password:
        logger.warning(
            "Neo4j env vars belum lengkap (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD). "
            "Fitur graph aktivitas tidak aktif."
        )
        return None

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("Neo4j driver berhasil diinisialisasi: %s", uri)
        return driver
    except Exception as e:
        logger.error("Gagal menginisialisasi Neo4j driver: %s", str(e))
        return None


def _get_database() -> str:
    """Nama database Neo4j target (default 'neo4j', bisa di-set via NEO4J_DATABASE)."""
    return os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j"


def _close_driver(driver) -> None:
    """Menutup driver Neo4j dengan aman (mencegah kebocoran koneksi di serverless)."""
    if driver is None:
        return
    try:
        driver.close()
    except Exception as e:
        logger.warning("Gagal menutup driver Neo4j: %s", str(e))


def _get_driver():
    """
    Membuat koneksi driver baru per pemanggilan (aman untuk serverless).
    Tidak menyimpan singleton lintas-request agar tidak terikat ke event loop
    yang sudah ditutup saat instance dibekukan (RuntimeError: Event loop is closed).
    """
    return _create_driver()


def _simpan_aktivitas_sync(user_id: str, aksi: str, objek: str, waktu: str) -> bool:
    """Synchronous: Simpan aktivitas ke Neo4j sebagai graph.

    Struktur graph:
        (User)-[:MELAKUKAN]->(Action)-[:TERHADAP]->(Object)
                            Action-[:PADA_WAKTU]->(Time)
    """
    query = """
    MERGE (u:User {id: $user_id})
    MERGE (o:Object {nama: $objek})
    CREATE (a:Action {nama: $aksi, waktu: $waktu})
    CREATE (t:Time {timestamp: $waktu})
    MERGE (u)-[:MELAKUKAN]->(a)
    MERGE (a)-[:TERHADAP]->(o)
    MERGE (a)-[:PADA_WAKTU]->(t)
    """
    driver = _create_driver()
    if not driver:
        return False

    try:
        with driver.session(database=_get_database()) as session:
            session.run(query, user_id=user_id, aksi=aksi, objek=objek, waktu=waktu)
        logger.info("[Neo4j] Simpan aktivitas OK: user=%s aksi=%r objek=%r", user_id, aksi, objek)
        return True
    except Exception as e:
        logger.error("Gagal menyimpan aktivitas ke Neo4j: %s", str(e))
        return False
    finally:
        _close_driver(driver)


def _cari_aktivitas_sync(user_id: str, limit: int = 50) -> list[dict]:
    """Synchronous: Cari aktivitas terakhir dari user di Neo4j graph."""
    query = """
    MATCH (u:User {id: $user_id})-[:MELAKUKAN]->(a:Action)-[:TERHADAP]->(o:Object)
    RETURN a.nama AS aksi, o.nama AS objek, a.waktu AS waktu
    ORDER BY a.waktu DESC
    LIMIT $limit
    """
    driver = _create_driver()
    if not driver:
        return []

    try:
        with driver.session(database=_get_database()) as session:
            result = session.run(query, user_id=user_id, limit=limit)
            return [
                {"aksi": r["aksi"], "objek": r["objek"], "waktu": r["waktu"]}
                for r in result
            ]
    except Exception as e:
        logger.error("Gagal membaca aktivitas dari Neo4j: %s", str(e))
        return []
    finally:
        _close_driver(driver)


async def simpan_aktivitas(user_id: str, aksi: str, objek: str, waktu: Optional[str] = None) -> bool:
    """
    Async wrapper: Simpan aktivitas ke Neo4j graph.
    Jika waktu tidak diberikan, gunakan waktu saat ini (WIB).
    """
    if not waktu:
        waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return await asyncio.to_thread(
        _simpan_aktivitas_sync, str(user_id), aksi, objek, waktu
    )


async def cari_aktivitas(user_id: str, limit: int = 50) -> list[dict]:
    """Async wrapper: Cari aktivitas terakhir dari user di Neo4j graph."""
    return await asyncio.to_thread(
        _cari_aktivitas_sync, str(user_id), limit
    )


# Cache hasil cek koneksi dalam proses (jangan cek berulang pada request yang sama)
_CONN_CACHE: dict = {"ok": None, "msg": "", "ts": 0.0}
_CONN_TTL = 60.0


async def cek_koneksi_neo4j(use_cache: bool = True) -> tuple[bool, str]:
    """
    Memverifikasi koneksi ke Neo4j (RETURN 1).
    Returns tuple (ok, message). Hasil di-cache singkat agar tidak boros koneksi.
    """
    now = time.time()
    if use_cache and _CONN_CACHE["ok"] is not None and (now - _CONN_CACHE["ts"]) < _CONN_TTL:
        return _CONN_CACHE["ok"], _CONN_CACHE["msg"]

    logger.info("[Neo4j] Cek koneksi dimulai...")

    def _probe() -> tuple[bool, str]:
        driver = _create_driver()
        if not driver:
            return False, "env belum lengkap"
        try:
            with driver.session(database=_get_database()) as session:
                res = session.run("RETURN 1 AS num")
                num = res.single()["num"]
                return num == 1, "koneksi OK" if num == 1 else "hasil tak terduga"
        finally:
            _close_driver(driver)

    try:
        ok, msg = await asyncio.to_thread(_probe)
    except Exception as e:
        ok, msg = False, str(e)

    _CONN_CACHE["ok"] = ok
    _CONN_CACHE["msg"] = msg
    _CONN_CACHE["ts"] = now
    logger.info("[Neo4j] Cek koneksi selesai: ok=%s (%s)", ok, msg)
    return ok, msg


async def auto_log_aktivitas(chat_id: int, aksi: str, objek: str) -> dict:
    """
    Rekam aktivitas ke graph Neo4j secara otomatis dengan log eksplisit.
    Mengembalikan dict status: {"success": bool, "message": str, "reason": str}.
    """
    try:
        logger.info("[Neo4j] Auto-log mulai: user=%s aksi=%r objek=%r", chat_id, aksi, objek)
        ok, msg = await cek_koneksi_neo4j()
        if not ok:
            logger.warning("[Neo4j] Auto-log dibatalkan (koneksi gagal): %s", msg)
            return {"success": False, "message": "Koneksi Neo4j gagal.", "reason": msg}

        sukses = await simpan_aktivitas(str(chat_id), aksi, objek)
        if sukses:
            logger.info("[Neo4j] Auto-log BERHASIL: %r -> %r", aksi, objek)
            return {"success": True, "message": f"Aktivitas '{aksi}' tercatat di graph Neo4j."}
        logger.warning("[Neo4j] Auto-log GAGAL simpan: %r -> %r", aksi, objek)
        return {"success": False, "message": "Gagal menyimpan ke Neo4j.", "reason": "simpan"}
    except Exception as e:
        logger.error("[Neo4j] Auto-log exception: %s", str(e))
        return {"success": False, "message": "Exception saat menyimpan ke Neo4j.", "reason": str(e)}
