
import os
import uuid
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple
from string import Template

from fastapi import FastAPI, Request, Query
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


# =========================
# Safe template renderer
# =========================
def _tpl_render(tpl, **kw) -> str:
    s = tpl.template if hasattr(tpl, "template") else str(tpl)
    for k, v in kw.items():
        s = s.replace(f"${{{k}}}", str(v))
        s = s.replace(f"${k}", str(v))
    return s


# ======================
# CONFIG
# ======================
BRAND_NAME = "Impura"
BRAND_TAGLINE = "Tempat beli AI Premium"
LOGO_URL = os.getenv(
    "LOGO_URL",
    "https://i.ibb.co.com/27YZkLgJ/logo-impura.jpg",
)

PRODUCTS = {
    "gemini": {
        "name": "Gemini AI Pro 1 Tahun",
        "price": 34_000,
        "features": [
            "Akses penuh Gemini AI Pro",
            "Google Drive 2TB",
            "Flow + 1.000 credit",
            "Aktivasi cepat",
        ],
    },
    "chatgpt": {
        "name": "ChatGPT Plus 1 Bulan",
        "price": 14_000,
        "features": [
            "Akses model ChatGPT terbaru",
            "Respons lebih cepat & akurat",
            "Cocok untuk riset & coding",
            "Aktivasi cepat",
        ],
    },
}

DEFAULT_FEATURES = [
    "Aktivasi cepat",
    "Proses rapi",
    "Akun dikirim otomatis",
]

PRODUCT_FEATS = {
    # Jika ingin custom fitur per produk, isi di sini.
    # "gemini": ["Fitur 1", "Fitur 2"],
}

QR_IMAGE_URL = os.getenv(
    "QR_IMAGE_URL",
    "https://i.ibb.co.com/6787VVXf/qris-impura.jpg",
)

ORDER_TTL_MINUTES = 15
RATE_WINDOW_SEC = 5 * 60
RATE_MAX_CHECKOUT = 6

_IP_BUCKET: Dict[str, list] = {}
_VISITOR_SESS: Dict[str, float] = {}
_VISITOR_BASE = 120


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
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_checkout(ip: str) -> bool:
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
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _ensure_not_expired(order: dict) -> Tuple[dict, bool]:
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


def claim_vouchers_for_order(order_id: str, product_id: str, qty: int) -> Optional[list[str]]:
    qty = max(1, int(qty))

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

    q = supabase.table("vouchers").update({"status": "used"})
    if hasattr(q, "in_"):
        q = q.in_("id", ids)
        q.execute()
    else:
        for vid in ids:
            supabase.table("vouchers").update({"status": "used"}).eq("id", vid).execute()

    supabase.table("orders").update({
        "status": "paid",
        "voucher_code": "\n".join(codes) if codes else None,
    }).eq("id", order_id).execute()

    return codes


