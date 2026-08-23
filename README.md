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

Pilih `seed/sample_contract.txt` (atau PDF kontrak apa pun) → klik **Analisis Kontrak**.

Halaman hasil melakukan polling tiap 2 detik via HTMX dan berhenti otomatis saat analisis
mencapai `done` atau `failed`.

### 5. Lihat hasil

Setelah analisis selesai (30–90 detik), halaman menampilkan:

- **Klausul Berisiko** — daftar klausul berbahaya dengan severity & saran negosiasi
- **Kepatuhan Pajak & Regulasi** — isu PPh 21/23, NPWP, yurisdiksi, dll., dengan sitasi
  dari `seed/datasetpasal1.json`
- **Draft Kontrak Balasan** — counter-proposal lengkap siap diedit/disalin

Klik **Kirim Draft** untuk mencatat pengiriman (stub, tidak ada SMTP nyata di MVP).

---

## Menjalankan test

Test berjalan di dalam Docker; host Python tidak dipakai.

```bash
docker compose run --rm --no-deps test              # pytest
docker compose run --rm --no-deps test ruff check . # lint
```

Service `test` memakai konfigurasi inline (`HERMES_BASE_URL=http://llm.invalid`), sehingga
test tidak pernah menghubungi provider LLM sungguhan. `--no-deps` cukup karena seluruh
test saat ini adalah unit test.

---

## Arsitektur Agent

```
Upload PDF/TXT
    │  validasi berbasis konten (magic bytes, bukan ekstensi)
    ▼
FastAPI (POST /api/contracts/upload)
    │  extract teks → simpan ke Postgres → enqueue ke Redis
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

Worker juga menjalankan thread **reaper** yang menandai `failed` kontrak yang terlanjur
`processing` tetapi kehilangan worker-nya (container mati, OOM, Redis di-flush).

**Self-improving skill store**: setiap klausul berbahaya dengan confidence ≥ 0.75 disimpan
ke `clause_patterns` dan dipakai sebagai konteks RAG pada analisis berikutnya. Deduplikasi
memakai fingerprint konten (sha256 dari `clause_type` + teks contoh yang dinormalisasi),
bukan `clause_type:severity`.

---

## Struktur Folder

```
legalshield/
├── docker-compose.yml              # postgres, redis, migrate, api, worker, test
├── .env.example
├── .memory/                       # catatan agent: mapping proyek & known issues
├── seed/
│   ├── sample_contract.txt        # contoh kontrak buruk untuk demo
│   ├── dataset1.json              # 100 klausul berlabel → bootstrap clause_patterns
│   └── datasetpasal1.json         # 50 regulasi Indonesia → sitasi agent pajak
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml             # dependency + config pytest/ruff
│   ├── alembic/                   # migrasi database (0001–0003)
│   ├── tests/                     # pytest suite
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
│       │                          # upload_validation, mailer
│       ├── templates/             # Jinja2 HTML (HTMX + Alpine.js)
│       └── static/                # app.css, app.js, vendor/ (htmx, alpine)
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
| `SECRET_KEY` | Kunci HMAC untuk tanda tangan cookie pemilik. **Wajib diisi di produksi** | *(acak per proses)* |
| `ACCESS_TOKEN` | Token bersama untuk seluruh app. Kosong = app terbuka | *(kosong)* |
| `ALLOWED_ORIGINS` | Daftar origin CORS dipisah koma. Kosong = CORS nonaktif | *(kosong)* |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Batas request per IP per menit (`0` = nonaktif) | `240` |
| `RATE_LIMIT_UPLOADS_PER_HOUR` | Batas unggahan per IP per jam (`0` = nonaktif) | `20` |
| `COOKIE_SECURE` | Set `true` bila dilayani via HTTPS | `false` |
| `TRUST_PROXY_HEADERS` | Percayai `X-Forwarded-For` untuk rate limit. Hanya di belakang proxy sendiri | `false` |
| `STUCK_CONTRACT_TIMEOUT_SECONDS` | Umur maksimum status non-terminal sebelum di-reap | `900` |
| `REAPER_INTERVAL_SECONDS` | Interval sweep reaper (`0` = nonaktif) | `300` |

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
| `POST` | `/api/contracts/upload` | Upload PDF/TXT, mulai analisis |
| `GET` | `/api/contracts/{id}/status` | Cek status analisis |
| `GET` | `/api/contracts/{id}/result` | Ambil hasil lengkap (JSON) |
| `POST` | `/api/contracts/{id}/send` | Kirim draft ke klien (stub) |
| `GET` | `/contracts/{id}` | Halaman hasil (HTML) |
| `GET` | `/partials/{id}/status` | Fragment status untuk HTMX |
| `GET` | `/partials/{id}/result` | Fragment hasil untuk HTMX |

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

`clause_patterns` bersifat **global**. Prompt injection dimitigasi dengan fencing (teks
kontrak dikurung penanda `UNTRUSTED CONTRACT TEXT` dan penanda itu di-strip dari payload),
bukan dihilangkan — unggahan berbahaya yang berhasil masih bisa memengaruhi konteks RAG
analisis pengguna lain. Skill store per-tenant akan membatasi dampaknya; lihat
`.memory/KNOWN_ISSUES.md` #6.

Untuk mengekspos ke internet: isi `SECRET_KEY`, set `ACCESS_TOKEN`, dan set
`COOKIE_SECURE=true` di belakang TLS.

---

## Acceptance Criteria MVP

- [x] `docker compose up` menjalankan seluruh stack tanpa error
- [x] User bisa upload PDF/TXT kontrak dari browser
- [x] Analisis 3 agent berjalan paralel (A & B) lalu C
- [x] UI menampilkan klausul berisiko, isu pajak/lokal, dan draft kontrak tandingan
- [x] Pattern klausul berbahaya baru tersimpan ke `clause_patterns` (self-improving RAG)
- [x] Tombol "Kirim ke Klien" membuat record `negotiation_sends` (stub)
- [x] README menjelaskan cara demo end-to-end
