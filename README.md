# Bot Keuangan Telegram

Kirim foto **struk belanja**, **screenshot riwayat e-wallet**, atau **foto belanjaan** ke bot Telegram.
Bot membaca isinya dengan AI (Gemini), kamu konfirmasi, lalu datanya tersimpan dan bisa dilihat di laporan.

## Fitur

- Baca struk: merchant, tanggal, total, dan kategori otomatis
- Baca screenshot e-wallet: satu foto bisa berisi banyak transaksi
- Foto belanjaan: bot mengenali barangnya, harga diisi manual (tidak ditebak)
- Konfirmasi dengan tombol ✅ Simpan / ❌ Batal sebelum data masuk
- Deteksi transaksi yang mirip dengan data tersimpan
- `/laporan` untuk pengeluaran per kategori bulan ini, `/riwayat` untuk 10 transaksi terakhir

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
| `ALLOWED_USER_IDS` | ID Telegram yang boleh memakai bot, pisahkan dengan koma. Kosong berarti semua orang boleh. Cek ID lewat @userinfobot |
