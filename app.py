import os
import uuid
import random
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from supabase import create_client, Client

# ======================
# ENV (Render -> Environment Variables)
# ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "ganti-tokenmu")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("WARNING: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY belum di-set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
app = FastAPI()

# ======================
# CONFIG PRODUK
# ======================
PRODUCTS = {
    "gemini": {"name": "Gemini AI Pro 1 Tahun", "price": 25_000, "stock_label": "Stok: 12 tersedia"},
    "chatgpt": {"name": "ChatGPT Plus 1 Bulan", "price": 10_000, "stock_label": "Stok: 8 tersedia"},
}

# pakai direct image link yang tampil sebagai gambar
QR_IMAGE_URL = "https://i.postimg.cc/qRkr7LcJ/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-1.png"

def rupiah(n: int) -> str:
    return f"{n:,}"

def require_admin(token: str | None) -> bool:
    return token == ADMIN_TOKEN

# ======================
# LANDING PAGE
# ======================
@app.get("/", response_class=HTMLResponse)
def home():
    cards = ""
    for pid, p in PRODUCTS.items():
        cards += f"""
        <div class="card">
            <h1>{p["name"]}</h1>
            <h2>Rp {rupiah(p["price"])}</h2>
            <p style="opacity:.8">{p["stock_label"]}</p>
            <a href="/checkout/{pid}" class="btn">Beli Sekarang</a>
        </div>
        """

    return f"""
    <html>
    <head>
        <title>AI Premium Store</title>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <style>
            body {{
                font-family: Arial;
                background:#0f172a;
                color:white;
                text-align:center;
                padding:30px;
            }}
            .wrap {{
                display:grid;
                gap:18px;
                max-width:820px;
                margin:0 auto;
            }}
            .card {{
                background:#1e293b;
                padding:22px;
                border-radius:15px;
                box-shadow:0 10px 25px rgba(0,0,0,0.35);
            }}
            .btn {{
                display:inline-block;
                background:#22c55e;
                padding:12px 18px;
                color:white;
                font-size:16px;
                border-radius:10px;
                text-decoration:none;
                margin-top:10px;
            }}
            .topnote {{
                opacity:.8;
                margin-bottom:20px;
                font-size:14px;
            }}
        </style>
    </head>
    <body>
        <h2>AI Premium Store</h2>
        <div class="topnote">Pilih produk → bayar QRIS → tunggu verifikasi</div>
        <div class="wrap">
            {cards}
        </div>
        <div style="opacity:.55;font-size:12px;margin-top:20px;">
            Admin panel: /admin?token=TOKEN
        </div>
    </body>
    </html>
    """

# ======================
# CHECKOUT (buat order pending + nominal unik)
# ======================
@app.get("/checkout/{product_id}", response_class=HTMLResponse)
def checkout(product_id: str):
    if product_id not in PRODUCTS:
        return HTMLResponse("<h3>Produk tidak ditemukan</h3>", status_code=404)

    base_price = int(PRODUCTS[product_id]["price"])
    unique_code = random.randint(101, 999)
    total = base_price + unique_code

    order_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    supabase.table("orders").insert({
        "id": order_id,
        "product_id": product_id,
        "amount_idr": total,
        "status": "pending",
        "created_at": now,
        "voucher_code": None
    }).execute()

    return f"""
    <html>
    <head>
        <title>Pembayaran QRIS</title>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <style>
            body {{
                font-family: Arial;
                text-align:center;
                background:#0f172a;
                color:white;
                padding:30px 14px;
            }}
            .box {{
                background:#1e293b;
                padding:22px;
                border-radius:16px;
                display:inline-block;
                max-width:360px;
                width:100%;
                box-shadow:0 10px 25px rgba(0,0,0,0.35);
            }}
            img {{
                margin-top:14px;
                border-radius:12px;
                background:white;
                padding:10px;
                width:100%;
                max-width:260px;
                height:auto;
            }}
            .total {{
                font-size:28px;
                font-weight:bold;
                color:#22c55e;
                margin:8px 0 6px;
            }}
            .muted {{ opacity:.75; font-size:13px; }}
            .oid {{
                margin-top:10px;
                padding:10px;
                border:1px dashed rgba(255,255,255,.25);
                border-radius:12px;
                font-size:12px;
                opacity:.8;
                word-break:break-all;
            }}
            .btn {{
                display:inline-block;
                margin-top:14px;
                padding:10px 14px;
                border-radius:10px;
                text-decoration:none;
                background:#334155;
                color:white;
            }}
        </style>
    </head>
    <body>
        <div class="box">
            <h2>Pembayaran QRIS</h2>
            <div class="muted">Produk: <b>{PRODUCTS[product_id]["name"]}</b></div>

            <div style="margin-top:12px;">Total transfer:</div>
            <div class="total">Rp {rupiah(total)}</div>
            <div class="muted">termasuk kode unik untuk verifikasi</div>

            <div style="margin-top:12px;">Scan QRIS:</div>
            <img src="{QR_IMAGE_URL}" alt="QRIS" />

            <div class="oid">
                Order ID:<br><b>{order_id}</b>
            </div>

            <a class="btn" href="/status/{order_id}">Cek Status</a>
            <div class="muted" style="margin-top:10px;">
                Setelah bayar, tunggu admin verifikasi.
            </div>
        </div>
    </body>
    </html>
    """

