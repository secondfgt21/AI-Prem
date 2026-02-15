import os
import uuid
import random
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from supabase import create_client, Client

# ======================
# ENV (Render -> Environment Variables)
# ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "ganti-tokenmu")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("WARNING: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY belum di-set / tidak terbaca")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
app = FastAPI()

# ======================
# LIVE VISITOR COUNTER (simple in-memory)
# NOTE: counts are approximate (reset on restart / multi-instance)
# ======================
VISITOR_TTL_SECONDS = 300  # 5 menit dianggap "aktif"
VISITORS_LAST_SEEN: dict[str, float] = {}


def _prune_visitors(now_ts: float) -> None:
    dead = [k for k, t in VISITORS_LAST_SEEN.items() if now_ts - t > VISITOR_TTL_SECONDS]
    for k in dead:
        VISITORS_LAST_SEEN.pop(k, None)


# ======================
# CONFIG PRODUK
# ======================
PRODUCTS = {
    "gemini": {"name": "Gemini AI Pro 1 Tahun", "price": 25_000, "stock_label": "Stok: 12 tersedia"},
    "chatgpt": {"name": "ChatGPT Plus 1 Bulan", "price": 10_000, "stock_label": "Stok: 8 tersedia"},
}

QR_IMAGE_URL = "https://i.postimg.cc/qRkr7LcJ/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-1.png"


def rupiah(n: int) -> str:
    return f"{n:,}"


def require_admin(token: str | None) -> bool:
    return token == ADMIN_TOKEN


# ======================
# VOUCHER CLAIM (tanpa RPC, sesuai schema vouchers kamu)
# vouchers columns: id(bigint), product_id(text), code(text), status(text)
# status voucher yang dipakai:
# - available
# - used
# ======================
def claim_voucher_for_order(order_id: str, product_id: str) -> str | None:
    # ambil 1 voucher available utk product_id
    v = (
        supabase.table("vouchers")
        .select("id,code")
        .eq("product_id", product_id)
        .eq("status", "available")
        .order("id", desc=False)
        .limit(1)
        .execute()
    )

    if not v.data:
        return None

    voucher_id = v.data[0]["id"]
    code = v.data[0]["code"]

    # set voucher jadi used
    supabase.table("vouchers").update({"status": "used"}).eq("id", voucher_id).execute()

    # set order jadi paid + simpan code voucher
    supabase.table("orders").update({"status": "paid", "voucher_code": code}).eq("id", order_id).execute()

    return code


