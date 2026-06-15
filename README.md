# FASIH-SM Scraper & Manager

Alat otomatisasi untuk melakukan scraping data dan manajemen penugasan (Approve, Revoke, Reject) pada platform FASIH-SM. Dibuat menggunakan Python dengan integrasi Selenium (Undetected Chromedriver) dan Requests API untuk performa optimal.

## ✨ Fitur Utama

- **Otomatisasi Login**: Mendukung login SSO dengan manajemen sesi (cookie persistence).
- **Parallel Processing**: Pengambilan data wilayah secara cepat menggunakan multi-threading.
- **Batch Operations**: Melakukan Approve, Revoke, atau Reject banyak penugasan sekaligus hanya dengan beberapa klik.
- **Auto-Installation**: Mengecek dan menginstall library yang diperlukan secara otomatis saat dijalankan.
- **Export Excel**: Hasil scraping dan log proses disimpan dalam format `.xlsx`.
- **Bot Detection Bypass**: Menggunakan `undetected-chromedriver` untuk meminimalisir blokir sistem.

## 📋 Prasyarat

Sebelum menjalankan script, pastikan Anda telah menginstal:
- [Python 3.8+](https://www.python.org/downloads/)
- [Google Chrome](https://www.google.com/chrome/) versi terbaru.

## 🚀 Cara Penggunaan

1. **Clone Repositori**:
   ```bash
   git clone https://github.com/kaizer26/fsh-sm-scraper.git
   cd fsh-sm-scraper
   ```

2. **Instalasi Dependency**:
   Script akan mencoba menginstall secara otomatis, namun disarankan untuk menginstall secara manual:
   ```bash
   pip install -r requirements.txt
   ```

3. **Jalankan Script**:
   ```bash
   python fasih_scraper.py
   ```

4. **Alur Kerja**:
   - Masukkan **Username SSO** Anda.
   - Jika sesi baru, browser akan terbuka untuk login (masukkan password dan OTP jika diminta).
   - Pilih **Survei**, **Provinsi**, dan **Kabupaten** yang ingin diproses.
   - Pilih aksi dari menu utama (Scrape, Approve, Revoke, atau Reject).

## 📂 Struktur Folder

- `sessions/`: Menyimpan data sesi login (jangan dibagikan ke orang lain).
- `Log/`: Menyimpan riwayat proses (berhasil/gagal).
- `Hasil/`: Folder default untuk menyimpan hasil scraping.

## ⚠️ Keamanan

> [!CAUTION]
> File di dalam folder `sessions/` berisi informasi kredensial yang tersamar (obfuscated). Meskipun tidak dalam bentuk teks polos, sangat disarankan untuk **tidak membagikan folder ini** atau menguploadnya ke repositori publik. Folder ini sudah masuk dalam `.gitignore` secara default.

## 🛠️ Troubleshooting

- **Browser Gagal Terbuka**: Pastikan Google Chrome Anda sudah di-update ke versi terbaru. Jika masih gagal, cek Task Manager dan pastikan tidak ada proses `chromedriver.exe` yang menggantung.
- **Error CookieConflict**: Terjadi jika ada masalah saat pengambilan session. Coba hapus folder `sessions/` dan login ulang.
- **Timeout**: Jika internet lambat, script mungkin mengalami timeout. Anda bisa menyesuaikan nilai `REQUEST_TIMEOUT` di dalam script.

---

**Kontribusi**: Jika Anda menemukan bug atau memiliki saran fitur, silakan buat *Issue* atau kirim *Pull Request*.

**Lisensi**: [MIT](LICENSE)
