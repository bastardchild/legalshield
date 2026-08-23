"""
Contract filter tests (the upload gate).

Before this filter the upload route accepted any PDF/TXT that decoded to text, so a CV,
an invoice, or a recipe was queued for three LLM agent calls and billed. The filter
scores extracted text against the weighted legal lexicon (`seed/legal_lexicon.json`,
>= 5000 positive entries) plus structural markers, and rejects documents that do not
score like a contract.
"""
from pathlib import Path

import pytest

from app.config import get_settings
from app.services.contract_filter import (
    Lexicon,
    assess_contract,
    load_lexicon,
    normalize_text,
    tokenize,
)

# --- Document samples ---------------------------------------------------------------
# Each non-contract sample is deliberately 60+ tokens so it reaches the scoring stage
# rather than being rejected by the too-short gate.

CV_TEXT = """\
CURRICULUM VITAE / DAFTAR RIWAYAT HIDUP

Nama: Budi Santoso
Tempat, tanggal lahir: Jakarta, 1 Januari 1990
Alamat: Jl. Merdeka No. 10, Jakarta Selatan
Telepon: 0812-3456-7890
Email: budi.santoso@example.com

Pendidikan:
- S1 Teknik Informatika, Universitas Indonesia, 2012-2016
- SMA Negeri 1 Jakarta, 2009-2012

Pengalaman Kerja:
- PT Maju Teknologi (2016-2019), Software Engineer. Bertanggung jawab mengembangkan
  aplikasi web, menulis kode Python, melakukan testing, dan berkolaborasi dengan tim produk.
- PT Digital Nusantara (2019-2023), Senior Engineer. Memimpin tim developer, melakukan
  code review, dan merancang arsitektur microservices.

Keterampilan: Python, JavaScript, Docker, Kubernetes, SQL, Git, CI/CD.
Bahasa: Indonesia (aktif), Inggris (pasif).
Hobi: membaca, bersepeda.
Referensi: tersedia atas permintaan.
"""

RECIPE_TEXT = """\
RESEP NASI GORENG SPESIAL

Bahan-bahan:
- 2 piring nasi putih
- 2 butir telur
- 3 siung bawang putih, cincang halus
- 1 sendok makan kecap manis
- 1 sendok teh garam dan merica bubuk
- 100 gram ayam suwir
- 50 gram wortel, potong dadu kecil

Cara membuat:
1. Panaskan minyak goreng di dalam wajan di atas api sedang.
2. Tumis bawang putih hingga harum dan berwarna keemasan.
3. Masukkan telur, lalu orak-arik hingga matang.
4. Tambahkan nasi, kecap manis, garam, dan merica. Aduk rata hingga tercampur.
5. Masukkan ayam suwir dan wortel, masak selama 2 menit sambil terus diaduk.
6. Angkat dan sajikan hangat dengan acar timun dan kerupuk.

Selamat menikmati!
"""

INVOICE_TEXT = """\
INVOICE / FAKTUR

Nomor Invoice: INV-2026-0042
Tanggal: 22 Agustus 2026
Dari: PT Maju Bersama Teknologi
Kepada: Budi Santoso
Alamat: Jl. Sudirman No. 100, Jakarta Selatan

Rincian tagihan:
1. Jasa pengembangan aplikasi web - Rp 15.000.000
2. Hosting dan domain 12 bulan - Rp 2.400.000
3. Lisensi perangkat lunak - Rp 1.200.000

Subtotal: Rp 18.600.000
Diskon: Rp 0
Total: Rp 18.600.000

Pembayaran melalui transfer bank ke rekening BCA 1234567890 atas nama PT Maju Bersama Teknologi.
Jatuh tempo pembayaran 30 hari sejak tanggal faktur diterima.
Terima kasih atas kerja sama Anda.
"""

# A short but realistic contract: 60+ tokens so it reaches the scoring stage.
CONTRACT_TEXT = """\
PERJANJIAN KERJA SAMA

Perjanjian ini dibuat pada tanggal 1 Agustus 2026, antara:

PIHAK PERTAMA (Klien): PT Maju Bersama Teknologi, beralamat di Jl. Sudirman No. 100,
Jakarta Selatan (selanjutnya disebut "Klien").

PIHAK KEDUA (Freelancer): Budi Santoso, beralamat di Jl. Kebon Jeruk No. 5, Jakarta Barat
(selanjutnya disebut "Freelancer").

PASAL 1 - LINGKUP PEKERJAAN
Freelancer setuju untuk menyediakan jasa pengembangan perangkat lunak secara penuh waktu
untuk Klien, termasuk desain, pengembangan, dan deployment seluruh produk digital Klien.

PASAL 2 - KOMPENSASI
Klien setuju membayar Freelancer sebesar Rp 5.000.000 per bulan. Pembayaran dilakukan
paling lambat 60 hari setelah invoice diterima.

PASAL 3 - KERAHASIAAN
Freelancer wajib menjaga kerahasiaan seluruh informasi yang diperoleh dari Klien,
termasuk setelah kontrak berakhir. Pelanggaran ketentuan ini mengakibatkan denda.

PASAL 4 - HUKUM YANG BERLAKU
Perjanjian ini diatur oleh hukum Republik Indonesia. Segala sengketa diselesaikan melalui
arbitrase di Singapura dengan bahasa Inggris.

PASAL 5 - PERUBAHAN KONTRAK
Klien berhak mengubah syarat dan ketentuan kontrak ini dengan pemberitahuan tertulis
24 jam sebelumnya.

Demikian perjanjian ini dibuat dengan itikad baik dan ditandatangani oleh para pihak,

PT Maju Bersama Teknologi                    Budi Santoso
Direktur Utama                                Freelancer
"""