# ======================
# Templates
# ======================
HOME_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Impura</title>

  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-YGSFDD04M4"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-YGSFDD04M4');
  </script>

  <style>
    :root{
      --bg:#050505;
      --bg-2:#0d0607;
      --surface:rgba(255,255,255,.05);
      --surface-2:rgba(255,255,255,.07);
      --line:rgba(255,255,255,.10);
      --line-strong:rgba(255,255,255,.16);
      --text:rgba(255,255,255,.95);
      --muted:rgba(255,255,255,.70);
      --red-1:#ff3b3b;
      --red-2:#c1121f;
      --red-3:#6e0b13;
      --shadow:0 18px 48px rgba(0,0,0,.42);
      --radius:24px;
      --radius-sm:16px;
      --wrap:1380px;
      --header-h:88px;
    }

    *{box-sizing:border-box}
    html{scroll-behavior:smooth}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      color:var(--text);
      background:
        radial-gradient(1200px 650px at 0% -10%, rgba(255,59,59,.20), transparent 58%),
        radial-gradient(900px 600px at 100% 0%, rgba(193,18,31,.18), transparent 55%),
        radial-gradient(700px 500px at 50% 100%, rgba(193,18,31,.10), transparent 65%),
        linear-gradient(180deg, #0c0507 0%, #070707 38%, #040404 100%);
      min-height:100vh;
    }

    a{color:inherit;text-decoration:none}
    img{display:block;max-width:100%}

    .container{
      width:min(var(--wrap), calc(100vw - 28px));
      margin:0 auto;
    }

    .site-header{
      position:sticky;
      top:0;
      z-index:1000;
      backdrop-filter:blur(16px);
      background:linear-gradient(180deg, rgba(7,7,7,.92), rgba(7,7,7,.72));
      border-bottom:1px solid rgba(255,255,255,.08);
    }

    .header-inner{
      min-height:var(--header-h);
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:16px;
      padding:14px 0;
    }

    .brand{
      display:flex;
      align-items:center;
      gap:14px;
      min-width:0;
    }

    .logo-wrap{
      width:50px;
      height:50px;
      border-radius:999px;
      padding:2px;
      background:linear-gradient(135deg, var(--red-1), #ffffff22, var(--red-2));
      box-shadow:0 12px 28px rgba(193,18,31,.24);
      flex:0 0 auto;
    }

    .logo{
      width:100%;
      height:100%;
      object-fit:cover;
      border-radius:999px;
      border:1px solid rgba(255,255,255,.12);
      background:#120809;
    }

    .brand-text{min-width:0}
    .brand-text h1{
      margin:0;
      font-size:18px;
      letter-spacing:.2px;
      white-space:nowrap;
    }
    .brand-text p{
      margin:3px 0 0;
      color:var(--muted);
      font-size:12.5px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
      max-width:60vw;
    }

    .header-chip{
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:10px 14px;
      border-radius:999px;
      background:rgba(255,255,255,.04);
      border:1px solid var(--line);
      color:var(--muted);
      font-size:12px;
      font-weight:700;
      white-space:nowrap;
    }

    .page{
      padding:24px 0 88px;
    }

    .hero{
      display:grid;
      grid-template-columns:minmax(0, 1.2fr) minmax(340px, .8fr);
      gap:18px;
      align-items:stretch;
    }

    .panel{
      position:relative;
      overflow:hidden;
      background:linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.04));
      border:1px solid var(--line);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      backdrop-filter:blur(12px);
    }

    .panel::before{
      content:"";
      position:absolute;
      inset:-1px;
      background:
        radial-gradient(650px 240px at 10% 0%, rgba(255,59,59,.18), transparent 60%),
        radial-gradient(500px 260px at 100% 0%, rgba(193,18,31,.12), transparent 58%);
      pointer-events:none;
    }

    .hero-main{
      padding:28px;
      min-height:100%;
    }

    .hero-kicker{
      display:inline-flex;
      flex-wrap:wrap;
      gap:10px;
      align-items:center;
      padding:10px 14px;
      border-radius:999px;
      background:rgba(255,255,255,.035);
      border:1px solid var(--line);
      color:var(--muted);
      font-size:12px;
      font-weight:700;
    }

    .hero-title{
      margin:16px 0 10px;
      font-size:clamp(33px, 4vw, 54px);
      line-height:1.08;
      letter-spacing:-.03em;
      max-width:12ch;
    }

    .hero-sub{
      margin:0;
      color:var(--muted);
      font-size:16px;
      line-height:1.75;
      max-width:60ch;
    }

    .hero-actions{
      display:flex;
      flex-wrap:wrap;
      gap:12px;
      margin-top:20px;
    }

    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:48px;
      padding:0 18px;
      border-radius:15px;
      border:1px solid transparent;
      font-weight:900;
      font-size:14px;
      transition:transform .18s ease, filter .18s ease, box-shadow .18s ease;
      cursor:pointer;
    }

    .btn:hover{transform:translateY(-2px)}
    .btn.primary{
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      box-shadow:0 16px 32px rgba(193,18,31,.22);
    }
    .btn.ghost{
      background:rgba(255,255,255,.04);
      border-color:var(--line);
      color:var(--text);
    }

    .hero-badges{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
      margin-top:18px;
    }

    .badge{
      padding:10px 12px;
      border-radius:14px;
      background:rgba(255,255,255,.04);
      border:1px solid var(--line);
      color:rgba(255,255,255,.84);
      font-size:12px;
      font-weight:700;
    }

    .steps{
      padding:22px;
    }

    .steps h3{
      margin:0 0 12px;
      font-size:17px;
    }

    .step{
      display:flex;
      gap:12px;
      padding:14px;
      border-radius:18px;
      background:rgba(255,255,255,.035);
      border:1px solid var(--line);
      margin-bottom:12px;
    }

    .step-num{
      width:34px;
      height:34px;
      flex:0 0 auto;
      border-radius:12px;
      display:flex;
      align-items:center;
      justify-content:center;
      font-weight:900;
      background:linear-gradient(135deg, rgba(255,59,59,.26), rgba(193,18,31,.22));
      border:1px solid rgba(255,255,255,.10);
    }

    .step b{
      display:block;
      font-size:14px;
      margin-bottom:3px;
    }

    .step span{
      display:block;
      color:var(--muted);
      font-size:13px;
      line-height:1.55;
    }

    .tip{
      color:var(--muted);
      font-size:12.5px;
      line-height:1.6;
      margin-top:8px;
    }

    .section-title{
      margin:22px 0 12px;
      font-size:16px;
      font-weight:900;
      letter-spacing:.2px;
    }

    .grid{
      display:grid;
      grid-template-columns:repeat(2, minmax(0, 1fr));
      gap:16px;
      align-items:stretch;
    }

    .product{
      display:flex;
      flex-direction:column;
      min-height:100%;
      padding:22px;
      border-radius:26px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.035)),
        linear-gradient(120deg, rgba(255,59,59,.04), transparent 45%);
      border:1px solid var(--line);
      box-shadow:var(--shadow);
      position:relative;
      overflow:hidden;
    }

    .product::before{
      content:"";
      position:absolute;
      inset:auto -30% -45% auto;
      width:260px;
      height:260px;
      border-radius:50%;
      background:radial-gradient(circle, rgba(255,59,59,.17), transparent 68%);
      pointer-events:none;
    }

    .product-top{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:14px;
    }

    .ptitle{
      margin:0;
      font-size:17px;
      font-weight:900;
      line-height:1.35;
    }

    .psub{
      margin-top:8px;
      color:var(--muted);
      font-size:13px;
    }

    .hot{
      display:inline-flex;
      align-items:center;
      gap:6px;
      padding:8px 11px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), #ff7b7b);
      color:#fff;
      font-size:11px;
      font-weight:900;
      box-shadow:0 12px 24px rgba(193,18,31,.22);
      animation:floaty 2.2s ease-in-out infinite;
      white-space:nowrap;
    }

    @keyframes floaty{
      0%,100%{transform:translateY(0)}
      50%{transform:translateY(-2px)}
    }

    .price{
      margin:14px 0 14px;
      font-size:40px;
      font-weight:950;
      letter-spacing:-.03em;
      line-height:1;
    }

    .feats{
      display:grid;
      grid-template-columns:repeat(2, minmax(0, 1fr));
      gap:10px;
      margin-bottom:16px;
    }

    .feat{
      display:flex;
      align-items:flex-start;
      gap:8px;
      min-height:100%;
      padding:10px 12px;
      border-radius:15px;
      background:rgba(255,255,255,.04);
      border:1px solid rgba(255,255,255,.08);
      font-size:13px;
      line-height:1.45;
      color:rgba(255,255,255,.90);
    }

    .feat i{
      font-style:normal;
      color:#ff9090;
      line-height:1.2;
      margin-top:1px;
    }

    .buyrow{
      display:flex;
      align-items:center;
      gap:12px;
      flex-wrap:wrap;
      margin-top:auto;
    }

    .qtybox{
      display:flex;
      align-items:center;
      gap:12px;
      padding:10px 12px;
      border-radius:16px;
      background:rgba(255,255,255,.05);
      border:1px solid rgba(255,255,255,.10);
    }

    .qtybtn{
      width:40px;
      height:40px;
      border:none;
      border-radius:13px;
      background:rgba(255,255,255,.05);
      border:1px solid rgba(255,255,255,.12);
      color:var(--text);
      font-size:18px;
      font-weight:900;
      cursor:pointer;
    }

    .qtybtn:disabled{opacity:.45;cursor:not-allowed}
    .qtyval{
      min-width:28px;
      text-align:center;
      font-weight:900;
      font-size:16px;
    }

    .note{
      margin-top:12px;
      color:var(--muted);
      font-size:12.5px;
      line-height:1.6;
    }

    .footer{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
      margin-top:22px;
      padding-top:18px;
      border-top:1px solid rgba(255,255,255,.08);
      color:rgba(255,255,255,.55);
      font-size:12px;
      flex-wrap:wrap;
    }

    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:inline-flex;
      align-items:center;
      gap:10px;
      min-height:54px;
      padding:0 18px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      font-weight:900;
      box-shadow:0 16px 30px rgba(0,0,0,.34);
      border:1px solid rgba(255,255,255,.10);
    }

    @media (min-width: 1300px){
      .container{
        width:min(var(--wrap), calc(100vw - 40px));
      }
      .hero-main{padding:34px}
      .steps{padding:26px}
    }

    @media (max-width: 980px){
      .hero{
        grid-template-columns:1fr;
      }
      .grid{
        grid-template-columns:1fr;
      }
    }

    @media (max-width: 640px){
      :root{--header-h:78px}
      .container{
        width:min(var(--wrap), calc(100vw - 20px));
      }
      .header-chip{display:none}
      .hero-main, .steps, .product{padding:18px}
      .hero-title{
        max-width:none;
        font-size:clamp(28px, 8vw, 40px);
      }
      .hero-sub{font-size:14px}
      .feats{grid-template-columns:1fr}
      .price{font-size:28px}
      .wa{
        right:14px;
        bottom:14px;
        min-height:50px;
        padding:0 16px;
      }
      .brand-text p{
        max-width:48vw;
      }
    }
  </style>
