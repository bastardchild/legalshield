# LegalShield Agent

Platform SaaS otonom yang menganalisis draft kontrak PDF untuk freelancer, kreator, dan startup kecil. Mendeteksi klausul berbahaya, memeriksa kepatuhan pajak/lokal Indonesia, dan menghasilkan draft kontrak tandingan secara otomatis menggunakan orkestrasi multi-agent.

## Stack

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 asyncio + PostgreSQL 16 + Redis 7/RQ
- **Agents**: `openai.AsyncOpenAI` terhadap endpoint OpenAI-compatible apa pun (default Hermes/Nous)
- **Frontend**: HTMX + Alpine.js + Jinja2, di-vendor lokal (tanpa build step, tanpa CDN)
- **Infrastructure**: Docker Compose

## Demo End-to-End

### 1. Setup environment

```bash
cp .env.example .env
# Edit .env — isi HERMES_API_KEY dengan key Anda
```

### 2. Jalankan stack

```bash
docker compose up --build
```

Untuk deployment (bukan development):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Overlay produksi menghapus bind mount `./backend:/app`, mematikan `--reload`, dan berhenti
mempublikasikan port Postgres/Redis ke host. Rincian alasannya ada di komentar
`docker-compose.prod.yml`. Isi dulu `SECRET_KEY`, `ACCESS_TOKEN`, dan `COOKIE_SECURE=true`
(bila di belakang TLS) sebelum memakainya.

Service `migrate` berjalan sekali (`alembic upgrade head` lalu `python -m app.seed`);
`api` dan `worker` menunggu sampai service itu keluar dengan status 0. Seeding bersifat
idempoten: 100 pola klausul dari `seed/dataset1.json` dimuat sekali, menjalankan ulang
tidak menduplikasi apa pun.

Tunggu sampai `docker compose ps` melaporkan `api` sebagai `healthy`.

### 3. Buka browser

```
http://localhost:8000
```

### 4. Upload kontrak

Pilih PDF kontrak apa pun (maks. 5 MB, 10 halaman) → klik **Analisis Kontrak**.

Unggahan melewati **filter kontrak** terlebih dahulu: teks hasil ekstraksi di-skor terhadap
leksikon hukum ber-pembobotan (`seed/legal_lexicon.json`, 5.112 entri positif + 622 negatif)
plus penanda struktur dokumen (pasal, "dengan ini", "antara ... dengan", blok tanggal, dll.).
File yang tidak menyerupai kontrak (CV, invoice, resep) ditolak `422` sebelum satu pun agen
LLM dipanggil — setiap unggahan yang diterima menjalankan tiga agen, jadi sampah harus
ditolak dengan murah. Kalibrasi default: kontrak nyata ~0.4–0.6, CV/invoice/resep < 0.35.

Halaman hasil melakukan polling tiap 2 detik via HTMX dan berhenti otomatis saat analisis
mencapai `done` atau `failed`.

### 5. Lihat hasil

Setelah analisis selesai (30–90 detik), halaman menampilkan:

- **Klausul Berisiko** — daftar klausul berbahaya dengan severity & saran negosiasi
- **Kepatuhan Pajak & Regulasi** — isu PPh 21/23, NPWP, yurisdiksi, dll., dengan sitasi
  dari `seed/datasetpasal1.json`
- **Draft Kontrak Balasan** — counter-proposal lengkap siap diedit/disalin

