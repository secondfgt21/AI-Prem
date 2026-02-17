import os
import asyncio
import time
from typing import Dict, Any, Optional

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =======================
# CONFIG (ubah ini)
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Base URL backend Render kamu (tanpa slash di akhir)
API_BASE = os.getenv("API_BASE", "https://ai-prem.onrender.com").rstrip("/")

# Endpoint paths (sesuaikan kalau beda)
PRODUCTS_PATH = os.getenv("PRODUCTS_PATH", "/api/products")
CHECKOUT_PATH  = os.getenv("CHECKOUT_PATH",  "/api/checkout")
ORDER_PATH_TPL = os.getenv("ORDER_PATH_TPL", "/api/order/{order_id}")

# QRIS image URL (sama seperti website kamu)
QR_IMAGE_URL = os.getenv(
    "QR_IMAGE_URL",
    "https://i.postimg.cc/qRkr7LcJ/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-1.png"
)

# Polling status (detik)
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))

# =======================
# Helpers
# =======================
def fmt_idr(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def now_ts() -> int:
    return int(time.time())

async def api_get(path: str) -> Any:
    url = f"{API_BASE}{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

async def api_post(path: str, payload: Dict[str, Any]) -> Any:
    url = f"{API_BASE}{path}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

async def get_products() -> list[dict]:
    data = await api_get(PRODUCTS_PATH)
    # Support beberapa bentuk response
    if isinstance(data, list):
        return data
    return data.get("products") or data.get("items") or []

async def get_order(order_id: str) -> dict:
    path = ORDER_PATH_TPL.format(order_id=order_id)
    return await api_get(path)

# =======================
# In-memory user state
# =======================
# user_id -> { product_id: str, qty: int }
USER_STATE: Dict[int, Dict[str, Any]] = {}

def get_user_state(user_id: int) -> Dict[str, Any]:
    if user_id not in USER_STATE:
        USER_STATE[user_id] = {"product_id": None, "qty": 1}
    return USER_STATE[user_id]

# =======================
# UI builders
# =======================
def products_keyboard(products: list[dict], selected_id: Optional[str], qty: int) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        pid = str(p.get("id", ""))
        name = str(p.get("name", pid))
        stock = int(p.get("stock", 0) or 0)
        prefix = "✅ " if selected_id == pid else ""
        label = f"{prefix}{name} (stok: {stock})"
        rows.append([InlineKeyboardButton(label, callback_data=f"pick:{pid}")])

    # qty controls
    rows.append([
        InlineKeyboardButton("➖", callback_data="qty:-"),
        InlineKeyboardButton(f"Qty: {qty}", callback_data="noop"),
        InlineKeyboardButton("➕", callback_data="qty:+"),
    ])
    rows.append([
        InlineKeyboardButton("🧾 Buat Invoice QRIS", callback_data="checkout"),
        InlineKeyboardButton("🔄 Refresh Produk", callback_data="refresh"),
    ])
    return InlineKeyboardMarkup(rows)

def invoice_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Cek Status", callback_data=f"status:{order_id}")],
        [InlineKeyboardButton("✅ Saya sudah bayar", callback_data=f"status:{order_id}")],
    ])

# =======================
# Commands
# =======================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    st = get_user_state(user_id)

    await update.message.reply_text(
        "🛒 *AI Premium Store Bot*\n"
        "Pilih produk, atur Qty, lalu buat invoice QRIS.\n\n"
        "Catatan: verifikasi tetap admin (tanpa PG/webhook), tapi stok bot & website 1 database.",
        parse_mode="Markdown"
    )

    try:
        products = await get_products()
    except Exception as e:
        await update.message.reply_text(f"Gagal load produk: {e}")
        return

    # auto pick produk pertama kalau belum
    if not st["product_id"] and products:
        st["product_id"] = str(products[0].get("id"))

    await update.message.reply_text(
        "Pilih produk:",
        reply_markup=products_keyboard(products, st["product_id"], st["qty"])
    )

