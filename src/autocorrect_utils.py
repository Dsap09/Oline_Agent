"""
Modul spell checker / auto-correct ejaan Bahasa Indonesia untuk Oline Bot.
Menggunakan library `pyspellchecker` dengan kosa kata Bahasa Indonesia dan keyword bot.
"""

import logging
from typing import Optional

try:
    from spellchecker import SpellChecker
    _HAS_SPELLCHECKER = True
except ImportError:
    _HAS_SPELLCHECKER = False

logger = logging.getLogger(__name__)

# Kosa kata Bahasa Indonesia & keyword bot Oline
INDONESIAN_VOCABULARY = [
    # Question & search keywords
    "apa", "itu", "siapa", "kapan", "dimana", "mengapa", "kenapa", "bagaimana",
    "cari", "search", "berita", "definisi", "pengertian", "fakta", "informasi",
    # Intent keywords
    "cuaca", "hujan", "panas", "suhu", "cerah", "berawan", "mendung", "payung",
    "rekomendasi", "film", "lagu", "buku", "seri", "anime", "horor", "komedi", "aksi", "romantis",
    "suara", "nyanyi", "gombal", "puisi", "baca", "bacain", "dengar",
    "jurnal", "catat", "rekap", "catatan", "diary",
    "pengeluaran", "belanja", "struk", "nota", "bon", "rupiah", "ribu", "juta",
    "kopi", "makan", "bensin", "grab", "gojek", "warung", "indomaret", "alfamart",
    "kuota", "token", "quota", "sisa", "pemakaian",
    "eksekusi", "jalankan", "script", "python", "javascript", "coding", "debug", "kode",
    "notion", "vercel", "deploy", "onlinekan", "gambar", "foto", "image",
    # Kota-kota populer
    "jakarta", "bandung", "yogyakarta", "surabaya", "semarang", "medan", "bali", "bogor",
    # Kata umum percakapan
    "hari", "ini", "besok", "lusa", "kemarin", "minggu", "bulan", "tahun",
    "tolong", "bantu", "kasih", "dong", "ya", "gak", "tidak", "bisa", "tahu", "tau",
    "mau", "ingin", "dengan", "untuk", "dari", "ke", "di", "yang", "dan", "atau",
    "sekarang", "terbaru", "terkini", "paling", "bagus", "enak", "santai",
]


class IndonesianSpeller:
    """Class pemroses auto-correct ejaan ringan Bahasa Indonesia."""

    def __init__(self):
        self.spell: Optional[SpellChecker] = None
        if _HAS_SPELLCHECKER:
            try:
                self.spell = SpellChecker(language=None)
                self.spell.word_frequency.load_words(INDONESIAN_VOCABULARY)
            except Exception as e:
                logger.warning("Failed to initialize SpellChecker: %s", str(e))
                self.spell = None

    def correct_text(self, text: str) -> str:
        """
        Mengoreksi kata-kata typo pada teks input.
        Jika kata tidak dikenal (misal nama orang "Ekin"), kata tersebut dipertahankan.
        """
        if not text or not self.spell:
            return text

        words = text.split()
        corrected_words = []

        for word in words:
            # Bersihkan tanda baca tepi (misal: "jkarta?", "flm,")
            clean_word = word.strip(".,!?\"'()[]{}")
            if not clean_word:
                corrected_words.append(word)
                continue

            word_lower = clean_word.lower()

            # Jangan ubah kata yang sangat pendek (<= 2 huruf) atau mengandung angka
            if len(word_lower) <= 2 or any(char.isdigit() for char in word_lower):
                corrected_words.append(word)
                continue

            # Jika kata ada di kosa kata kita, pertahankan
            if word_lower in self.spell.word_frequency:
                corrected_words.append(word)
                continue

            # Dapatkan usulan koreksi terbaik dari pyspellchecker
            correction = self.spell.correction(word_lower)

            # Jika ada usulan koreksi dan valid
            if correction and correction != word_lower:
                # Ganti kata dasar dalam token asli (mempertahankan tanda baca tepi)
                corrected_token = word.replace(clean_word, correction)
                if clean_word[0].isupper():
                    corrected_token = corrected_token.capitalize()
                corrected_words.append(corrected_token)
            else:
                corrected_words.append(word)

        return " ".join(corrected_words)


# Global instance
_speller_instance = IndonesianSpeller()


def correct_typo(text: str) -> str:
    """Helper function untuk mengoreksi teks."""
    return _speller_instance.correct_text(text)