Klik **Kirim Draft** untuk mengirim draft ke email klien. Tanpa `SMTP_HOST` app berjalan
dalam **mode stub**: pengiriman dicatat di `negotiation_sends` dan di log, tetapi tidak ada
email yang keluar — cukup untuk demo. Isi variabel `SMTP_*` (lihat
[Environment Variables](#environment-variables)) untuk mengirim sungguhan.

---

## Menjalankan test

Test berjalan di dalam Docker; host Python tidak dipakai.

```bash
docker compose run --rm --no-deps test              # unit test saja (cepat, ~18s)
docker compose run --rm test                       # unit + integration (butuh Postgres)
docker compose run --rm --no-deps test ruff check . # lint
```

Service `test` memakai konfigurasi inline (`HERMES_BASE_URL=http://llm.invalid`), sehingga
test tidak pernah menghubungi provider LLM sungguhan.

`tests/integration/` membutuhkan PostgreSQL sungguhan: fixture membuat database
`legalshield_test` terpisah, menjalankan `alembic upgrade head` di atasnya, lalu
menghapusnya. Bila Postgres tidak terjangkau (`--no-deps`) test tersebut **di-skip**, bukan
gagal — jadi jalur cepat tetap hijau. Yang diuji di sana adalah hal-hal yang bug-nya hidup
di SQL: rantai migrasi, paritas backfill fingerprint SQL vs Python, upsert `ON CONFLICT`,
scoping `owner_id`, sweep reaper, dan engine per-event-loop.

---

## Arsitektur Agent

```
Upload PDF (maks. 5 MB, 10 halaman)
    │  validasi berbasis konten (magic bytes, bukan ekstensi)
    ▼
FastAPI (POST /api/contracts/upload)
    │  extract teks → filter kontrak (leksikon + struktur, 422 bila bukan kontrak)
    │  → simpan ke Postgres → enqueue ke Redis
    │  jika enqueue gagal → kontrak ditandai `failed` (bukan menggantung)
    ▼
RQ Worker
    │
    ├──[parallel]── Sub-agent A: Risk Clause Detector (+ RAG dari clause_patterns)
    ├──[parallel]── Sub-agent B: Tax/Local Compliance Checker (+ daftar regulasi)
    │
    └──[setelah A+B]── Sub-agent C: Counter-Draft Composer
    │                  tetap jalan walau A atau B gagal
    ▼
Postgres (analysis_results) ← HTMX polling tiap 2s
    │
    ▼
UI (hasil + draft balasan)
```

Filter kontrak **fail-open**: bila `seed/legal_lexicon.json` tidak terbaca, unggahan
diterima dan peringatan dicatat — salah konfigurasi harus menghabiskan budget LLM, bukan
menyandera dokumen pengguna. Matikan lewat `CONTRACT_FILTER_ENABLED=false` bila perlu.

Worker juga menjalankan thread **reaper** yang menandai `failed` kontrak yang terlanjur
`processing` tetapi kehilangan worker-nya (container mati, OOM, Redis di-flush).

**Self-improving skill store**: setiap klausul berbahaya dengan confidence ≥ 0.75 disimpan
ke `clause_patterns` **milik pengunggah** dan dipakai sebagai konteks RAG pada analisis
berikutnya, bersama 100 pola kurasi bersama (`global`). Deduplikasi memakai fingerprint
konten (sha256 dari `clause_type` + teks contoh yang dinormalisasi), bukan
`clause_type:severity`.

---

## Struktur Folder

```
legalshield/
├── docker-compose.yml              # postgres, redis, migrate, api, worker, test
├── docker-compose.prod.yml         # overlay produksi: tanpa bind mount / --reload / port DB
├── .env.example
├── .memory/                       # catatan agent: mapping proyek & known issues
├── seed/
│   ├── sample_contract.txt        # contoh kontrak buruk untuk demo
│   ├── dataset1.json              # 100 klausul berlabel → bootstrap clause_patterns
│   ├── datasetpasal1.json         # 50 regulasi Indonesia → sitasi agent pajak
│   └── legal_lexicon.json         # leksikon hukum ber-pembobotan → filter unggahan
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml             # dependency + config pytest/ruff
│   ├── alembic/                   # migrasi database (0001–0005)
│   ├── tests/                     # pytest suite (unit + tests/integration/)
│   ├── scripts/                   # e2e_access_check.py (butuh stack hidup),
│   │                              # build_legal_lexicon.py (generator leksikon)
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── worker.py              # RQ worker entrypoint (+ reaper)
│       ├── seed.py                # CLI seeding idempoten
│       ├── db/                    # SQLAlchemy models & session per-event-loop
│       ├── api/                   # FastAPI routes (+ health)
│       ├── agents/                # orchestrator + 3 sub-agents + prompts
│       ├── services/              # llm_client, findings, pdf_extractor, queue,
│       │                          # skill_store, seed_loader, reaper,
│       │                          # upload_validation, contract_filter, mailer
│       ├── templates/             # Jinja2 HTML (HTMX + Alpine.js), tanpa inline style
│       └── static/                # app.css (semua layout), app.js, vendor/ (htmx, alpine)
└── README.md
```

---

## Environment Variables

| Variable | Deskripsi | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async URL | `postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield` |
| `REDIS_URL` | Redis URL | `redis://redis:6379/0` |
| `HERMES_BASE_URL` | Base URL provider LLM (OpenAI-compatible) | `https://hermes-agent.nousresearch.com` |
| `HERMES_API_KEY` | API key provider LLM | `changeme` |
| `LLM_MODEL` | Nama model | `NousResearch/Hermes-3-Llama-3.1-70B` |
| `DEFAULT_LOCALE` | Locale default | `id-ID` |
| `LLM_TIMEOUT_SECONDS` | Timeout per request LLM | `120.0` |
| `LLM_MAX_RETRIES` | Retry transport (429/5xx/connection) | `2` |
| `LLM_JSON_RETRIES` | Retry saat model membalas JSON tak valid | `2` |
| `MAX_CONTRACT_CHARS` | Batas karakter kontrak yang dikirim ke model | `60000` |
| `MAX_RAG_PATTERNS` | Batas pola skill store dalam prompt | `40` |
| `MAX_LEGAL_REFERENCES` | Batas regulasi dalam prompt agent pajak | `25` |
| `SEED_DIR` | Lokasi data seed | `seed` |
| `MAX_UPLOAD_MB` | Batas ukuran file unggahan PDF | `5` |
| `MAX_PDF_PAGES` | Batas jumlah halaman PDF (`0` = tanpa batas) | `10` |
| `SECRET_KEY` | Kunci HMAC untuk tanda tangan cookie pemilik. **Wajib diisi di produksi** | *(acak per proses)* |
| `ACCESS_TOKEN` | Token bersama untuk seluruh app. Kosong = app terbuka | *(kosong)* |
| `ALLOWED_ORIGINS` | Daftar origin CORS dipisah koma. Kosong = CORS nonaktif | *(kosong)* |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Batas request per IP per menit (`0` = nonaktif) | `240` |
| `RATE_LIMIT_UPLOADS_PER_HOUR` | Batas unggahan per IP per jam (`0` = nonaktif) | `20` |
| `COOKIE_SECURE` | Set `true` bila dilayani via HTTPS | `false` |
| `TRUST_PROXY_HEADERS` | Percayai `X-Forwarded-For` untuk rate limit. Hanya di belakang proxy sendiri | `false` |
| `STUCK_CONTRACT_TIMEOUT_SECONDS` | Umur maksimum status non-terminal sebelum di-reap | `900` |
| `REAPER_INTERVAL_SECONDS` | Interval sweep reaper (`0` = nonaktif) | `300` |
| `SMTP_HOST` | Host relay SMTP. **Kosong = mode stub** (hanya dicatat di log) | *(kosong)* |
| `SMTP_PORT` | Port SMTP | `587` |
| `SMTP_USERNAME` | User untuk AUTH. Kosong = kirim tanpa autentikasi | *(kosong)* |
| `SMTP_PASSWORD` | Password untuk AUTH | *(kosong)* |
| `SMTP_USE_TLS` | STARTTLS di port plaintext (587) | `true` |
| `SMTP_USE_SSL` | TLS implisit dari byte pertama (biasanya port 465). Jangan gabung dengan `SMTP_USE_TLS` | `false` |
| `SMTP_FROM` | Alamat pengirim. Kosong = pakai `SMTP_USERNAME` | *(kosong)* |
| `SMTP_FROM_NAME` | Nama tampilan pengirim | `LegalShield Agent` |
| `SMTP_TIMEOUT_SECONDS` | Timeout koneksi SMTP. Pengiriman terjadi di dalam handler POST, jadi jangan besar | `15.0` |
| `CONTRACT_FILTER_ENABLED` | Matikan filter kontrak saat unggah (`false` = semua dokumen diterima). Tidak disarankan — tiap unggahan menjalankan 3 agen LLM | `true` |
| `CONTRACT_FILTER_MIN_SCORE` | Skor minimum agar dokumen dianggap kontrak. Di bawah ini ditolak `422` | `0.35` |
| `CONTRACT_FILTER_MIN_TOKENS` | Di bawah jumlah token ini dokumen terlalu pendek untuk dinilai | `60` |

> `HERMES_BASE_URL` boleh diisi dengan atau tanpa akhiran `/v1` — akhiran itu di-strip
> lalu ditambahkan kembali, jadi `http://host.docker.internal:11434/v1` dan
> `http://host.docker.internal:11434` sama-sama benar.
>
> Untuk Ollama lokal: `HERMES_BASE_URL=http://host.docker.internal:11434` dan
> `HERMES_API_KEY=ollama`.

---

## API Endpoints

| Method | Path | Deskripsi |
|---|---|---|
| `GET` | `/` | Halaman upload |
| `GET` | `/health` | Liveness — tidak menyentuh dependency |
| `GET` | `/health/ready` | Readiness — cek Postgres + Redis, `503` bila salah satu mati |
| `POST` | `/api/contracts/upload` | Upload PDF (maks. 5 MB, 10 halaman), mulai analisis |
| `GET` | `/api/contracts/{id}/status` | Cek status analisis |
| `GET` | `/api/contracts/{id}/result` | Ambil hasil lengkap (JSON) |
| `POST` | `/api/contracts/{id}/send` | Kirim draft ke email klien (`502` bila pengiriman gagal) |
| `GET` | `/contracts/{id}` | Halaman hasil (HTML) |
| `GET` | `/partials/{id}/status` | Fragment status untuk HTMX |
| `GET` | `/partials/{id}/result` | Fragment hasil untuk HTMX |

---

## Pengiriman Email

`POST /api/contracts/{id}/send` mengirim draft balasan sebagai email dengan draft
terlampir sebagai `.txt`.

- **Mode stub adalah default.** `SMTP_HOST` kosong → envelope dicatat di log dan endpoint
  membalas sukses. Tidak ada socket yang dibuka, jadi demo dan test tidak butuh kredensial.
- **Mode nyata** memakai `smtplib`. `SMTP_USE_SSL=true` untuk TLS implisit (port 465);
  selain itu `SMTP_USE_TLS=true` melakukan `STARTTLS` (port 587). AUTH dilewati bila
  `SMTP_USERNAME` kosong.
- Draft dikirim dua kali dalam satu pesan: inline di body supaya langsung terbaca, dan
  sebagai lampiran `draft-kontrak-<id>.txt` — draft biasanya ~20 KB dan penerimanya akan
  mengeditnya, bukan sekadar membacanya.
- Record `negotiation_sends` **selalu** ditulis, berhasil maupun gagal, lalu status HTTP
  dipilih dari hasilnya (`502` bila pengiriman gagal). Jejak audit itu justru paling
  dibutuhkan saat pengiriman gagal.
- Pesan error SMTP hanya masuk log, tidak dikembalikan ke klien — isinya menyebut host relay
  dan user autentikasi.
- Alamat tujuan yang memuat CR/LF ditolak, supaya tidak ada header tambahan yang bisa
  diselipkan ke envelope.

Untuk memverifikasi konfigurasi SMTP tanpa mengirim ke orang sungguhan, jalankan pemeriksa
SMTP bawaan di dalam container `api`. Ia menyalakan listener SMTP sekali-pakai di
`127.0.0.1:2525`, mengarahkan mailer ke sana, dan memeriksa isi DATA yang benar-benar tiba:

```bash
docker compose exec -T api python scripts/smtp_live_check.py
```

---

## Keamanan

Tiga lapisan, masing-masing bisa dimatikan lewat konfigurasi:

**1. Kepemilikan kontrak (selalu aktif).** Setiap pengunjung mendapat *owner id* anonim
yang ditandatangani HMAC dan disimpan di cookie `ls_owner` (HttpOnly, SameSite=Lax).
Kontrak dicap dengan id itu saat diunggah dan **setiap** pembacaan difilter olehnya, jadi
mengetahui UUID kontrak tidak lagi cukup untuk membacanya. Kontrak milik orang lain
dijawab `404` — bukan `403` — supaya keberadaan UUID tidak terkonfirmasi.

Ini bukan sistem akun: menghapus cookie berarti kehilangan akses ke kontrak lama.
Itu keputusan sadar — cukup untuk menutup celah tanpa memaksa produk memilih alur login.

> **Isi `SECRET_KEY` di produksi.** Bila kosong, kunci acak dibuat per proses: cookie batal
> setiap restart dan tidak valid antar replika. App mencatat peringatan saat ini terjadi.

**2. Token akses bersama (opsional).** Set `ACCESS_TOKEN` dan seluruh app tertutup. Token
dikirim via header `X-Access-Token`, atau `?token=...` sekali yang lalu ditukar menjadi
cookie. `/health*` dan `/static/*` tetap terbuka: orchestrator tidak bisa membawa
kredensial, dan menutup liveness probe mengubah salah konfigurasi menjadi restart loop.

**3. Rate limit.** Dua jendela per IP: `240 req/menit` untuk semua request dan
`20 unggahan/jam` — unggahan dibatasi terpisah karena satu unggahan menjalankan tiga agen
LLM, yaitu satu-satunya endpoint yang menghabiskan uang. State ada di Redis supaya batas
berlaku lintas replika; bila Redis mati limiter **fail open** ke jendela in-memory
per-proses, karena mematikan seluruh trafik hanya karena limiter tumbang akan mengubah
gangguan kecil menjadi outage.

**CORS** nonaktif total kecuali `ALLOWED_ORIGINS` diisi. UI ini same-origin, jadi default
yang benar adalah browser menolak pembacaan cross-origin.

### Yang masih terbuka

Prompt injection dimitigasi dengan fencing (teks kontrak dikurung penanda
`UNTRUSTED CONTRACT TEXT` dan penanda itu di-strip dari payload), bukan dihilangkan — model
masih bisa terpengaruh oleh teks di dalam pagar. Yang sudah tertutup adalah *dampak
jangka panjangnya*: `clause_patterns` tidak lagi global.

Setiap pola disimpan per `owner_id`, dengan dua sentinel yang tidak mungkin dihasilkan
`new_owner_id()` (selalu 32 karakter hex):

- `global` — 100 pola kurasi dari `seed/dataset1.json`. Dibaca semua orang, **tidak pernah
  ditulis** saat runtime, jadi tidak bisa diracuni.
- `legacy` — pola yang dipelajari sebelum kepemilikan ada. Disimpan untuk audit tapi tidak
  pernah dibaca, karena tidak ada cara tahu mana yang berasal dari unggahan berbahaya.

Pembaca melihat `pola sendiri + global`; penulis hanya menulis di bawah id-nya sendiri.
Jadi injection yang berhasil paling jauh hanya memengaruhi analisis pemiliknya sendiri.
Detail di `.memory/KNOWN_ISSUES.md` #6.

Untuk mengekspos ke internet: isi `SECRET_KEY`, set `ACCESS_TOKEN`, dan set
`COOKIE_SECURE=true` di belakang TLS.

---

## Acceptance Criteria MVP

- [x] `docker compose up` menjalankan seluruh stack tanpa error
- [x] User bisa upload PDF kontrak dari browser (maks. 5 MB, 10 halaman)
- [x] Analisis 3 agent berjalan paralel (A & B) lalu C
- [x] UI menampilkan klausul berisiko, isu pajak/lokal, dan draft kontrak tandingan
- [x] Pattern klausul berbahaya baru tersimpan ke `clause_patterns` (self-improving RAG)
- [x] Tombol "Kirim ke Klien" membuat record `negotiation_sends` dan mengirim via SMTP
      (mode stub bila `SMTP_HOST` kosong)
- [x] README menjelaskan cara demo end-to-end
