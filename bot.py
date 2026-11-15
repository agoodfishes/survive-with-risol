"""Bot Telegram pencatat keuangan v2.

Fitur: baca foto (struk / e-wallet / belanjaan), input manual, pemasukan dibagi
otomatis ke kategori (amplop), pengeluaran mengurangi saldo kategori, dan koreksi
hasil deteksi (sebelum maupun sesudah disimpan).
"""
import asyncio
import html
import json
import logging
import math
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # supaya token tidak tampil di log
log = logging.getLogger("bot-keuangan")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}
DB_PATH = os.getenv("DB_PATH", "keuangan.db")
# 0 = saat /rencana hanya kirim total per kategori ke AI (tanpa deskripsi transaksi)
KIRIM_RIWAYAT = os.getenv("KIRIM_RIWAYAT_KE_AI", "1") != "0"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# ---------- Kategori (amplop) ----------
TABUNGAN = "Tabungan & Dana Darurat"
KATEGORI_POS = ["Kebutuhan", "Keinginan", TABUNGAN]
KATEGORI_AI = KATEGORI_POS
DEFAULT_PERSEN = {"Kebutuhan": 55, "Keinginan": 10, TABUNGAN: 35}
ALIAS_KATEGORI = {
    "kebutuhan": "Kebutuhan", "keinginan": "Keinginan",
    "tabungan & dana darurat": TABUNGAN, "tabungan dan dana darurat": TABUNGAN,
    "dana darurat": TABUNGAN, "tabungan": TABUNGAN,
}
# Kategori lama (versi 1) dipetakan ke 3 kategori baru
LEGACY = {
    "Makanan": "Kebutuhan", "Belanja Harian": "Kebutuhan", "Transportasi": "Kebutuhan",
    "Tagihan": "Kebutuhan", "Kesehatan": "Kebutuhan", "Pendidikan": "Kebutuhan",
    "Lainnya": "Kebutuhan", "Hiburan": "Keinginan", "Tabungan": TABUNGAN,
}
# Urutan penting: yang "keinginan" dicek lebih dulu agar "makan di cafe" masuk Keinginan.
KATA_KUNCI = {
    TABUNGAN: ["dana darurat", "darurat"],
    "Keinginan": ["kopi", "boba", "jajan", "snack", "camilan", "cafe", "kafe", "resto", "restoran",
                  "nonton", "bioskop", "game", "netflix", "spotify", "topup", "top up", "film",
                  "konser", "wisata", "liburan", "karaoke", "youtube", "hobi", "fashion", "baju",
                  "sepatu", "tas", "gadget"],
    "Kebutuhan": ["makan", "makanan", "minum", "nasi", "ayam", "bakso", "sarapan", "warteg", "geprek",
                  "mie", "indomie", "soto", "roti", "sate", "pecel", "gado", "bubur", "kantin", "lunch",
                  "dinner", "gofood", "grabfood", "shopeefood", "bensin", "parkir", "ojek", "ojol",
                  "grab", "gojek", "tol", "bus", "kereta", "angkot", "krl", "mrt", "pertamax",
                  "pertalite", "taksi", "listrik", "pdam", "wifi", "internet", "pulsa", "token", "kos",
                  "kost", "sewa", "cicilan", "bpjs", "indihome", "tagihan", "asuransi", "pajak",
                  "kontrakan", "air", "sabun", "shampo", "sampo", "minyak", "beras", "telur", "galon",
                  "indomaret", "alfamart", "sembako", "belanja", "deterjen", "tisu", "gula", "sayur",
                  "buah", "pasar", "supermarket", "superindo", "obat", "dokter", "apotek", "vitamin",
                  "klinik", "rumah sakit", "puskesmas", "lab", "medis", "buku", "kursus", "spp",
                  "kuliah", "les", "fotokopi", "print", "sekolah", "ukt", "alat tulis"],
}
JENIS_LABEL = {
    "struk": "Struk belanja", "ewallet": "Riwayat e-wallet", "belanjaan": "Foto belanjaan",
    "manual": "Input manual", "edit": "Edit transaksi",
}

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
      "kategori": salah satu dari __KATEGORI__ (untuk arah "masuk" isi "Pemasukan")
    }
  ]
}

Aturan:
- "struk": satu struk = satu transaksi, jumlah = TOTAL akhir yang dibayar.
- "ewallet": screenshot riwayat e-wallet/mutasi, satu baris = satu transaksi.
  Uang yang diterima = arah "masuk", uang yang keluar = arah "keluar".
- "belanjaan": foto barang belanjaan (bukan struk). Gabungkan jadi SATU transaksi,
  isi deskripsi dengan daftar barang dan jumlahnya (misal "2 botol minyak, 1 karton telur").
  JANGAN menebak harga: jumlah harus null.
- Kategori: "Kebutuhan" = makanan pokok, transportasi, tagihan, belanja harian, kesehatan, pendidikan.
  "Keinginan" = hiburan, jajan/kafe/kopi, hobi, belanja non-esensial.
  "Tabungan & Dana Darurat" hanya jika jelas uang dipindah ke tabungan/investasi/dana darurat.
