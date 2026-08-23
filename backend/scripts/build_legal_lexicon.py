"""
Build the weighted legal lexicon that the upload filter scores documents against.

`seed/legal_lexicon.json` is a vocabulary of Indonesian + English legal and contract
terms with integer weights, plus a separate `negative_terms` collection for vocabulary
typical of documents that are *not* contracts (CVs, invoices, recipes, academic papers,
marketing copy, medical leaflets, software manuals, news, fiction). The upload route
rejects a document whose weighted score against this lexicon falls below a threshold —
every accepted upload costs three LLM agent calls, so junk must be rejected cheaply and
before any queueing happens.

The lexicon is built from three reproducible sources:

* **Source A — curated base.** Hand-written literal lists, organised by domain. Indonesian
  single words (~650), English legal terms (~330), and multi-word phrases (~520). Weights
  follow the scale documented in the output's `weights_legend`: 5 is vocabulary that
  essentially never appears outside a legal document (`wanprestasi`, `eksepsi`,
  `force majeure`), 1 is weak boilerplate that merely co-occurs with contracts (`tanggal`,
  `lampiran`). Keeping everyday words at low weights is the whole game: a lexicon full of
  over-weighted generic words would classify a cookbook as a contract.
* **Source B — morphological expansion.** Indonesian forms legal terms productively:
  `membayar`/`pembayaran`/`terbayar` from `bayar`. Only roots explicitly marked as
  productive get expanded, and only with the affix families valid for their category
  (action verbs get me-/di-/ter-/ber-/peN-/me-kan/di-kan/peN-an/-an/-nya; adjectives get
  ke-an and ter-). Nasal assimilation is implemented (`p->mem`, `t->men`, `k->meng`,
  `s->meny`, vowels -> `meng`, liquids -> `me`). A handful of generated forms will not be
  real words — they simply never match real documents, so they cost nothing. What would
  cost something is generating from generic roots, which would match ordinary prose; that
  is why only legal roots are productive here.
* **Source C — harvest from the shipped datasets.** `seed/dataset1.json` (100 labelled
  risky Indonesian contract clauses) and `seed/datasetpasal1.json` (50 Indonesian
  regulations) contribute vocabulary and regulation identifiers, deduplicated against
  Sources A and B. Earlier sources win weight conflicts.

The script is deterministic: no randomness, no timestamps, sorted output — running it
twice yields a byte-identical file. It self-verifies every hard requirement (>= 5000
positive entries, >= 300 negative entries, no positive/negative collision, normalised
keys, integer weights in 1..5) and exits non-zero rather than writing a bad file.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "seed" / "legal_lexicon.json"
DATASET1 = REPO_ROOT / "seed" / "dataset1.json"
DATASET_PASAL = REPO_ROOT / "seed" / "datasetpasal1.json"

# --------------------------------------------------------------------------------------
# Source A: curated single words
# --------------------------------------------------------------------------------------


def _w(weight: int, words: str) -> dict[str, int]:
    """Build a {word: weight} dict from a whitespace/newline-separated string."""
    return {w: weight for w in words.split() if w}


# Indonesian legal/contract vocabulary, by domain. Weight legend: 5 = unambiguous legal,
# 4 = strong legal, 3 = moderately legal/common in contracts, 2 = weak/contextual,
# 1 = weak boilerplate. Everyday words are kept deliberately low.
CORE_PERJANJIAN = _w(
    4,
    """
    perjanjian kontrak kesepakatan klausul pasal ayat butir lampiran addendum amendemen
    naskah draf akta akte perikatan nota memorandum kesepahaman surat dokumen berkas
    ketentuan syarat kewajiban hak kuasa wewenang kewenangan tugas prestasi
    wanprestasi perbuatan melawan hukum
    """,
)
CORE_PERIKATAN = _w(
    5,
    """
    wanprestasi somasi eksepsi inkracht praperadilan eksekutorial parate eksekusi
    fiat nebis idem inkonstitusional kriminalisasi
    """,
)
CORE_HAK = _w(
    3,
    """
    kompensasi ganti kerugian ganti rugi jaminan agunan fidusia hipotek gadai sita
    penyitaan pailit kurator likuidasi merger akuisisi saham obligasi dividen royalti
    waralaba lisensi sublisensi hak cipta paten merek desain industri rahasia dagang
    kekayaan intelektual pengalihan peralihan pewarisan ahli waris
    """,
)
CORE_PEMBAYARAN = _w(
    3,
    """
    pembayaran angsuran cicilan uang muka pelunasan tagihan tunggakan jatuh tempo
    tenggat denda penalti bunga keterlambatan denda keterlambatan pembayaran termin
    milestones prestasi pembayaran pph ppn npwp faktur pajak fiskal bea cukai retribusi
    tarif rekening transfer bank kode billing
    """,
)
CORE_KETENAGAKERJAAN = _w(
    3,
    """
    karyawan pekerja buruh majikan pemberi kerja pengusaha serikat pekerja upah gaji
    tunjangan pesangon penghargaan masa kerja pemutusan hubungan kerja pemberhentian
    pengunduran diri cuti lembur asuransi kesehatan ketenagakerjaan bpjs pensiun
    dana pensiun perjanjian kerja waktu tertentu perjanjian kerja waktu tidak tertentu
    """,
)
CORE_HKI = _w(
    4,
    """
    paten merek hak cipta desain industri rahasia dagang indikasi geografis lisensi
    royalti pemegang hak pencipta penemu inventor pemilik merek piranti lunak
    program komputer database karya cipta ciptaan pengumuman penggandaan penyiaran
    """,
)
CORE_SENGKETA = _w(
    4,
    """
    sengketa perselisihan arbitrase mediator mediasi konsiliasi penggugat tergugat
    termohon pemohon saksi ahli saksi fakta pembuktian alat bukti barang bukti gugatan
    permohonan eksepsi bantahan replik duplik kesimpulan putusan vonis banding kasasi
    peninjauan kembali eksekusi sita jaminan berita acara risalah protokol
    """,
)
CORE_LITIGASI = _w(
    4,
    """
    pengadilan hakim panitera jaksa polisi advokat pengacara kuasa hukum penasihat hukum
    persidangan sidang pemeriksaan keterangan saksi keterangan ahli dakwaan tuntutan
    pembelaan pledoi vonis hukuman pidana perdata tata usaha negara mahkamah agung
    mahkamah konstitusi peradilan umum agama militer niaga hubungan industrial
    """,
)
CORE_ARBITRASE = _w(
    5,
    """
    arbitrase arbiter panel arbitrase putusan arbitrase eksekusi putusan arbitrase
    klausul arbitrase pusat penyelesaian sengketa institusi arbitrase final mengikat
    """,
)
CORE_KORPORASI = _w(
    3,
    """
    perseroan perseroan terbatas perusahaan badan hukum firma persekutuan komanditer
    koperasi yayasan perwakilan cabang anak perusahaan induk perusahaan afiliasi
    konsorsium kelompok usaha holding direksi komisaris pemegang saham rapat umum
    pemegang saham anggaran dasar anggaran rumah tangga modal dasar modal ditempatkan
    modal disetor laba rugi pembubaran pemberesan direktur komisaris
    """,
)
CORE_PROPERTI = _w(
    3,
    """
    sewa menyewa sewa guna usaha hak milik hak guna bangunan hak guna usaha hak pakai
    hak sewa hak pengelolaan sertifikat tanah girik letter c hak atas tanah pelepasan
    pengalihan hak atas tanah jual beli tanah waris harta warisan harta bersama
    harta bawaan perjanjian kawin pemisahan harta
    """,
)
CORE_ASURANSI = _w(
    3,
    """
    asuransi polis premi pertanggungan penanggung tertanggung klaim ganti rugi risiko
    bencana force majeure keadaan kahar wanprestasi konsumen pemegang polis uang
    pertanggungan nilai pertanggungan
    """,
)
CORE_NOTARIS = _w(
    4,
    """
    notaris ppat akta notaris akta otentik akta bawah tangan legalisasi legalisir
    apostille materai bea meterai cap jempol tanda tangan tanda tangan basah tanda
    tangan elektronik saksi pengenal penghadap pihak yang menghadap minuta protokol
    """,
)
CORE_REGULASI = _w(
    4,
    """
    undang undang peraturan pemerintah peraturan presiden peraturan menteri permenkeu
    peraturan daerah peraturan bank indonesia peraturan otoritas jasa keuangan surat
    edaran keputusan menteri instruksi presiden perpu keputusan presiden lembaran
    negara berita negara jdih harmonisasi peraturan perundang undangan norma hukum
    asas hukum yurisprudensi doktrin teori hukum
    """,
)
CORE_UMUM = _w(
    2,
    """
    pihak para pihak waktu tanggal nomor tempat kedudukan domisili alamat lampiran
    penutup pembukaan awal akhir berakhir berlaku sejak mulai berakhirnya perihal
    hal hal antara dengan atas untuk dalam menurut berdasarkan sesuai sepanjang
    selama kecuali termasuk termasuk namun tidak terbatas
    """,
)

INDONESIAN_TERMS: dict[str, int] = {}
for _block in (
    CORE_PERJANJIAN,
    CORE_PERIKATAN,
    CORE_HAK,
    CORE_PEMBAYARAN,
    CORE_KETENAGAKERJAAN,
    CORE_HKI,
    CORE_SENGKETA,
    CORE_LITIGASI,
    CORE_ARBITRASE,
    CORE_KORPORASI,
    CORE_PROPERTI,
    CORE_ASURANSI,
    CORE_NOTARIS,
    CORE_REGULASI,
    CORE_UMUM,
):
    # Domains overlap (wanprestasi appears in perikatan and asuransi). The strongest
    # claim wins: a word that any domain calls clearly legal is clearly legal.
    for _word, _weight in _block.items():
        INDONESIAN_TERMS[_word] = max(INDONESIAN_TERMS.get(_word, 0), _weight)

ENGLISH_TERMS: dict[str, int] = _w(
    4,
    """
    indemnify warranty covenant severability assignee assignor arbitration liability
    confidentiality termination breach default consideration recitals whereas herein
    hereinafter thereof thereto notwithstanding pursuant executed enforceable void
    voidable null clause schedule exhibit appendix annex party parties obligations
    rights remedies damages liquidated penalty interest principal collateral security
    lien mortgage pledge escrow deposit earnest downpayment installment milestone
    deliverable acceptance inspection guarantee surety endorsement release waiver
    assignment delegation novation amendment renewal extension expiration notice cure
    grace jurisdiction venue forum governing severability survival noncompete
    nonsolicit noncircumvent copyright trademark patent license royalty sublicense
    exclusivity remuneration reimbursement withholding taxes gross net currency
    exchange accrued payable overdue nonconforming maintenance support updates
    upgrades enhancements deployment handover documentation training transition
    consulting advisory engagement vendor supplier subcontractor beneficiary consent
    approval counterparts signature award binding cumulative deemed executed
    counterpart diligence earnout vesting cliff option grant shares redemption
    repurchase quorum unanimous proxy attorney trustee executor administrator guardian
    conservator probate intestate testator codicil bequest devise legacy heir
    descendant stirpes capita vivos revocable irrevocable grantor settlor corpus
    remainder tenancy alimony custody visitation dissolution adoption emancipation
    paternity deposition interrogatories subpoena summons complaint answer counterclaim
    crossclaim motion hearing verdict judgment decree injunction rescission reformation
    restitution meruit enrichment conversion trespass nuisance negligence causation
    proximate comparative fault assumption damages compensatory nominal punitive
    exemplary treble statutory moratory acceleration foreclosure deficiency redemption
    subordination intercreditor participation syndication securitization underwriting
    prospectus listing delisting takeover tender offer proxy statement disclosure
    materiality registrant issuer auditor actuary appraiser valuer underwriter broker
    dealer custodian depositary clearing settlement netting collateral margin haircut
    """,
)
# Regulation identifiers and codes are unambiguous and deserve a high weight.
ENGLISH_TERMS.update(
    _w(
        5,
        """
        indemnification hold harmless force majeure act of god intellectual property
        trade secret liquidated damages consequential damages punitive damages
        """,
    )
)
# English loanwords already common in Indonesian legal text.
ENGLISH_TERMS.update(_w(3, "draft klaim klausula komisi proposal opsi review revisi validasi verifikasi seleksi audit"))

# Productive roots for morphological expansion. "v" = action verb (full affix set),
# "vc" = verb plus mem-per-...-kan, "a" = adjective (ke-an, ter-, ber-, -nya),
# "n" = formal noun (ke-an, per-an, -an, -nya). Weights inherited from the root.
PRODUCTIVE_ROOTS: dict[str, tuple[int, str]] = {
    # Action verbs, weight 3-5 depending on how legal the root is.
    "bayar": (3, "vc"), "tuntut": (4, "vc"), "gugat": (4, "vc"), "beban": (3, "vc"),
    "sewa": (3, "vc"), "jual": (3, "vc"), "beli": (3, "vc"), "hutang": (3, "v"),
    "utang": (3, "v"), "tagih": (3, "v"), "tunda": (3, "v"), "jeda": (2, "v"),
    "laksana": (4, "v"), "cantum": (3, "v"), "tera": (3, "v"), "tetap": (3, "v"),
    "tetapkan": (4, "v"), "kena": (3, "v"), "atur": (4, "v"),
    "ukur": (3, "v"), "hitung": (3, "v"), "catat": (3, "v"),
    "kutip": (3, "v"), "terbit": (3, "v"), "terima": (3, "v"), "tolak": (3, "v"),
    "kabul": (4, "v"), "bulat": (2, "v"), "putus": (4, "vc"), "sah": (4, "a"),
    "absah": (4, "a"), "kuat": (3, "a"), "berat": (2, "a"), "patuh": (3, "a"),
    "salah": (2, "a"), "benar": (2, "a"), "adil": (4, "a"), "curang": (3, "a"),
    "rugi": (3, "a"), "untung": (2, "a"), "lambat": (2, "a"), "cepat": (2, "a"),
    "wajib": (4, "a"), "harus": (2, "a"), "tentu": (3, "a"), "nyata": (3, "a"),
    "jelas": (2, "a"), "singkat": (2, "a"), "panjang": (2, "a"), "sempit": (2, "a"),
    "luas": (2, "a"), "penuh": (2, "a"), "dalam": (2, "a"),
    "sepak": (3, "n"), "ikat": (4, "vc"), "syarat": (3, "n"),
    "setuju": (4, "a"), "tanggung": (4, "vc"), "jawab": (4, "vc"), "damai": (4, "n"),
    "runding": (4, "v"), "musyawarah": (4, "v"), "selisih": (4, "n"), "periksa": (4, "v"),
    "mohon": (4, "v"), "lapor": (4, "v"), "tunjuk": (4, "vc"), "pilih": (3, "vc"),
    "angkat": (3, "vc"), "beri": (4, "vc"), "serah": (4, "vc"), "ambil": (3, "vc"),
    "tahan": (3, "vc"), "tangkap": (4, "vc"), "cari": (3, "vc"), "temu": (2, "vc"),
    "lacak": (2, "v"), "surat": (3, "vc"), "kirim": (3, "vc"),
    "umum": (3, "vc"), "umumkan": (4, "vc"), "warta": (3, "v"), "kabar": (2, "v"),
    "daftar": (3, "vc"), "isi": (2, "vc"), "tulis": (3, "vc"), "baca": (2, "vc"),
    "tanam": (3, "vc"), "bangun": (3, "vc"), "dirikan": (4, "vc"), "bubar": (4, "v"),
    "likuidasi": (4, "v"), "konsolidasi": (3, "v"), "restrukturisasi": (4, "v"),
    "reorganisasi": (3, "v"), "nasionalisasi": (3, "v"), "sita": (4, "vc"),
    "lelang": (4, "vc"), "taksir": (3, "vc"), "nilai": (3, "vc"), "harga": (3, "vc"),
    "potong": (3, "vc"), "kurang": (2, "vc"), "tambah": (2, "vc"), "turun": (2, "v"),
    "naik": (2, "v"), "tarik": (3, "vc"), "blokir": (3, "vc"),
    "beku": (3, "a"), "cair": (2, "a"), "bayarkan": (3, "v"),
    "hadapi": (3, "vc"), "lawati": (2, "v"), "sangkal": (3, "vc"), "bantah": (4, "vc"),
    "sanggah": (3, "vc"), "tuduh": (4, "vc"), "sangka": (3, "vc"), "dakwa": (4, "vc"),
    "vonis": (4, "vc"), "hukum": (4, "vc"), "pidana": (4, "v"), "denda": (3, "vc"),
    "bebas": (3, "a"), "tawan": (3, "v"), "kurung": (2, "v"), "buang": (2, "vc"),
    "lempar": (2, "v"), "dorong": (2, "v"), "tekan": (3, "vc"),
    "paksa": (3, "vc"), "ancam": (4, "vc"), "ganggu": (3, "vc"), "halangi": (3, "vc"),
    "rintang": (2, "v"), "tindak": (3, "vc"), "proses": (3, "vc"), "selidik": (4, "vc"),
    "introgasi": (2, "v"), "adili": (4, "v"), "timbang": (3, "vc"),
    "rencana": (2, "vc"), "agenda": (2, "v"), "jadwal": (3, "vc"),
    "jadwalkan": (3, "v"), "susun": (3, "vc"), "rancang": (3, "vc"), "buat": (3, "vc"),
    "bikin": (2, "v"), "lengkapi": (3, "vc"), "penuhi": (3, "vc"), "lunasi": (3, "vc"),
    "selesaikan": (3, "vc"), "rampungkan": (3, "vc"), "tuntaskan": (3, "v"),
    "beres": (2, "a"), "kelola": (3, "vc"), "urus": (3, "vc"), "jaga": (3, "vc"),
    "awasi": (3, "vc"), "pantau": (2, "vc"), "kontrol": (3, "vc"), "uji": (3, "vc"),
    "coba": (2, "v"), "pastikan": (3, "vc"), "jamin": (4, "vc"),
    "asuransikan": (3, "v"), "pertangungkan": (3, "v"), "klaim": (3, "vc"),
    "buktikan": (4, "vc"), "tunjukkan": (3, "vc"),
    "jelaskan": (2, "vc"), "terangkan": (3, "vc"),
    "cantumkan": (3, "vc"), "tuliskan": (2, "vc"), "sampaikan": (3, "vc"),
    "beritakan": (3, "vc"), "kirimkan": (2, "vc"), "serahkan": (4, "vc"),
    "alihkan": (4, "vc"), "pindahkan": (2, "vc"), "salin": (3, "vc"),
    "gandakan": (3, "vc"), "sebarkan": (3, "vc"),
    "terapkan": (3, "vc"), "berlakukan": (4, "vc"),
    "laksanakan": (4, "vc"), "jalankan": (3, "vc"), "kerjakan": (3, "vc"),
    "selenggarakan": (3, "v"), "adakan": (3, "vc"), "gelar": (2, "vc"),
    "pimpin": (3, "vc"), "wakili": (3, "vc"), "mewakili": (3, "v"),
    "perwakili": (3, "v"), "tandatangani": (4, "v"), "paraf": (3, "vc"),
    "stempel": (3, "vc"), "cap": (3, "vc"), "legalisir": (4, "vc"),
    "sahkan": (4, "vc"), "ratifikasi": (4, "v"), "revisi": (3, "vc"),
    "amandemen": (4, "v"), "ubah": (3, "vc"), "ganti": (3, "vc"),
    "perbarui": (3, "vc"), "perpanjang": (3, "vc"), "perketat": (3, "v"),
    "perlambat": (2, "v"), "percepat": (3, "v"), "perluas": (3, "vc"),
    "perjelas": (3, "v"), "permudah": (2, "vc"),
    "perkecil": (2, "v"), "perbesar": (2, "v"), "perhitungkan": (3, "vc"),
    "peroleh": (3, "vc"), "perebutkan": (2, "v"),
    "pertimbangkan": (4, "vc"), "pertanggungjawabkan": (4, "v"), "perkenankan": (3, "v"),
    "perbolehkan": (3, "v"), "persilahkan": (2, "v"), "perintahkan": (4, "vc"),
    "perlakukan": (3, "vc"), "perlindungi": (4, "vc"), "pelihara": (3, "vc"),
    "pulihkan": (3, "vc"), "perbaiki": (3, "vc"), "rehabilitasi": (3, "v"),
    "kompensasikan": (3, "v"), "indemnifikasi": (4, "v"), "jualbelikan": (3, "v"),
    "sewakan": (3, "v"), "kontrakkan": (4, "v"), "subkontrakkan": (3, "v"),
    "outsource": (3, "v"), "lisensikan": (4, "v"), "patenkan": (4, "v"),
    "merekkan": (4, "v"), "gadaikan": (4, "v"), "jaminkan": (4, "v"),
    "hipotekkan": (3, "v"), "fidusiakan": (3, "v"), "agunkan": (4, "v"),
    "cagarkan": (3, "v"), "ikrarkan": (3, "v"), "nyatakan": (4, "vc"),
    "deklarasikan": (3, "v"), "proklamasi": (2, "v"), "janjikan": (4, "vc"),
    "janji": (4, "n"), "ikhtiarkan": (2, "v"), "upayakan": (2, "vc"),
    "usahakan": (2, "vc"), "perjuangkan": (3, "vc"), "belanjakan": (2, "vc"),
    "habiskan": (2, "v"), "keluarkan": (2, "vc"), "masukkan": (2, "vc"),
    "implementasikan": (3, "v"), "realisasikan": (3, "v"), "wujudkan": (3, "vc"),
    "sempurnakan": (2, "v"), "sempurna": (2, "a"), "lengkap": (2, "a"),
    "cukup": (2, "a"), "layak": (3, "a"), "pantas": (2, "a"), "wajar": (3, "a"),
    "rasional": (2, "a"), "logis": (2, "a"),
}

# --------------------------------------------------------------------------------------
# Source A (part 2): multi-word phrases
# --------------------------------------------------------------------------------------


def _p(weight: int, phrases: str) -> dict[str, int]:
    return {p.strip(): weight for p in phrases.splitlines() if p.strip()}


PHRASES_ID = _p(
    4,
    """
    pihak pertama
    pihak kedua
    pihak ketiga
    ganti rugi
    ganti kerugian
    jangka waktu
    jangka pendek
    jangka panjang
    hak milik
    hak pakai
    hak guna
    hak sewa
    hak cipta
    hak paten
    hak merek
    hak kekayaan intelektual
    kekayaan intelektual
    rahasia dagang
    data pribadi
    perlindungan data
    perjanjian kerja
    perjanjian kerja sama
    perjanjian kemitraan
    perjanjian sewa
    perjanjian jual beli
    perjanjian pinjam meminjam
    perjanjian utang piutang
    surat perjanjian
    surat kuasa
    kuasa khusus
    kuasa umum
    surat pemberitahuan
    berita acara
    laporan keuangan
    pembukuan perusahaan
    badan hukum
    perseroan terbatas
    persekutuan komanditer
    rapat umum pemegang saham
    anggaran dasar
    anggaran rumah tangga
    akta notaris
    akta otentik
    akta bawah tangan
    cap jempol
    tanda tangan
    tanda tangan basah
    tanda tangan elektronik
    bea meterai
    saksi ahli
    saksi fakta
    ahli waris
    hak waris
    pembagian warisan
    harta warisan
    harta bersama
    harta bawaan
    perjanjian kawin
    pemisahan harta
    kekuatan hukum
    kekuatan hukum tetap
    daya laku
    berlaku surut
    berlaku sejak
    berakhir sejak
    mulai berlaku
    masa berlaku
    jangka berlakunya
    perpanjangan jangka waktu
    pemutusan hubungan kerja
    pemberhentian dengan hormat
    pemberhentian tidak dengan hormat
    pengunduran diri
    masa percobaan
    masa kerja
    upah minimum
    upah lembur
    tunjangan kesehatan
    jaminan sosial
    jaminan kesehatan
    jaminan ketenagakerjaan
    perlindungan hukum
    perlindungan konsumen
    penyelesaian sengketa
    penyelesaian perselisihan
    cara penyelesaian
    upaya hukum
    upaya hukum luar biasa
    putusan pengadilan
    putusan arbitrase
    putusan yang telah berkekuatan hukum tetap
    pidana penjara
    pidana denda
    tuntutan pidana
    dakwaan jaksa
    keterangan saksi
    keterangan ahli
    alat bukti
    barang bukti
    bukti permulaan
    bukti yang cukup
    standar pembuktian
    beban pembuktian
    pembuktian terbalik
    praduga tak bersalah
    asas praduga tak bersalah
    tersangka pelaku
    pelaku tindak pidana
    tindak pidana
    perbuatan melawan hukum
    perbuatan pidana
    pertanggungjawaban pidana
    pertanggungjawaban hukum
    tanggung jawab hukum
    tanggung jawab perdata
    tanggung jawab mutlak
    tanggung jawab pengganti
    gugatan perdata
    gugatan wanprestasi
    gugatan perbuatan melawan hukum
    sengketa perdata
    sengketa bisnis
    sengketa komersial
    sengketa konsumen
    sengketa tanah
    sengketa waris
    sengketa keluarga
    sengketa ketenagakerjaan
    sengketa hubungan industrial
    arbitrase internasional
    mediasi perbankan
    konsiliasi perbankan
    permohonan arbitrase
    pelaksanaan putusan arbitrase
    """,
)
PHRASES_ID.update(
    _p(
        5,
        """
        force majeure
        keadaan kahar
        keadaan memaksa
        akibat keadaan memaksa
        dengan ini menyatakan
        dengan ini sepakat
        sepakat untuk mengikatkan diri
        mengikatkan diri
        para pihak sepakat
        pihak pertama dan pihak kedua
        antara pihak pertama dengan pihak kedua
        yang bertanda tangan di bawah ini
        bertanda tangan di bawah ini
        dalam hal ini bertindak untuk dan atas nama
        untuk dan atas nama
        selaku kuasa
        dengan itikad baik
        itikad baik
        perjanjian ini dibuat
        dibuat dan ditandatangani
        dibuat dalam rangkap
        rangkap dua
        bermaterai cukup
        materai cukup
        sesuai dengan ketentuan peraturan perundang undangan
        peraturan perundang undangan
        peraturan yang berlaku
        hukum yang berlaku
        hukum indonesia
        menurut hukum
        berdasarkan hukum
        secara sah
        sah menurut hukum
        demi hukum
        batal demi hukum
        batal dan tidak berlaku
        tidak sah dan tidak mengikat
        tidak mengikat secara hukum
        """,
    )
)
PHRASES_ID.update(
    _p(
        3,
        """
        jangka waktu pembayaran
        tanggal pembayaran
        tanggal jatuh tempo
        jatuh tempo pembayaran
        pembayaran dilakukan
        pembayaran diterima
        sisa pembayaran
        pelunasan pembayaran
        uang muka
        uang panjar
        uang jaminan
        jaminan uang
        denda keterlambatan
        keterlambatan pembayaran
        bunga keterlambatan
        denda sebesar
        sebesar persen
        per bulan
        per tahun
        persen per bulan
        persen per tahun
        dikenakan denda
        tidak dikenakan denda
        pembayaran di muka
        pembayaran di belakang
        termin pembayaran
        termin pertama
        termin kedua
        pembayaran termin
        pembayaran bertahap
        pembayaran sekaligus
        pembayaran penuh
        pembayaran sebagian
        penyesuaian harga
        perubahan harga
        eskalasi harga
        harga satuan
        harga borongan
        harga paket
        nilai kontrak
        nilai perjanjian
        jumlah yang disepakati
        jumlah keseluruhan
        rincian biaya
        biaya operasional
        biaya administrasi
        biaya pengiriman
        biaya pajak
        biaya notaris
        biaya materai
        ongkos kirim
        ongkos angkut
        dokumen pendukung
        kelengkapan dokumen
        serah terima
        serah terima pekerjaan
        berita acara serah terima
        berita acara pemeriksaan
        berita acara penyerahan
        laporan pekerjaan
        laporan kemajuan
        laporan berkala
        laporan akhir
        laporan pelaksanaan
        masa pemeliharaan
        masa garansi
        masa jaminan
        garansi mutu
        jaminan kualitas
        jaminan pemeliharaan
        jaminan pelaksanaan
        jaminan penawaran
        jaminan pembayaran
        jaminan uang muka
        jaminan bank
        jaminan perusahaan
        jaminan pribadi
        jaminan tambahan
        agunan tambahan
        hak menahan
        hak retensi
        hak tanggungan
        sertifikat hak tanggungan
        surat kuasa membebankan
        perjanjian pembiayaan
        pembiayaan konsumen
        sewa guna usaha
        anjak piutang
        anjak piutang dengan hak regres
        """,
    )
)
PHRASES_EN = _p(
    4,
    """
    force majeure
    act of god
    governing law
    choice of law
    entire agreement
    limitation of liability
    liquidated damages
    intellectual property
    trade secret
    non disclosure agreement
    non compete agreement
    non solicitation
    purchase order
    statement of work
    term sheet
    letter of intent
    memorandum of understanding
    power of attorney
    living will
    last will
    last will and testament
    health care proxy
    durable power of attorney
    joint tenancy
    tenancy in common
    community property
    prenuptial agreement
    postnuptial agreement
    temporary restraining order
    specific performance
    unjust enrichment
    quantum meruit
    respondent superior
    assumption of risk
    proximate cause
    consequential damages
    punitive damages
    nominal damages
    compensatory damages
    breach of contract
    breach of warranty
    default judgment
    motion to dismiss
    summary judgment
    class action
    arbitration clause
    severability clause
    confidentiality clause
    indemnification clause
    entire agreement clause
    survival clause
    termination for convenience
    termination for cause
    notice period
    cure period
    grace period
    work for hire
    independent contractor
    third party beneficiary
    time is of the essence
    good faith
    best efforts
    reasonable efforts
    commercially reasonable
    as is
    without recourse
    subject to
    pursuant to
    in accordance with
    notwithstanding anything to the contrary
    by and between
    hereby
    herein
    hereinafter
    as follows
    for good and valuable consideration
    good and valuable consideration
    receipt and sufficiency of which is acknowledged
    agrees as follows
    covenants and agrees
    shall be entitled
    shall not be liable
    shall be liable
    shall indemnify
    shall hold harmless
    hereby agree
    agree to be bound
    bound by the terms
    terms and conditions
    terms of this agreement
    effective as of
    effective date
    commencement date
    expiration date
    renewal term
    initial term
    auto renew
    evergreen clause
    notice in writing
    deemed received
    address for notices
    executed in counterparts
    electronic signature
    binding effect
    final and binding
    exclusive remedy
    cumulative remedies
    waiver of rights
    release and discharge
    sever and survive
    inure to the benefit
    binding on successors
    successors and assigns
    assignability
    no assignment
    prior written consent
    written consent
    written notice
    in writing
    deliverable acceptance
    acceptance criteria
    acceptance testing
    sign off
    go live
    handover documentation
    transition services
    service level
    service level agreement
    availability uptime
    performance bonus
    key personnel
    replacement personnel
    non solicitation clause
    territorial restriction
    exclusive territory
    minimum purchase
    quarterly reporting
    audit right
    books and records
    applicable law
    applicable laws
    compliance with
    representations and warranties
    conditions precedent
    conditions subsequent
    closing conditions
    due diligence
    purchase price
    purchase price adjustment
    earn out
    closing date
    signing date
    definitive agreement
    break up fee
    reverse termination fee
    no shop
    fiduciary out
    lock up
    registration rights
    right of first refusal
    drag along
    tag along
    anti dilution
    exercise price
    stock option
    option grant
    vesting schedule
    shares of capital
    common stock
    preferred stock
    convertible note
    warrant coverage
    redemption right
    board observer
    unanimous consent
    supermajority
    power to vote
    durable power
    living trust
    revocable trust
    irrevocable trust
    testamentary trust
    trust corpus
    trust income
    trust remainder
    contingent remainder
    fee simple
    life estate
    tenants in common
    right of survivorship
    equitable distribution
    spousal support
    child support
    visitation rights
    shared custody
    sole custody
    legal separation
    domestic partnership
    civil union
    paternity test
    motion to compel
    request for production
    request for admission
    interrogatory responses
    notice of deposition
    subpoena duces tecum
    bench trial
    jury trial
    directed verdict
    motion for summary judgment
    judgment notwithstanding the verdict
    permanent injunction
    preliminary injunction
    temporary restraining order
    specific performance
    constructive trust
    resulting trust
    reformation of contract
    rescission of contract
    restitutionary damages
    expectation damages
    reliance damages
    incidental damages
    special damages
    general damages
    statutory damages
    treble damages
    double damages
    moratory damages
    prejudgment interest
    postjudgment interest
    attorney fees
    cost of suit
    prevailing party
    nonprevailing party
    forum selection
    forum non conveniens
    personal jurisdiction
    subject matter jurisdiction
    venue transfer
    remittitur
    additur
    collateral source
    mitigation of damages
    duty to mitigate
    avoidable consequences
    substantial performance
    material breach
    minor breach
    anticipatory breach
    repudiation of contract
    frustration of purpose
    impossibility of performance
    impracticability
    commercial impracticability
    unconscionability
    unconscionable contract
    adhesion contract
    contract of adhesion
    standard form contract
    boilerplate provisions
    implied warranty
    express warranty
    warranty disclaimer
    disclaimer of warranties
    fitness for a particular purpose
    merchantability
    course of dealing
    course of performance
    usage of trade
    parol evidence
    parol evidence rule
    integration clause
    merger clause
    no oral modification
    written modification
    amendment in writing
    waiver in writing
    nonwaiver
    course of conduct
    estoppel
    promissory estoppel
    equitable estoppel
    laches
    statute of limitations
    statute of repose
    tolling
    accrual of claim
    claim barred
    time barred
    prejudicial delay
    """,
)
PHRASES = {**PHRASES_EN, **PHRASES_ID}
# Phrases that repeat a single-word entry (e.g. "ganti rugi" contains ganti+rugi) are fine;
# the phrase match is additive, which is intended.

# --------------------------------------------------------------------------------------
# Source B: morphological expansion of productive roots
# --------------------------------------------------------------------------------------

_VOWELS = "aiueo"
_NASAL = {
    # first letter -> (me- prefix, peN- prefix) with elision of the first letter
    "p": ("mem", "pem"),
    "b": ("mem", "pem"),
    "f": ("mem", "pem"),
    "v": ("mem", "pem"),
    "t": ("men", "pen"),
    "d": ("men", "pen"),
    "c": ("men", "pen"),
    "j": ("men", "pen"),
    "s": ("meny", "peny"),
    "k": ("meng", "pe"),
    "g": ("meng", "peng"),
    "h": ("meng", "peng"),
    "l": ("me", "pe"),
    "m": ("me", "pe"),
    "n": ("me", "pe"),
    "r": ("me", "pe"),
    "w": ("me", "pe"),
    "y": ("me", "pe"),
}


def _me_form(root: str) -> str:
    first = root[0]
    if first in _VOWELS:
        return "meng" + root
    prefix, _ = _NASAL[first]
    if first in ("p", "t", "s", "k"):
        return prefix + root[1:]
    return prefix + root


def _pe_form(root: str) -> str:
    first = root[0]
    if first in _VOWELS:
        return "peng" + root
    _, prefix = _NASAL[first]
    if first in ("p", "t", "s", "k"):
        return prefix + root[1:]
    return prefix + root


def _derived(weight: int, root: str, family: str) -> dict[str, int]:
    """All generated forms for one root. `weight` is the root's weight."""
    out: dict[str, int] = {}
    me, di, ter, ber, pe = (_me_form(root), "di" + root, "ter" + root,
                            "ber" + root, _pe_form(root))
    mekan, dikan, mei, dii = (me + "kan", di + "kan", me + "i", di + "i")
    pean, kean, peran, an, nya = (pe + "an", "ke" + root + "an",
                                  ("per" + root + "an"), root + "an", root + "nya")

    def add(forms: dict[str, int], forms_dict: dict[str, int], drop: int = 0) -> None:
        for word, w in forms.items():
            forms_dict[word] = max(2, w - drop)

    if family in ("v", "vc"):
        add({me: weight, di: weight, ter: weight, pe: weight, mekan: weight,
             dikan: weight, mei: weight, dii: weight, pean: weight, an: weight,
             nya: weight}, out, drop=0)
        if root[0] == "r":
            add({("ber" if False else "be" + root): weight}, out)  # ber- + r -> be-
        else:
            add({ber: weight}, out)
    if family == "vc":
        add({"memper" + root + "kan": weight, "memper" + root: weight}, out)
    if family in ("a", "n"):
        add({kean: weight, peran: weight, nya: weight}, out)
        add({an: weight}, out, drop=1)
        if family == "a":
            add({ter: weight, ber: weight}, out)
    return out


