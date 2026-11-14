# Bot Keuangan Telegram

Kirim foto struk / screenshot e-wallet / foto belanjaan → bot membaca, kamu konfirmasi, data tersimpan.

## Cara menjalankan

1. Buat bot di Telegram: chat **@BotFather** → `/newbot` → salin token.
2. Buat API key gratis di https://aistudio.google.com/apikey
3. Install & siapkan:
   ```bash
   python -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env            # lalu isi token & API key
   ```
4. Jalankan: `python bot.py`
5. Buka bot kamu di Telegram, kirim `/start`, lalu kirim foto struk.

## Perintah
- `/laporan` — ringkasan pengeluaran per kategori bulan ini
- `/riwayat` — 10 transaksi terakhir

## Catatan
- Foto tidak disimpan, hanya data hasil ekstraksi (di file `keuangan.db`).
- Isi `ALLOWED_USER_IDS` di `.env` supaya hanya kamu yang bisa memakai bot (menghemat kuota gratis).
- Jika model gratis berubah, ganti `GEMINI_MODEL` di `.env`.