</head>
<body>
  <header class="site-header">
    <div class="container header-inner">
      <div class="brand">
        <div class="logo-wrap">
          <img class="logo" src="$logo_url" alt="Logo Impura"/>
        </div>
        <div class="brand-text">
          <h1>$brand_name</h1>
          <p>$brand_tagline</p>
        </div>
      </div>      
    </div>
  </header>

  <main class="page">
    <div class="container">
      <section class="hero">
        <div class="panel hero-main">
          <div class="hero-kicker">⚡ Fast Checkout • 📌 Harga Jelas • ✅ Bergaransi</div>
          <h2 class="hero-title">Beli akses AI premium dengan proses rapi & cepat.</h2>
          <p class="hero-sub">
            Pilih produk → bayar QRIS → tunggu verifikasi → sistem otomatis kirim akun email.
            Cocok untuk kerja, kuliah, riset, coding, dan konten.
          </p>

          <div class="hero-actions">
            <a class="btn primary" href="#produk">Lihat Produk</a>
            <a class="btn ghost" href="#cara">Cara Beli</a>
          </div>

          <div class="hero-badges">
            <div class="badge">✅ Pembayaran QRIS</div>
            <div class="badge">✅ Status otomatis</div>
            <div class="badge">✅ Bergaransi</div>
            <div class="badge">✅ Support after sales</div>
          </div>
        </div>

        <div class="panel steps" id="cara">
          <h3>Cara beli (3 langkah)</h3>
          <div class="step">
            <div class="step-num">1</div>
            <div>
              <b>Pilih produk</b>
              <span>Klik “Beli Sekarang” di produk yang kamu mau.</span>
            </div>
          </div>
          <div class="step">
            <div class="step-num">2</div>
            <div>
              <b>Bayar QRIS</b>
              <span>Transfer sesuai nominal (termasuk kode unik).</span>
            </div>
          </div>
          <div class="step">
            <div class="step-num">3</div>
            <div>
              <b>Verifikasi</b>
              <span>Tunggu verifikasi → akun email tampil otomatis.</span>
            </div>
          </div>
          <div class="tip">
            Tip: setelah bayar, buka halaman status order untuk auto-redirect ke halaman akun email.
          </div>
        </div>
      </section>

      <div class="section-title" id="produk">Produk tersedia</div>
      <section class="grid">
        $cards
      </section>

      <footer class="footer">
        <div>© $year impura</div>
      </footer>
    </div>
  </main>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>

  <script>
    async function loadStock(){
      try{
        const r = await fetch("/api/stock", {cache:"no-store"});
        const j = await r.json();
        if(!j || !j.ok) return;
        for(const pid in j.stock){
          const el = document.getElementById("stock-"+pid);
          if(el) el.textContent = "Stok: " + j.stock[pid] + " tersedia";
        }
      }catch(e){}
    }
    loadStock();
    setInterval(loadStock, 8000);
  </script>

  <script>
    (function(){
      function syncCard(card){
        const stock = parseInt(card.getAttribute("data-stock") || "0", 10) || 0;
        const qtyEl = card.querySelector(".qtyval");
        const minus = card.querySelector(".qty-minus");
        const plus  = card.querySelector(".qty-plus");
        const buy   = card.querySelector(".buybtn");
        let qty = parseInt((qtyEl && qtyEl.textContent) || "1", 10) || 1;

        if(stock <= 0){
          qty = 1;
          if(qtyEl) qtyEl.textContent = "1";
          if(minus) minus.disabled = true;
          if(plus)  plus.disabled = true;
          if(buy){ buy.disabled = true; buy.setAttribute("aria-disabled","true"); }
          return;
        }

        qty = Math.max(1, Math.min(qty, stock));
        if(qtyEl) qtyEl.textContent = String(qty);
        if(minus) minus.disabled = qty <= 1;
        if(plus)  plus.disabled  = qty >= stock;
        if(buy){ buy.disabled = false; buy.removeAttribute("aria-disabled"); }
      }

      function bind(){
        document.querySelectorAll(".product[data-product]").forEach(card=>{
          syncCard(card);
          const minus = card.querySelector(".qty-minus");
          const plus  = card.querySelector(".qty-plus");
          const buy   = card.querySelector(".buybtn");

          if(minus){
            minus.addEventListener("click", ()=>{
              const qtyEl = card.querySelector(".qtyval");
              let qty = parseInt((qtyEl && qtyEl.textContent) || "1", 10) || 1;
              qty -= 1;
              if(qtyEl) qtyEl.textContent = String(qty);
              syncCard(card);
            });
          }

          if(plus){
            plus.addEventListener("click", ()=>{
              const qtyEl = card.querySelector(".qtyval");
              let qty = parseInt((qtyEl && qtyEl.textContent) || "1", 10) || 1;
              qty += 1;
              if(qtyEl) qtyEl.textContent = String(qty);
              syncCard(card);
            });
          }

          if(buy){
            buy.addEventListener("click", ()=>{
              if(buy.disabled) return;
              const pid = buy.getAttribute("data-buy") || card.getAttribute("data-product");
              const qtyEl = card.querySelector(".qtyval");
              const qty = parseInt((qtyEl && qtyEl.textContent) || "1", 10) || 1;
              window.location.href = "/checkout/" + encodeURIComponent(pid) + "?qty=" + encodeURIComponent(qty);
            });
          }
        });
      }

      async function refreshStock(){
        try{
          const r = await fetch("/api/stock", {cache:"no-store"});
          const j = await r.json();
          if(!j || !j.stock) return;
          for(const pid in j.stock){
            const el = document.getElementById("stock-" + pid);
            if(el) el.textContent = "Stok: " + j.stock[pid] + " tersedia";
            const card = document.querySelector('.product[data-product="' + pid + '"]');
            if(card){ card.setAttribute("data-stock", j.stock[pid]); syncCard(card); }
          }
        }catch(e){}
      }

      bind();
      refreshStock();
      setInterval(refreshStock, 15000);
    })();
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
      --bg:#050505;
      --text:rgba(255,255,255,.95);
      --muted:rgba(255,255,255,.72);
      --line:rgba(255,255,255,.10);
      --red-1:#ff3b3b;
      --red-2:#c1121f;
      --shadow:0 18px 48px rgba(0,0,0,.42);
      --radius:24px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px 14px;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      color:var(--text);
      background:
        radial-gradient(1100px 600px at 0% -10%, rgba(255,59,59,.18), transparent 58%),
        radial-gradient(800px 500px at 100% 0%, rgba(193,18,31,.14), transparent 55%),
        linear-gradient(180deg, #0d0607 0%, #060606 100%);
    }
    .box{
      width:min(540px, 100%);
      padding:24px;
      border-radius:var(--radius);
      background:linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.04));
      border:1px solid var(--line);
      box-shadow:var(--shadow);
      text-align:center;
      position:relative;
      overflow:hidden;
      backdrop-filter:blur(12px);
    }
    .box::before{
      content:"";
      position:absolute;
      inset:-1px;
      background:radial-gradient(600px 220px at 15% 0%, rgba(255,59,59,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px;font-size:30px}
    .muted{color:var(--muted);font-size:13px;line-height:1.6}
    .total{
      margin:10px 0 6px;
      font-size:42px;
      font-weight:950;
      color:#ff7676;
      letter-spacing:-.03em;
    }
    .qris{
      margin:16px auto 6px;
      width:min(360px, 100%);
      padding:12px;
      border-radius:20px;
      background:#fff;
      box-shadow:0 16px 34px rgba(0,0,0,.36);
    }
    .qris img{
      width:100%;
      height:auto;
      display:block;
      border-radius:12px;
    }
    .oid{
      margin-top:14px;
      padding:12px;
      border-radius:16px;
      border:1px dashed rgba(255,255,255,.16);
      background:rgba(255,255,255,.03);
      word-break:break-all;
      font-size:12px;
      color:rgba(255,255,255,.82);
    }
    .row{
      display:flex;
      gap:10px;
      justify-content:center;
      flex-wrap:wrap;
      margin-top:14px;
    }
    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:46px;
      padding:0 16px;
      border-radius:14px;
      font-weight:900;
      border:1px solid rgba(255,255,255,.10);
      background:rgba(255,255,255,.05);
      color:#fff;
      text-decoration:none;
    }
    .btn.primary{
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      border-color:transparent;
    }
    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:inline-flex;
      align-items:center;
      min-height:54px;
      padding:0 18px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      font-weight:900;
      text-decoration:none;
      box-shadow:0 16px 30px rgba(0,0,0,.34);
      border:1px solid rgba(255,255,255,.10);
    }
    .toast{
      position:fixed;
      left:50%;
      transform:translateX(-50%);
      bottom:18px;
      z-index:1000;
      background:rgba(0,0,0,.72);
      border:1px solid rgba(255,255,255,.12);
      color:#fff;
      padding:12px 14px;
      border-radius:14px;
      opacity:0;
      pointer-events:none;
      transition:opacity .2s ease, transform .2s ease;
      backdrop-filter:blur(10px);
    }
    .toast.show{
      opacity:1;
      transform:translateX(-50%) translateY(-6px);
    }
  </style>
