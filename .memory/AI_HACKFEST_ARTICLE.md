# LegalShield Agent: Analisis Kontrak Kerja Otomatis dengan AI Multi-Agent

## Latar Belakang Masalah

Di Indonesia, pencari pekerja sering kali menghadapi perjanjian kerja yang tidak seimbang.
Banyak dari mereka tidak memiliki akses ke layanan hukum profesional untuk
membaca dan memahami kontrak sebelum menandatanganinya. Akibatnya, mereka terjebak dalam
klausul-klausul merugikan seperti larangan kerja di bidang yang sama yang terlalu panjang, pemindahan hak
cipta yang total, atau ketentuan pembayaran yang tidak adil.

Masalah ini semakin diperparah dengan pertumbuhan ekonomi digital yang pesat. Baik pekerja
lepas, UMKM yang merekrut karyawan, maupun bisnis kecil yang menjalin kerja sama dengan
pihak lain — semua berisiko terjebak dalam kontrak yang tidak adil. Sayangnya, sarana yang
tersedia untuk membantu mereka memahami kontrak masih sangat terbatas. Kebanyakan solusi
yang ada hanya berupa template kontrak statis yang tidak bisa menyesuaikan dengan konteks
spesifik dari setiap perjanjian.

Data dari Kementerian Ketenagakerjaan menunjukkan bahwa kasus sengketa hubungan kerja
terus meningkat setiap tahunnya. Banyak dari kasus ini sebenarnya bisa dicegah jika pencari pekerja 
memiliki pemahaman yang lebih baik tentang hak dan kewajiban mereka dalam kontrak.

## Pendekatan Solusi

LegalShield Agent dirancang sebagai platform SaaS otonom yang menggunakan kecerdasan buatan
untuk menganalisis kontrak PDF secara otomatis. Berbeda dari pendekatan konvensional
yang hanya berfokus pada pencarian kata kunci, sistem ini menggunakan arsitektur multi-agent
yang mampu memahami konteks hukum dan memberikan rekomendasi yang relevan.

Sistem ini tidak bertujuan untuk menggantikan ahli hukum, tetapi memberikan edukasi dan
pemahaman kepada pengguna tentang risiko-risiko yang ada didalam kontrak. Dengan
pengentahuan ini, pengguna bisa membuat keputusan yang lebih baik sebelum menandatangani perjanjian.

Fitur utama LegalShield Agent meliputi:

1. **Deteksi Klausul Berisiko**: Mengidentifikasi klausul-klausul yang berpotensi merugikan
   dengan tingkatan yang berbeda (rendah, sedang, tinggi, kritis).

2. **Verifikasi Kepatuhan Pajak**: Memeriksa aspek pajak dan regulasi lokal Indonesia yang
   terkait dengan perjanjian, termasuk sitasi peraturan yang relevan.

3. **Pembuatan Kontrak Tandingan**: Menghasilkan draft kontrak tandingan yang lebih adil
   berdasarkan temuan analisis.

4. **Notifikasi WhatsApp**: Mengirimkan link hasil analisis secara otomatis melalui WhatsApp
   begitu proses selesai, sehingga pengguna tidak perlu mengecek secara manual.

## Arsitektur AI Agent

LegalShield Agent dibangun dengan arsitektur microservices yang terdiri dari beberapa
komponen utama:

### Backend
- **Python FastAPI**: Framework web async yang menangani API endpoints dan serving halaman HTML
- **SQLAlchemy 2.0**: ORM untuk interaksi database dengan dukungan async
- **PostgreSQL 16**: Database utama untuk menyimpan data kontrak, hasil analisis, dan
  pola klausul
- **Redis 7**: Message broker untuk antrian pekerjaan dan caching

### AI Agents
Sistem ini menggunakan tiga agen AI yang bekerja secara paralel dan sequential:

1. **Agent A (Risk Clause Detector)**: Menganalisis teks kontrak untuk mengidentifikasi
   klausul berisiko. Menggunakan RAG (Retrieval-Augmented Generation) dengan pola klausul
   yang disimpan dari analisis sebelumnya.