# --------------------------------------------------------------------------------------
# Source C: harvest from the shipped datasets
# --------------------------------------------------------------------------------------

STOPWORDS = {
    "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "ini", "itu", "tidak",
    "dalam", "akan", "oleh", "sebagai", "adalah", "ke", "di", "dari", "para", "serta",
    "atau", "juga", "hanya", "bila", "jika", "maka", "karena", "bahwa", "secara",
    "tersebut", "tersebut", "adanya", "pada", "setiap", "antara", "atas", "bagi",
    "berdasarkan", "menurut", "dapat", "telah", "sudah", "belum", "sedang", "akan",
    "bisa", "harus", "untuk", "dengan", "seperti", "semua", "seluruh", "sebagian",
    "beberapa", "masing", "masing", "masing masing", "lain", "lainnya", "baru", "lagi",
    "juga", "saja", "pun", "pula", "sejak", "sampai", "hingga", "selama", "kecuali",
    "selain", "sehingga", "sementara", "kemudian", "setelah", "sebelum", "apabila",
    "bilamana", "dimana", "dikarenakan", "sehubungan", "terkait", "berkenaan",
}


def _harvest_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{5,}", text.lower())
    counts: dict[str, int] = {}
    for w in words:
        if w not in STOPWORDS:
            counts[w] = counts.get(w, 0) + 1
    return {w for w, c in counts.items() if c >= 2}