</head>
<body>
  <div class="box">
    <h1>Pembayaran QRIS</h1>
    <div class="muted">Produk: <b>$product_name</b></div>
    <div class="muted">Jumlah: <b>$qty</b></div>

    <div style="margin-top:12px;">Total transfer:</div>
    <div class="total">Rp $total</div>
    <div class="muted">Transfer sesuai nominal untuk verifikasi</div>

    <div style="margin-top:14px;">Scan QRIS:</div>
    <div class="qris"><img src="$qris" alt="QRIS"/></div>

    <div class="oid">Order ID:<br><b>$order_id</b></div>

    <div class="row">
      <a class="btn primary" href="/status/$order_id">Cek Status</a>
      <a class="btn" href="/">Kembali</a>
    </div>

    <div class="muted" style="margin-top:12px;">
      Catatan: order ini akan otomatis <b>cancel</b> jika belum dibayar dalam $ttl menit.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>
  <div id="toast" class="toast">Akun email berhasil dikirim ✅ Mengarahkan...</div>

  <script>
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
      --bg:#050505;
      --text:rgba(255,255,255,.95);
      --muted:rgba(255,255,255,.72);
      --line:rgba(255,255,255,.10);
      --red-1:#ff3b3b;
      --red-2:#c1121f;
      --shadow:0 18px 48px rgba(0,0,0,.42);
      --radius:24px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px 14px;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      color:var(--text);
      background:
        radial-gradient(1100px 600px at 0% -10%, rgba(255,59,59,.18), transparent 58%),
        radial-gradient(800px 500px at 100% 0%, rgba(193,18,31,.14), transparent 55%),
        linear-gradient(180deg, #0d0607 0%, #060606 100%);
    }
    .box{
      width:min(560px, 100%);
      padding:24px;
      border-radius:var(--radius);
      background:linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.04));
      border:1px solid var(--line);
      box-shadow:var(--shadow);
      text-align:center;
      position:relative;
      overflow:hidden;
      backdrop-filter:blur(12px);
    }
    .box::before{
      content:"";
      position:absolute;
      inset:-1px;
      background:radial-gradient(600px 220px at 15% 0%, rgba(255,59,59,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px;font-size:30px}
    .muted{color:var(--muted);font-size:13px;line-height:1.65}
    .badge{
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:10px 14px;
      margin-top:10px;
      border-radius:999px;
      font-weight:950;
      letter-spacing:.5px;
      background:$badge;
      color:#fff;
      box-shadow:0 12px 26px rgba(0,0,0,.20);
    }
    .spin{
      width:14px;
      height:14px;
      border:2px solid rgba(255,255,255,.22);
      border-top-color:rgba(255,255,255,.92);
      border-radius:50%;
      animation:spin 1s linear infinite;
    }
    @keyframes spin{to{transform:rotate(360deg)}}
    .grid{
      margin-top:16px;
      display:grid;
      grid-template-columns:repeat(2, minmax(0,1fr));
      gap:10px;
    }
    .mini{
      padding:14px;
      border-radius:18px;
      background:rgba(255,255,255,.04);
      border:1px solid rgba(255,255,255,.08);
    }
    .mini .t{font-size:12px;color:var(--muted)}
    .mini .v{font-size:24px;font-weight:950;margin-top:4px}
    .toast{
      position:fixed;
      left:50%;
      transform:translateX(-50%);
      bottom:18px;
      z-index:1000;
      background:rgba(0,0,0,.72);
      border:1px solid rgba(255,255,255,.12);
      color:#fff;
      padding:12px 14px;
      border-radius:14px;
      opacity:0;
      pointer-events:none;
      transition:opacity .2s ease, transform .2s ease;
      backdrop-filter:blur(10px);
    }
    .toast.show{
      opacity:1;
      transform:translateX(-50%) translateY(-6px);
    }
    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:inline-flex;
      align-items:center;
      min-height:54px;
      padding:0 18px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      font-weight:900;
      text-decoration:none;
      box-shadow:0 16px 30px rgba(0,0,0,.34);
      border:1px solid rgba(255,255,255,.10);
    }
    @media (max-width:520px){ .grid{grid-template-columns:1fr} }
  </style>
</head>
<body>
  <div class="box">
    <h1>Status Order</h1>
    <div class="muted">Produk: <b>$pid</b></div>
    <div class="muted">Jumlah: <b>$Jumlah</b></div>
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
      Halaman ini akan otomatis redirect ke akun email setelah verifikasi.<br/>
      Jika sudah bayar tapi lama, klik tombol Chat Admin di pojok kanan bawah untuk konfirmasi.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>
  <div id="toast" class="toast">Akun email berhasil dikirim ✅ Mengarahkan...</div>

  <script>
    let ttl = $ttl_sec;
    let every = 2;
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

    setInterval(poll, every * 1000);
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
  <title>Akun Akses</title>
  <style>
    :root{
      --bg:#050505;
      --text:rgba(255,255,255,.95);
      --muted:rgba(255,255,255,.72);
      --line:rgba(255,255,255,.10);
      --red-1:#ff3b3b;
      --red-2:#c1121f;
      --shadow:0 18px 48px rgba(0,0,0,.42);
      --radius:24px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px 14px;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      color:var(--text);
      background:
        radial-gradient(1100px 600px at 0% -10%, rgba(255,59,59,.18), transparent 58%),
        radial-gradient(800px 500px at 100% 0%, rgba(193,18,31,.14), transparent 55%),
        linear-gradient(180deg, #0d0607 0%, #060606 100%);
    }
    .box{
      width:min(580px, 100%);
      padding:24px;
      border-radius:var(--radius);
      background:linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.04));
      border:1px solid var(--line);
      box-shadow:var(--shadow);
      text-align:center;
      position:relative;
      overflow:hidden;
      backdrop-filter:blur(12px);
    }
    .box::before{
      content:"";
      position:absolute;
      inset:-1px;
      background:radial-gradient(600px 220px at 15% 0%, rgba(255,59,59,.16), transparent 60%);
      pointer-events:none;
    }
    h1{margin:0 0 8px;font-size:30px}
    .muted{color:var(--muted);font-size:13px;line-height:1.65}
    .success{
      display:inline-flex;
      align-items:center;
      gap:10px;
      padding:10px 14px;
      margin-top:10px;
      border-radius:999px;
      background:rgba(255,59,59,.14);
      border:1px solid rgba(255,255,255,.10);
      font-weight:900;
    }
    .code{
      margin:16px auto 12px;
      padding:16px 14px;
      border-radius:18px;
      background:rgba(0,0,0,.34);
      border:1px solid rgba(255,255,255,.12);
      font-size:18px;
      font-weight:950;
      letter-spacing:.3px;
      word-break:break-all;
      white-space:pre-wrap;
      text-align:left;
    }
    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:48px;
      padding:0 18px;
      border-radius:14px;
      border:none;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      font-weight:900;
      cursor:pointer;
      box-shadow:0 16px 30px rgba(193,18,31,.20);
    }
    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:inline-flex;
      align-items:center;
      min-height:54px;
      padding:0 18px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      font-weight:900;
      text-decoration:none;
      box-shadow:0 16px 30px rgba(0,0,0,.34);
      border:1px solid rgba(255,255,255,.10);
    }
  </style>
