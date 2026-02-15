import os
import uuid
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple
from string import Template

from fastapi import FastAPI, Request, Query, Path, Body
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

# ======================
# ENV (Render -> Environment Variables)
# ======================
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    print("WARNING: SUPABASE env belum di-set, checkout akan pakai memory fallback")
# ===== CORS FIX (WAJIB) =====
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-prem.onrender.com",
        "https://ai-prem-12f0d56.ingress-earth.ewp.live",
    ],
    allow_origin_regex=r"https://.*\.ewp\.live",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# CONFIG
# ======================
PRODUCTS = {
    "gemini": {"name": "Gemini AI Pro 1 Tahun", "price": 25_000},
    "chatgpt": {"name": "ChatGPT Plus 1 Bulan", "price": 10_000},
}

QR_IMAGE_URL = os.getenv(
    "QR_IMAGE_URL",
    "https://i.postimg.cc/qRkr7LcJ/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-1.png",
)

ORDER_TTL_MINUTES = 15  # auto cancel kalau belum bayar
RATE_WINDOW_SEC = 5 * 60
RATE_MAX_CHECKOUT = 6  # anti spam: max 6 order baru / 5 menit / IP

# In-memory anti-spam (cukup untuk Render single instance)
_IP_BUCKET: Dict[str, list] = {}
_VISITOR_SESS: Dict[str, float] = {}
_VISITOR_BASE = 120  # tampilan marketing saja (bukan analytics akurat)

# ======================
# Helpers
# ======================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def rupiah(n: int) -> str:
    return f"{n:,}"

def require_admin(token: Optional[str]) -> bool:
    return token == ADMIN_TOKEN

def _client_ip(request: Request) -> str:
    # Render biasanya set X-Forwarded-For
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _rate_limit_checkout(ip: str) -> bool:
    """Return True kalau boleh lanjut, False kalau kena limit."""
    t = time.time()
    bucket = _IP_BUCKET.get(ip, [])
    bucket = [x for x in bucket if (t - x) < RATE_WINDOW_SEC]
    if len(bucket) >= RATE_MAX_CHECKOUT:
        _IP_BUCKET[ip] = bucket
        return False
    bucket.append(t)
    _IP_BUCKET[ip] = bucket
    return True

def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Supabase biasanya ISO8601
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _ensure_not_expired(order: dict) -> Tuple[dict, bool]:
    """
    Kalau pending & lebih dari TTL -> auto-cancel di DB.
    return (order_after, expired_bool)
    """
    st = (order.get("status") or "pending").lower()
    if st != "pending":
        return order, False

    created = _parse_dt(order.get("created_at", "")) or now_utc()
    if now_utc() - created > timedelta(minutes=ORDER_TTL_MINUTES):
        try:
            supabase.table("orders").update({"status": "cancelled"}).eq("id", order["id"]).execute()
        except Exception as e:
            print("[AUTO_CANCEL] err:", e)
        order["status"] = "cancelled"
        return order, True
    return order, False

def get_stock_map() -> Dict[str, int]:
    """
    Hitung stok realtime dari table vouchers: status='available'
    """
    stock = {pid: 0 for pid in PRODUCTS.keys()}
    try:
        res = supabase.table("vouchers").select("product_id").eq("status", "available").execute()
        for row in (res.data or []):
            pid = row.get("product_id")
            if pid in stock:
                stock[pid] += 1
    except Exception as e:
        print("[STOCK] err:", e)
    return stock

def claim_voucher_for_order(order_id: str, product_id: str, qty: int) -> Optional[str]:
    """Ambil voucher sebanyak qty untuk product_id.
    Simpan semua kode voucher ke orders.voucher_code dipisah newline.
    """
    qty = int(qty or 1)
    if qty < 1:
        qty = 1

    v = (
        supabase.table("vouchers")
        .select("id,code")
        .eq("product_id", product_id)
        .eq("status", "available")
        .order("id", desc=False)
        .limit(qty)
        .execute()
    )

    if not v.data or len(v.data) < qty:
        return None

    ids = [row["id"] for row in v.data]
    codes = [row["code"] for row in v.data]
    codes_joined = "\n".join(codes)

    # set voucher jadi used (batch)
    supabase.table("vouchers").update({"status": "used"}).in_("id", ids).execute()

    # set order jadi paid + simpan kode voucher
    supabase.table("orders").update({"status": "paid", "voucher_code": codes_joined}).eq("id", order_id).execute()

    return codes_joined