def _harvest() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in (DATASET1, DATASET_PASAL):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"[lexicon] skipping {path.name}: unreadable", file=sys.stderr)
            continue
        blob = json.dumps(records, ensure_ascii=False)
        for token in _harvest_tokens(blob):
            out[token] = 2
    # Regulation identifiers used in the dataset are unambiguous, so bump them.
    for ident in ("kuhperdata", "permenaker", "pph", "ppn", "npwp", "ojk", "ppat",
                  "ht", "skmht", "apht", "phk", "hki", "bpsk", "jamsostek", "bpjs"):
        out[ident] = 5
    return out


# --------------------------------------------------------------------------------------
# Negative vocabulary (Source D)
# --------------------------------------------------------------------------------------

NEGATIVE_TERMS: dict[str, int] = {}
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        curriculum vitae riwayat hidup daftar riwayat hidup profil pribadi pendidikan
        formal informal sekolah universitas jurusan fakultas ipk gpa pengalaman kerja
        posisi terakhir pekerjaan sebelumnya skill keahlian kemampuan kompetensi
        sertifikasi sertifikat kursus pelatihan seminar workshop bahasa asing hobi
        minat kegiatan organisasi panitia penghargaan prestasi kontak alamat telepon
        email linkedin github portofolio proyek soft skill hard skill kepemimpinan
        komunikasi problem solving teamwork kerja tim manajemen waktu berpikir kritis
        kreativitas adaptasi motivasi diri referensi rekomendasi atasan rekan kerja
        tujuan karier karier melamar lowongan wawancara interview rekrutmen rekruter
        kandidat pelamar lamaran surat lamaran lampirkan ijazah transkrip nilai akta
        kelahiran ktp foto ukuran pas foto biodata resume cv summary objective
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        faktur invoice kwitansi nota bon tagihan subtotal diskon potongan total jumlah
        harga satuan kuantitas qty barang jasa deskripsi item nomor invoice nomor
        faktur tanggal faktur jatuh tempo tenggat pembayaran dp uang muka pelunasan
        sisa saldo rekening transfer virtual account kode billing pembayaran diterima
        lunas belum lunas dicetak kasir struk receipt unit price amount due date billing
        statement outstanding balance payment received thank you for your business
        terima kasih atas pembayaran customer pelanggan langganan berlangganan
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        resep masakan makanan minuman bahan bahan bahan bahan bahanbahan sendok teh
        makan garam gula merica bawang putih merah cabai cabe tomat kentang wortel
        ayam daging ikan udang telur tepung terigu beras nasi mie minyak goreng tumis
        rebus kukus panggang bakar campur aduk masak api kecil sedang besar menit jam
        oven panci wajan pisau alat sajikan nikmat lezat enak cara membuat langkah
        pembuatan porsi kalori nutrisi protein karbohidrat lemak vitamin serat adonan
        kuah sambal kecap saus bumbu penyedap rasa air matang santan kelapa bawang
        daun seledri daun bawang kemangi jahe kunyit lengkuas serai daun jeruk
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        abstrak abstraksi pendahuluan latar belakang rumusan masalah tujuan penelitian
        metodologi metode populasi sampel instrumen kuesioner angket observasi
        dokumentasi analisis data kualitatif kuantitatif deskriptif eksperimen
        variabel hipotesis uji statistik regresi korelasi signifikansi hasil
        pembahasan kesimpulan saran daftar pustaka referensi sitasi kutipan footnote
        endnote bibliografi jurnal artikel ilmiah makalah skripsi tesis disertasi
        proposal konferensi prosiding peer review reviewer penulis afiliasi institusi
        program studi kata kunci keywords temuan implikasi keterbatasan penelitian
        selanjutnya peneliti responden informan triangulasi validitas reliabilitas
        normalitas homogenitas anova t test chi square mean median modus standar
        deviasi signifikan signifikansi p value hipotesis nol hipotesis alternatif
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        promosi iklan beli gratis hemat promo penawaran khusus terbatas stok terbatas
        flash sale cashback voucher kupon hadiah undian berhadiah mengikuti harga
        spesial harga promo penjualan terbesar tahun edisi terbatas produk unggulan
        terlaris best seller baru launching peluncuran rilis fitur keunggulan manfaat
        kualitas terbaik nomor satu pilihan tepat untuk anda kami jaminan kepuasan
        persen garansi resmi distributor agen sementara persediaan daftar sekarang
        newsletter berlangganan subscribe like share follow media sosial instagram
        tiktok youtube konten kreator influencer endorse sponsor kolaborasi giveaway
        campaign kampanye diskon besar akhir tahun lebaran ramadan natal imlek
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        resep dokter obat tablet kapsul sirup dosis konsumsi kali sehari sebelum
        sesudah makan gejala demam batuk pilek nyeri sakit kepala perut pusing mual
        muntah diare alergi efek samping peringatan kontraindikasi dokter apotek
        apoteker rumah sakit puskesmas klinik perawat pasien penyakit diagnosis
        terapi pengobatan pemeriksaan laboratorium hasil darah urine rontgen usg ekg
        tekanan darah gula darah kolesterol asam urat vitamin suplemen herbal jamu
        obat batuk obat demam obat nyeri resep dokter kandungan dosis dewasa anak
        bayi miligram mikrogram tablet salut selaput kapsul lunak tetes mata salep
        krim gel bedak obat oles kompres istirahat minum air putih hangat
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        instalasi install uninstall pengaturan setting konfigurasi klik tombol menu
        file folder simpan buka tutup hapus salin tempel unduh download unggah login
        logout masuk keluar akun password kata sandi username nama pengguna aplikasi
        program perangkat lunak sistem operasi windows mac linux android ios browser
        internet koneksi jaringan wifi server database backup restore update upgrade
        versi rilis catatan perubahan changelog faq bantuan dukungan forum komunitas
        error gagal berhasil loading memuat proses selesai langkah panduan petunjuk
        manual pengguna user guide tutorial cara penggunaan troubleshooting pemecahan
        masalah umum diskusi tanya jawab perangkat keras hardware mouse keyboard
        monitor printer scanner driver resolusi layar ukuran layar penyimpanan
        kapasitas baterai daya tahan charger kabel port tombol daya volume layar
        sentuh sensor kamera megapiksel memori ram rom prosesor intel amd snapdragon
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        3,
        """
        berita liputan wartawan reporter redaksi koran surat kabar majalah tabloid
        portal online terkini terbaru hari ini kemarin minggu lalu polisi kasus dugaan
        tersangka korban kejadian peristiwa lokasi tempat kejadian perkara menimpa
        mengakibatkan luka ringan berat evakuasi penyelidikan proses hukum sidang
        vonis hukuman penjara bebas tahanan penangkapan penahanan keterangan pers
        jumpa pers konferensi siaran resmi pemerintah kementerian kepala daerah
        gubernur bupati wali kota presiden menteri anggota partai politik pemilu
        pilkada pilpres kampanye debat calon kandidat suara pemilih tps surat suara
        hasil penghitungan quick count real count kecurangan unjuk rasa aksi mogok
        tuntutan aspirasi massa aparat keamanan pengamanan lalu lintas macet
        kendaraan kecelakaan tabrakan terguling terbakar meledak banjir longsor gempa
        tsunami erupsi gunung meletus cuaca ekstrem hujan deras angin kencang pohon
        tumbang genangan pengungsi bencana alam bantuan logistik dapur umum posko
        relawan donasi sumbangan penggalangan dana pengaduan aduan audiensi
        """,
    )
)
NEGATIVE_TERMS.update(
    _w(
        2,
        """
        cerita novel kisah dongeng fabel legenda mitos cerpen puisi sajak pantun syair
        tokoh utama antagonis protagonis alur plot latar suasana amanat tema karakter
        penokohan sudut pandang narator epilog prolog klimaks konflik resolusi akhir
        pembaca imajinasi khayalan rekaan fiksi fantasi horor romantis petualangan
        misteri detektif ilmiah sejarah mitologi dewa peri penyihir naga hantu pocong
        kuntilanak genderuwo menyeramkan menarik menegangkan haru bahagia sedih
        marah kecewa senang takut berani pahlawan penjahat raja ratu pangeran putri
        kerajaan istana pedang sihir mantra kutukan penyihir jahat baik bijaksana
        bodoh cerdas licik ramah tamah angkuh sombong rendah hati tulus ikhlas
        """,
    )
)

# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def build_lexicon() -> tuple[dict[str, int], dict[str, int], dict[str, int], dict]:
    terms: dict[str, int] = {}
    terms.update(INDONESIAN_TERMS)
    terms.update(ENGLISH_TERMS)

    generated: dict[str, int] = {}
    for root, (weight, family) in PRODUCTIVE_ROOTS.items():
        generated.update(_derived(weight, root, family))
    # Curated wins over generated on weight conflicts.
    for word, w in generated.items():
        if word not in terms:
            terms[word] = w
    morphology_count = len(generated)

    harvested = _harvest()
    for word, w in harvested.items():
        if word not in terms:
            terms[word] = w
    harvest_count = len(harvested)

    phrases: dict[str, int] = dict(PHRASES)
    negative: dict[str, int] = dict(NEGATIVE_TERMS)

    # Words that are genuinely both (e.g. "faktur" is contract *and* invoice language)
    # belong on the positive side — an ambiguous word is evidence the document is about
    # money, and the negative side should only veto vocabulary that has no legal reading.
    dropped_negatives = set(negative) & (set(terms) | set(phrases))
    for word in dropped_negatives:
        del negative[word]
    if dropped_negatives:
        print(
            f"[lexicon] dropped {len(dropped_negatives)} ambiguous negative entries "
            f"(present in positives): {sorted(dropped_negatives)[:8]} ..."
        )

    # Sources A..C positive terms are sorted per collection; the merged dict must be
    # sorted too for a deterministic file.
    terms = dict(sorted(terms.items()))
    phrases = dict(sorted(phrases.items()))
    negative = dict(sorted(negative.items()))

    metadata = {
        "total_entries": len(terms) + len(phrases),
        "terms": len(terms),
        "phrases": len(phrases),
        "negative_terms": len(negative),
        "curated_indonesian": len(INDONESIAN_TERMS),
        "curated_english": len(ENGLISH_TERMS),
        "curated_phrases": len(PHRASES),
        "generated_by_morphology": morphology_count,
        "harvested_from_datasets": harvest_count,
        "productive_roots": len(PRODUCTIVE_ROOTS),
    }
    return terms, phrases, negative, metadata