- Jika tanggal tidak terlihat, isi null. Jangan mengarang data yang tidak ada di gambar.
- Jika bukan gambar keuangan/belanjaan, jenis = "tidak_dikenali" dan transaksi = [].
""".replace("__KATEGORI__", "[" + ", ".join(KATEGORI_AI) + "]")


# ---------- Util ----------
def esc(s) -> str:
    return html.escape(str(s or ""))


def rp(n) -> str:
    n = int(n)
    s = f"{abs(n):,}".replace(",", ".")
    return f"-Rp{s}" if n < 0 else f"Rp{s}"


def authorized(update: Update) -> bool:
    return not ALLOWED or (update.effective_user and update.effective_user.id in ALLOWED)


def norm_kat(k: str) -> str:
    return k if k in KATEGORI_POS else LEGACY.get(k, "Kebutuhan")


def parse_amount(s: str):
    """'25000', '25.000', '25rb', '25k', '1,5jt' -> int. Gagal -> None."""
    s = s.strip().lower().replace("rp", "").replace(" ", "")
    m = re.fullmatch(r"(\d+(?:[.,]\d+)*)(jt|juta|rb|ribu|k)?", s)
    if not m:
        return None
    num, suf = m.groups()
    if suf:
        mult = 1_000_000 if suf in ("jt", "juta") else 1_000
        try:
            return int(round(float(num.replace(",", ".")) * mult))
        except ValueError:
            return None
    return int(re.sub(r"[.,]", "", num))


def parse_date(s: str):
    s = s.strip().lower()
    if s in ("hari ini", "sekarang", "today"):
        return date.today().isoformat()
    if s == "kemarin":
        return (date.today() - timedelta(days=1)).isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d/%m"):
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if fmt == "%d/%m":
            d = d.replace(year=date.today().year)
        return d.isoformat()
    return None


def parse_input(teks: str):
    """'25rb makan siang' atau 'makan siang 25rb' -> (25000, 'makan siang')."""
    parts = teks.strip().split()
    if not parts:
        return None
    jumlah = parse_amount(parts[0])
    if jumlah is not None:
        return jumlah, " ".join(parts[1:])
    jumlah = parse_amount(parts[-1])
    if jumlah is not None and len(parts) > 1:
        return jumlah, " ".join(parts[:-1])
    return None


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
        c.execute(
            """CREATE TABLE IF NOT EXISTS alokasi_persen (
                user_id INTEGER NOT NULL, kategori TEXT NOT NULL, persen REAL NOT NULL,
                PRIMARY KEY (user_id, kategori))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS alokasi_transaksi (
                transaksi_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                kategori TEXT NOT NULL, jumlah INTEGER NOT NULL)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS aturan (
                user_id INTEGER NOT NULL, kata TEXT NOT NULL, kategori TEXT NOT NULL,
                PRIMARY KEY (user_id, kata))"""
        )
        # Migrasi satu kali: kategori versi 1 -> 3 kategori baru (aman diulang)
        for lama, baru in LEGACY.items():
            for tabel in ("transaksi", "alokasi_transaksi", "aturan"):
                c.execute(f"UPDATE {tabel} SET kategori=? WHERE kategori=?", (baru, lama))
        c.execute("DELETE FROM alokasi_persen WHERE kategori NOT IN (%s)" % ",".join("?" * len(KATEGORI_POS)),
                  KATEGORI_POS)
    backfill_alokasi()


def get_persen(uid: int) -> dict:
    with db() as c:
        rows = c.execute("SELECT kategori, persen FROM alokasi_persen WHERE user_id=?", (uid,)).fetchall()
    if not rows:
        return {k: float(DEFAULT_PERSEN.get(k, 0)) for k in KATEGORI_POS}
    d = {r["kategori"]: r["persen"] for r in rows}
    return {k: float(d.get(k, 0)) for k in KATEGORI_POS}


def set_persen(uid: int, persen: dict):
    with db() as c:
        c.execute("DELETE FROM alokasi_persen WHERE user_id=?", (uid,))
        c.executemany(
            "INSERT INTO alokasi_persen (user_id,kategori,persen) VALUES (?,?,?)",
            [(uid, k, float(persen.get(k, 0))) for k in KATEGORI_POS],
        )


def hitung_alokasi(jumlah: int, persen: dict) -> dict:
    hasil = {k: math.floor(jumlah * p / 100) for k, p in persen.items() if p > 0}
    if not hasil:
        return {KATEGORI_POS[0]: jumlah}
    sisa = jumlah - sum(hasil.values())
    if sisa:
        terbesar = max(hasil, key=lambda k: persen[k])
        hasil[terbesar] += sisa
    return hasil


def _simpan_alokasi(c, tid: int, uid: int, jumlah: int, persen: dict):
    c.execute("DELETE FROM alokasi_transaksi WHERE transaksi_id=?", (tid,))
    for k, v in hitung_alokasi(jumlah, persen).items():
        if v:
            c.execute(
                "INSERT INTO alokasi_transaksi (transaksi_id,user_id,kategori,jumlah) VALUES (?,?,?,?)",
                (tid, uid, k, v),
            )


def backfill_alokasi():
    """Pemasukan lama (sebelum fitur alokasi) ikut dibagi memakai persen yang berlaku."""
    with db() as c:
        rows = c.execute(
            """SELECT t.id, t.user_id, t.jumlah FROM transaksi t
               WHERE t.arah='masuk' AND NOT EXISTS
               (SELECT 1 FROM alokasi_transaksi a WHERE a.transaksi_id=t.id)"""
        ).fetchall()
    for r in rows:
        persen = get_persen(r["user_id"])
        with db() as c:
            _simpan_alokasi(c, r["id"], r["user_id"], r["jumlah"], persen)


def insert_transaksi(uid: int, items: list, sumber: str) -> list:
    persen = get_persen(uid)
    ids = []
    with db() as c:
        for t in items:
            cur = c.execute(
                """INSERT INTO transaksi (user_id,tanggal,merchant,deskripsi,jumlah,arah,kategori,sumber)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (uid, t["tanggal"], t["merchant"], t["deskripsi"], t["jumlah"],
                 t["arah"], t["kategori"], sumber),
            )
            if t["arah"] == "masuk":
                _simpan_alokasi(c, cur.lastrowid, uid, t["jumlah"], persen)
            ids.append(cur.lastrowid)
    return ids


def get_transaksi(uid: int, tid: int):
    with db() as c:
        r = c.execute("SELECT * FROM transaksi WHERE id=? AND user_id=?", (tid, uid)).fetchone()
    return dict(r) if r else None


