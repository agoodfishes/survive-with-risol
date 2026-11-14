"""Bot Telegram pencatat keuangan: baca foto struk / riwayat e-wallet / belanjaan."""
import asyncio
import json
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import date

from dotenv import load_dotenv # type: ignore
from google import genai
import telegram  # type: ignore[import-not-found]
from telegram.ext import (  # type: ignore[import-not-found]
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot-keuangan")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}
DB_PATH = os.getenv("DB_PATH", "keuangan.db")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

PROMPT = """Kamu asisten pencatat keuangan. Analisis gambar ini dan kembalikan HANYA JSON valid
(tanpa teks lain, tanpa markdown) dengan format:

{
  "jenis": "struk" | "ewallet" | "belanjaan" | "tidak_dikenali",
  "transaksi": [
    {
      "tanggal": "YYYY-MM-DD" atau null,
      "merchant": "nama toko/penerima" atau "",
      "deskripsi": "ringkasan singkat",
      "jumlah": angka bulat Rupiah tanpa titik/koma, atau null jika tidak diketahui,
      "arah": "keluar" | "masuk",
      "kategori": salah satu dari [Makanan, Belanja Harian, Transportasi, Tagihan, Hiburan, Kesehatan, Pendidikan, Transfer, Pemasukan, Lainnya]
    }
  ]
}

Aturan:
- "struk": satu struk = satu transaksi, jumlah = TOTAL akhir yang dibayar.
- "ewallet": screenshot riwayat e-wallet/mutasi, satu baris = satu transaksi.
- "belanjaan": foto barang belanjaan (bukan struk). Gabungkan jadi SATU transaksi,
  isi deskripsi dengan daftar barang dan jumlahnya (misal "2 botol minyak, 1 karton telur").
  JANGAN menebak harga: jumlah harus null.
- Jika tanggal tidak terlihat, isi null. Jangan mengarang data yang tidak ada di gambar.
- Jika bukan gambar keuangan/belanjaan, jenis = "tidak_dikenali" dan transaksi = [].
"""


# ---------- Database ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS transaksi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                merchant TEXT,
                deskripsi TEXT,
                jumlah INTEGER NOT NULL,
                arah TEXT NOT NULL,
                kategori TEXT,
                sumber TEXT,
                dibuat TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def is_duplicate(user_id: int, t: dict) -> bool:
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM transaksi WHERE user_id=? AND tanggal=? AND jumlah=? AND arah=? LIMIT 1",
            (user_id, t["tanggal"], t["jumlah"], t["arah"]),
        ).fetchone()
    return row is not None