</head>
<body>
  <div class="box">
    <h1>Akun Email</h1>
    <div class="muted">Status: <b>PAID ✅</b></div>
    <div class="muted">Produk: <b>$pid</b></div>

    <div class="success">✅ Akun email berhasil dikirim</div>

    <div class="code" id="vcode">$code</div>

    <button class="btn" id="copyBtn" type="button">Salin Email</button>

    <div class="muted" style="margin-top:12px;">
      Jangan gunakan temp mail/number untuk pemulihan, gunakan email/nomor asli untuk pemulihan.
      Kalau akun kena verif karena pakai data palsu, saya tidak tanggung jawab.
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">💬 Chat Admin</a>

  <script>
    const btn = document.getElementById("copyBtn");
    const code = document.getElementById("vcode").innerText;

    btn.onclick = async () => {
      try{
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(code);
        } else {
          const ta = document.createElement("textarea");
          ta.value = code;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
        btn.innerText = "✅ Tersalin";
        setTimeout(()=>btn.innerText="Salin Email",1500);
      }catch(e){
        btn.innerText = "Gagal menyalin";
        setTimeout(()=>btn.innerText="Salin Email",1500);
      }
    };
  </script>
</body>
</html>
""")

ADMIN_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Admin Panel</title>
  <style>
    body{
      margin:0;
      padding:20px;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      background:
        radial-gradient(1100px 600px at 0% -10%, rgba(255,59,59,.18), transparent 58%),
        radial-gradient(800px 500px at 100% 0%, rgba(193,18,31,.14), transparent 55%),
        linear-gradient(180deg, #0d0607 0%, #060606 100%);
      color:#fff;
    }
    .box{max-width:980px;margin:0 auto}
    .row{
      background:rgba(255,255,255,.06);
      border:1px solid rgba(255,255,255,.10);
      padding:14px;
      border-radius:18px;
      margin-bottom:10px;
      display:flex;
      gap:12px;
      align-items:center;
      justify-content:space-between;
      backdrop-filter:blur(10px);
    }
    .muted{opacity:.75;font-size:12px;word-break:break-all}
    .vbtn{
      background:linear-gradient(135deg, #ff3b3b, #c1121f);
      border:none;
      color:#fff;
      padding:10px 12px;
      border-radius:12px;
      cursor:pointer;
      font-weight:950;
    }
    .lbtn{
      display:inline-block;
      background:rgba(255,255,255,.06);
      border:1px solid rgba(255,255,255,.12);
      color:white;
      padding:10px 12px;
      border-radius:12px;
      text-decoration:none;
      font-weight:950;
    }
    .act{
      min-width:260px;
      display:flex;
      flex-direction:column;
      align-items:flex-end;
      gap:8px;
    }
    @media(max-width:740px){
      .row{flex-direction:column;align-items:flex-start}
      .act{align-items:flex-start;min-width:unset;width:100%}
    }
  </style>
</head>
<body>
  <div class="box">
    <h2 style="margin:0 0 10px;">Admin Panel</h2>
    <div style="opacity:.75;margin-bottom:12px;">
      Klik tombol untuk verifikasi + otomatis assign akun email lalu redirect ke halaman akun email.
    </div>
    $items
  </div>
</body>
</html>
""")