@pytest.fixture(scope="module")
def lexicon() -> Lexicon:
    """The shipped lexicon, loaded from the seed dir the way production loads it."""
    loaded = load_lexicon()
    assert loaded is not None, "seed/legal_lexicon.json must be mounted for these tests"
    return loaded


class TestLexicon:
    def test_ships_with_at_least_5000_positive_entries(self, lexicon):
        assert len(lexicon.terms) + len(lexicon.phrases) >= 5000

    def test_ships_with_at_least_300_negative_entries(self, lexicon):
        assert len(lexicon.negative) >= 300

    def test_contains_multi_word_phrases(self, lexicon):
        assert lexicon.max_phrase_len >= 3
        assert any(len(p.split()) >= 3 for p in lexicon.phrases)

    def test_no_positive_negative_collision(self, lexicon):
        overlap = set(lexicon.terms) | set(lexicon.phrases)
        assert not (overlap & set(lexicon.negative))

    def test_loads_from_explicit_path(self):
        path = Path(get_settings().seed_dir) / "legal_lexicon.json"
        assert load_lexicon(str(path)) is not None

    def test_missing_lexicon_fails_open(self, monkeypatch):
        monkeypatch.setattr("app.services.contract_filter.load_lexicon", lambda *a, **k: None)
        out = assess_contract(CONTRACT_TEXT)
        assert out.is_contract is True
        assert out.reason == "lexicon_unavailable"


class TestNormalizeAndTokenize:
    def test_lowercases_and_collapses_whitespace(self):
        assert normalize_text("  PASAL 1 —\nLingkup\tPekerjaan  ") == "pasal 1 — lingkup pekerjaan"

    def test_tokens_are_alphanumeric_runs(self):
        assert tokenize("Pasal 1-A: hak & kewajiban") == ["pasal", "1", "a", "hak", "kewajiban"]

    def test_empty_text_yields_no_tokens(self):
        assert tokenize("   \n\t ") == []


class TestAssessContract:
    def test_real_contract_is_accepted(self, lexicon):
        out = assess_contract(CONTRACT_TEXT, lexicon)
        assert out.is_contract is True, f"contract rejected: {out.reason} score={out.score:.3f}"
        assert out.score >= 0.35

    def test_shipped_demo_contract_is_accepted(self):
        """The exact file the README tells users to upload must pass the gate."""
        path = Path(get_settings().seed_dir) / "sample_contract.txt"
        text = path.read_text(encoding="utf-8")
        out = assess_contract(text)
        assert out.is_contract is True, f"demo contract rejected: {out.reason} score={out.score:.3f}"

    @pytest.mark.parametrize(
        "label,text",
        [
            ("cv", CV_TEXT),
            ("recipe", RECIPE_TEXT),
            ("invoice", INVOICE_TEXT),
        ],
    )
    def test_non_contracts_are_rejected(self, lexicon, label, text):
        out = assess_contract(text, lexicon)
        assert out.is_contract is False, f"{label} accepted with score={out.score:.3f}"
        assert out.reason == "low_score"
        assert out.score < 0.35

    def test_empty_text_is_rejected_as_empty(self, lexicon):
        out = assess_contract("   ", lexicon)
        assert out.is_contract is False
        assert out.reason == "empty"

    def test_short_text_is_rejected_as_too_short(self, lexicon):
        out = assess_contract("Perjanjian kerja sama", lexicon)
        assert out.is_contract is False
        assert out.reason == "too_short"

    def test_token_gate_can_be_lowered_for_short_text(self, lexicon):
        out = assess_contract("Perjanjian kerja sama", lexicon, min_tokens=1)
        assert out.token_count == 3
        assert out.reason != "too_short"

    def test_min_score_override_rejects_a_real_contract(self, lexicon):
        out = assess_contract(CONTRACT_TEXT, lexicon, min_score=0.99)
        assert out.is_contract is False
        assert out.reason == "low_score"

    def test_min_score_override_accepts_everything(self, lexicon):
        out = assess_contract(CV_TEXT, lexicon, min_score=0.0)
        assert out.is_contract is True

    def test_repetition_of_one_word_cannot_game_the_score(self, lexicon):
        """A 'PASAL PASAL PASAL...' doc gets its contribution capped, not a free pass."""
        spam = " ".join(["Pasal"] * 200)
        out = assess_contract(spam, lexicon)
        assert out.is_contract is False
        assert out.reason == "low_score"

    def test_negative_penalty_is_bounded(self, lexicon):
        """A lexicon-negative-heavy document is penalised, but never below zero."""
        text = " ".join(["resep", "masak", "nasi"] * 40)
        out = assess_contract(text, lexicon)
        assert out.is_contract is False
        assert 0.0 <= out.score <= 1.0