# ======================
# STATUS ORDER
# ======================
@app.get("/status/{order_id}", response_class=HTMLResponse)
def status(order_id: str):
    res = supabase.table("orders").select("*").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    st = order.get("status", "pending")
    amount = int(order.get("amount_idr", 0))
    pid = order.get("product_id", "")

    badge = "#f59e0b" if st == "pending" else "#22c55e" if st == "paid" else "#ef4444"
    voucher_btn = f'<a class="btn" href="/voucher/{order_id}">Lihat Voucher</a>' if st == "paid" else ""

    return f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <style>
        body{{font-family:Arial;background:#0f172a;color:white;text-align:center;padding:30px}}
        .box{{background:#1e293b;padding:22px;border-radius:16px;display:inline-block;max-width:360px;width:100%}}
        .badge{{display:inline-block;padding:6px 10px;border-radius:999px;background:{badge};font-weight:bold}}
        .btn{{display:inline-block;margin-top:14px;padding:10px 14px;border-radius:10px;text-decoration:none;background:#334155;color:white}}
        .muted{{opacity:.75;font-size:13px}}
      </style>
    </head>
    <body>
      <div class="box">
        <h2>Status Order</h2>
        <div class="muted">Produk: <b>{pid}</b></div>
        <div class="muted">Nominal: <b>Rp {rupiah(amount)}</b></div>
        <div style="margin-top:12px;">Status: <span class="badge">{st.upper()}</span></div>
        {voucher_btn}
        <div class="muted" style="margin-top:12px;">Refresh halaman ini setelah admin verifikasi.</div>
      </div>
    </body>
    </html>
    """

# ======================
# ADMIN PANEL
# ======================
@app.get("/admin", response_class=HTMLResponse)
def admin(token: str | None = None):
    if not require_admin(token):
        return HTMLResponse("<h3>Unauthorized</h3>", status_code=401)

    res = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
    rows = res.data or []

    items = ""
    for o in rows:
        oid = o.get("id")
        st = o.get("status", "pending")
        pid = o.get("product_id", "")
        amt = int(o.get("amount_idr") or 0)
        created = o.get("created_at", "")

        action = f"""
        <form method="post" action="/admin/verify/{oid}?token={token}" style="margin:0;">
          <button class="vbtn" type="submit">VERIFIKASI</button>
        </form>
        """ if st == "pending" else f"<div class='done'>{st}</div>"

        items += f"""
        <div class="row">
          <div class="col">
            <div><b>{pid}</b> — Rp {rupiah(amt)}</div>
            <div class="muted">ID: {oid}</div>
            <div class="muted">{created}</div>
          </div>
          <div class="act">{action}</div>
        </div>
        """

    return f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <style>
        body{{font-family:Arial;background:#0f172a;color:white;padding:20px}}
        .box{{max-width:900px;margin:0 auto}}
        .row{{background:#1e293b;padding:14px;border-radius:14px;margin-bottom:10px;display:flex;gap:12px;align-items:center;justify-content:space-between}}
        .muted{{opacity:.7;font-size:12px;word-break:break-all}}
        .vbtn{{background:#22c55e;border:none;color:white;padding:10px 12px;border-radius:10px;cursor:pointer;font-weight:bold}}
        .done{{opacity:.8}}
      </style>
    </head>
    <body>
      <div class="box">
        <h2>Admin Panel</h2>
        <div style="opacity:.75;margin-bottom:12px;">Klik VERIFIKASI untuk mengubah order menjadi PAID.</div>
        {items if items else "<div style='opacity:.7'>Belum ada order</div>"}
      </div>
    </body>
    </html>
    """

@app.post("/admin/verify/{order_id}")
def admin_verify(order_id: str, token: str | None = None):
    if not require_admin(token):
        return PlainTextResponse("Unauthorized", status_code=401)

    supabase.table("orders").update({"status": "paid"}).eq("id", order_id).execute()
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@app.get("/voucher/{order_id}", response_class=HTMLResponse)
def voucher(order_id: str):
    res = supabase.table("orders").select("*").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    if order.get("status") != "paid":
        return HTMLResponse("<h3>Belum diverifikasi admin</h3><p>Silakan tunggu.</p>", status_code=400)

    return """
    <html><body style="font-family:Arial;text-align:center;padding:40px;">
      <h2>Voucher</h2>
      <p>Status: PAID ✅</p>
      <p>(Step 2) Nanti voucher otomatis tampil di sini.</p>
    </body></html>
    """