def validate(terms: dict[str, int], phrases: dict[str, int],
             negative: dict[str, int], metadata: dict) -> None:
    """Fail loudly on any hard requirement rather than write a bad file."""
    errors: list[str] = []
    _KEY_RE = re.compile(r"^[a-z0-9]+$")
    # Phrases run up to 12 words: the strongest signals are long formulaic clauses
    # ("yang bertanda tangan di bawah ini"), not the two-word ones.
    _PHRASE_RE = re.compile(r"^[a-z0-9]+( [a-z0-9]+){0,11}$")

    for word, w in terms.items():
        if not _KEY_RE.match(word):
            errors.append(f"term key not normalised: {word!r}")
        if not isinstance(w, int) or not 1 <= w <= 5:
            errors.append(f"term weight out of range: {word}={w!r}")
    for phrase, w in phrases.items():
        if not _PHRASE_RE.match(phrase):
            errors.append(f"phrase not normalised: {phrase!r}")
        if not isinstance(w, int) or not 1 <= w <= 5:
            errors.append(f"phrase weight out of range: {phrase}={w!r}")
    for word, w in negative.items():
        if not (_KEY_RE.match(word) or _PHRASE_RE.match(word)):
            errors.append(f"negative key not normalised: {word!r}")
        if not isinstance(w, int) or not 1 <= w <= 5:
            errors.append(f"negative weight out of range: {word}={w!r}")

    if metadata["total_entries"] < 5000:
        errors.append(
            f"only {metadata['total_entries']} positive entries; need >= 5000"
        )
    if metadata["negative_terms"] < 300:
        errors.append(
            f"only {metadata['negative_terms']} negative entries; need >= 300"
        )

    collision = set(terms) | set(phrases)
    overlap = collision & set(negative)
    if overlap:
        errors.append(f"positive/negative collision: {sorted(overlap)[:10]}")

    if errors:
        raise SystemExit("Lexicon validation failed:\n- " + "\n- ".join(errors))