def update_transaksi(uid: int, tid: int, t: dict):
    persen = get_persen(uid)
    with db() as c:
        c.execute(
            """UPDATE transaksi SET tanggal=?, merchant=?, deskripsi=?, jumlah=?, arah=?, kategori=?
               WHERE id=? AND user_id=?""",
            (t["tanggal"], t["merchant"], t["deskripsi"], t["jumlah"], t["arah"], t["kategori"], tid, uid),
        )
        c.execute("DELETE FROM alokasi_transaksi WHERE transaksi_id=?", (tid,))
        if t["arah"] == "masuk":
            _simpan_alokasi(c, tid, uid, t["jumlah"], persen)


def hapus_transaksi(uid: int, tid: int) -> bool:
    with db() as c:
        cur = c.execute("DELETE FROM transaksi WHERE id=? AND user_id=?", (tid, uid))
        if cur.rowcount:
            c.execute("DELETE FROM alokasi_transaksi WHERE transaksi_id=?", (tid,))
        return cur.rowcount > 0


def is_duplicate(uid: int, t: dict) -> bool:
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM transaksi WHERE user_id=? AND tanggal=? AND jumlah=? AND arah=? LIMIT 1",
            (uid, t["tanggal"], t["jumlah"], t["arah"]),
        ).fetchone()
    return row is not None


def hitung_saldo(uid: int) -> dict:
    """Saldo kategori = total alokasi pemasukan - total pengeluaran kategori itu."""
    saldo = {k: 0 for k in KATEGORI_POS}
    with db() as c:
        for r in c.execute(
            "SELECT kategori, SUM(jumlah) t FROM alokasi_transaksi WHERE user_id=? GROUP BY kategori", (uid,)
        ):
            saldo[norm_kat(r["kategori"])] += r["t"]
        for r in c.execute(
            "SELECT kategori, SUM(jumlah) t FROM transaksi WHERE user_id=? AND arah='keluar' GROUP BY kategori",
            (uid,),
        ):
            saldo[norm_kat(r["kategori"])] -= r["t"]
    return saldo


# ---------- Aturan belajar dari koreksi ----------
def simpan_aturan(uid: int, t: dict):
    kata = (t.get("merchant") or t.get("deskripsi") or "").strip().lower()
    if not kata or len(kata) > 40 or t["arah"] != "keluar":
        return
    with db() as c:
        c.execute("INSERT OR REPLACE INTO aturan (user_id,kata,kategori) VALUES (?,?,?)",
                  (uid, kata, t["kategori"]))


def cari_aturan(uid: int, teks: str):
    teks = teks.lower()
    with db() as c:
        rows = c.execute("SELECT kata, kategori FROM aturan WHERE user_id=? ORDER BY length(kata) DESC",
                         (uid,)).fetchall()
    for r in rows:
        if r["kata"] in teks:
            return r["kategori"]
    return None


def tebak_kategori(uid: int, teks: str) -> str:
    k = cari_aturan(uid, teks)
    if k:
        return k
    teks_l = teks.lower()
    tokens = re.findall(r"[a-z]+", teks_l)
    for kat, daftar in KATA_KUNCI.items():
        for w in daftar:
            if " " in w:
                if w in teks_l:
                    return kat
            elif len(w) <= 3:
                if w in tokens:
                    return kat
            elif any(tok.startswith(w) for tok in tokens):
                return kat
    return "Kebutuhan"


# ---------- Normalisasi & tampilan ----------
def normalisasi_item(t: dict) -> dict:
    jumlah = t.get("jumlah")
    try:
        jumlah = int(round(float(jumlah))) if jumlah is not None else None
    except (TypeError, ValueError):
        jumlah = None
    if jumlah is not None and jumlah <= 0:
        jumlah = None
    arah = "masuk" if t.get("arah") == "masuk" else "keluar"
    kat = str(t.get("kategori") or "")
    return {
        "tanggal": parse_date(str(t.get("tanggal") or "")) or date.today().isoformat(),
        "merchant": str(t.get("merchant") or "").strip()[:60],
        "deskripsi": str(t.get("deskripsi") or "").strip()[:200],
        "jumlah": jumlah,
        "arah": arah,
        "kategori": "Pemasukan" if arah == "masuk" else (kat if kat in KATEGORI_POS else LEGACY.get(kat, "Kebutuhan")),
    }


def baris_item(i: int, t: dict, dup: bool) -> list:
    tanda = "➖" if t["arah"] == "keluar" else "➕"
    jumlah = rp(t["jumlah"]) if t["jumlah"] is not None else "❓ belum diisi"
    nama = t["merchant"] or t["deskripsi"] or "-"
    baris = [f"{i}. {tanda} <b>{esc(nama)}</b> — {jumlah}",
             f"    📅 {t['tanggal']}  🏷 {esc(t['kategori'])}"]
    if t["merchant"] and t["deskripsi"]:
        baris.append(f"    📝 {esc(t['deskripsi'])}")
    if dup:
        baris.append("    ⚠️ Mirip dengan transaksi yang sudah tersimpan")
    return baris


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def kb_preview(pid):
    return InlineKeyboardMarkup([[btn("✅ Simpan", f"save:{pid}"), btn("✏️ Koreksi", f"fix:{pid}"),
                                  btn("❌ Batal", f"cancel:{pid}")]])


