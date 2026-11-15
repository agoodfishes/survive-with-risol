# SURVIVE WITH RISOL (Bot Keuangan Telegram)

Kirim foto **struk belanja**, **screenshot riwayat e-wallet**, atau **foto belanjaan** ke bot Telegram.
Bot membaca isinya dengan AI (Gemini), kamu konfirmasi, lalu datanya tersimpan dan bisa dilihat di laporan.

## Fitur

- **Baca foto**: struk, screenshot e-wallet (banyak transaksi sekaligus), dan foto belanjaan (harga diisi manual)
- **Input manual**: ketik `-25rb makan siang` (pengeluaran) atau `+5jt gaji` (pemasukan)
- **Pemasukan dibagi otomatis** ke kategori (amplop) sesuai persentase yang bisa kamu atur
- **Pengeluaran mengurangi saldo kategorinya**, misalnya beli kopi mengurangi saldo Keinginan
- **Koreksi hasil deteksi**: ubah jumlah, tanggal, nama, kategori, atau masuk/keluar sebelum disimpan, juga untuk transaksi yang sudah tersimpan
- **Bot belajar dari koreksi**: kalau kamu memperbaiki kategori suatu toko, transaksi berikutnya dari toko itu langsung memakai kategori yang benar
- **Rekomendasi anggaran (AI)**: tentukan berapa lama uang harus cukup, AI membagi ke pos seperti makan & minum, transportasi, dan kebutuhan kuliah. Persentase dari AI, nominal rupiahnya dihitung oleh kode supaya totalnya selalu pas
- Deteksi transaksi yang mirip dengan data tersimpan

## Perintah

| Perintah | Fungsi |
|---|---|
| `-25rb makan siang` | Catat pengeluaran (kategori ditebak otomatis) |
| `+5jt gaji` | Catat pemasukan (dibagi otomatis) |
| `/masuk 5jt gaji`, `/keluar 25rb kopi` | Cara lain mengetik transaksi |
| `/saldo` | Saldo tiap kategori dan total |
| `/alokasi` | Lihat atau ubah pembagian pemasukan. Contoh: `/alokasi Kebutuhan 50 Keinginan 10 Tabungan 40`. Total harus 100%. `/alokasi reset` mengembalikan ke awal |
| `/riwayat` | 10 transaksi terakhir beserta nomornya |
| `/edit 12` | Koreksi transaksi nomor 12 |
| `/hapus 12` | Hapus transaksi nomor 12 |
| `/laporan` | Pengeluaran per kategori bulan ini |
| `/rencana 500rb 1 minggu` | Rekomendasi AI: uang itu dibagi ke pos apa saja agar cukup selama periodenya. Tanpa jumlah (`/rencana 1 minggu`), bot memakai saldo Kebutuhan + Keinginan saat ini |

Jumlah boleh ditulis `25000`, `25.000`, `25rb`, `25k`, atau `1,5jt`.

Kategori (amplop): **Kebutuhan**, **Keinginan**, dan **Tabungan & Dana Darurat**.
Pembagian awal: Kebutuhan 55%, Keinginan 10%, Tabungan & Dana Darurat 35%. Ubah dengan `/alokasi`.
Kategori pengeluaran ditebak dari kata kunci, dan bisa dikoreksi lewat tombol ✏️ Koreksi. Bot mengingat koreksimu.

Kalau kamu upgrade dari versi dengan 9 kategori, data lama otomatis dipetakan: Makanan, Belanja Harian, Transportasi, Tagihan, Kesehatan, Pendidikan, dan Lainnya menjadi Kebutuhan; Hiburan menjadi Keinginan; Tabungan menjadi Tabungan & Dana Darurat.

## Persiapan

1. **Python 3.10 atau lebih baru.** Cek dengan `python --version`.
2. **Token bot Telegram.** Chat [@BotFather](https://t.me/BotFather), kirim `/newbot`, lalu salin tokennya.
3. **API key Gemini (gratis).** Buat di https://aistudio.google.com/apikey

## Instalasi di Windows (cmd)

Buka Command Prompt, lalu masuk ke folder proyek:

```cmd
cd "C:\lokasi\folder\bot-manage-keuangan"
```

Buat virtual environment dan install library (cukup sekali):

```cmd
python -m venv venv
venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

Kalau berhasil, muncul `(venv)` di awal baris.

Buat file konfigurasi:

```cmd
copy .env.example .env
notepad .env
```

Isi `.env` seperti ini, **tanpa tanda kutip dan tanpa spasi di sekitar `=`**:

```
TELEGRAM_TOKEN=123456:ABC-xxxx
GEMINI_API_KEY=AIza-xxxx
GEMINI_MODEL=gemini-3.5-flash-lite
ALLOWED_USER_IDS=
```

Nama file harus persis `.env`. Kalau memakai Notepad, simpan dengan **Save as type: All Files**,
kalau tidak akan tersimpan sebagai `.env.txt`.

### Menjalankan bot

```cmd
venv\Scripts\activate.bat
py bot.py
```

Atau klik dua kali **`start-bot.bat`** (lihat bagian di bawah). Kalau muncul tulisan `Bot berjalan...`,
buka bot di Telegram, kirim `/start`, lalu kirim foto struk.

Untuk menghentikan bot, tekan **Ctrl+C**. Bot hanya aktif selama jendela terminal terbuka
dan komputer menyala.

## Instalasi di Linux / Mac

```bash
cd bot-manage-keuangan
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## File `start-bot.bat` (opsional, Windows)

Buat file bernama `start-bot.bat` di folder proyek dengan isi:

```bat
@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
py bot.py
pause
```

Kalau library dipasang di Python global (tanpa venv), hapus baris `call venv\Scripts\activate.bat`.

## Konfigurasi `.env`

| Variabel | Fungsi |
|---|---|
| `TELEGRAM_TOKEN` | Token dari @BotFather |
| `GEMINI_API_KEY` | API key dari Google AI Studio |
| `GEMINI_MODEL` | Nama model Gemini. Ganti kalau model lama dihentikan |
| `KIRIM_RIWAYAT_KE_AI` | `1` (default): `/rencana` mengirim deskripsi pengeluaran 30 hari terakhir ke AI agar saran lebih pas. `0`: hanya total per kategori yang dikirim |
| `ALLOWED_USER_IDS` | ID Telegram yang boleh memakai bot, pisahkan dengan koma. Kosong berarti semua orang boleh. Cek ID lewat @userinfobot |
- Foto tidak disimpan, hanya data hasil ekstraksi (di `keuangan.db`).
- Kalau token bocor (misalnya tampil di log yang dibagikan), buat yang baru lewat @BotFather dengan `/revoke`.
- Isi `ALLOWED_USER_IDS` supaya hanya kamu yang bisa memakai bot dan kuota gratismu tidak terpakai orang lain.