# ======================
# Templates
# ======================
HOME_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AI Premium Store</title>
  <style>
    :root{
      --bg:#070c18;
      --glass:rgba(255,255,255,.06);
      --glass2:rgba(255,255,255,.09);
      --line:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92);
      --muted:rgba(255,255,255,.70);
      --g1:#22c55e;
      --g2:#38bdf8;
      --shadow:0 18px 42px rgba(0,0,0,.45);
      --r:20px;
    }
    *{box-sizing:border-box}
    html{scroll-behavior:smooth}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(900px 520px at 20% -10%, rgba(56,189,248,.28), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(34,197,94,.22), transparent 55%),
        radial-gradient(700px 700px at 50% 120%, rgba(34,197,94,.10), transparent 60%),
        var(--bg);
      min-height:100vh;
    }
    a{color:inherit}
    .wrap{max-width:1100px;margin:0 auto;padding:22px 16px 90px}
    .top{
      display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px;
    }
    .brand{display:flex;align-items:center;gap:12px}
    .logo{
      width:40px;height:40px;border-radius:14px;
      background:linear-gradient(135deg, rgba(56,189,248,.95), rgba(34,197,94,.95));
      box-shadow:0 12px 28px rgba(0,0,0,.35);
    }
    .brand h1{margin:0;font-size:16px;letter-spacing:.2px}
    .tag{margin-top:2px;font-size:12px;color:var(--muted)}
    .pill{
      border:1px solid var(--line);
      background:rgba(255,255,255,.04);
      padding:10px 12px;border-radius:999px;
      font-size:12px;color:var(--muted);
      backdrop-filter: blur(10px);
      white-space:nowrap;
    }

    .hero{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:stretch;margin:8px 0 18px}
    .card{
      background:var(--glass);
      border:1px solid var(--line);
      border-radius:var(--r);
      box-shadow:var(--shadow);
      backdrop-filter: blur(12px);
      position:relative;
      overflow:hidden;
    }
    .card:before{
      content:"";
      position:absolute;inset:-2px;
      background: radial-gradient(700px 260px at 30% 10%, rgba(56,189,248,.18), transparent 60%);
      pointer-events:none;
    }
    .heroL{padding:22px}
    .kicker{
      display:inline-flex;gap:8px;align-items:center;
      font-size:12px;color:var(--muted);
      border:1px solid var(--line);
      background:rgba(255,255,255,.03);
      padding:8px 10px;border-radius:999px;
    }
    .title{font-size:30px;line-height:1.15;margin:12px 0 8px}
    .sub{font-size:14px;line-height:1.55;color:var(--muted);max-width:62ch}
    .actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
    .btn{
      display:inline-flex;align-items:center;justify-content:center;gap:8px;
      padding:12px 14px;border-radius:14px;text-decoration:none;
      font-weight:900;font-size:14px;
      border:1px solid transparent;
      transition: transform .18s ease, box-shadow .18s ease, filter .18s ease;
      position:relative; overflow:hidden;
    }
    .btn:after{
      content:""; position:absolute; inset:-2px;
      background: radial-gradient(120px 60px at 10% 20%, rgba(255,255,255,.25), transparent 60%);
      opacity:.0; transition:opacity .25s ease;
    }
    .btn:hover{transform: translateY(-2px)}
    .btn:hover:after{opacity:1}
    .p .btn{margin-top:auto}
    .btn.primary{
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.70));
      color:#061a0d;
      box-shadow:0 14px 28px rgba(34,197,94,.14);
    }
    .btn.ghost{
      background:rgba(255,255,255,.04);
      border-color:var(--line);
      color:var(--text);
    }
    .badges{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
    .badge{
      font-size:12px;color:var(--muted);
      border:1px solid var(--line);
      background:rgba(255,255,255,.03);
      padding:8px 10px;border-radius:999px;
    }

    .heroR{padding:16px}
    .steps-title{font-weight:950;margin:0 0 10px;font-size:14px}
    .step{
      display:flex;gap:10px;
      padding:10px;border-radius:16px;
      background:rgba(255,255,255,.03);
      border:1px solid rgba(255,255,255,.08);
      margin-bottom:10px;
    }
    .num{
      width:28px;height:28px;border-radius:10px;
      display:flex;align-items:center;justify-content:center;
      font-weight:950;
      background:rgba(56,189,248,.18);
      border:1px solid rgba(56,189,248,.25);
      flex:0 0 auto;
    }
    .step b{display:block;font-size:13px}
    .step span{display:block;font-size:12px;color:var(--muted);margin-top:2px}

    .section{margin:18px 0 10px;font-size:14px;font-weight:950;letter-spacing:.2px;color:rgba(255,255,255,.86)}
    .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));align-items:stretch}

    .p{background:var(--panel2);border:1px solid var(--line);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);transition:transform .2s ease, box-shadow .2s ease;display:flex;flex-direction:column;min-height:260px}
    .p:hover{
      transform: translateY(-4px);
      box-shadow: 0 24px 50px rgba(0,0,0,.50);
    }
    .p:before{
      content:"";
      position:absolute; inset:-2px;
      background: radial-gradient(360px 160px at 10% 10%, rgba(56,189,248,.12), transparent 60%);
      opacity:.9;
      pointer-events:none;
    }
    .ptitle{font-weight:950;font-size:15px;margin-bottom:4px}
    .psub{font-size:12px;color:var(--muted)}
    .price{font-size:22px;font-weight:950;margin:12px 0 10px;letter-spacing:.2px}
    .feats{display:grid;gap:6px;margin-bottom:12px;flex:1}
    .feat{font-size:12px;color:rgba(255,255,255,.86);
      background:rgba(255,255,255,.03);
      border:1px solid rgba(255,255,255,.08);
      padding:8px 10px;border-radius:12px;
    }
    .note{margin-top:10px;font-size:12px;color:var(--muted)}

    /* TERLARIS badge animation */
    .hot{
      display:inline-flex;align-items:center;gap:6px;
      font-size:11px;
      font-weight:950;
      color:#05210f;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(56,189,248,.70));
      padding:6px 10px;
      border-radius:999px;
      box-shadow: 0 10px 24px rgba(34,197,94,.18);
      position:relative;
      overflow:hidden;
      animation: pop 2.2s ease-in-out infinite;
    }
    .hot:after{
      content:"";
      position:absolute; inset:-2px;
      background: linear-gradient(120deg, transparent 0%, rgba(255,255,255,.35) 35%, transparent 70%);
      transform: translateX(-120%);
      animation: shine 2.2s ease-in-out infinite;
    }
    @keyframes shine{0%{transform:translateX(-120%)} 60%{transform:translateX(140%)} 100%{transform:translateX(140%)}}
    @keyframes pop{0%,100%{transform:translateY(0)} 50%{transform:translateY(-2px)}}

    /* shimmer loading for stock */
    .shimmer{
      display:inline-block;
      width:110px;height:14px;border-radius:999px;
      background: rgba(255,255,255,.07);
      position:relative; overflow:hidden;
      vertical-align:middle;
    }
    .shimmer:after{
      content:""; position:absolute; inset:0;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent);
      transform: translateX(-100%);
      animation: shimmer 1.2s infinite;
    }
    @keyframes shimmer{to{transform:translateX(100%)}}

    .footer{
      margin-top:18px;
      color:rgba(255,255,255,.55);
      font-size:12px;
      display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;
      border-top:1px solid rgba(255,255,255,.10);
      padding-top:14px;
    }

    /* Floating WhatsApp */
    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:flex;align-items:center;gap:10px;
      padding:14px 16px;
      border-radius:999px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.75));
      color:#06210f;
      text-decoration:none;
      font-weight:950;
      box-shadow:0 14px 28px rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
    }
    .wa:hover{filter:brightness(1.05)}
    .wa small{font-weight:800;opacity:.8}

    /* Responsive */
    @media (max-width: 920px){.hero{grid-template-columns:1fr}.grid{grid-template-columns:1fr}.title{font-size:26px}}
      .grid{grid-template-columns:1fr}
      .title{font-size:26px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo"></div>
        <div>
          <h1>AI Premium Store</h1>
          <div class="tag">Akses AI premium • pembayaran QRIS • proses cepat</div>
        </div>
      </div>
      <div class="pill">🛡️ Aman • Admin verifikasi • Voucher otomatis • <span id="vis">...</span> online</div>
    </div>

    <div class="hero">
      <div class="card heroL">
        <div class="kicker">⚡ Fast checkout <span style="opacity:.5">•</span> 📌 Harga jelas <span style="opacity:.5">•</span> ✅ Auto voucher</div>
        <div class="title">Beli akses AI premium dengan proses rapi & cepat.</div>
        <div class="sub">
          Pilih produk → bayar QRIS → admin verifikasi → sistem otomatis kirim voucher/akses.
          Cocok untuk kerja, kuliah, riset, coding, dan konten.
        </div>
        <div class="actions">
          <a class="btn primary" href="#produk">Lihat Produk</a>
          <a class="btn ghost" href="#cara">Cara Beli</a>
        </div>
        <div class="badges">
          <div class="badge">✅ Pembayaran QRIS</div>
          <div class="badge">✅ Status otomatis</div>
          <div class="badge">✅ Voucher 1x klik</div>
          <div class="badge">✅ Support after sales</div>
        </div>
      </div>

      <div class="card heroR" id="cara">
        <div class="steps-title">Cara beli (3 langkah)</div>
        <div class="step"><div class="num">1</div><div><b>Pilih produk</b><span>Klik “Beli Sekarang” di produk yang kamu mau.</span></div></div>
        <div class="step"><div class="num">2</div><div><b>Bayar QRIS</b><span>Transfer sesuai nominal (termasuk kode unik).</span></div></div>
        <div class="step"><div class="num">3</div><div><b>Verifikasi & voucher</b><span>Admin verifikasi → voucher tampil otomatis.</span></div></div>
        <div style="margin-top:10px;font-size:12px;color:var(--muted);">
          Tip: setelah bayar, buka halaman status order untuk auto-redirect ke voucher.
        </div>
      </div>
    </div>

    <div class="section" id="produk">Produk tersedia</div>
    <div class="grid">
      $cards
    </div>

    <div class="footer">
      <div>© $year AI Premium Store</div>
      <div style="opacity:.7">Admin panel: <code>/admin?token=TOKEN</code></div>
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">
    💬 Chat Admin <small>WA</small>
  </a>

  <script>
    // live visitor counter (simple marketing, not analytics)
    async function loadVis(){
      try{
        const r = await fetch("/api/visitors", {cache:"no-store"});
        const j = await r.json();
        if(j && j.ok){
          document.getElementById("vis").textContent = j.count;
        }
      }catch(e){}
    }
    loadVis();
    setInterval(loadVis, 6000);

    // stock realtime
    async function loadStock(){
      try{
        const r = await fetch("/api/stock", {cache:"no-store"});
        const j = await r.json();
        if(!j || !j.ok) return;
        for(const pid in j.stock){
          const el = document.getElementById("stock-"+pid);
          if(!el) continue;
          el.textContent = "Stok: " + j.stock[pid] + " tersedia";
        }
      }catch(e){}
    }
    loadStock();
    setInterval(loadStock, 8000);
  </script>
</body>
</html>
""")

PAY_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Pembayaran QRIS</title>
  <style>
    :root{
      --bg:#070c18; --glass:rgba(255,255,255,.06); --line:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.70);
      --g1:#22c55e; --g2:#38bdf8; --shadow:0 18px 42px rgba(0,0,0,.45); --r:22px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(900px 520px at 20% -10%, rgba(56,189,248,.25), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(34,197,94,.20), transparent 55%),
        var(--bg);
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:26px 14px;
    }
    .box{
      width:min(520px, 100%);
      background:var(--glass);
      border:1px solid var(--line);
      border-radius:var(--r);
      box-shadow:var(--shadow);
      padding:22px;
      text-align:center;
      backdrop-filter: blur(12px);
      position:relative; overflow:hidden;
    }
    .box:before{
      content:""; position:absolute; inset:-2px;
      background: radial-gradient(700px 260px at 30% 10%, rgba(56,189,248,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px; font-size:28px}
    .muted{color:var(--muted); font-size:13px}
    .total{font-size:42px; font-weight:950; color:var(--g1); margin:10px 0 6px; letter-spacing:.4px}
    .qris{
      margin:16px auto 6px;
      width:min(360px, 100%);
      background:#fff;
      border-radius:18px;
      padding:12px;
      box-shadow: 0 14px 30px rgba(0,0,0,.35);
    }
    .qris img{width:100%; height:auto; border-radius:12px; display:block}
    .oid{
      margin-top:14px;
      padding:12px;
      border:1px dashed rgba(255,255,255,.20);
      border-radius:16px;
      font-size:12px;
      color:rgba(255,255,255,.80);
      word-break:break-all;
    }
    .btn{
      display:inline-flex; align-items:center; justify-content:center; gap:8px;
      margin-top:14px;
      padding:12px 16px;
      border-radius:14px;
      text-decoration:none;
      background:rgba(255,255,255,.06);
      border:1px solid rgba(255,255,255,.12);
      color:var(--text);
      font-weight:950;
      transition: transform .18s ease, filter .18s ease;
    }
    .btn:hover{transform: translateY(-2px); filter:brightness(1.05)}
    .row{display:flex; gap:10px; flex-wrap:wrap; justify-content:center}
    .wa{
      position:fixed; right:18px; bottom:18px; z-index:999;
      display:flex;align-items:center;gap:10px;
      padding:14px 16px;border-radius:999px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.75));
      color:#06210f;text-decoration:none;font-weight:950;
      box-shadow:0 14px 28px rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
    }
    .toast{
      position:fixed; left:50%; transform:translateX(-50%);
      bottom:18px; z-index:1000;
      background:rgba(0,0,0,.55);
      border:1px solid rgba(255,255,255,.12);
      color:#fff;
      padding:12px 14px;
      border-radius:14px;
      opacity:0; pointer-events:none;
      transition: opacity .2s ease, transform .2s ease;
      backdrop-filter: blur(10px);
    }
    .toast.show{opacity:1; transform:translateX(-50%) translateY(-6px);}
  </style>
</head>
<body>
  <div class="box">
    <h1>Pembayaran QRIS</h1>
    <div class="muted">Produk: <b>$product_name</b></div>

    <div style="margin-top:12px;">Total transfer:</div>
    <div class="total">Rp $total</div>
    <div class="muted">termasuk kode unik untuk verifikasi</div>

    <div style="margin-top:14px;">Scan QRIS:</div>
    <div class="qris"><img src="$qris" alt="QRIS"/></div>

    <div class="oid">Order ID:<br><b>$order_id</b></div>

    <div class="row">
      <a class="btn" href="/status/$order_id">Cek Status</a>
      <a class="btn" href="/" style="opacity:.9">Kembali</a>
    </div>

    <div class="muted" style="margin-top:12px;">
      Catatan: order ini akan otomatis <b>cancel</b> jika belum dibayar dalam $ttl menit.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>
  <div id="toast" class="toast">Voucher berhasil dikirim ✅ Mengarahkan...</div>

  <script>
    // auto cek setelah bayar (supaya user tidak perlu klik)
    async function poll(){
      try{
        const r = await fetch("/api/order/$order_id", {cache:"no-store"});
        const j = await r.json();
        if(!j || !j.ok) return;
        if(j.status === "paid"){
          const t=document.getElementById("toast");
          t.classList.add("show");
          setTimeout(()=>{window.location.href="/voucher/$order_id";}, 650);
        }
        if(j.status === "cancelled"){
          // kalau sudah expired, arahkan ke status biar jelas
          window.location.href="/status/$order_id";
        }
      }catch(e){}
    }
    setInterval(poll, 2000);
    poll();
  </script>
</body>
</html>
""")