# ======================
# ROUTES
# ======================
@app.get("/", response_class=HTMLResponse)
def home():
    stock = get_stock_map()
    cards = ""

    for pid, p in PRODUCTS.items():
        stok = int(stock.get(pid, 0))
        stok_txt = f"Stok: {stok} tersedia"
        feats = PRODUCT_FEATS.get(pid) or p.get("features") or DEFAULT_FEATURES
        feats_html = "".join(
            f'<div class="feat"><i>✅</i><span>{f}</span></div>'
            for f in feats
        )
        hot = '<span class="hot">🔥 TERLARIS</span>' if pid == "gemini" else ""
        disabled_attr = "disabled aria-disabled='true'" if stok <= 0 else ""

        cards += f"""
          <article class="product" data-product="{pid}" data-stock="{stok}">
            <div class="product-top">
              <div>
                <h3 class="ptitle">{p["name"]}</h3>
                <div class="psub" id="stock-{pid}">{stok_txt}</div>
              </div>
              {hot}
            </div>

            <div class="price">Rp {rupiah(int(p["price"]))}</div>

            <div class="feats">
              {feats_html}
            </div>

            <div class="buyrow">
              <div class="qtybox">
                <button class="qtybtn qty-minus" type="button" {disabled_attr}>-</button>
                <span class="qtyval">1</span>
                <button class="qtybtn qty-plus" type="button" {disabled_attr}>+</button>
              </div>

              <button class="btn primary buybtn" type="button" data-buy="{pid}" {disabled_attr}>
                Beli Sekarang
              </button>
            </div>

            <div class="note">{("Stok habis, tombol beli dinonaktifkan." if stok <= 0 else "Bayar QRIS → tunggu verifikasi → akun email terkirim")}</div>
          </article>
        """

    html = _tpl_render(
        HOME_HTML,
        cards=cards,
        year=now_utc().year,
        logo_url=LOGO_URL,
        brand_name=BRAND_NAME,
        brand_tagline=BRAND_TAGLINE,
    )
    return HTMLResponse(html)


