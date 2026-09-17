# VIDEO SCRIPT: LegalShield Agent — AI Contract Analyzer

**Durasi target:** 3–4 menit
**Style:** Screen recording + narasi santai + caption di layar
**Tools:** OBS / Loom / CapCut (rekam layar + suara)

---

## 0:00–0:15 — Pembuka (Hook)

Layar: logo LegalShield Agent, caption "Kontrak itu bikin was-was gak?"

> "Halo semuanya. Pernah gak dapat kontrak kerja sama yang isinya kayak jebakan Batman?
> Pas dibaca, kok ada tuh 'dilarang kerja di bidang yang sama selama 5 tahun'. Yang bikin kamu
> mau tanda tangan tapi takut, dan gak tanda tangan malah kehilangan kerjaan. Nah, hari ini
> aku mau demoin sebuah AI agent yang bisa ngebaca kontrak kayak gitu dalam hitungan menit."

---

## 0:15–0:35 — Kenalan sama masalahnya

Layar: tampilkan file `sample_contract.txt`, sorot bagian-bagian bahaya.

> "Ini contoh kontrak yang lumayan 'nakal'. Ada klausul yang melarang kita kerja di kompetitor
> selama 5 tahun, seluruh isi karya kita jadi milik klien bahkan sebelum dibayar, dan urusan
> pembayarannya juga lama, 60 hari, dengan alasan yang bisa dipakai sepihak. Buat freelancer
> atau startup kecil, kayak gini tuh bukan cuma gak nyaman, tapi bisa bunuh karier."

---

## 0:35–1:00 — Kasih tahu solusinya (3 agent)

Layar: diagram sederhana 3 agent (deteksi risiko → cek pajak → bikin draft balasan).

> "LegalShield Agent itu kerjanya pakai 3 agent. Yang pertama, deteksi klausul berisiko —
> dia bakal tandain bagian mana yang bahaya. Kedua, cek kepatuhan pajak dan aturan lokal
> Indonesia. Dan ketiga, yang paling penting, dia otomatis bikin draft kontrak versi
> tandingan yang lebih adil buat kita. Jadi bukan cuma dikasih tahu 'ih ini bahaya', tapi
> juga dikasih solusinya."

---

## 1:00–1:50 — Demo langsung

Layar: buka `http://localhost:8000`, drag & drop PDF, tunggu analisis, tunjukkan hasil.

> "Langsung aja kita coba. Gampang banget, tinggal tarik file PDF-nya ke sini, terus klik
> analisis. Bentar, ini prosesnya jalan paralel jadi gak nunggu lama. Sambil nunggu,
> sistemnya juga ngecek dulu file ini beneran kontrak atau bukan, biar gak buang-buang
> biaya LLM buat file asal."

[Jeda ~20–30 detik sambil menunggu]

> "Udah selesai. Nah ini hasilnya. Di bagian atas ada klausul berisiko lengkap sama level
> bahayanya — yang ini kedudukannya kritis. Terus ada juga isu pajaknya, plus sitasi peraturannya
> biar kita bisa sambil yakin. Di paling bawah, ada draft kontrak tandingan yang udah
> disiapin, tinggal kita copy atau langsung dikirim."

---

## 1:50–2:20 — Fitur WhatsApp (yang baru)

Layar: tunjukkan form opsi nomor WhatsApp saat upload, lalu simulasi pesan masuk.

> "Bonusnya, sekarang ada notifikasi WhatsApp. Jadi pas upload tadi kita bisa kasih nomor
> WhatsApp, dan begitu analisisnya kelar, link hasilnya otomatis dikirim lewat WhatsApp.
> Gak perlu nge-refresh halaman terus buat ngecek, gak perlu kirim manual. Pas cocok buat
> yang lagi nungguin jawaban dari klien."

---

## 2:20–2:50 — Keunggulan (kenapa beda)

Layar: bullet point singkat.

> "Yang bikin ini beda dari sekadar 'minta ChatGPT bacain kontrak': pas upload dia udah
> nge-filter dulu, jadi file bukan kontrak langsung ditolak. Dia juga makin pinter — setiap
> klausul bahaya yang pernah kelihatan disimpan dan dipakai buat analisis berikutnya. Terus
> datanya aman, karena hasil analisis cuma bisa dilihat sama yang upload. Dan semua ini
> jalan di atas model yang open source."

---

## 2:50–3:20 — Dampak & penutup

Layar: closing + link repo (QR code).

> "Intinya, buat freelancer dan usaha kecil yang gak punya tim legal, tools kayak gini bisa
> ngehemat waktu dari hari-hari jadi menit-menit, dan yang lebih penting, ngehindarin kita
> dari kontrak yang bisa ngerugiin. Kalau kalian penasaran, kode sumbernya ada di repo
> LegalShield Agent — link di bawah. Terima kasih udah nonton, dan sampe ketemu di proyek
> berikutnya!"

---

**Catatan eksekusi:**
- Pakai suara santai, jangan kaku — bayangkan lagi ngobrol sama teman.
- Biarkan jeda alami saat nunggu analisis, jangan dipenuhi obrolan.
- Caption di layar itu kunci, karena banyak juri nonton tanpa suara.

**Metadata video:**
- Judul: LegalShield Agent — Baca Kontrak Berbahaya Otomatis dalam Menit
- Deskripsi: Demo AI agent untuk deteksi klausul berbahaya, cek kepatuhan pajak, dan
  pembuatan kontrak tandingan otomatis. Dibuat untuk AI Hackfest 2026.
- Hashtag: #AIHackfest2026 #LegalShieldAgent #AIIndonesia