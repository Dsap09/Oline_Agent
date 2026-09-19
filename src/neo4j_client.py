"""
Neo4j AuraDB client untuk Oline bot.
Menyimpan dan membaca data aktivitas pengguna dalam bentuk graph (node & relasi).
"""

import asyncio
import logging
import os
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
        with driver.session() as session:
            session.run(query, user_id=user_id, aksi=aksi, objek=objek, waktu=waktu)
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
        with driver.session() as session:
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


async def auto_log_aktivitas(chat_id: int, aksi: str, objek: str) -> None:
    """
    Fire-and-forget: Rekam aktivitas penting ke graph secara otomatis.
    Tidak akan mempengaruhi alur utama jika gagal.
    """
    try:
        await simpan_aktivitas(str(chat_id), aksi, objek)
        logger.info("Auto-logged aktivitas ke Neo4j: %s -> %s", aksi, objek)
    except Exception as e:
        logger.warning("Auto-log aktivitas gagal (non-critical): %s", str(e))