def render(uid: int, pid: str, pending: dict, prompt_idx=None):
    items = pending["items"]
    saved_id = pending.get("saved_id")
    if saved_id:
        judul = f"✏️ <b>Edit transaksi #{saved_id}</b>"
    else:
        judul = f"📄 <b>{esc(JENIS_LABEL.get(pending['jenis'], pending['jenis']))}</b>"
    lines = [judul, ""]
    masuk = [t for t in items if t["arah"] == "masuk" and t["jumlah"]]
    persen = get_persen(uid) if len(masuk) == 1 else None
    for i, t in enumerate(items, 1):
        dup = (not saved_id) and t["jumlah"] is not None and is_duplicate(uid, t)
        lines += baris_item(i, t, dup)
        if persen and t is masuk[0]:
            lines.append("    💼 Dibagi otomatis:")
            for k, v in hitung_alokasi(t["jumlah"], persen).items():
                lines.append(f"       • {esc(k)}: {rp(v)}")
    if len(masuk) > 1:
        lines.append("\n💼 Pemasukan akan dibagi otomatis ke kategori (lihat /saldo setelah disimpan).")
    if prompt_idx is not None:
        lines.append(f"\n💬 Balas dengan <b>total harga</b> untuk item {prompt_idx + 1} (contoh: 85000 atau 85rb).")
        markup = InlineKeyboardMarkup([[btn("❌ Batal", f"cancel:{pid}")]])
    else:
        markup = kb_preview(pid)
    return "\n".join(lines), markup


def next_missing(items):
    for i, t in enumerate(items):
        if t["jumlah"] is None:
            return i
    return None


def kb_field(pid, idx, t, saved):
    rows = [[btn("💰 Jumlah", f"fld:{pid}:{idx}:jml"), btn("📅 Tanggal", f"fld:{pid}:{idx}:tgl")],
            [btn("🏪 Nama", f"fld:{pid}:{idx}:mer"), btn("↕️ Masuk/Keluar", f"fld:{pid}:{idx}:arh")]]
    if t["arah"] == "keluar":
        rows.append([btn("🏷 Kategori", f"fld:{pid}:{idx}:kat")])
    last = [btn("⬅️ Kembali", f"back:{pid}")]
    if not saved:
        last.insert(0, btn("🗑 Hapus item", f"fld:{pid}:{idx}:del"))
    rows.append(last)
    return InlineKeyboardMarkup(rows)


def teks_field(pending, idx):
    t = pending["items"][idx]
    return (f"✏️ <b>Koreksi item {idx + 1}</b>\n\n" + "\n".join(baris_item(idx + 1, t, False))
            + "\n\nMau ubah bagian mana?")


def trim_pending(context):
    p = context.user_data.setdefault("pending", {})
    while len(p) > 10:
        p.pop(next(iter(p)))


def fmt_saldo(v: int) -> str:
    return f"⚠️ {rp(v)} (minus)" if v < 0 else rp(v)


async def ubah(q, text, markup=None):
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "Halo! Saya pencatat keuanganmu 💸\n\n"
        "📸 Kirim foto struk, screenshot e-wallet, atau foto belanjaan\n"
        "✍️ Atau ketik langsung:\n"
        "   -25rb makan siang   (pengeluaran)\n"
        "   +5jt gaji   (pemasukan, dibagi otomatis ke kategori)\n\n"
        "Perintah:\n"
        "/saldo — saldo tiap kategori\n"
        "/laporan — ringkasan bulan ini\n"
        "/riwayat — 10 transaksi terakhir\n"
        "/edit 12 — koreksi transaksi tersimpan (nomor dari /riwayat)\n"
        "/hapus 12 — hapus transaksi\n"
        "/alokasi — atur pembagian pemasukan\n"
        "/rencana 500rb 1 minggu — rekomendasi anggaran dari AI\n"
        "/masuk dan /keluar — cara lain mengetik transaksi"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = update.message
    uid = update.effective_user.id
    wait = await msg.reply_text("⏳ Membaca gambar...")

    photo = msg.photo[-1]
    data = bytes(await (await photo.get_file()).download_as_bytearray())

    try:
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        result = json.loads(resp.text)
        if not isinstance(result, dict):
            raise ValueError("format JSON tidak sesuai")
    except Exception as e:
        log.exception("Gagal memproses gambar")
        await wait.edit_text(
            "😕 Gagal membaca gambar. Coba foto yang lebih jelas, atau tunggu sebentar "
            f"kalau kuota harian habis.\n({type(e).__name__})"
        )
        return

    jenis = result.get("jenis", "tidak_dikenali")
    raw = result.get("transaksi") or []
    if jenis == "tidak_dikenali" or not raw:
        await wait.edit_text("🤔 Saya tidak menemukan transaksi di gambar ini.")
        return

    items = [normalisasi_item(t) for t in raw if isinstance(t, dict)]
    for t in items:  # terapkan aturan hasil koreksi sebelumnya
        if t["arah"] == "keluar":
            k = cari_aturan(uid, f"{t['merchant']} {t['deskripsi']}")
            if k:
                t["kategori"] = k

    pid = uuid.uuid4().hex[:8]
    pending = {"jenis": jenis, "items": items}
    context.user_data.setdefault("pending", {})[pid] = pending
    trim_pending(context)

    miss = next_missing(items)
    if miss is not None:
        context.user_data["awaiting"] = {"pid": pid, "idx": miss, "field": "jml"}
    text, markup = render(uid, pid, pending, prompt_idx=miss)
    await wait.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def buat_draft_manual(update, context, arah: str, teks: str):
    hasil = parse_input(teks)
    if not hasil:
        contoh = "/masuk 5jt gaji" if arah == "masuk" else "/keluar 25rb makan siang"
        await update.message.reply_text(
            f"Format belum dikenali. Contoh: {contoh}\n"
            "Jumlah boleh ditulis 25000, 25.000, 25rb, atau 1,5jt."
        )
        return
    uid = update.effective_user.id
    jumlah, ket = hasil
    t = normalisasi_item({"jumlah": jumlah, "arah": arah, "deskripsi": ket or
                          ("Pemasukan" if arah == "masuk" else "Pengeluaran")})
    if arah == "keluar":
        t["kategori"] = tebak_kategori(uid, ket)
    pid = uuid.uuid4().hex[:8]
    pending = {"jenis": "manual", "items": [t]}
    context.user_data.setdefault("pending", {})[pid] = pending
    trim_pending(context)
    text, markup = render(uid, pid, pending)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_masuk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if authorized(update):
        await buat_draft_manual(update, context, "masuk", " ".join(context.args))