STATUS_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Status Order</title>
  <style>
    :root{
      --bg:#070c18; --glass:rgba(255,255,255,.06); --line:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.70);
      --g1:#22c55e; --shadow:0 18px 42px rgba(0,0,0,.45); --r:22px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(900px 520px at 20% -10%, rgba(56,189,248,.20), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(34,197,94,.18), transparent 55%),
        var(--bg);
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:26px 14px;
    }
    .box{
      width:min(560px, 100%);
      background:var(--glass);
      border:1px solid var(--line);
      border-radius:var(--r);
      box-shadow:var(--shadow);
      padding:22px;
      text-align:center;
      backdrop-filter: blur(12px);
      position:relative; overflow:hidden;
    }
    .box:before{
      content:""; position:absolute; inset:-2px;
      background: radial-gradient(700px 260px at 30% 10%, rgba(56,189,248,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px; font-size:30px}
    .muted{color:var(--muted); font-size:13px}
    .badge{
      display:inline-flex;align-items:center;gap:8px;
      padding:10px 14px;
      border-radius:999px;
      font-weight:950;
      letter-spacing:.5px;
      background:$badge;
      color:#071016;
      margin-top:10px;
    }
    .spin{
      width:14px;height:14px;
      border:2px solid rgba(255,255,255,.22);
      border-top-color: rgba(255,255,255,.85);
      border-radius:50%;
      animation: spin 1s linear infinite;
    }
    @keyframes spin{to{transform:rotate(360deg)}}
    .grid{
      margin-top:16px;
      display:grid;
      grid-template-columns: repeat(2, minmax(0,1fr));
      gap:10px;
    }
    .mini{
      background:rgba(255,255,255,.04);
      border:1px solid rgba(255,255,255,.10);
      border-radius:18px;
      padding:12px;
    }
    .mini .t{font-size:12px;color:var(--muted)}
    .mini .v{font-size:22px;font-weight:950;margin-top:4px}
    .toast{
      position:fixed; left:50%; transform:translateX(-50%);
      bottom:18px; z-index:1000;
      background:rgba(0,0,0,.55);
      border:1px solid rgba(255,255,255,.12);
      color:#fff;
      padding:12px 14px;
      border-radius:14px;
      opacity:0; pointer-events:none;
      transition: opacity .2s ease, transform .2s ease;
      backdrop-filter: blur(10px);
    }
    .toast.show{opacity:1; transform:translateX(-50%) translateY(-6px);}
    .wa{
      position:fixed; right:18px; bottom:18px; z-index:999;
      display:flex;align-items:center;gap:10px;
      padding:14px 16px;border-radius:999px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.75));
      color:#06210f;text-decoration:none;font-weight:950;
      box-shadow:0 14px 28px rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
    }
    @media (max-width:520px){ .grid{grid-template-columns:1fr} }
  </style>