@app.get("/checkout/{product_id}")
def checkout(product_id: str, request: Request, qty: int = Query(1, ge=1, le=99)):
    if product_id not in PRODUCTS:
        return HTMLResponse("<h3>Produk tidak ditemukan</h3>", status_code=404)

    ip = _client_ip(request)
    if not _rate_limit_checkout(ip):
        return HTMLResponse("<h3>Terlalu banyak request</h3><p>Coba lagi beberapa menit.</p>", status_code=429)

    cookie_key = f"oid_{product_id}"
    oid = request.cookies.get(cookie_key)
    if oid:
        try:
            r = supabase.table("orders").select("*").eq("id", oid).limit(1).execute()
            if r.data:
                order = r.data[0]
                order, expired = _ensure_not_expired(order)
                if not expired and (order.get("status") or "").lower() == "pending":
                    return RedirectResponse(url=f"/pay/{oid}", status_code=302)
        except Exception:
            pass

    stock_map = get_stock_map()
    stock = int(stock_map.get(product_id, 0))
    if stock <= 0:
        return HTMLResponse("<h3>Stok habis</h3>", status_code=400)

    if qty > stock:
        qty = stock

    base_price = int(PRODUCTS[product_id]["price"])
    unique_code = random.randint(101, 999)
    total = (base_price * int(qty)) + unique_code

    order_id = str(uuid.uuid4())
    created_at = now_utc().isoformat()

    ins = supabase.table("orders").insert(
        {
            "id": order_id,
            "product_id": product_id,
            "qty": int(qty),
            "unit": int(base_price),
            "amount_idr": int(total),
            "status": "pending",
            "created_at": created_at,
            "voucher_code": None,
        }
    ).execute()

    if not ins.data:
        return HTMLResponse("<h3>Gagal membuat order</h3><p>Cek RLS / key / schema orders.</p>", status_code=500)

    resp = RedirectResponse(url=f"/pay/{order_id}", status_code=302)
    resp.set_cookie(cookie_key, order_id, max_age=ORDER_TTL_MINUTES * 60, httponly=True, samesite="lax")
    return resp


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
    qty = int(order.get("qty") or 1)
    unit = int(order.get("unit") or PRODUCTS.get(pid, {}).get("price", 0) or 0)
    subtotal = unit * qty
    product_name = PRODUCTS.get(pid, {}).get("name", pid)

    html = _tpl_render(
        PAY_HTML,
        product_name=product_name,
        qty=str(qty),
        unit=rupiah(unit),
        subtotal=rupiah(subtotal),
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
    qty = int(order.get("qty") or 1)

    badge = "#eab308" if st == "pending" else "#c1121f"
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))

    html = _tpl_render(
        STATUS_HTML,
        pid=pid,
        qty=str(qty),
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
        <html><body style="font-family:Arial;background:#070707;color:white;text-align:center;padding:40px">
          <h2>Akun Email</h2>
          <p>Status: PAID ✅</p>
          <p style="opacity:.8">Maaf, stok untuk produk ini sedang habis.</p>
        </body></html>
        """)

    html = _tpl_render(VOUCHER_HTML, pid=order.get("product_id"), code=code)
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
    sid = request.cookies.get("vis_sid")
    if not sid:
        sid = str(uuid.uuid4())

    t = time.time()
    for k, v in list(_VISITOR_SESS.items()):
        if t - v > 45:
            _VISITOR_SESS.pop(k, None)
    _VISITOR_SESS[sid] = t

    count = _VISITOR_BASE + len(_VISITOR_SESS) + random.randint(0, 9)
    resp = JSONResponse({"ok": True, "count": count})
    resp.set_cookie("vis_sid", sid, max_age=24 * 3600, httponly=True, samesite="lax")
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
        .select("id,product_id,qty,unit,amount_idr,status,created_at,voucher_code")
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
            qty = int(o.get("qty") or 1)
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
                <a class="lbtn" href="/voucher/{oid}">Buka Akun Email</a>
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
                <div><b>{pid}</b> — Qty {qty} — Rp {rupiah(amt)}</div>
                <div class="muted">ID: {oid}</div>
                <div class="muted">{created}</div>
                <div class="muted">Status: {st}</div>
              </div>
              <div class="act">{action}</div>
            </div>
            """

    return HTMLResponse(_tpl_render(ADMIN_HTML, items=items))


@app.post("/admin/verify/{order_id}")
def admin_verify(order_id: str, token: Optional[str] = None):
    if not require_admin(token):
        return PlainTextResponse("Unauthorized", status_code=401)

    res = supabase.table("orders").select("id,product_id,qty,status,voucher_code").eq("id", order_id).limit(1).execute()
    if not res.data:
        return PlainTextResponse("Order not found", status_code=404)

    order = res.data[0]
    pid = order.get("product_id")
    st = (order.get("status") or "pending").lower()
    vcode = order.get("voucher_code")

    if st == "paid" and vcode:
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)

    if st == "cancelled":
        return HTMLResponse("<h3>Order sudah cancelled/expired</h3>", status_code=410)

    qty = int(order.get("qty") or 1)
    claim_vouchers_for_order(order_id, pid, qty)

    return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)