2. **Agent B (Tax Compliance Checker)**: Memeriksa kepatuhan pajak dan regulasi lokal.
   Menggunakan daftar regulasi Indonesia yang relevan sebagai referensi.

3. **Agent C (Counter-Draft Composer)**: Menghasilkan kontrak tandingan berdasarkan temuan
   dari Agent A dan B. Berjalan setelah kedua agen selesai karena membutuhkan output mereka.

### Infrastructure
- **Docker**: Orkestrasi seluruh layanan dalam lingkungan terisolasi
- **Alembic**: Manajemen migrasi database
- **RQ (Redis Queue)**: Worker queue untuk memproses analisis secara asynchronous

### Self-Improving RAG
Sistem memiliki kemampuan self-improving melalui skill store yang menyimpan pola klausul
berisiko dari setiap analisis. Pola-pola ini disimpan per pengguna (owner-scoped) dan
digunakan sebagai konteks RAG untuk analisis berikutnya. Deduplikasi dilakukan dengan
fingerprint berbasis konten untuk menghindari duplikasi data.

### Security
- **Owner-scoped Access**: Setiap kontrak dibatasi aksesnya hanya oleh pemiliknya melalui
  cookie HMAC yang ditandatangani
- **Contract Filter**: Sistem memfilter unggahan berdasarkan leksikon hukum untuk memastikan
  hanya kontrak yang valid yang diproses. Logic ini melakukan verifikasi keabsahan dokumen untuk 
  memastikan bahwa dokumen tersebut merupakan perjanjian kerja yang sah.
- **Rate Limiting**: Pembatasan jumlah request untuk mencegah penyalahgunaan

## Dampak yang Diharapkan

LegalShield Agent diharapkan dapat memberikan dampak positif dalam beberapa aspek:

### Edukasi Hukum Digital
Platform ini berfungsi sebagai alat edukasi yang membantu pekerja memahami kontrak
kerja dengan lebih baik. Dengan antarmuka yang sederhana dan mudah digunakan, pengguna
tidak perlu memiliki latar belakang hukum untuk memanfaatkannya.

### Peningkatan Kesadaran
Dengan mendeteksi klausul berisiko dan menjelaskan dampaknya, sistem ini meningkatkan
kesadaran pengguna tentang pentingnya membaca dan memahami kontrak sebelum tanda tangan.

### Efisiensi Proses
Bagi para pencari kerja, freelancer dan usaha kecil yang tidak memiliki tim hukum, LegalShield Agent 
memberikan cara yang efisien untuk meninjau kontrak. Proses yang biasanya membutuhkan waktu 
berhari-hari bisa diselesaikan dalam hitungan menit.

### Inklusivitas
Sistem ini dirancang untuk dapat diakses oleh semua orang, termasuk mereka yang berada di
daerah terpencil atau dengan keterbatasan akses terhadap layanan hukum profesional.

### Kontribusi Ekosistem AI Indonesia
LegalShield Agent berkontribusi dalam pengembangan ekosistem AI di Indonesia dan menunjukkan 
potensi aplikasi AI untuk menyelesaikan masalah nyata di masyarakat.

## Kesimpulan

LegalShield Agent merupakan solusi inovatif yang memanfaatkan kecerdasan buatan untuk
membantu pencari pekerja, freelancer dan usaha kecil memahami kontrak kerja. Dengan arsitektur
multi-agent yang solid, fitur edukasi yang komprehensif, dan pendekatan yang berfokus
pada pemahaman pengguna, platform ini diharapkan dapat menjadi alat yang berguna dalam
menciptakan ekosistem kerja digital yang lebih adil dan transparan.

---

**Kontak:**
- Website: https://legalshield.afjn.site

**Tag:** #AIHackfest2026 #LegalShieldAgent #AIIndonesia #FreelancerProtection #OmnibusLaw