</head>
<body>
  <div class="box">
    <h1>Status Order</h1>
    <div class="muted">Produk: <b>$pid</b></div>
    <div class="muted">Nominal: <b>Rp $amount</b></div>

    <div class="badge"><span id="st">$st</span> <span class="spin" title="auto cek"></span></div>

    <div class="grid">
      <div class="mini">
        <div class="t">Countdown verifikasi</div>
        <div class="v" id="cd">--:--</div>
      </div>
      <div class="mini">
        <div class="t">Auto cek</div>
        <div class="v" id="tick">2s</div>
      </div>
    </div>

    <div class="muted" style="margin-top:14px;">
      Halaman ini akan otomatis redirect ke voucher setelah admin verifikasi.<br/>
      Jika sudah bayar tapi lama, klik tombol WhatsApp untuk konfirmasi.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>
  <div id="toast" class="toast">Voucher berhasil dikirim ✅ Mengarahkan...</div>

  <script>
    let ttl = $ttl_sec;
    let every = 2; // seconds
    document.getElementById("tick").textContent = every + "s";

    function fmt(sec){
      sec = Math.max(0, sec|0);
      const m = (sec/60)|0;
      const s = sec%60;
      return String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");
    }
    function updateCd(){
      document.getElementById("cd").textContent = fmt(ttl);
      ttl = Math.max(0, ttl - 1);
    }
    setInterval(updateCd, 1000);
    updateCd();

    async function poll(){
      try{
        const r = await fetch("/api/order/$order_id", {cache:"no-store"});
        const j = await r.json();
        if(!j || !j.ok) return;

        if(j.status === "paid"){
          const t=document.getElementById("toast");
          t.classList.add("show");
          setTimeout(()=>{window.location.href="/voucher/$order_id";}, 650);
          return;
        }
        if(j.status === "cancelled"){
          document.getElementById("st").textContent = "CANCELLED";
          return;
        }
        if(typeof j.ttl_sec === "number") ttl = j.ttl_sec;
      }catch(e){}
    }
    setInterval(poll, every*1000);
    poll();
  </script>