def save_transactions(user_id: int, items: list[dict], sumber: str):
    with db() as c:
        c.executemany(
            """INSERT INTO transaksi (user_id,tanggal,merchant,deskripsi,jumlah,arah,kategori,sumber)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (user_id, t["tanggal"], t.get("merchant", ""), t.get("deskripsi", ""),
                 t["jumlah"], t["arah"], t.get("kategori", "Lainnya"), sumber)
                for t in items
            ],
        )


# ---------- Util ----------
def rp(n) -> str:
    return "Rp" + f"{int(n):,}".replace(",", ".")


def authorized(update: telegram.Update) -> bool:
    return not ALLOWED or (update.effective_user and update.effective_user.id in ALLOWED)


def format_preview(jenis: str, items: list[dict]) -> str:
    lines = [f"📄 Terdeteksi: *{jenis}*", ""]
    for i, t in enumerate(items, 1):
        tanda = "➖" if t["arah"] == "keluar" else "➕"
        jumlah = rp(t["jumlah"]) if t["jumlah"] is not None else "❓ (harga belum diisi)"
        nama = t.get("merchant") or t.get("deskripsi") or "-"
        lines.append(f"{i}. {tanda} {nama} — {jumlah}")
        lines.append(f"    📅 {t['tanggal']}  🏷 {t.get('kategori', 'Lainnya')}")
        if t.get("deskripsi") and t.get("merchant"):
            lines.append(f"    📝 {t['deskripsi']}")
        if t.get("duplikat"):
            lines.append("    ⚠️ Mirip dengan transaksi yang sudah tersimpan")
    return "\n".join(lines)


def keyboard(pid: str) -> telegram.InlineKeyboardMarkup:
    return telegram.InlineKeyboardMarkup(
        [[
            telegram.InlineKeyboardButton("✅ Simpan", callback_data=f"save:{pid}"),
            telegram.InlineKeyboardButton("❌ Batal", callback_data=f"cancel:{pid}"),
        ]]
    )


# ---------- Handlers ----------
async def start(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Halo! Kirim saya foto:\n"
        "🧾 struk belanja\n"
        "📱 screenshot riwayat e-wallet\n"
        "🛒 foto belanjaan (harga nanti kamu isi)\n\n"
        "Perintah:\n"
        "/laporan — ringkasan bulan ini\n"
        "/riwayat — 10 transaksi terakhir"
    )


async def handle_photo(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = update.message
    wait = await msg.reply_text("⏳ Membaca gambar...")

    photo = msg.photo[-1]  # resolusi tertinggi
    data = bytes(await (await photo.get_file()).download_as_bytearray())

    try:
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[{"inline_data": {"data": data, "mime_type": "image/jpeg"}}, PROMPT],
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        result = json.loads(resp.text)
    except Exception as e:  # kuota habis, JSON rusak, dll.
        log.exception("Gagal memproses gambar")
        await wait.edit_text(
            "😕 Gagal membaca gambar. Coba foto yang lebih jelas, atau tunggu sebentar "
            f"kalau kuota harian habis.\n({type(e).__name__})"
        )
        return

    jenis = result.get("jenis", "tidak_dikenali")
    items = result.get("transaksi") or []
    if jenis == "tidak_dikenali" or not items:
        await wait.edit_text("🤔 Saya tidak menemukan transaksi di gambar ini.")
        return

    uid = update.effective_user.id
    today = date.today().isoformat()
    for t in items:
        t["tanggal"] = t.get("tanggal") or today
        t["arah"] = t.get("arah") if t.get("arah") in ("keluar", "masuk") else "keluar"
        if t.get("jumlah") is not None:
            t["jumlah"] = int(t["jumlah"])
            t["duplikat"] = is_duplicate(uid, t)

    pid = uuid.uuid4().hex[:8]
    context.user_data.setdefault("pending", {})[pid] = {"jenis": jenis, "items": items}

    text = format_preview(jenis, items)
    needs_amount = any(t["jumlah"] is None for t in items)
    if needs_amount:
        context.user_data["awaiting"] = pid
        text += "\n\n💬 Balas dengan *total harga* (angka saja, contoh: 85000) untuk melanjutkan."
        await wait.edit_text(text, parse_mode="Markdown")
    else:
        await wait.edit_text(text, parse_mode="Markdown", reply_markup=keyboard(pid))


async def handle_text(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipakai untuk mengisi harga belanjaan."""
    if not authorized(update):
        return
    pid = context.user_data.get("awaiting")
    pending = context.user_data.get("pending", {}).get(pid) if pid else None
    if not pending:
        return
    digits = re.sub(r"[^\d]", "", update.message.text)
    if not digits:
        await update.message.reply_text("Kirim angka saja ya, contoh: 85000")
        return
    for t in pending["items"]:
        if t["jumlah"] is None:
            t["jumlah"] = int(digits)
            t["duplikat"] = is_duplicate(update.effective_user.id, t)
    context.user_data.pop("awaiting", None)
    await update.message.reply_text(
        format_preview(pending["jenis"], pending["items"]),
        parse_mode="Markdown",
        reply_markup=keyboard(pid),
    )


async def handle_button(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, pid = q.data.split(":", 1)
    pending = context.user_data.get("pending", {}).pop(pid, None)
    if not pending:
        await q.edit_message_text("Data ini sudah tidak berlaku.")
        return
    if action == "save":
        save_transactions(q.from_user.id, pending["items"], pending["jenis"])
        await q.edit_message_text(f"✅ Tersimpan ({len(pending['items'])} transaksi).")
    else:
        await q.edit_message_text("❌ Dibatalkan, tidak ada yang disimpan.")


async def laporan(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    bulan = date.today().strftime("%Y-%m")
    with db() as c:
        rows = c.execute(
            """SELECT kategori, SUM(jumlah) total FROM transaksi
               WHERE user_id=? AND arah='keluar' AND substr(tanggal,1,7)=?
               GROUP BY kategori ORDER BY total DESC""",
            (update.effective_user.id, bulan),
        ).fetchall()
        masuk = c.execute(
            """SELECT COALESCE(SUM(jumlah),0) FROM transaksi
               WHERE user_id=? AND arah='masuk' AND substr(tanggal,1,7)=?""",
            (update.effective_user.id, bulan),
        ).fetchone()[0]
    if not rows and not masuk:
        await update.message.reply_text("Belum ada data bulan ini.")
        return
    total_keluar = sum(r["total"] for r in rows)
    lines = [f"📊 *Laporan {bulan}*", ""]
    lines += [f"• {r['kategori']}: {rp(r['total'])}" for r in rows]
    lines += ["", f"➖ Pengeluaran: {rp(total_keluar)}", f"➕ Pemasukan: {rp(masuk)}",
              f"💰 Selisih: {rp(masuk - total_keluar)}"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def riwayat(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    with db() as c:
        rows = c.execute(
            "SELECT * FROM transaksi WHERE user_id=? ORDER BY tanggal DESC, id DESC LIMIT 10",
            (update.effective_user.id,),
        ).fetchall()
    if not rows:
        await update.message.reply_text("Belum ada transaksi.")
        return
    lines = ["🧾 *10 transaksi terakhir*", ""]
    for r in rows:
        tanda = "➖" if r["arah"] == "keluar" else "➕"
        nama = r["merchant"] or r["deskripsi"] or "-"
        lines.append(f"{r['tanggal']} {tanda} {nama} — {rp(r['jumlah'])} ({r['kategori']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))
    log.info("Bot berjalan...")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
