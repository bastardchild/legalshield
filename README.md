# LegalShield Agent

Platform SaaS otonom yang menganalisis draft kontrak PDF untuk freelancer, kreator, dan startup kecil. Mendeteksi klausul berbahaya, memeriksa kepatuhan pajak/lokal Indonesia, dan menghasilkan draft kontrak tandingan secara otomatis menggunakan orkestrasi multi-agent.

## Stack

- **Backend**: FastAPI (async) + SQLAlchemy asyncio + PostgreSQL + Redis/RQ
- **Agents**: LangChain + OpenAI-compatible LLM (Hermes via Nous Research)
- **Frontend**: HTMX + Alpine.js + Jinja2 (no heavy JS framework)
- **Infrastructure**: Docker Compose

## Demo End-to-End (5 Langkah)

### 1. Setup environment

```bash
cp .env.example .env
# Edit .env — isi HERMES_API_KEY dengan key Anda
```

### 2. Jalankan stack

```bash
docker-compose up --build
```

Tunggu sampai semua service healthy (postgres, redis, api, worker).

### 3. Buka browser

```
http://localhost:8000
```

### 4. Upload kontrak

Klik "Upload Kontrak" → pilih `seed/sample_contract.txt` (atau PDF kontrak apapun) → klik **Analisis Kontrak**.

Anda akan diarahkan ke halaman hasil. Status diperbarui otomatis setiap 2 detik via HTMX polling.

### 5. Lihat hasil

Setelah analisis selesai (30–90 detik), halaman menampilkan:
- **Klausul Berisiko** — daftar klausul berbahaya dengan severity & saran negosiasi
- **Kepatuhan Pajak & Regulasi** — isu PPh 21/23, NPWP, yurisdiksi, dll.
- **Draft Kontrak Balasan** — counter-proposal lengkap siap diedit/disalin

Klik **Kirim Draft** untuk mencatat pengiriman (stub, tidak SMTP nyata di MVP).

---

## Arsitektur Agent

```
Upload PDF
    │
    ▼
FastAPI (POST /api/contracts/upload)
    │  extract teks, simpan ke Postgres, push ke Redis queue
    ▼
RQ Worker
    │
    ├──[parallel]── Sub-agent A: Risk Clause Detector
    ├──[parallel]── Sub-agent B: Tax/Local Compliance Checker
    │
    └──[setelah A+B selesai]── Sub-agent C: Counter-Draft Composer
    │
    ▼
Postgres (analysis_results) ← HTMX polling setiap 2s
    │
    ▼
UI (hasil + draft balasan)
```

**Self-improving skill store**: setiap klausul berbahaya baru dengan confidence ≥ 0.75 disimpan ke tabel `clause_patterns` dan dipakai sebagai konteks RAG pada analisis berikutnya.

---

## Struktur Folder

```
legalshield/
├── docker-compose.yml
├── .env.example
├── seed/
│   └── sample_contract.txt     # contoh kontrak buruk untuk demo
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                # migrasi database
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── worker.py           # RQ worker entrypoint
│       ├── db/                 # SQLAlchemy models & session
│       ├── api/                # FastAPI routes
│       ├── agents/             # orchestrator + 3 sub-agents + prompts
│       ├── services/           # llm_client, pdf_extractor, queue, skill_store, mailer
│       ├── templates/          # Jinja2 HTML (HTMX + Alpine.js)
│       └── static/             # app.js
└── README.md
```

---

## Environment Variables

| Variable | Deskripsi | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async URL | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis URL | `redis://redis:6379/0` |
| `HERMES_BASE_URL` | Base URL LLM provider (OpenAI-compatible) | `https://hermes-agent.nousresearch.com` |
| `HERMES_API_KEY` | API key untuk LLM provider | `changeme` |
| `LLM_MODEL` | Nama model | `NousResearch/Hermes-3-Llama-3.1-70B` |
| `DEFAULT_LOCALE` | Locale default | `id-ID` |

> Untuk testing lokal dengan Ollama: set `HERMES_BASE_URL=http://host.docker.internal:11434/v1` dan `HERMES_API_KEY=ollama`.

---

## API Endpoints

| Method | Path | Deskripsi |
|---|---|---|
| `GET` | `/` | Halaman upload |
| `POST` | `/api/contracts/upload` | Upload PDF, mulai analisis |
| `GET` | `/api/contracts/{id}/status` | Cek status analisis |
| `GET` | `/api/contracts/{id}/result` | Ambil hasil lengkap (JSON) |
| `POST` | `/api/contracts/{id}/send` | Kirim draft ke klien (stub) |
| `GET` | `/contracts/{id}` | Halaman hasil (HTML) |

---

## Acceptance Criteria MVP

- [x] `docker-compose up` menjalankan seluruh stack tanpa error
- [x] User bisa upload PDF kontrak dari browser
- [x] Analisis 3 agent berjalan paralel (A & B) lalu C
- [x] UI menampilkan klausul berisiko, isu pajak/lokal, dan draft kontrak tandingan
- [x] Pattern klausul berbahaya baru tersimpan ke `clause_patterns` (self-improving RAG)
- [x] Tombol "Kirim ke Klien" membuat record `negotiation_sends` (stub)
- [x] README menjelaskan cara demo end-to-end