async def cmd_keluar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if authorized(update):
        await buat_draft_manual(update, context, "keluar", " ".join(context.args))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    teks = update.message.text.strip()

    aw = context.user_data.get("awaiting")
    pending = context.user_data.get("pending", {}).get(aw["pid"]) if aw else None
    if aw and not pending:
        context.user_data.pop("awaiting", None)
        aw = None
    if aw:
        pid, idx, field = aw["pid"], aw["idx"], aw["field"]
        t = pending["items"][idx]
        if field == "jml":
            val = parse_amount(teks)
            if not val or val <= 0:
                await update.message.reply_text("Format belum dikenali. Contoh: 85000 atau 85rb")
                return
            t["jumlah"] = val
        elif field == "tgl":
            d = parse_date(teks)
            if not d:
                await update.message.reply_text("Tanggal belum dikenali. Contoh: 14/11/2026, 2026-11-14, atau kemarin")
                return
            t["tanggal"] = d
        elif field == "mer":
            t["merchant"] = teks[:60]
        context.user_data.pop("awaiting", None)
        miss = next_missing(pending["items"])
        if miss is not None:
            context.user_data["awaiting"] = {"pid": pid, "idx": miss, "field": "jml"}
        text, markup = render(uid, pid, pending, prompt_idx=miss)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
        return

    m = re.match(r"^([+-])\s*(.+)$", teks)
    if m:
        await buat_draft_manual(update, context, "masuk" if m.group(1) == "+" else "keluar", m.group(2))
        return
    await update.message.reply_text(
        "Ketik transaksi seperti ini:\n-25rb makan siang\n+5jt gaji\n\n"
        "Atau kirim foto struk. Ketik /start untuk bantuan."
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not authorized(update):
        await q.answer()
        return
    uid = q.from_user.id
    parts = q.data.split(":")
    action = parts[0]

    if action == "hps":
        await q.answer()
        ok = hapus_transaksi(uid, int(parts[1]))
        await ubah(q, f"🗑 Transaksi #{parts[1]} dihapus." if ok else "Transaksi tidak ditemukan.")
        return
    if action == "hpsx":
        await q.answer()
        await ubah(q, "Dibatalkan, transaksi tidak dihapus.")
        return

    pid = parts[1]
    pendings = context.user_data.setdefault("pending", {})
    pending = pendings.get(pid)
    if not pending:
        await q.answer()
        await ubah(q, "Data ini sudah tidak berlaku. Kirim ulang fotonya atau ketik transaksinya lagi.")
        return
    items = pending["items"]
    aw = context.user_data.get("awaiting")

    def clear_awaiting():
        if aw and aw["pid"] == pid:
            context.user_data.pop("awaiting", None)

    if action == "save":
        if any(t["jumlah"] is None for t in items):
            await q.answer("Isi jumlahnya dulu ya.", show_alert=True)
            return
        await q.answer()
        clear_awaiting()
        pendings.pop(pid, None)
        if pending.get("saved_id"):
            update_transaksi(uid, pending["saved_id"], items[0])
            lines = [f"✅ Transaksi #{pending['saved_id']} diperbarui."]
        else:
            insert_transaksi(uid, items, pending["jenis"])
            lines = [f"✅ Tersimpan ({len(items)} transaksi)."]
        saldo = hitung_saldo(uid)
        for k in dict.fromkeys(t["kategori"] for t in items if t["arah"] == "keluar"):
            lines.append(f"💼 Saldo {esc(norm_kat(k))}: {fmt_saldo(saldo[norm_kat(k)])}")
        if any(t["arah"] == "masuk" for t in items):
            lines.append("💰 Pemasukan sudah dibagi ke kategori. Cek /saldo.")
            lines.append("📅 Mau rencana pengeluaran? Contoh: /rencana 1 minggu")
        await ubah(q, "\n".join(lines))
        return

    await q.answer()
    if action == "cancel":
        clear_awaiting()
        pendings.pop(pid, None)
        await ubah(q, "❌ Dibatalkan, tidak ada yang disimpan.")
    elif action == "back":
        clear_awaiting()
        text, markup = render(uid, pid, pending)
        await ubah(q, text, markup)
    elif action == "fix":
        clear_awaiting()
        if len(items) == 1:
            await ubah(q, teks_field(pending, 0), kb_field(pid, 0, items[0], pending.get("saved_id")))
        else:
            rows = [[btn(f"{i + 1}. {(t['merchant'] or t['deskripsi'] or '-')[:22]} "
                         f"{rp(t['jumlah']) if t['jumlah'] is not None else '?'}", f"pick:{pid}:{i}")]
                    for i, t in enumerate(items)]
            rows.append([btn("⬅️ Kembali", f"back:{pid}")])
            await ubah(q, "✏️ <b>Koreksi</b>\n\nPilih item yang mau diperbaiki:", InlineKeyboardMarkup(rows))
    elif action == "pick":
        idx = int(parts[2])
        await ubah(q, teks_field(pending, idx), kb_field(pid, idx, items[idx], pending.get("saved_id")))
    elif action == "fld":
        idx, field = int(parts[2]), parts[3]
        t = items[idx]
        if field in ("jml", "tgl", "mer"):
            context.user_data["awaiting"] = {"pid": pid, "idx": idx, "field": field}
            prompt = {
                "jml": "💰 Ketik jumlah yang benar (contoh: 85000 atau 85rb).",
                "tgl": "📅 Ketik tanggal yang benar (contoh: 14/11/2026, 2026-11-14, atau kemarin).",
                "mer": "🏪 Ketik nama toko/penerima yang benar.",
            }[field]
            await ubah(q, f"Item {idx + 1}: {prompt}", InlineKeyboardMarkup([[btn("⬅️ Batal", f"back:{pid}")]]))
        elif field == "kat":
            rows = [[btn(KATEGORI_POS[i], f"cat:{pid}:{idx}:{i}"),
                     *([btn(KATEGORI_POS[i + 1], f"cat:{pid}:{idx}:{i + 1}")] if i + 1 < len(KATEGORI_POS) else [])]
                    for i in range(0, len(KATEGORI_POS), 2)]
            rows.append([btn("⬅️ Kembali", f"pick:{pid}:{idx}")])
            await ubah(q, f"🏷 Pilih kategori yang benar untuk item {idx + 1}:", InlineKeyboardMarkup(rows))
        elif field == "arh":
            await ubah(q, f"↕️ Item {idx + 1} sebenarnya:", InlineKeyboardMarkup([
                [btn("➖ Pengeluaran", f"dir:{pid}:{idx}:keluar"), btn("➕ Pemasukan", f"dir:{pid}:{idx}:masuk")],
                [btn("⬅️ Kembali", f"pick:{pid}:{idx}")]]))
        elif field == "del":
            items.pop(idx)
            if not items:
                pendings.pop(pid, None)
                await ubah(q, "Semua item dihapus, tidak ada yang disimpan.")
            else:
                text, markup = render(uid, pid, pending)
                await ubah(q, text, markup)
    elif action == "cat":
        idx, ci = int(parts[2]), int(parts[3])
        items[idx]["kategori"] = KATEGORI_POS[ci]
        simpan_aturan(uid, items[idx])  # ingat untuk transaksi berikutnya
        text, markup = render(uid, pid, pending)
        await ubah(q, text, markup)
    elif action == "dir":
        idx, arah = int(parts[2]), parts[3]
        t = items[idx]
        t["arah"] = arah
        if arah == "masuk":
            t["kategori"] = "Pemasukan"
        elif t["kategori"] not in KATEGORI_POS:
            t["kategori"] = tebak_kategori(uid, f"{t['merchant']} {t['deskripsi']}")
        text, markup = render(uid, pid, pending)
        await ubah(q, text, markup)


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    s = hitung_saldo(update.effective_user.id)
    lines = ["💼 <b>Saldo per kategori</b>", ""]
    lines += [f"• {esc(k)}: {fmt_saldo(v)}" for k, v in s.items()]
    lines += ["", f"💰 <b>Total: {rp(sum(s.values()))}</b>", "",
              "Atur pembagian pemasukan dengan /alokasi"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def tabel_persen(persen: dict) -> str:
    return "\n".join(f"• {esc(k)}: {persen[k]:g}%" for k in KATEGORI_POS) + \
        f"\n<b>Total: {sum(persen.values()):g}%</b>"


async def alokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    arg = " ".join(context.args).strip()
    if arg.lower() == "reset":
        set_persen(uid, DEFAULT_PERSEN)
        await update.message.reply_text("🔄 Dikembalikan ke pembagian awal:\n\n" + tabel_persen(get_persen(uid)),
                                        parse_mode="HTML")
        return
    if arg:
        nama = "|".join(re.escape(k) for k in sorted(ALIAS_KATEGORI, key=len, reverse=True))
        found = re.findall(rf"({nama})\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*%?", arg, re.I)
        if not found:
            await update.message.reply_text("Format: /alokasi Kebutuhan 55 Keinginan 10 Tabungan 35")
            return
        baru = get_persen(uid)
        for n, v in found:
            baru[ALIAS_KATEGORI[n.lower()]] = float(v.replace(",", "."))
        if abs(sum(baru.values()) - 100) > 0.01:
            await update.message.reply_text(
                f"Total jadi {sum(baru.values()):g}%, harus tepat 100%. Belum disimpan.\n"
                "Sebutkan kategori lain yang ikut berubah supaya totalnya 100.\n\n"
                "Pembagian saat ini:\n" + tabel_persen(get_persen(uid)), parse_mode="HTML")
            return
        set_persen(uid, baru)
        await update.message.reply_text("✅ Pembagian disimpan (berlaku untuk pemasukan berikutnya):\n\n"
                                        + tabel_persen(baru), parse_mode="HTML")
        return
    await update.message.reply_text(
        "📐 <b>Pembagian pemasukan</b>\n\n" + tabel_persen(get_persen(uid)) +
        "\n\nUbah dengan contoh:\n/alokasi Kebutuhan 50 Keinginan 10 Tabungan 40\n(total harus 100%)\n/alokasi reset — kembali ke awal",
        parse_mode="HTML")


async def laporan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    bulan = date.today().strftime("%Y-%m")
    with db() as c:
        rows = c.execute(
            """SELECT kategori, SUM(jumlah) total FROM transaksi
               WHERE user_id=? AND arah='keluar' AND substr(tanggal,1,7)=?
               GROUP BY kategori ORDER BY total DESC""", (uid, bulan)).fetchall()
        masuk = c.execute(
            """SELECT COALESCE(SUM(jumlah),0) FROM transaksi
               WHERE user_id=? AND arah='masuk' AND substr(tanggal,1,7)=?""", (uid, bulan)).fetchone()[0]
    if not rows and not masuk:
        await update.message.reply_text("Belum ada data bulan ini.")
        return
    total_keluar = sum(r["total"] for r in rows)
    lines = [f"📊 <b>Laporan {bulan}</b>", ""]
    lines += [f"• {esc(r['kategori'])}: {rp(r['total'])}" for r in rows]
    lines += ["", f"➖ Pengeluaran: {rp(total_keluar)}", f"➕ Pemasukan: {rp(masuk)}",
              f"💰 Selisih: {rp(masuk - total_keluar)}"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    with db() as c:
        rows = c.execute("SELECT * FROM transaksi WHERE user_id=? ORDER BY tanggal DESC, id DESC LIMIT 10",
                         (update.effective_user.id,)).fetchall()
    if not rows:
        await update.message.reply_text("Belum ada transaksi.")
        return
    lines = ["🧾 <b>10 transaksi terakhir</b>", ""]
    for r in rows:
        tanda = "➖" if r["arah"] == "keluar" else "➕"
        nama = r["merchant"] or r["deskripsi"] or "-"
        lines.append(f"#{r['id']} {r['tanggal']} {tanda} {esc(nama)} — {rp(r['jumlah'])} ({esc(r['kategori'])})")
    lines += ["", "Koreksi: /edit 12   Hapus: /hapus 12 (ganti 12 dengan nomor di atas)"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _ambil_id(context):
    if context.args and context.args[0].lstrip("#").isdigit():
        return int(context.args[0].lstrip("#"))
    return None


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    tid = _ambil_id(context)
    row = get_transaksi(uid, tid) if tid else None
    if not row:
        await update.message.reply_text("Pakai nomor dari /riwayat, contoh: /edit 12")
        return
    t = normalisasi_item(row)
    t["jumlah"] = row["jumlah"]
    pid = uuid.uuid4().hex[:8]
    pending = {"jenis": "edit", "items": [t], "saved_id": tid}
    context.user_data.setdefault("pending", {})[pid] = pending
    trim_pending(context)
    text, markup = render(uid, pid, pending)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def hapus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    tid = _ambil_id(context)
    row = get_transaksi(uid, tid) if tid else None
    if not row:
        await update.message.reply_text("Pakai nomor dari /riwayat, contoh: /hapus 12")
        return
    nama = row["merchant"] or row["deskripsi"] or "-"
    await update.message.reply_text(
        f"Hapus transaksi #{tid}?\n{esc(nama)} — {rp(row['jumlah'])} ({row['tanggal']})",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[btn("🗑 Ya, hapus", f"hps:{tid}"), btn("Batal", "hpsx:0")]]))


# ---------- Rekomendasi anggaran (AI) ----------
def parse_durasi(teks: str):
    """'500rb 1 minggu' -> (7, '500rb'). Tanpa durasi -> (None, teks)."""
    t = teks.lower()
    for pola, hari in ((r"\bseminggu\b", 7), (r"\bsebulan\b", 30), (r"\bsehari\b", 1)):
        if re.search(pola, t):
            return hari, re.sub(pola, " ", t).strip()
    m = re.search(r"(\d+)\s*(hari|hr|minggu|mgg|pekan|bulan|bln)\b", t)
    if not m:
        return None, teks
    n, satuan = int(m.group(1)), m.group(2)
    kali = 1 if satuan in ("hari", "hr") else 7 if satuan in ("minggu", "mgg", "pekan") else 30
    return n * kali, (t[:m.start()] + " " + t[m.end():]).strip()


def susun_rupiah(total: int, bobot: list) -> list:
    """Bagi total sesuai bobot, dibulatkan ke ribuan, jumlahnya pas total."""
    unit = 1000 if total >= 10_000 else 1
    unit_total, sisa_rp = divmod(total, unit)
    s = sum(bobot)
    mentah = [unit_total * b / s for b in bobot]
    dasar = [math.floor(x) for x in mentah]
    kurang = unit_total - sum(dasar)
    for i in sorted(range(len(bobot)), key=lambda i: mentah[i] - dasar[i], reverse=True)[:kurang]:
        dasar[i] += 1
    hasil = [d * unit for d in dasar]
    hasil[max(range(len(hasil)), key=lambda i: hasil[i])] += sisa_rp
    return hasil


def ringkas_riwayat(uid: int, hari: int = 30, batas: int = 40):
    awal = (date.today() - timedelta(days=hari)).isoformat()
    with db() as c:
        baris = c.execute(
            """SELECT tanggal, deskripsi, merchant, jumlah, kategori FROM transaksi
               WHERE user_id=? AND arah='keluar' AND tanggal>=? ORDER BY tanggal DESC""",
            (uid, awal)).fetchall()
    if not baris:
        return None
    total = sum(r["jumlah"] for r in baris)
    span = max(7, (date.today() - date.fromisoformat(min(r["tanggal"] for r in baris))).days + 1)
    per_kat = {}
    for r in baris:
        k = norm_kat(r["kategori"])
        per_kat[k] = per_kat.get(k, 0) + r["jumlah"]
    out = {"rata_per_hari": round(total / span), "per_kategori": per_kat, "hari_data": span}
    if KIRIM_RIWAYAT:
        out["contoh"] = [f"{(r['deskripsi'] or r['merchant'] or '-')[:40]} | {norm_kat(r['kategori'])} | {r['jumlah']}"
                         for r in baris[:batas]]
    return out


def bangun_prompt(dana: int, hari: int, ringkas) -> str:
    if ringkas:
        riwayat = (f"Rata-rata pengeluaran harian dari riwayat ({ringkas['hari_data']} hari terakhir): "
                   f"{rp(ringkas['rata_per_hari'])}\nTotal per kategori: "
                   + ", ".join(f"{k} {rp(v)}" for k, v in ringkas["per_kategori"].items()))
        if ringkas.get("contoh"):
            riwayat += "\nContoh pengeluaran (deskripsi | kategori | jumlah):\n" + "\n".join(ringkas["contoh"])
    else:
        riwayat = "Belum ada riwayat pengeluaran."
    return f"""Kamu penasihat keuangan untuk mahasiswa/individu di Indonesia.
Susun rencana pembagian uang agar dana ini cukup bertahan selama periodenya.

Dana yang bisa dipakai: {rp(dana)} untuk {hari} hari (sekitar {rp(round(dana / hari))} per hari).
Tabungan sudah dipisah di luar dana ini.
{riwayat}

Kembalikan HANYA JSON valid (tanpa teks lain, tanpa markdown):
{{
  "pos": [{{"nama": "...", "persen": angka, "catatan": "1 kalimat pendek"}}],
  "tips": ["..."],
  "peringatan": "..." atau null
}}

Aturan:
- 4 sampai 7 pos. Contoh pos: Makan & minum, Transportasi, Kebutuhan kuliah (print, buku, alat tulis),
  Pulsa/internet, Kebutuhan harian, Jajan/hiburan, Dana cadangan. Sesuaikan dengan riwayat
  (misalnya kalau ada bayar kos, masukkan sebagai pos sendiri).
- Prioritaskan bertahan hidup: makan & minum dan transportasi dulu, jajan/hiburan paling kecil.
- Sisakan dana cadangan sekitar 5-10%.
- "persen" adalah bagian dari dana (angka, total 100). Jangan menghitung rupiah.
- Kalau dana per hari tampak terlalu kecil untuk makan, minum, dan transportasi dasar, isi
  "peringatan" dengan saran singkat. Kalau cukup, isi null.
- Maksimal 3 tips. Bahasa Indonesia santai dan singkat."""


def validasi_rencana(data):
    if not isinstance(data, dict) or not isinstance(data.get("pos"), list):
        raise ValueError("format rencana tidak sesuai")
    pos = []
    for p in data["pos"][:8]:
        if not isinstance(p, dict):
            continue
        try:
            persen = float(p.get("persen"))
        except (TypeError, ValueError):
            continue
        nama = str(p.get("nama") or "").strip()[:40]
        if nama and persen > 0:
            pos.append({"nama": nama, "persen": persen, "catatan": str(p.get("catatan") or "").strip()[:160]})
    if len(pos) < 2:
        raise ValueError("pos terlalu sedikit")
    tips = [str(x).strip()[:160] for x in (data.get("tips") or []) if str(x).strip()][:3] \
        if isinstance(data.get("tips"), list) else []
    per = data.get("peringatan")
    per = str(per).strip()[:300] if per else None
    if per and per.lower() in ("null", "none", "-"):
        per = None
    return pos, tips, per


def format_rencana(dana, hari, pos, jumlah, tips, peringatan, ket_sumber):
    lines = [f"📅 <b>Rencana {hari} hari</b> — {rp(dana)}",
             f"≈ {rp(round(dana / hari))} per hari" + (f" ({esc(ket_sumber)})" if ket_sumber else ""), ""]
    for p, j in zip(pos, jumlah):
        per_hari = f" (≈{rp(round(j / hari))}/hari)" if hari > 1 else ""
        lines.append(f"• <b>{esc(p['nama'])}</b>: {rp(j)}{per_hari}")
        if p["catatan"]:
            lines.append(f"   {esc(p['catatan'])}")
    if tips:
        lines += ["", "💡 <b>Tips</b>"] + [f"• {esc(t)}" for t in tips]
    if peringatan:
        lines += ["", f"⚠️ {esc(peringatan)}"]
    lines += ["", "Ini saran dari AI berdasarkan kebiasaanmu, bukan nasihat keuangan profesional."]
    return "\n".join(lines)


async def rencana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    uid = update.effective_user.id
    hari, sisa = parse_durasi(" ".join(context.args))
    if not hari or hari > 93:
        await update.message.reply_text(
            "Berapa lama uangnya harus cukup? Contoh:\n"
            "/rencana 500rb 1 minggu\n"
            "/rencana 1,5jt 2 minggu\n"
            "/rencana 1 minggu  (memakai saldo Kebutuhan + Keinginan saat ini)\n\n"
            "Durasi 1 sampai 93 hari. Bisa ditulis hari, minggu, atau bulan.")
        return
    dana, ket = None, ""
    if sisa.strip():
        dana = parse_amount(sisa.strip())
        if not dana or dana <= 0:
            await update.message.reply_text("Jumlah belum dikenali. Contoh: /rencana 500rb 1 minggu")
            return
    else:
        s = hitung_saldo(uid)
        dana = max(s["Kebutuhan"], 0) + max(s["Keinginan"], 0)
        ket = "dari saldo Kebutuhan + Keinginan"
        if dana <= 0:
            await update.message.reply_text(
                "Saldo Kebutuhan + Keinginan masih kosong. Catat pemasukan dulu (misalnya +1jt uang saku), "
                "atau sebut jumlahnya: /rencana 500rb 1 minggu")
            return

    wait = await update.message.reply_text("⏳ Menyusun rencana...")
    try:
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=bangun_prompt(dana, hari, ringkas_riwayat(uid)),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4),
        )
        pos, tips, peringatan = validasi_rencana(json.loads(resp.text))
    except Exception as e:
        log.exception("Gagal menyusun rencana")
        await wait.edit_text("😕 Gagal menyusun rencana. Coba lagi sebentar, atau cek kuota harian Gemini."
                             f"\n({type(e).__name__})")
        return
    jumlah = susun_rupiah(dana, [p["persen"] for p in pos])
    await wait.edit_text(format_rencana(dana, hari, pos, jumlah, tips, peringatan, ket), parse_mode="HTML")


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("saldo", "Saldo tiap kategori"),
        BotCommand("laporan", "Ringkasan bulan ini"),
        BotCommand("riwayat", "10 transaksi terakhir"),
        BotCommand("masuk", "Catat pemasukan"),
        BotCommand("keluar", "Catat pengeluaran"),
        BotCommand("edit", "Koreksi transaksi tersimpan"),
        BotCommand("hapus", "Hapus transaksi"),
        BotCommand("alokasi", "Atur pembagian pemasukan"),
        BotCommand("rencana", "Rekomendasi anggaran untuk N hari"),
    ])


def main():
    init_db()
    asyncio.set_event_loop(asyncio.new_event_loop())  # perlu untuk Python 3.14
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("alokasi", alokasi))
    app.add_handler(CommandHandler("rencana", rencana))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(CommandHandler("masuk", cmd_masuk))
    app.add_handler(CommandHandler("keluar", cmd_keluar))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))
    log.info("Bot berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