</body>
</html>
""")

VOUCHER_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Voucher</title>
  <style>
    :root{
      --bg:#070c18; --glass:rgba(255,255,255,.06); --line:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.70);
      --g1:#22c55e; --shadow:0 18px 42px rgba(0,0,0,.45); --r:22px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(900px 520px at 20% -10%, rgba(56,189,248,.20), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(34,197,94,.18), transparent 55%),
        var(--bg);
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:26px 14px;
    }
    .box{
      width:min(560px, 100%);
      background:var(--glass);
      border:1px solid var(--line);
      border-radius:var(--r);
      box-shadow:var(--shadow);
      padding:22px;
      text-align:center;
      backdrop-filter: blur(12px);
      position:relative; overflow:hidden;
    }
    .box:before{
      content:""; position:absolute; inset:-2px;
      background: radial-gradient(700px 260px at 30% 10%, rgba(56,189,248,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px; font-size:30px}
    .muted{color:var(--muted); font-size:13px}
    .code{
      margin:16px auto 12px;
      background:rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
      padding:14px 12px;
      border-radius:16px;
      font-size:18px;
      font-weight:950;
      letter-spacing:.4px;
      word-break:break-all;
      position:relative;
    }
    .btn{
      display:inline-flex;align-items:center;justify-content:center;gap:8px;
      padding:12px 16px;border-radius:14px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.78));
      color:#06210f;
      border:1px solid rgba(255,255,255,.12);
      font-weight:950;
      cursor:pointer;
      box-shadow: 0 14px 28px rgba(34,197,94,.14);
      transition: transform .18s ease, filter .18s ease;
    }
    .btn:hover{transform: translateY(-2px); filter:brightness(1.05)}
    .success{
      display:inline-flex;align-items:center;gap:10px;
      margin-top:10px;
      padding:10px 12px;
      border-radius:999px;
      background:rgba(34,197,94,.10);
      border:1px solid rgba(34,197,94,.18);
      color:rgba(255,255,255,.92);
      font-weight:900;
      animation: pop .6s ease both;
    }
    @keyframes pop{from{transform:scale(.96);opacity:0} to{transform:scale(1);opacity:1}}
    .wa{
      position:fixed; right:18px; bottom:18px; z-index:999;
      display:flex;align-items:center;gap:10px;
      padding:14px 16px;border-radius:999px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.75));
      color:#06210f;text-decoration:none;font-weight:950;
      box-shadow:0 14px 28px rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
    }
  </style>
</head>
<body>
  <div class="box">
    <h1>Voucher</h1>
    <div class="muted">Status: <b>PAID ✅</b></div>
    <div class="muted">Produk: <b>$pid</b></div>

    <div class="success">✅ Voucher berhasil dikirim</div>

    <div class="code" id="vcode">$code</div>

    <button class="btn" onclick="navigator.clipboard.writeText('$code')">Salin Voucher</button>

    <div class="muted" style="margin-top:12px;">
      Simpan kode ini. Jangan dibagikan ke orang lain.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>
</body>
</html>
""")