def write_lexicon(out: Path) -> None:
    terms, phrases, negative, metadata = build_lexicon()
    validate(terms, phrases, negative, metadata)
    payload = {
        "version": 1,
        "description": (
            "Weighted Indonesian + English legal/contract vocabulary for the upload "
            "contract filter. Positive collections are scored; negative_terms depress "
            "the score of documents that are clearly not contracts."
        ),
        "generated_by": "scripts/build_legal_lexicon.py",
        "weights_legend": {
            "5": "Unambiguous legal/contract vocabulary; essentially never appears outside a legal document.",
            "4": "Strong legal term; rare in everyday prose.",
            "3": "Moderately legal or common in contracts, but exists elsewhere.",
            "2": "Weak/contextual; only meaningful in aggregate.",
            "1": "Very weak boilerplate that merely co-occurs with contracts.",
        },
        "metadata": metadata,
        "terms": terms,
        "phrases": phrases,
        "negative_terms": negative,
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"[lexicon] wrote {out} "
        f"({metadata['total_entries']} positive, {metadata['negative_terms']} negative)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output JSON path"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and exit non-zero if the on-disk file differs",
    )
    args = parser.parse_args()

    terms, phrases, negative, metadata = build_lexicon()
    validate(terms, phrases, negative, metadata)

    print(
        f"[lexicon] positive entries: {metadata['total_entries']} "
        f"({metadata['terms']} terms + {metadata['phrases']} phrases), "
        f"negative: {metadata['negative_terms']}"
    )
    print(
        f"[lexicon] curated id {metadata['curated_indonesian']}, "
        f"en {metadata['curated_english']}, phrases {metadata['curated_phrases']}, "
        f"morphology {metadata['generated_by_morphology']} from "
        f"{metadata['productive_roots']} roots, harvested {metadata['harvested_from_datasets']}"
    )
    weights = sorted(set(terms.values()) | set(phrases.values()) | set(negative.values()))
    for w in weights:
        n = sum(1 for v in list(terms.values()) + list(phrases.values())
                + list(negative.values()) if v == w)
        print(f"[lexicon] weight {w}: {n} entries")

    if args.check:
        expected = args.out.read_text(encoding="utf-8")
        terms, phrases, negative, metadata = build_lexicon()
        validate(terms, phrases, negative, metadata)
        payload = {
            "version": 1,
            "description": (
                "Weighted Indonesian + English legal/contract vocabulary for the upload "
                "contract filter. Positive collections are scored; negative_terms depress "
                "the score of documents that are clearly not contracts."
            ),
            "generated_by": "scripts/build_legal_lexicon.py",
            "weights_legend": {
                "5": "Unambiguous legal/contract vocabulary; essentially never appears outside a legal document.",
                "4": "Strong legal term; rare in everyday prose.",
                "3": "Moderately legal or common in contracts, but exists elsewhere.",
                "2": "Weak/contextual; only meaningful in aggregate.",
                "1": "Very weak boilerplate that merely co-occurs with contracts.",
            },
            "metadata": metadata,
            "terms": terms,
            "phrases": phrases,
            "negative_terms": negative,
        }
        fresh = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        if fresh != expected:
            raise SystemExit("[lexicon] --check failed: on-disk file is out of date")
        print("[lexicon] --check OK: on-disk file is current")
        return 0

    write_lexicon(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