# =======================
# Callback handler
# =======================
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    st = get_user_state(user_id)

    data = q.data or ""
    if data == "noop":
        return

    # Load products (for stock rules)
    try:
        products = await get_products()
    except Exception as e:
        await q.edit_message_text(f"Gagal load produk: {e}")
        return

    # helper find selected
    def find_prod(pid: str) -> Optional[dict]:
        for p in products:
            if str(p.get("id")) == str(pid):
                return p
        return None

    if data.startswith("pick:"):
        pid = data.split(":", 1)[1]
        st["product_id"] = pid
        st["qty"] = 1  # reset qty pas ganti produk biar aman
        await q.edit_message_reply_markup(reply_markup=products_keyboard(products, st["product_id"], st["qty"]))
        return

    if data.startswith("qty:"):
        op = data.split(":", 1)[1]
        pid = st["product_id"]
        p = find_prod(pid) if pid else None
        stock = int(p.get("stock", 0) or 0) if p else 0

        if op == "-" and st["qty"] > 1:
            st["qty"] -= 1
        elif op == "+":
            # tidak boleh lebih dari stock
            if stock > 0 and st["qty"] < stock:
                st["qty"] += 1

        await q.edit_message_reply_markup(reply_markup=products_keyboard(products, st["product_id"], st["qty"]))
        return

    if data == "refresh":
        await q.edit_message_reply_markup(reply_markup=products_keyboard(products, st["product_id"], st["qty"]))
        return

    if data == "checkout":
        pid = st["product_id"]
        if not pid:
            await q.edit_message_text("Pilih produk dulu.")
            return

        p = find_prod(pid)
        if not p:
            await q.edit_message_text("Produk tidak ditemukan. Klik refresh.")
            return

        stock = int(p.get("stock", 0) or 0)
        if stock <= 0:
            await q.edit_message_text("Stok habis. Pilih produk lain.")
            return

        qty = int(st["qty"] or 1)
        if qty > stock:
            qty = stock
            st["qty"] = qty

        # checkout backend
        try:
            payload = {"product_id": pid, "qty": qty, "source": "telegram"}
            out = await api_post(CHECKOUT_PATH, payload)
        except httpx.HTTPStatusError as e:
            await q.edit_message_text(f"Checkout gagal (HTTP): {e.response.text}")
            return
        except Exception as e:
            await q.edit_message_text(f"Checkout gagal: {e}")
            return

        order_id = out.get("order_id") or out.get("id")
        amount = int(out.get("amount_idr") or out.get("amount") or 0)

        # countdown (kalau backend kasih expires_at)
        expires_at = out.get("expires_at")
        exp_txt = ""
        if expires_at:
            exp_txt = f"\n⏳ Batas bayar: {expires_at} (±15 menit)"

        caption = (
            f"🧾 *Invoice QRIS*\n\n"
            f"📦 Produk: *{p.get('name', pid)}*\n"
            f"🔢 Qty: *{qty}*\n"
            f"💰 Total bayar: *Rp {fmt_idr(amount)}*\n"
            f"🆔 Order ID: `{order_id}`\n"
            f"{exp_txt}\n\n"
            f"✅ Setelah bayar, tekan *Cek Status*.\n"
            f"(Tanpa PG/webhook, status berubah setelah admin verifikasi.)"
        )

        # kirim QRIS + caption
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(media=QR_IMAGE_URL, caption=caption, parse_mode="Markdown"),
                reply_markup=invoice_keyboard(order_id)
            )
        except Exception:
            # fallback kalau edit media gagal (misal pesan bukan media)
            await q.edit_message_text(caption, parse_mode="Markdown", reply_markup=invoice_keyboard(order_id))
            await q.message.reply_photo(QR_IMAGE_URL)

        # optional: auto-polling status in background per user/order
        context.application.create_task(poll_until_paid(chat_id=q.message.chat_id, order_id=order_id, context=context))
        return

    if data.startswith("status:"):
        order_id = data.split(":", 1)[1]
        await send_status_update(q, order_id)
        return

async def send_status_update(q, order_id: str):
    try:
        j = await get_order(order_id)
    except Exception as e:
        await q.edit_message_caption(caption=f"Gagal cek status: {e}", parse_mode=None)
        return

    # Support beberapa bentuk response
    status = (j.get("status") or j.get("data", {}).get("status") or "").lower()
    voucher = j.get("voucher_code") or j.get("data", {}).get("voucher_code")

    if status == "paid":
        msg = f"✅ *PAID*\nOrder `{order_id}` sudah diverifikasi.\n\n🎟️ *Voucher:*\n`{voucher or '(voucher belum ter-assign)'}`"
    elif status == "cancelled":
        msg = f"❌ *CANCELLED*\nOrder `{order_id}` dibatalkan / expired."
    else:
        msg = f"⏳ *PENDING*\nOrder `{order_id}` belum diverifikasi.\nSilakan tunggu atau coba cek lagi."

    # update caption jika pesan berupa foto
    try:
        await q.edit_message_caption(caption=msg, parse_mode="Markdown", reply_markup=invoice_keyboard(order_id))
    except Exception:
        # fallback ke text
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=invoice_keyboard(order_id))

# =======================
# Auto polling
# =======================
async def poll_until_paid(chat_id: int, order_id: str, context: ContextTypes.DEFAULT_TYPE):
    # polling sampai paid/cancelled atau max 20 menit
    start = now_ts()
    while True:
        if now_ts() - start > 20 * 60:
            return
        await asyncio.sleep(POLL_SECONDS)

        try:
            j = await get_order(order_id)
        except Exception:
            continue

        status = (j.get("status") or j.get("data", {}).get("status") or "").lower()
        voucher = j.get("voucher_code") or j.get("data", {}).get("voucher_code")

        if status == "paid":
            text = (
                f"✅ *Voucher berhasil dikirim!*\n"
                f"Order `{order_id}` sudah *PAID*.\n\n"
                f"🎟️ Voucher:\n`{voucher or '(voucher belum ter-assign)'}`"
            )
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            return

        if status == "cancelled":
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Order `{order_id}` dibatalkan/expired. Silakan buat order baru.",
                parse_mode="Markdown"
            )
            return

# =======================
# Main
# =======================
def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN belum diset")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(cb_handler))

    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