ADMIN_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Admin Panel</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;background:#070c18;color:#fff;padding:20px}
    .box{max-width:980px;margin:0 auto}
    .row{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);padding:14px;border-radius:16px;margin-bottom:10px;display:flex;gap:12px;align-items:center;justify-content:space-between;backdrop-filter: blur(10px)}
    .muted{opacity:.75;font-size:12px;word-break:break-all}
    .vbtn{background:#22c55e;border:none;color:#06210f;padding:10px 12px;border-radius:12px;cursor:pointer;font-weight:950}
    .lbtn{display:inline-block;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:white;padding:10px 12px;border-radius:12px;text-decoration:none;font-weight:950}
    .act{min-width:260px;display:flex;flex-direction:column;align-items:flex-end;gap:8px}
    @media(max-width:740px){.row{flex-direction:column;align-items:flex-start}.act{align-items:flex-start;min-width:unset;width:100%}}
  </style>
</head>
<body>
  <div class="box">
    <h2 style="margin:0 0 10px;">Admin Panel</h2>
    <div style="opacity:.75;margin-bottom:12px;">
      Klik tombol untuk verifikasi + otomatis assign voucher lalu redirect ke halaman voucher.
    </div>
    $items
  </div>
</body>
</html>
""")

# ======================
# API (untuk WordPress)
# ======================
@app.get("/api/products")
def api_products():
    stock = get_stock_map()
    items = []
    for pid, p in PRODUCTS.items():
        items.append({
            "id": pid,
            "name": p["name"],
            "price": int(p["price"]),
            "stock": int(stock.get(pid, 0)),
        })
    return {"products": items}

@app.post("/api/checkout")
def api_checkout(payload: dict, request: Request):
    """Buat order dan kembalikan pay_url.
    Payload: {product_id: str, qty: int}
    """
    pid = (payload.get("product_id") or "").strip()
    qty = int(payload.get("qty") or 1)
    if qty < 1:
        qty = 1
    if pid not in PRODUCTS:
        return {"ok": False, "error": "Produk tidak ditemukan"}

    stock = get_stock_map().get(pid, 0)
    if stock <= 0:
        return {"ok": False, "error": "Stok habis"}
    if qty > stock:
        return {"ok": False, "error": f"Stok tidak cukup. Maks {stock}"}

    ip = _client_ip(request)
    if not _rate_limit_checkout(ip):
        return {"ok": False, "error": "Terlalu banyak request. Coba lagi nanti."}

    base_price = int(PRODUCTS[pid]["price"]) * qty
    unique_code = random.randint(101, 999)
    total = base_price + unique_code

    order_id = str(uuid.uuid4())
    created_at = now_utc().isoformat()

    ins = supabase.table("orders").insert({
        "id": order_id,
        "product_id": pid,
        "qty": int(qty),
        "amount_idr": int(total),
        "status": "pending",
        "created_at": created_at,
        "voucher_code": None,
    }).execute()

    if not ins.data:
        return {"ok": False, "error": "Gagal membuat order"}

    return {"ok": True, "order_id": order_id, "amount_idr": int(total), "pay_url": f"/pay/{order_id}"}

# ======================
# ROUTES
# ======================
@app.get("/", response_class=HTMLResponse)
def home():
    stock = get_stock_map()
    cards = ""
    for pid, p in PRODUCTS.items():
        stok_txt = f"Stok: {stock.get(pid,0)} tersedia"
        hot = '<span class="hot">🔥 TERLARIS</span>' if pid == "gemini" else ""
        cards += f"""
          <div class="p">
            <div class="ptitle">{p["name"]}</div>
            <div style="margin:6px 0 6px;">{hot}</div>
            <div class="psub" id="stock-{pid}">{stok_txt}</div>
            <div class="price">Rp {rupiah(int(p["price"]))}</div>
            <div class="feats">
              <div class="feat">✅ Aktivasi cepat</div>
              <div class="feat">✅ Garansi sesuai produk</div>
              <div class="feat">✅ Support after sales</div>
            </div>
            <a class="btn primary" href="/checkout/{pid}">Beli Sekarang</a>
            <div class="note">Bayar QRIS → verifikasi admin → voucher/akses terkirim</div>
          </div>
        """
    html = HOME_HTML.substitute(cards=cards, year=now_utc().year)
    return HTMLResponse(html)
    from fastapi import HTTPException

@app.post("/api/checkout")
async def api_checkout(payload: dict = Body(...), request: Request = None):
    """
    Buat order dan kembalikan order_id + pay_url.
    Payload: { "product_id": str, "qty": int }
    """
    try:
        product_id = str(payload.get("product_id", "")).strip()
        qty = int(payload.get("qty", 1))

        if not product_id:
            raise HTTPException(status_code=422, detail="product_id wajib diisi")
        if qty < 1:
            raise HTTPException(status_code=422, detail="qty minimal 1")

        if product_id not in PRODUCTS:
            raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

        price = int(PRODUCTS[product_id].get("price", 0))
        name = str(PRODUCTS[product_id].get("name", product_id))
        amount = price * qty

        order_id = uuid.uuid4().hex[:12]

        # pay url (dipakai WP untuk redirect)
        base = str(request.base_url).rstrip("/") if request else ""
        pay_url = f"{base}/pay/{order_id}" if base else f"/pay/{order_id}"

        order_obj = {
            "order_id": order_id,
            "product_id": product_id,
            "product_name": name,
            "qty": qty,
            "amount": amount,
            "status": "UNPAID",
            "created_at": now_utc().isoformat(),
        }

        # ===== Simpan ke Supabase kalau ada =====
        # kalau Supabase error, fallback ke memory biar tidak 500
        saved_to_db = False
        try:
            if supabase is not None:
                # pastikan tabel 'orders' ada di Supabase
                # kolom minimal: order_id (text), product_id (text), qty (int), amount (int), status (text), created_at (timestamptz)
                supabase.table("orders").insert(order_obj).execute()
                saved_to_db = True
        except Exception as db_err:
            # fallback ke memory
            saved_to_db = False

        if not saved_to_db:
            ORDERS[order_id] = order_obj

        return {"order_id": order_id, "pay_url": pay_url}

    except HTTPException:
        raise
    except Exception as e:
        # penting: kasih detail biar ketahuan errornya
        raise HTTPException(status_code=500, detail=f"checkout_error: {type(e).__name__}: {str(e)}")

@app.get("/pay/{order_id}", response_class=HTMLResponse)
def pay(order_id: str):
    res = supabase.table("orders").select("*").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    order, _ = _ensure_not_expired(order)

    st = (order.get("status") or "pending").lower()
    if st == "paid":
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=302)
    if st == "cancelled":
        return HTMLResponse("<h3>Order sudah expired</h3><p>Silakan buat order baru dari halaman utama.</p>", status_code=410)

    pid = order.get("product_id", "")
    amount = int(order.get("amount_idr") or 0)
    product_name = PRODUCTS.get(pid, {}).get("name", pid)

    html = PAY_HTML.substitute(
        product_name=product_name,
        total=rupiah(amount),
        qris=QR_IMAGE_URL,
        order_id=order_id,
        ttl=ORDER_TTL_MINUTES,
    )
    return HTMLResponse(html)

@app.get("/status/{order_id}", response_class=HTMLResponse)
def status(order_id: str):
    res = supabase.table("orders").select("*").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    order, _ = _ensure_not_expired(order)

    st = (order.get("status") or "pending").lower()
    if st == "paid":
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=302)

    amount = int(order.get("amount_idr") or 0)
    pid = order.get("product_id", "")

    badge = "#f59e0b" if st == "pending" else "#ef4444"
    # TTL seconds left
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))

    html = STATUS_HTML.substitute(
        pid=pid,
        amount=rupiah(amount),
        st=st.upper(),
        badge=badge,
        order_id=order_id,
        ttl_sec=str(ttl_sec),
    )
    return HTMLResponse(html)

@app.get("/voucher/{order_id}", response_class=HTMLResponse)
def voucher(order_id: str):
    res = supabase.table("orders").select("status,product_id,voucher_code").eq("id", order_id).limit(1).execute()
    if not res.data:
        return HTMLResponse("<h3>Order tidak ditemukan</h3>", status_code=404)

    order = res.data[0]
    if (order.get("status") or "").lower() != "paid":
        return HTMLResponse("<h3>Belum diverifikasi admin</h3><p>Silakan tunggu.</p>", status_code=400)

    code = order.get("voucher_code")
    if not code:
        return HTMLResponse("""
        <html><body style="font-family:Arial;background:#070c18;color:white;text-align:center;padding:40px">
          <h2>Voucher</h2>
          <p>Status: PAID ✅</p>
          <p style="opacity:.8">Maaf, stok voucher untuk produk ini sedang habis.</p>
        </body></html>
        """)

    html = VOUCHER_HTML.substitute(pid=order.get("product_id"), code=code)
    return HTMLResponse(html)

# ======================
# API
# ======================
@app.get("/api/order/{order_id}")
def api_order(order_id: str):
    res = supabase.table("orders").select("*").eq("id", order_id).limit(1).execute()
    if not res.data:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    order = res.data[0]
    order, _ = _ensure_not_expired(order)

    st = (order.get("status") or "pending").lower()
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))

    return {"ok": True, "status": st, "ttl_sec": ttl_sec}

@app.get("/api/stock")
def api_stock():
    return {"ok": True, "stock": get_stock_map()}

@app.get("/api/visitors")
def api_visitors(request: Request):
    # visitor counter: unique by cookie session
    sid = request.cookies.get("vis_sid")
    if not sid:
        sid = str(uuid.uuid4())
    t = time.time()
    # expire old
    for k, v in list(_VISITOR_SESS.items()):
        if t - v > 45:
            _VISITOR_SESS.pop(k, None)
    _VISITOR_SESS[sid] = t
    count = _VISITOR_BASE + len(_VISITOR_SESS) + random.randint(0, 9)
    resp = JSONResponse({"ok": True, "count": count})
    resp.set_cookie("vis_sid", sid, max_age=24*3600, httponly=True, samesite="lax")
    return resp

# ======================
# ADMIN PANEL
# ======================
@app.get("/admin", response_class=HTMLResponse)
def admin(token: Optional[str] = None):
    if not require_admin(token):
        return HTMLResponse("<h3>Unauthorized</h3>", status_code=401)

    res = (
        supabase.table("orders")
        .select("id,product_id,amount_idr,status,created_at,voucher_code")
        .order("created_at", desc=True)
        .limit(80)
        .execute()
    )
    rows = res.data or []

    items = ""
    if not rows:
        items = "<div style='opacity:.75'>Belum ada order</div>"
    else:
        for o in rows:
            oid = o.get("id")
            st = (o.get("status") or "pending").lower()
            pid = o.get("product_id", "")
            amt = int(o.get("amount_idr") or 0)
            created = o.get("created_at", "")
            vcode = o.get("voucher_code")

            if st == "pending":
                action = f"""
                <form method="post" action="/admin/verify/{oid}?token={token}" style="margin:0;">
                  <button class="vbtn" type="submit">VERIFIKASI + KIRIM VOUCHER</button>
                </form>
                <div class="muted">Auto-cancel: {ORDER_TTL_MINUTES} menit</div>
                """
            elif st == "paid":
                label = f"Voucher: {vcode}" if vcode else "Voucher: (habis / belum ada)"
                action = f"""
                <a class="lbtn" href="/voucher/{oid}">Buka Voucher</a>
                <div class="muted">{label}</div>
                """
            else:
                action = f"""
                <div class="muted">Status: {st.upper()}</div>
                <a class="lbtn" href="/pay/{oid}">Buka Pay</a>
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

    return HTMLResponse(ADMIN_HTML.substitute(items=items))

@app.post("/admin/verify/{order_id}")
def admin_verify(order_id: str, token: Optional[str] = None):
    if not require_admin(token):
        return PlainTextResponse("Unauthorized", status_code=401)

    res = supabase.table("orders").select("id,product_id,status,voucher_code").eq("id", order_id).limit(1).execute()
    if not res.data:
        return PlainTextResponse("Order not found", status_code=404)

    order = res.data[0]
    pid = order.get("product_id")
    st = (order.get("status") or "pending").lower()
    vcode = order.get("voucher_code")

    # kalau sudah paid dan voucher sudah ada
    if st == "paid" and vcode:
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)

    # kalau expired/cancelled
    if st == "cancelled":
        return HTMLResponse("<h3>Order sudah cancelled/expired</h3>", status_code=410)

    # set paid dulu (idempotent)
    supabase.table("orders").update({"status": "paid"}).eq("id", order_id).execute()

    # kalau voucher belum ada, claim dari table vouchers
    code = None
    try:
        chk = supabase.table("orders").select("voucher_code,product_id,status").eq("id", order_id).limit(1).execute()
        if chk.data:
            row = chk.data[0]
            if (row.get("status") or "").lower() == "paid":
                if row.get("voucher_code"):
                    code = row["voucher_code"]
                else:
                    code = claim_voucher_for_order(order_id, row.get("product_id"), int(row.get("qty") or 1))
    except Exception as e:
        print("[ADMIN_VERIFY] err:", e)

    # redirect buyer ke voucher (atau halaman voucher akan bilang stok habis)
    return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)