# ======================
# LANDING PAGE (PRO)
# ======================
@app.get("/", response_class=HTMLResponse)
def home():
    # build product cards (ONLY cards, no extra blocks inside grid)
    cards = ""
    for pid, p in PRODUCTS.items():
        cards += f"""
        <div class="p-card">
          <div class="p-top">
            <div class="p-title">{p["name"]}</div>
            <div class="p-sub"><span class="stock skeleton" id="stock-{pid}">Memuat stok…</span></div>
            <span class="hot">TERLARIS</span>
          </div>

          <div class="p-price">Rp {rupiah(int(p["price"]))}</div>

          <div class="p-feats">
            <div class="feat">✅ Aktivasi cepat</div>
            <div class="feat">✅ Garansi sesuai produk</div>
            <div class="feat">✅ Support after sales</div>
          </div>

          <a class="btn primary" href="/checkout/{pid}">Beli Sekarang</a>
          <div class="p-note">Bayar QRIS → verifikasi admin → voucher/akses terkirim</div>
        </div>
        """

    return f"""
    <html>
    <head>
      <title>AI Premium Store</title>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <style>
        :root {{
          --bg: #070b14;
          --bg2: #0b1220;
          --panel: rgba(255,255,255,.06);
          --panel2: rgba(255,255,255,.08);
          --text: rgba(255,255,255,.92);
          --muted: rgba(255,255,255,.70);
          --line: rgba(255,255,255,.12);
          --green: #22c55e;
          --blue: #38bdf8;
          --pink: #fb7185;
          --shadow: 0 18px 40px rgba(0,0,0,.45);
          --radius: 18px;
        }}
        *{{box-sizing:border-box}}
        body {{
          margin:0;
          font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
          background:
            radial-gradient(1200px 520px at 15% -10%, rgba(56,189,248,.22), transparent 58%),
            radial-gradient(1200px 540px at 85% 0%, rgba(34,197,94,.18), transparent 58%),
            radial-gradient(1000px 520px at 50% 120%, rgba(251,113,133,.10), transparent 55%),
            linear-gradient(180deg, var(--bg), var(--bg2));
          color: var(--text);
        }}
        a{{color:inherit}}
        .wrap {{
          max-width: 1100px;
          margin: 0 auto;
          padding: 22px 16px 90px;
        }}

        /* Top bar */
        .topbar {{
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:12px;
          margin-bottom:18px;
        }}
        .brand {{
          display:flex; align-items:center; gap:10px;
          min-width: 0;
        }}
        .logo {{
          width:40px; height:40px; border-radius:14px;
          background: linear-gradient(135deg, rgba(56,189,248,.95), rgba(34,197,94,.95));
          box-shadow: 0 0 0 1px rgba(255,255,255,.08), 0 18px 40px rgba(0,0,0,.45), 0 0 24px rgba(56,189,248,.18);
          flex: 0 0 auto;
        }}
        .brand h1 {{
          font-size:16px; margin:0; letter-spacing:.2px;
        }}
        .brand .tag {{
          font-size:12px; color:var(--muted); margin-top:2px;
          max-width: 46ch;
        }}
        .pill {{
          border:1px solid var(--line);
          background: rgba(255,255,255,.04);
          padding:10px 12px;
          border-radius:999px;
          font-size:12px;
          color:var(--muted);
          white-space:nowrap;
          box-shadow: 0 0 0 1px rgba(255,255,255,.04) inset;
        }}

        /* Hero */
        .hero {{
          display:grid;
          grid-template-columns: 1.1fr .9fr;
          gap: 14px;
          align-items:stretch;
          margin: 8px 0 18px;
        }}
        .hero-left {{
          background: var(--panel);
          border: 1px solid var(--line);
          border-radius: var(--radius);
          padding: 20px;
          box-shadow: var(--shadow);
          position:relative;
          overflow:hidden;
        }}
        .hero-left:before {{
          content:"";
          position:absolute; inset:-2px;
          background:
            radial-gradient(700px 260px at 28% 10%, rgba(56,189,248,.18), transparent 60%),
            radial-gradient(700px 260px at 70% 40%, rgba(34,197,94,.14), transparent 55%);
          pointer-events:none;
          filter: blur(.1px);
        }}
        .kicker {{
          display:inline-flex;
          gap:8px;
          align-items:center;
          font-size:12px;
          color:var(--muted);
          border:1px solid var(--line);
          background: rgba(255,255,255,.03);
          padding:8px 10px;
          border-radius:999px;
        }}
        .title {{
          font-size:32px;
          margin: 12px 0 8px;
          line-height:1.12;
          letter-spacing:.2px;
        }}
        .subtitle {{
          color:var(--muted);
          font-size:14px;
          line-height:1.6;
          max-width: 64ch;
        }}
        .hero-actions {{
          display:flex;
          gap:10px;
          align-items:center;
          margin-top:14px;
          flex-wrap:wrap;
        }}
        .btn {{
          display:inline-flex;
          align-items:center;
          justify-content:center;
          padding: 12px 14px;
          border-radius: 12px;
          text-decoration:none;
          font-weight: 800;
          font-size:14px;
          border:1px solid transparent;
          cursor:pointer;
        }}
        .btn.primary {{
          background: linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.72));
          color:#06210f;
          box-shadow: 0 0 0 1px rgba(34,197,94,.18) inset, 0 14px 28px rgba(34,197,94,.18);
        }}
        .btn.ghost {{
          background: rgba(255,255,255,.04);
          border-color: var(--line);
          color: var(--text);
        }}
        .badges {{
          display:flex; flex-wrap:wrap; gap:8px;
          margin-top:14px;
        }}
        .badge {{
          font-size:12px;
          color: var(--muted);
          border:1px solid var(--line);
          background: rgba(255,255,255,.03);
          padding: 8px 10px;
          border-radius: 999px;
        }}

        .hero-right {{
          background: var(--panel);
          border: 1px solid var(--line);
          border-radius: var(--radius);
          padding: 16px;
          box-shadow: var(--shadow);
        }}
        .steps-title {{font-weight:900; margin:0 0 10px; font-size:14px}}
        .step {{
          display:flex;
          gap:10px;
          padding:10px;
          border-radius:14px;
          background: rgba(255,255,255,.03);
          border: 1px solid rgba(255,255,255,.08);
          margin-bottom:10px;
        }}
        .num {{
          width:28px; height:28px; border-radius:10px;
          display:flex; align-items:center; justify-content:center;
          font-weight:950;
          background: rgba(56,189,248,.18);
          border:1px solid rgba(56,189,248,.25);
          color: rgba(255,255,255,.92);
          flex: 0 0 auto;
        }}
        .step b{{display:block; font-size:13px}}
        .step span{{display:block; font-size:12px; color:var(--muted); margin-top:2px}}

        /* Products */
        .section-title {{
          margin: 18px 0 10px;
          font-size: 14px;
          color: rgba(255,255,255,.86);
          font-weight:950;
          letter-spacing:.2px;
        }}
        .grid {{
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 14px;
          align-items: stretch;
        }}
        .p-card {{
          position:relative;
          background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.05));
          border: 1px solid rgba(255,255,255,.12);
          border-radius: var(--radius);
          padding: 16px;
          box-shadow: var(--shadow);
          transition: transform .2s ease, box-shadow .2s ease;
          overflow:hidden;
          min-height: 260px;
        }}
        .p-card:before {{
          content:"";
          position:absolute; inset:-2px;
          background: radial-gradient(700px 220px at 20% 0%, rgba(56,189,248,.10), transparent 60%);
          pointer-events:none;
        }}
        .p-card:hover{{
          transform: translateY(-4px);
          box-shadow: 0 24px 55px rgba(0,0,0,.55);
        }}
        .p-top {{
          position:relative;
          padding-right: 88px;
        }}
        .p-top .p-title {{
          font-weight: 950;
          font-size: 15px;
          margin-bottom: 4px;
        }}
        .p-top .p-sub {{
          color: var(--muted);
          font-size: 12px;
        }}
        .hot {{
          position:absolute;
          top: 0;
          right: 0;
          font-size: 11px;
          font-weight: 950;
          letter-spacing:.4px;
          padding: 6px 10px;
          border-radius: 999px;
          color: rgba(255,255,255,.92);
          background: rgba(34,197,94,.12);
          border: 1px solid rgba(34,197,94,.28);
          box-shadow: 0 0 18px rgba(34,197,94,.18);
          animation: hotPulse 1.8s ease-in-out infinite;
        }}
        @keyframes hotPulse {{
          0%, 100% {{ transform: translateY(0); box-shadow: 0 0 18px rgba(34,197,94,.18); }}
          50%      {{ transform: translateY(-1px); box-shadow: 0 0 28px rgba(34,197,94,.34); }}
        }}

        .p-price {{
          font-size: 24px;
          font-weight: 950;
          margin: 12px 0 10px;
          letter-spacing: .2px;
        }}
        .p-feats {{
          display:grid;
          gap: 6px;
          margin-bottom: 12px;
        }}
        .feat {{
          font-size: 12px;
          color: rgba(255,255,255,.86);
          background: rgba(255,255,255,.03);
          border: 1px solid rgba(255,255,255,.08);
          padding: 8px 10px;
          border-radius: 12px;
        }}
        .p-note {{
          margin-top: 10px;
          font-size: 12px;
          color: var(--muted);
        }}


        .btn.primary{{ position:relative; overflow:hidden; }}
.btn.primary:after{{
  content:"";
  position:absolute; inset:-2px;
  background: radial-gradient(220px 90px at 30% 0%, rgba(255,255,255,.20), transparent 55%),
              radial-gradient(240px 120px at 70% 0%, rgba(56,189,248,.18), transparent 60%);
  opacity:.0;
  transition: opacity .25s ease;
  pointer-events:none;
}}
.btn.primary:hover:after{{ opacity:.85; }}

        /* Trust + FAQ */
        .trust {{
          margin-top: 16px;
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 12px;
        }}
        .box {{
          background: rgba(255,255,255,.04);
          border:1px solid var(--line);
          border-radius: var(--radius);
          padding: 14px;
          box-shadow: 0 0 0 1px rgba(255,255,255,.03) inset;
        }}
        .box h3 {{
          margin:0 0 8px;
          font-size: 13px;
          font-weight: 950;
        }}
        .box p, .box li {{
          margin:0;
          font-size: 12px;
          color: var(--muted);
          line-height: 1.6;
        }}
        .box ul{{margin:0; padding-left:18px}}

        .footer {{
          margin-top: 18px;
          color: rgba(255,255,255,.55);
          font-size: 12px;
          display:flex;
          justify-content:space-between;
          flex-wrap:wrap;
          gap:10px;
          border-top: 1px solid rgba(255,255,255,.10);
          padding-top: 14px;
        }}
        .admin {{
          opacity:.7;
        }}

        /* Floating WA */
        .wa {{
          position: fixed;
          right: 18px;
          bottom: 18px;
          display:flex;
          align-items:center;
          gap:10px;
          padding: 12px 14px;
          border-radius: 999px;
          background: linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.70));
          color: #06210f;
          font-weight: 950;
          text-decoration:none;
          box-shadow: 0 18px 40px rgba(0,0,0,.45), 0 0 24px rgba(34,197,94,.22);
          border: 1px solid rgba(255,255,255,.14);
          z-index: 9999;
        }}
        .wa small {{
          display:block;
          font-weight: 800;
          opacity:.85;
        }}


        /* Effects */
        html{{scroll-behavior:smooth;}}
        .glass{{
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
        }}
        .p-card, .hero-left, .hero-right, .box{{
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
        }}
        .p-card{{ position:relative; overflow:hidden; }}
        .p-card:before{{
          content:"";
          position:absolute; inset:-2px;
          background: radial-gradient(420px 180px at 20% 0%, rgba(56,189,248,.18), transparent 60%),
                      radial-gradient(420px 180px at 80% 0%, rgba(34,197,94,.14), transparent 60%);
          opacity:.7;
          pointer-events:none;
          transition: opacity .25s ease, transform .25s ease;
        }}
        .p-card:hover:before{{ opacity:1; transform: scale(1.02); }}
        .p-card:hover{{ box-shadow: 0 22px 60px rgba(0,0,0,.55); }}

        /* Shimmer / skeleton */
        .skeleton{{
          position:relative;
          display:inline-block;
          min-width: 110px;
          height: 16px;
          border-radius: 999px;
          background: rgba(255,255,255,.08);
          color: rgba(255,255,255,.0);
          overflow:hidden;
          vertical-align: middle;
        }}
        .skeleton:after{{
          content:"";
          position:absolute; top:0; left:-60%;
          height:100%; width:60%;
          background: linear-gradient(90deg, transparent, rgba(255,255,255,.18), transparent);
          animation: shimmer 1.1s infinite;
        }}
        @keyframes shimmer{{ to{{ left:120%; }} }}

        /* TERLARIS badge animation */
        .hot{{
          animation: hotPulse 1.5s ease-in-out infinite;
          box-shadow: 0 10px 25px rgba(34,197,94,.14);
        }}
        @keyframes hotPulse{{
          0%,100%{{ transform: translateY(0) scale(1); filter: drop-shadow(0 0 0 rgba(34,197,94,0)); }}
          50%{{ transform: translateY(-1px) scale(1.03); filter: drop-shadow(0 0 10px rgba(34,197,94,.35)); }}
        }}

        /* Mobile */
        @media (max-width: 860px) {{
          .hero {{grid-template-columns: 1fr;}}
          .title {{font-size: 28px;}}
          .pill {{display:none;}}
        }}
      </style>
    </head>

    <body>
      
      <div id="toast" style="display:none; position:fixed; top:16px; left:50%; transform:translateX(-50%); z-index:9999;
        background:rgba(34,197,94,.18); border:1px solid rgba(34,197,94,.30); color:#d1fae5;
        padding:12px 14px; border-radius:14px; box-shadow:0 18px 40px rgba(0,0,0,.45); backdrop-filter: blur(10px);">
        ✅ Voucher berhasil dikirim
      </div>

      <div id="success" style="display:none; position:fixed; inset:0; z-index:9998; background:rgba(2,6,23,.55); backdrop-filter: blur(6px);
        align-items:center; justify-content:center;">
        <div style="background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.12); border-radius:22px; padding:22px 20px; width:min(420px,92vw);
          text-align:center; box-shadow:0 30px 80px rgba(0,0,0,.55);">
          <div style="width:64px;height:64px;border-radius:20px;margin:0 auto 10px; display:flex;align-items:center;justify-content:center;
            background:rgba(34,197,94,.18); border:1px solid rgba(34,197,94,.35); font-size:34px;">✅</div>
          <div style="font-weight:900; font-size:18px;">Berhasil diverifikasi</div>
          <div style="opacity:.75; margin-top:6px; font-size:13px;">Voucher kamu sudah siap. Silakan salin kodenya.</div>
        </div>
      </div>
<div class="wrap">
        <div class="topbar">
          <div class="brand">
            <div class="logo"></div>
            <div style="min-width:0">
              <h1>AI Premium Store</h1>
              <div class="tag">Akses AI premium • pembayaran QRIS • proses cepat</div>
            </div>
          </div>
          <div class="pill">🛡️ Aman • Admin verifikasi • Voucher otomatis • 👀 <span id='vc' class='skeleton'>...</span> online</div>
        </div>

        <div class="hero">
          <div class="hero-left">
            <div class="kicker">⚡ Fast checkout <span style="opacity:.5">•</span> 📌 Harga jelas <span style="opacity:.5">•</span> ✅ Auto voucher</div>
            <div class="title">Beli akses AI premium dengan proses rapi & cepat.</div>
            <div class="subtitle">
              Pilih produk → bayar QRIS → admin verifikasi → sistem otomatis kirim voucher/akses.
              Cocok untuk kerja, kuliah, riset, coding, dan konten.
            </div>

            <div class="hero-actions">
              <a class="btn primary" href="#produk">Lihat Produk</a>
              <a class="btn ghost" href="#cara">Cara Beli</a>
            </div>

            <div class="badges">
              <div class="badge">✅ Pembayaran QRIS</div>
              <div class="badge">✅ Status otomatis</div>
              <div class="badge">✅ Voucher 1x klik</div>
              <div class="badge">✅ Support after sales</div>
              <div class="badge">✨ Dark neon glow</div>
            </div>
          </div>

          <div class="hero-right" id="cara">
            <div class="steps-title">Cara beli (3 langkah)</div>
            <div class="step">
              <div class="num">1</div>
              <div><b>Pilih produk</b><span>Klik “Beli Sekarang” di produk yang kamu mau.</span></div>
            </div>
            <div class="step">
              <div class="num">2</div>
              <div><b>Bayar QRIS</b><span>Transfer sesuai nominal (termasuk kode unik).</span></div>
            </div>
            <div class="step">
              <div class="num">3</div>
              <div><b>Verifikasi & voucher</b><span>Admin verifikasi → voucher tampil otomatis.</span></div>
            </div>

            <div style="margin-top:10px; font-size:12px; color:var(--muted);">
              Tip: setelah bayar, buka halaman status order untuk auto-redirect ke voucher.
            </div>
          </div>
        </div>

        <div class="section-title" id="produk">Produk tersedia</div>
        <div class="grid">
          {cards}
        </div>

        <div class="trust">
          <div class="box">
            <h3>Kenapa beli di sini?</h3>
            <ul>
              <li>Proses jelas: checkout → status → voucher</li>
              <li>Nominal unik memudahkan verifikasi</li>
              <li>Voucher tersimpan di database (lebih rapi)</li>
            </ul>
          </div>
          <div class="box">
            <h3>FAQ singkat</h3>
            <p><b>Q:</b> Setelah bayar, berapa lama?<br/>
               <b>A:</b> Tergantung antrian admin. Status akan berubah otomatis setelah diverifikasi.</p>
            <p style="margin-top:8px;"><b>Q:</b> Voucher habis?<br/>
               <b>A:</b> Sistem akan menampilkan info stok habis. Admin bisa tambah stok kapan saja.</p>
          </div>
        </div>

        <div class="footer">
          <div>© {datetime.utcnow().year} AI Premium Store</div>
          <div class="admin">Admin panel: <code>/admin?token=TOKEN</code></div>
        </div>
      </div>

      <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noopener">
        💬 <span>Chat Admin<br><small>Fast response</small></span>
      </a>
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

    # insert order
    ins = supabase.table("orders").insert(
        {
            "id": order_id,
            "product_id": product_id,
            "amount_idr": total,
            "status": "pending",
            "created_at": now,
            "voucher_code": None,
        }
    ).execute()

    if not ins.data:
        return HTMLResponse("<h3>Gagal membuat order (cek RLS / key / schema orders)</h3>", status_code=500)

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
        
        <script>
          async function refreshStock(){{
            try{{
              const r = await fetch('/api/stock', {{cache:'no-store'}});
              const j = await r.json();
              if(!j.ok) return;
              const stock = j.stock || {{}};
              for(const pid in stock){{
                const el = document.getElementById('stock-' + pid);
                if(!el) continue;
                el.classList.remove('skeleton');
                el.style.color = 'rgba(255,255,255,.70)';
                el.textContent = 'Stok: ' + stock[pid] + ' tersedia';
              }}
            }}catch(e){{}}
          }}

          async function pingVisit(){{
            try{{
              const vid = (document.cookie.match(/(?:^|; )vid=([^;]+)/)||[])[1];
              const r = await fetch('/api/visit' + (vid ? ('?vid=' + encodeURIComponent(vid)) : ''), {{cache:'no-store'}});
              const j = await r.json();
              if(!j.ok) return;
              const vc = document.getElementById('vc');
              if(vc){{
                vc.classList.remove('skeleton');
                vc.style.color = 'rgba(255,255,255,.78)';
                vc.textContent = j.active;
              }}
            }}catch(e){{}}
          }}

          refreshStock();
          pingVisit();
          setInterval(refreshStock, 8000);
          setInterval(pingVisit, 15000);
        </script>
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

    # hitung countdown estimasi verifikasi (default 15 menit dari created_at)
    VERIFY_WINDOW_SEC = 15 * 60
    created_at = order.get("created_at")
    seconds_left = VERIFY_WINDOW_SEC
    try:
        if created_at:
            s = str(created_at).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            elapsed = (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds()
            seconds_left = max(0, int(VERIFY_WINDOW_SEC - elapsed))
    except Exception:
        seconds_left = VERIFY_WINDOW_SEC

    # kalau sudah paid, langsung lempar ke voucher (biar buyer gak nyasar)
    if st == "paid":
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=302)

    badge = "#f59e0b" if st == "pending" else "#22c55e" if st == "paid" else "#ef4444"

    return f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Status Order</title>
      <style>
        :root {{
          --bg: #0b1220;
          --panel: rgba(255,255,255,.06);
          --text: rgba(255,255,255,.92);
          --muted: rgba(255,255,255,.70);
          --line: rgba(255,255,255,.12);
          --green:#22c55e;
          --shadow: 0 18px 40px rgba(0,0,0,.45);
        }}
        body{{
          margin:0;
          font-family:Arial;
          background:
            radial-gradient(900px 420px at 20% -10%, rgba(56,189,248,.22), transparent 60%),
            radial-gradient(900px 420px at 80% 0%, rgba(34,197,94,.18), transparent 55%),
            var(--bg);
          color:var(--text);
          text-align:center;
          padding:30px 14px;
        }}
        .box{{
          background: var(--panel);
          border:1px solid var(--line);
          padding:22px;
          border-radius:16px;
          display:inline-block;
          max-width:460px;
          width:100%;
          box-shadow:var(--shadow);
        }}
        .badge{{
          display:inline-block;
          padding:7px 12px;
          border-radius:999px;
          background:{badge};
          font-weight:900;
          letter-spacing:.3px;
        }}
        .muted{{opacity:.8;font-size:13px;line-height:1.55}}
        .row{{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-top:10px}}
        .kpi{{flex:1;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.10);border-radius:14px;padding:10px}}
        .kpi b{{display:block;font-size:12px;opacity:.85}}
        .kpi span{{display:block;font-size:16px;font-weight:950;margin-top:4px}}
        .spin{{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-left:6px}}
        @keyframes spin{{to{{transform:rotate(360deg)}}}}

        .wa {{
          position: fixed;
          right: 18px;
          bottom: 18px;
          display:flex;
          align-items:center;
          gap:10px;
          padding: 12px 14px;
          border-radius: 999px;
          background: linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.70));
          color: #06210f;
          font-weight: 950;
          text-decoration:none;
          box-shadow: 0 18px 40px rgba(0,0,0,.45), 0 0 24px rgba(34,197,94,.22);
          border: 1px solid rgba(255,255,255,.14);
          z-index: 9999;
        }}
      </style>
    </head>
    <body>
      <div class="box">
        <h2 style="margin:0 0 8px">Status Order</h2>
        <div class="muted">Produk: <b>{pid}</b></div>
        <div class="muted">Nominal: <b>Rp {rupiah(amount)}</b></div>

        <div style="margin-top:12px;">
          Status: <span id="st" class="badge">{st.upper()}</span>
          <span class="spin" title="mengecek otomatis"></span>
        </div>

        <div class="row">
          <div class="kpi">
            <b>Countdown verifikasi</b>
            <span id="cd">--:--</span>
          </div>
          <div class="kpi">
            <b>Auto cek</b>
            <span id="poll">2.5s</span>
          </div>
        </div>

        <div class="muted" style="margin-top:12px;">
          Halaman ini akan otomatis redirect ke voucher setelah admin verifikasi.
        </div>

        <div class="muted" id="hint" style="margin-top:10px;">
          Jika sudah bayar tapi lama, klik tombol WhatsApp untuk konfirmasi.
        </div>
      </div>

      <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noopener">💬 Chat Admin</a>

      <script>
        let pollEveryMs = 2500;
        let nextPoll = pollEveryMs;
        let secondsLeft = {seconds_left};

        function fmt(sec) {{
          sec = Math.max(0, sec|0);
          const m = String(Math.floor(sec/60)).padStart(2,'0');
          const s = String(sec%60).padStart(2,'0');
          return m+":"+s;
        }}

        function tick() {{
          // countdown verifikasi (estimasi)
          if (secondsLeft > 0) secondsLeft -= 1;
          document.getElementById("cd").textContent = fmt(secondsLeft);

          // countdown poll
          nextPoll -= 1000;
          if (nextPoll < 0) nextPoll = 0;
          document.getElementById("poll").textContent = (nextPoll/1000).toFixed(1) + "s";
        }}
        setInterval(tick, 1000);
        document.getElementById("cd").textContent = fmt(secondsLeft);

        async function poll() {{
          nextPoll = pollEveryMs;
          try {{
            const r = await fetch("/api/order/{order_id}", {{cache:"no-store"}});
            if (!r.ok) return;
            const j = await r.json();
            if (!j.ok) return;

            if (j.status === "paid") {{
              window.location.href = "/voucher/{order_id}?sent=1";
              return;
            }}
          }} catch (e) {{}}
        }}
        setInterval(poll, pollEveryMs);
        poll();
      </script>
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

    res = supabase.table("orders").select("id,product_id,amount_idr,status,created_at,voucher_code").order("created_at", desc=True).limit(50).execute()
    rows = res.data or []

    items = ""
    for o in rows:
        oid = o.get("id")
        st = o.get("status", "pending")
        pid = o.get("product_id", "")
        amt = int(o.get("amount_idr") or 0)
        created = o.get("created_at", "")
        vcode = o.get("voucher_code")

        if st == "pending":
            action = f"""
            <form method="post" action="/admin/verify/{oid}?token={token}" style="margin:0;">
              <button class="vbtn" type="submit">VERIFIKASI + KIRIM VOUCHER</button>
            </form>
            """
        else:
            label = f"Voucher: {vcode}" if vcode else "Voucher: (habis / belum ada)"
            action = f"""
            <a class="lbtn" href="/voucher/{oid}">Buka Voucher</a>
            <div class="muted" style="margin-top:6px;">{label}</div>
            """

        items += f"""
        <div class="row">
          <div class="col">
            <div><b>{pid}</b> — Rp {rupiah(amt)}</div>
            <div class="muted">ID: {oid}</div>
            <div class="muted">{created}</div>
            <div class="muted">Status: {st}</div>
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
        .lbtn{{display:inline-block;background:#334155;color:white;padding:10px 12px;border-radius:10px;text-decoration:none;font-weight:bold}}
      </style>
    </head>
    <body>
      <div class="box">
        <h2>Admin Panel</h2>
        <div style="opacity:.75;margin-bottom:12px;">
          Klik tombol untuk verifikasi + otomatis assign voucher lalu redirect ke halaman voucher.
        </div>
        {items if items else "<div style='opacity:.7'>Belum ada order</div>"}
      </div>
    </body>
    </html>
    """


@app.post("/admin/verify/{order_id}")
def admin_verify(order_id: str, token: str | None = None):
    if not require_admin(token):
        return PlainTextResponse("Unauthorized", status_code=401)

    # Ambil order
    res = supabase.table("orders").select("id,product_id,status").eq("id", order_id).limit(1).execute()
    if not res.data:
        return PlainTextResponse("Order not found", status_code=404)

    order = res.data[0]
    pid = order.get("product_id")
    st  = order.get("status")

    print("[ADMIN_VERIFY] before:", order_id, st, pid)

    # Kalau sudah paid -> langsung ke voucher
    if st == "paid":
        return RedirectResponse(url=f"/voucher/{order_id}?sent=1", status_code=303)

    # 1) UPDATE ke PAID
    upd = supabase.table("orders").update({"status": "paid"}).eq("id", order_id).execute()
    print("[ADMIN_VERIFY] update_resp:", upd.data)

    # 2) Re-check status biar pasti
    chk = supabase.table("orders").select("status").eq("id", order_id).limit(1).execute()
    new_st = (chk.data[0]["status"] if chk.data else None)
    print("[ADMIN_VERIFY] after:", order_id, new_st)

    # Kalau masih bukan paid, berarti update gagal (biasanya RLS / key)
    if new_st != "paid":
        return HTMLResponse(
            f"<h3>Gagal verifikasi</h3><p>Status masih: {new_st}</p><p>Cek Render logs untuk detail.</p>",
            status_code=500
        )

    # 3) Claim voucher (sesuaikan struktur table vouchers kamu: pakai kolom status)
    rpc = supabase.rpc("claim_voucher", {"p_order_id": order_id, "p_product_id": pid}).execute()
    print("[ADMIN_VERIFY] claim_voucher rpc:", rpc.data)

    return RedirectResponse(url=f"/voucher/{order_id}?sent=1", status_code=303)

# ======================
# VOUCHER PAGE (buyer lihat kode)
# ======================
@app.get("/voucher/{order_id}", response_class=HTMLResponse)
def voucher(order_id: str, sent: str | None = None):
    res = supabase.table("orders").select("status,product_id,voucher_code").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    if order.get("status") != "paid":
        return HTMLResponse("<h3>Belum diverifikasi admin</h3><p>Silakan tunggu.</p>", status_code=400)

    code = order.get("voucher_code")

    if not code:
        return HTMLResponse("""
        <html><body style="font-family:Arial;background:#0f172a;color:white;text-align:center;padding:40px">
          <h2>Voucher</h2>
          <p>Status: PAID ✅</p>
          <p style="opacity:.8">Maaf, stok voucher untuk produk ini sedang habis.</p>
        </body></html>
        """)

    return HTMLResponse(f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <style>
        body{{font-family:Arial;background:#0f172a;color:white;text-align:center;padding:30px}}
        .box{{background:#1e293b;padding:22px;border-radius:16px;display:inline-block;max-width:420px;width:100%}}
        .code{{margin:14px auto;background:#0b1220;padding:14px;border-radius:12px;font-size:18px;font-weight:bold;word-break:break-all}}
        .btn{{display:inline-block;margin-top:12px;padding:10px 14px;border-radius:10px;text-decoration:none;background:#22c55e;color:white;font-weight:bold;border:none}}
        .muted{{opacity:.75;font-size:13px}}
      </style>
    </head>
    <body>
      <div class="box">
        <h2>Voucher</h2>
        <div class="muted">Status: PAID ✅</div>
        <div class="muted">Produk: <b>{order.get("product_id")}</b></div>

        <div class="code" id="vcode">{code}</div>

        <button class="btn" onclick="navigator.clipboard.writeText('{code}')">Salin Voucher</button>

        <div class="muted" style="margin-top:12px;">
          Simpan kode ini. Jangan dibagikan ke orang lain.
        </div>
      </div>
      <a href="https://wa.me/6281317391284" target="_blank"
style="
position:fixed;
bottom:18px;
right:18px;
background:#22c55e;
color:white;
padding:14px 16px;
border-radius:50px;
font-weight:bold;
text-decoration:none;
box-shadow:0 8px 20px rgba(0,0,0,.3);
z-index:999;
">
💬 Chat Admin
</a>
    
      <script>
        (function(){{
          const params = new URLSearchParams(window.location.search);
          const sent = params.get('sent');
          if(sent === '1'){{
            const toast = document.getElementById('toast');
            const success = document.getElementById('success');
            if(toast){{ toast.style.display = 'block'; setTimeout(()=>toast.style.display='none', 3200); }}
            if(success){{
              success.style.display = 'flex';
              setTimeout(()=>{{ success.style.display='none'; }}, 1400);
            }}
          }}
        }})();
      </script>
    </body>
    </html>
    """)
