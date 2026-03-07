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
# Safe template renderer (avoid string.Template parsing errors)
# We only replace our own $placeholders like $cards, $year, etc.
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
PRODUCTS = {
    "gemini": {"name": "Gemini AI Pro 1 Tahun", "price": 34_000,
        "features": ['Akses penuh Gemini AI Pro', 'Google Drive 2TB', 'Flow + 1.000 credit', 'Aktivasi cepat']
    },
    "chatgpt": {"name": "ChatGPT Plus 1 Bulan", "price": 14_000,
        "features": ['Akses model ChatGPT terbaru', 'Respons lebih cepat & akurat', 'Cocok untuk riset & coding', 'Aktivasi cepat']
    },
}


# Per-product fitur/benefit (beda produk beda isi)
# Isi list ini akan ditampilkan sebagai 1 kotak (1 div) dengan teks turun ke bawah (line break)
PRODUCT_FEATS = {
    # Contoh: sesuaikan key dengan id produk di PRODUCTS (pid)
    # "gemini": ["...", "..."],
}

QR_IMAGE_URL = os.getenv(
    "QR_IMAGE_URL",
    "https://i.ibb.co.com/fGKd9LT4/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-1.png",
)

LOGO_IMAGE_URL = os.getenv(
    "LOGO_IMAGE_URL",
    "https://i.ibb.co.com/3m2fyH71/Picsart-24-11-05-00-57-51-857.jpg",
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

def claim_vouchers_for_order(order_id: str, product_id: str, qty: int) -> Optional[list[str]]:
    """
    Ambil voucher sejumlah qty (status='available') untuk product_id,
    set jadi used, lalu simpan ke order (voucher_code newline-separated).
    """
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

    # set voucher jadi used
    q = supabase.table("vouchers").update({"status": "used"})
    if hasattr(q, "in_"):
        q = q.in_("id", ids)
        q.execute()
    else:
        for vid in ids:
            supabase.table("vouchers").update({"status": "used"}).eq("id", vid).execute()

    # set order jadi paid + simpan codes voucher (newline-separated)
    supabase.table("orders").update({
        "status": "paid",
        "voucher_code": "\n".join(codes) if codes else None,
    }).eq("id", order_id).execute()

    return codes

def voucher_lines(v: str | None) -> list[str]:
    txt = (v or "").strip()
    if not txt:
        return []
    return [line.strip() for line in txt.splitlines() if line.strip()]
# ======================
# Templates
# ======================
HOME_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-YGSFDD04M4"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-YGSFDD04M4');
  </script>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Impura</title>
  <style>
    :root{
      --bg:#050507;
      --bg-2:#0b0b0f;
      --panel:rgba(17,17,22,.82);
      --panel-soft:rgba(255,255,255,.04);
      --panel-strong:rgba(255,255,255,.06);
      --line:rgba(255,255,255,.10);
      --line-strong:rgba(255,90,90,.18);
      --text:#f7f7fb;
      --muted:rgba(255,255,255,.70);
      --red-1:#ff2b2b;
      --red-2:#c50017;
      --red-3:#ff6a6a;
      --red-glow:rgba(255,43,43,.28);
      --shadow:0 20px 60px rgba(0,0,0,.50);
      --shadow-soft:0 16px 36px rgba(0,0,0,.35);
      --radius:24px;
      --radius-sm:16px;
      --container:1360px;
      --header-h:88px;
    }
    *{box-sizing:border-box}
    html{scroll-behavior:smooth}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(760px 420px at 10% -10%, rgba(255,0,51,.18), transparent 60%),
        radial-gradient(980px 520px at 100% 0%, rgba(150,0,0,.16), transparent 55%),
        radial-gradient(820px 520px at 50% 100%, rgba(255,40,40,.08), transparent 58%),
        linear-gradient(180deg, #08080b 0%, #050507 100%);
      min-height:100vh;
      overflow-x:hidden;
    }
    body:before{
      content:"";
      position:fixed; inset:0;
      background:
        linear-gradient(rgba(255,255,255,.015) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.015) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: radial-gradient(circle at center, #000 40%, transparent 95%);
      pointer-events:none;
      opacity:.45;
      z-index:0;
    }
    a{color:inherit}
    .site-header{
      position:sticky;
      top:0;
      z-index:1000;
      backdrop-filter: blur(18px);
      background:linear-gradient(180deg, rgba(8,8,12,.92), rgba(8,8,12,.72));
      border-bottom:1px solid rgba(255,255,255,.07);
      box-shadow:0 12px 24px rgba(0,0,0,.18);
    }
    .header-inner,
    .wrap{
      width:min(var(--container), calc(100vw - 28px));
      margin:0 auto;
      position:relative;
      z-index:1;
    }
    .header-inner{
      min-height:var(--header-h);
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:14px;
      padding:14px 0;
    }
    .wrap{
      padding:26px 0 96px;
    }
    .brand{
      display:flex;
      align-items:center;
      gap:14px;
      min-width:0;
    }
    .logo-shell{
      position:relative;
      width:56px;
      height:56px;
      border-radius:999px;
      padding:3px;
      background:linear-gradient(135deg, rgba(255,106,106,.95), rgba(197,0,23,.85));
      box-shadow:0 0 0 1px rgba(255,255,255,.10), 0 0 28px rgba(255,43,43,.26);
      flex:0 0 auto;
      animation: pulseGlow 3.8s ease-in-out infinite;
    }
    .logo-shell:after{
      content:"";
      position:absolute;
      inset:-8px;
      border-radius:999px;
      background:radial-gradient(circle, rgba(255,43,43,.26), transparent 70%);
      z-index:-1;
      filter:blur(10px);
    }
    .logo{
      width:100%;
      height:100%;
      border-radius:999px;
      display:block;
      object-fit:cover;
      background:#110608;
    }
    .brand-copy{min-width:0}
    .brand h1{
      margin:0;
      font-size:clamp(18px, 2.6vw, 24px);
      letter-spacing:.2px;
      line-height:1.1;
    }
    .tag{
      margin-top:4px;
      color:var(--muted);
      font-size:13px;
      max-width:60ch;
    }
    .nav-pill{
      display:flex;
      align-items:center;
      gap:10px;
      flex-wrap:wrap;
      justify-content:flex-end;
    }
    .pill{
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.035);
      padding:11px 14px;
      border-radius:999px;
      font-size:12px;
      color:var(--muted);
      white-space:nowrap;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
    }
    .pill.cta{
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      border-color:transparent;
      text-decoration:none;
      font-weight:900;
      box-shadow:0 12px 24px rgba(197,0,23,.22);
      transition:transform .2s ease, box-shadow .2s ease;
    }
    .pill.cta:hover{transform:translateY(-2px); box-shadow:0 18px 34px rgba(197,0,23,.30)}

    .hero{
      display:grid;
      grid-template-columns:minmax(0,1.2fr) minmax(340px,.8fr);
      gap:18px;
      align-items:stretch;
      margin-top:8px;
    }
    .card,
    .p{
      position:relative;
      overflow:hidden;
      border-radius:var(--radius);
      background:
        linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.028)),
        rgba(11,11,15,.88);
      border:1px solid rgba(255,255,255,.08);
      box-shadow:var(--shadow);
      isolation:isolate;
    }
    .card:before,
    .p:before{
      content:"";
      position:absolute;
      inset:0;
      background:
        radial-gradient(520px 220px at 0% 0%, rgba(255,43,43,.18), transparent 60%),
        radial-gradient(420px 220px at 100% 0%, rgba(197,0,23,.16), transparent 55%);
      pointer-events:none;
      opacity:.95;
    }
    .card:after,
    .p:after{
      content:"";
      position:absolute;
      inset:0;
      border-radius:inherit;
      padding:1px;
      background:linear-gradient(135deg, rgba(255,88,88,.34), rgba(255,255,255,.05), rgba(145,0,0,.28));
      -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
      -webkit-mask-composite:xor;
      mask-composite:exclude;
      pointer-events:none;
    }
    .heroL{
      padding:28px;
      min-height:360px;
      display:flex;
      flex-direction:column;
      justify-content:center;
    }
    .eyebrow{
      display:inline-flex;
      align-items:center;
      gap:8px;
      width:max-content;
      max-width:100%;
      border:1px solid rgba(255,255,255,.10);
      background:rgba(255,255,255,.035);
      color:rgba(255,255,255,.82);
      border-radius:999px;
      padding:10px 14px;
      font-size:12px;
      letter-spacing:.2px;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.05);
    }
    .eyebrow .dot{
      width:8px; height:8px; border-radius:999px;
      background:linear-gradient(135deg, var(--red-3), var(--red-2));
      box-shadow:0 0 12px rgba(255,90,90,.5);
      flex:0 0 auto;
    }
    .title{
      font-size:clamp(32px, 5vw, 56px);
      line-height:1.05;
      margin:18px 0 12px;
      max-width:12ch;
      letter-spacing:-.04em;
    }
    .title .accent{
      background:linear-gradient(180deg, #fff, #ff8f8f 120%);
      -webkit-background-clip:text;
      -webkit-text-fill-color:transparent;
    }
    .sub{
      font-size:15px;
      line-height:1.75;
      color:var(--muted);
      max-width:60ch;
    }
    .actions{
      display:flex;
      gap:12px;
      flex-wrap:wrap;
      margin-top:22px;
    }
    .btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      gap:8px;
      padding:14px 18px;
      border-radius:16px;
      text-decoration:none;
      font-weight:900;
      font-size:14px;
      letter-spacing:.2px;
      border:1px solid rgba(255,255,255,.10);
      transition:transform .22s ease, box-shadow .22s ease, border-color .22s ease, background .22s ease;
      position:relative;
      overflow:hidden;
      cursor:pointer;
    }
    .btn:hover{transform:translateY(-3px)}
    .btn.primary{
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      border-color:transparent;
      box-shadow:0 16px 34px rgba(197,0,23,.28);
    }
    .btn.primary:hover{box-shadow:0 22px 42px rgba(197,0,23,.34)}
    .btn.ghost{
      background:rgba(255,255,255,.03);
      color:var(--text);
      box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
    }
    .hero-metrics{
      display:grid;
      grid-template-columns:repeat(4, minmax(0, 1fr));
      gap:10px;
      margin-top:22px;
    }
    .metric{
      padding:14px 12px;
      border-radius:18px;
      background:rgba(255,255,255,.035);
      border:1px solid rgba(255,255,255,.08);
      box-shadow:var(--shadow-soft);
      transform:translateY(0);
      transition:transform .24s ease, border-color .24s ease;
    }
    .metric:hover{
      transform:translateY(-3px);
      border-color:rgba(255,106,106,.22);
    }
    .metric b{
      display:block;
      font-size:16px;
      margin-bottom:4px;
    }
    .metric span{
      display:block;
      color:var(--muted);
      font-size:12px;
      line-height:1.4;
    }

    .heroR{
      padding:22px;
      display:flex;
      flex-direction:column;
      justify-content:space-between;
      gap:14px;
    }
    .steps-head{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:12px;
      margin-bottom:4px;
    }
    .steps-title{
      font-weight:950;
      font-size:15px;
      margin:0;
      letter-spacing:.2px;
    }
    .mini-status{
      padding:8px 12px;
      border-radius:999px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.03);
      color:var(--muted);
      font-size:12px;
    }
    .step{
      display:grid;
      grid-template-columns:40px 1fr;
      gap:12px;
      padding:14px;
      border-radius:18px;
      background:rgba(255,255,255,.03);
      border:1px solid rgba(255,255,255,.08);
      box-shadow:var(--shadow-soft);
      transition:transform .22s ease, border-color .22s ease, background .22s ease;
    }
    .step:hover{
      transform:translateX(3px);
      border-color:rgba(255,106,106,.24);
      background:rgba(255,255,255,.042);
    }
    .num{
      width:40px;
      height:40px;
      border-radius:14px;
      display:flex;
      align-items:center;
      justify-content:center;
      font-weight:950;
      background:linear-gradient(135deg, rgba(255,70,70,.18), rgba(120,0,0,.24));
      border:1px solid rgba(255,106,106,.18);
      box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
      color:#fff;
    }
    .step b{display:block; font-size:14px; margin-bottom:4px}
    .step span{display:block; font-size:13px; color:var(--muted); line-height:1.55}
    .tip{
      border-radius:18px;
      padding:14px;
      border:1px solid rgba(255,255,255,.08);
      background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.02));
      color:var(--muted);
      font-size:12px;
      line-height:1.65;
    }

    .section-head{
      display:flex;
      justify-content:space-between;
      align-items:end;
      gap:14px;
      margin:26px 0 14px;
    }
    .section{
      font-size:18px;
      font-weight:950;
      letter-spacing:.2px;
      color:#fff;
    }
    .section-sub{
      font-size:13px;
      color:var(--muted);
      max-width:56ch;
      text-align:right;
    }
    .grid{
      display:grid;
      gap:16px;
      grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));
      align-items:stretch;
    }
    .p{
      padding:20px;
      display:flex;
      flex-direction:column;
      min-height:100%;
      transition:transform .25s ease, box-shadow .25s ease, border-color .25s ease;
    }
    .p:hover{
      transform:translateY(-6px);
      border-color:rgba(255,90,90,.22);
      box-shadow:0 28px 60px rgba(0,0,0,.56);
    }
    .card-top{
      display:flex;
      justify-content:space-between;
      gap:12px;
      align-items:flex-start;
    }
    .ptitle{
      font-weight:950;
      font-size:24px;
      line-height:1.18;
      margin:0 0 8px;
      letter-spacing:-.03em;
    }
    .psub{
      font-size:13px;
      color:var(--muted);
    }
    .hot{
      display:inline-flex;
      align-items:center;
      gap:8px;
      font-size:11px;
      font-weight:950;
      color:#fff;
      background:linear-gradient(135deg, rgba(255,60,60,.96), rgba(120,0,0,.88));
      padding:8px 12px;
      border-radius:999px;
      box-shadow:0 12px 28px rgba(197,0,23,.24);
      animation:floatY 3s ease-in-out infinite;
      white-space:nowrap;
    }
    .price{
      font-size:36px;
      font-weight:950;
      margin:18px 0 14px;
      letter-spacing:-.04em;
      line-height:1;
    }
    .price small{
      font-size:14px;
      color:var(--muted);
      font-weight:700;
      letter-spacing:0;
      margin-left:6px;
    }
    .feats{
      display:grid;
      grid-template-columns:repeat(2, minmax(0, 1fr));
      gap:10px;
      margin-bottom:16px;
      flex:1;
    }
    .feat{
      display:flex;
      align-items:flex-start;
      gap:10px;
      font-size:13px;
      color:rgba(255,255,255,.9);
      background:rgba(255,255,255,.035);
      border:1px solid rgba(255,255,255,.08);
      padding:12px;
      border-radius:16px;
      line-height:1.5;
      box-shadow:var(--shadow-soft);
      transition:transform .2s ease, border-color .2s ease;
    }
    .feat:hover{
      transform:translateY(-2px);
      border-color:rgba(255,106,106,.22);
    }
    .feat i{
      width:20px;
      height:20px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      box-shadow:0 0 18px rgba(255,43,43,.22);
      display:inline-flex;
      align-items:center;
      justify-content:center;
      font-style:normal;
      font-size:11px;
      flex:0 0 auto;
      margin-top:1px;
    }
    .buyrow{
      display:flex;
      gap:12px;
      align-items:center;
      flex-wrap:wrap;
      margin-top:8px;
    }
    .qtybox{
      display:flex;
      align-items:center;
      gap:10px;
      background:rgba(255,255,255,.04);
      border:1px solid rgba(255,255,255,.10);
      border-radius:16px;
      padding:10px 12px;
      box-shadow:var(--shadow-soft);
    }
    .qtybtn{
      width:40px;
      height:40px;
      border-radius:14px;
      border:1px solid rgba(255,255,255,.14);
      background:rgba(255,255,255,.02);
      color:var(--text);
      font-weight:900;
      font-size:16px;
      transition:transform .2s ease, border-color .2s ease;
    }
    .qtybtn:hover:not(:disabled){
      transform:translateY(-1px);
      border-color:rgba(255,106,106,.24);
    }
    .qtybtn:disabled{opacity:.45; cursor:not-allowed}
    .qtyval{min-width:28px; text-align:center; font-weight:900}
    .note{
      margin-top:12px;
      font-size:12px;
      color:var(--muted);
      line-height:1.6;
      padding-top:12px;
      border-top:1px solid rgba(255,255,255,.06);
    }

    .footer{
      margin-top:22px;
      color:rgba(255,255,255,.52);
      font-size:12px;
      display:flex;
      justify-content:space-between;
      flex-wrap:wrap;
      gap:10px;
      border-top:1px solid rgba(255,255,255,.08);
      padding-top:16px;
    }

    .wa{
      position:fixed;
      right:18px;
      bottom:18px;
      z-index:999;
      display:flex;
      align-items:center;
      gap:10px;
      padding:14px 18px;
      border-radius:999px;
      background:linear-gradient(135deg, var(--red-1), var(--red-2));
      color:#fff;
      text-decoration:none;
      font-weight:950;
      box-shadow:0 18px 34px rgba(197,0,23,.28);
      border:1px solid rgba(255,255,255,.10);
      transition:transform .2s ease, box-shadow .2s ease;
    }
    .wa:hover{
      transform:translateY(-3px);
      box-shadow:0 24px 40px rgba(197,0,23,.36);
    }

    .reveal{
      opacity:0;
      transform:translateY(18px);
      transition:opacity .6s ease, transform .6s ease;
    }
    .reveal.show{
      opacity:1;
      transform:translateY(0);
    }

    @keyframes floatY{
      0%,100%{transform:translateY(0)}
      50%{transform:translateY(-3px)}
    }
    @keyframes pulseGlow{
      0%,100%{box-shadow:0 0 0 1px rgba(255,255,255,.10), 0 0 26px rgba(255,43,43,.22)}
      50%{box-shadow:0 0 0 1px rgba(255,255,255,.12), 0 0 34px rgba(255,43,43,.32)}
    }

    @media (max-width: 1080px){
      .hero{
        grid-template-columns:1fr;
      }
      .heroL{min-height:unset}
    }
    @media (max-width: 760px){
      :root{ --header-h:74px; }
      .header-inner,.wrap{
        width:min(var(--container), calc(100vw - 20px));
      }
      .header-inner{
        padding:10px 0;
      }
      .logo-shell{
        width:48px; height:48px;
      }
      .tag{
        font-size:12px;
      }
      .nav-pill{
        display:none;
      }
      .heroL,.heroR,.p{
        padding:18px;
      }
      .hero-metrics{
        grid-template-columns:repeat(2, minmax(0,1fr));
      }
      .feats{
        grid-template-columns:1fr;
      }
      .price{
        font-size:32px;
      }
      .section-head{
        flex-direction:column;
        align-items:flex-start;
      }
      .section-sub{
        text-align:left;
      }
    }
    @media (max-width: 520px){
      .wrap{
        padding:18px 0 92px;
      }
      .title{
        max-width:100%;
      }
      .actions,
      .buyrow{
        flex-direction:column;
        align-items:stretch;
      }
      .btn,
      .qtybox{
        width:100%;
      }
      .grid{
        grid-template-columns:1fr;
      }
      .hero-metrics{
        grid-template-columns:1fr 1fr;
      }
      .price{
        font-size:30px;
      }
      .wa{
        right:14px;
        bottom:14px;
      }
    }
  </style>
</head>
<body>
  <header class="site-header">
    <div class="header-inner">
      <div class="brand">
        <div class="logo-shell">
          <img class="logo" src="$logo" alt="Logo Impura"/>
        </div>
        <div class="brand-copy">
          <h1>Impura</h1>
          <div class="tag">Layanan AI premium dengan tampilan lebih profesional, cepat, dan rapi.</div>
        </div>
      </div>
      <div class="nav-pill">
        <div class="pill">⚡ Fast checkout</div>
        <div class="pill">🔒 QRIS aman</div>
        <a class="pill cta" href="#produk">Lihat Produk</a>
      </div>
    </div>
  </header>

  <div class="wrap">
    <div class="hero">
      <div class="card heroL reveal">
        <div class="eyebrow"><span class="dot"></span> Cyber red premium storefront</div>
        <div class="title">Beli akses AI premium dengan nuansa <span class="accent">lebih mewah & tegas</span>.</div>
        <div class="sub">
          Pilih produk → bayar QRIS → tunggu verifikasi → sistem otomatis kirim akun email.
          Sekarang tampilannya dibuat lebih premium dengan gaya cyber merah hitam yang lebih kuat di desktop maupun mobile.
        </div>
        <div class="actions">
          <a class="btn primary" href="#produk">Lihat Produk</a>
          <a class="btn ghost" href="#cara">Cara Beli</a>
        </div>
        <div class="hero-metrics">
          <div class="metric"><b>QRIS</b><span>Pembayaran cepat dan jelas tanpa langkah rumit.</span></div>
          <div class="metric"><b>Otomatis</b><span>Status order dan alur pembelian terasa lebih rapi.</span></div>
          <div class="metric"><b>Premium</b><span>Tampilan merah hitam lebih cocok dengan identitas brand.</span></div>
          <div class="metric"><b>Support</b><span>Admin tetap mudah dihubungi lewat tombol chat.</span></div>
        </div>
      </div>

      <div class="card heroR reveal" id="cara">
        <div class="steps-head">
          <div class="steps-title">Cara beli (3 langkah)</div>
          <div class="mini-status">UI baru • sticky header</div>
        </div>
        <div class="step"><div class="num">1</div><div><b>Pilih produk</b><span>Klik tombol beli pada produk yang kamu inginkan, lalu atur jumlah pembelian dengan cepat.</span></div></div>
        <div class="step"><div class="num">2</div><div><b>Bayar QRIS</b><span>Transfer sesuai nominal. Tampilan baru dibuat lebih kontras supaya fokus user tetap ke aksi utama.</span></div></div>
        <div class="step"><div class="num">3</div><div><b>Verifikasi</b><span>Setelah pembayaran masuk, sistem akan meneruskan user ke halaman akun email secara otomatis.</span></div></div>
        <div class="tip">
          Tip: header akan tetap muncul saat user scroll, jadi navigasi ke produk dan cara beli tetap mudah terlihat kapan pun.
        </div>
      </div>
    </div>

    <div class="section-head reveal">
      <div class="section" id="produk">Produk tersedia</div>
      <div class="section-sub">Card produk dibuat lebih premium, fitur tidak lagi menyatu dalam satu kotak, dan spacing kiri-kanan lebih pas di mobile maupun desktop.</div>
    </div>
    <div class="grid">
      $cards
    </div>

    <div class="footer">
      <div>© $year impura</div>
      <div>Cyber red UI • premium storefront</div>
    </div>
  </div>

  <a class="wa" href="https://wa.me/6281317391284" target="_blank" rel="noreferrer">
    💬 Chat Admin
  </a>

  <script>
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

    (function(){
      const io = new IntersectionObserver((entries)=>{
        entries.forEach((entry)=>{
          if(entry.isIntersecting){
            entry.target.classList.add("show");
          }
        });
      }, {threshold:.14});
      document.querySelectorAll(".reveal, .p").forEach((el)=> io.observe(el));
    })();
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
      document.querySelectorAll(".p[data-product]").forEach(card=>{
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
        const r = await fetch("/api/stock");
        const j = await r.json();
        if(!j || !j.stock) return;
        for(const pid in j.stock){
          const el = document.getElementById("stock-" + pid);
          if(el) el.textContent = "Stok: " + j.stock[pid] + " tersedia";
          const card = document.querySelector('.p[data-product="' + pid + '"]');
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
      --bg:#070c18; --glass:rgba(255,255,255,.06); --line:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.70);
      --g1:#ff3b3b; --g2:#a40019; --shadow:0 18px 42px rgba(0,0,0,.45); --r:22px;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;
      color:var(--text);
      background:
        radial-gradient(900px 520px at 20% -10%, rgba(255,43,43,.18), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(164,0,25,.16), transparent 55%),
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
      background:linear-gradient(135deg, rgba(255,43,43,.95), rgba(164,0,25,.85));
      color:#fff;text-decoration:none;font-weight:950;
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
    <div class="muted">Qty: <b>$qty</b> × Rp $unit = <b>Rp $subtotal</b></div>

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
  <div id="toast" class="toast">Akun email berhasil dikirim ✅ Mengarahkan...</div>

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
        radial-gradient(900px 520px at 20% -10%, rgba(255,43,43,.16), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(164,0,25,.15), transparent 55%),
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
      background:linear-gradient(135deg, rgba(255,43,43,.96), rgba(164,0,25,.85));
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
      background:linear-gradient(135deg, rgba(255,43,43,.95), rgba(164,0,25,.85));
      color:#fff;text-decoration:none;font-weight:950;
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
    <div class="muted">Qty: <b>$qty</b></div>
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
  <title>Akun Akses</title>
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
        radial-gradient(900px 520px at 20% -10%, rgba(255,43,43,.16), transparent 60%),
        radial-gradient(900px 520px at 80% 0%, rgba(164,0,25,.15), transparent 55%),
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
      white-space:pre-wrap;
      position:relative;
    }
    .btn{
      display:inline-flex;align-items:center;justify-content:center;gap:8px;
      padding:12px 16px;border-radius:14px;
      background:linear-gradient(135deg, rgba(34,197,94,.95), rgba(34,197,94,.78));
      color:#fff;
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
      border:1px solid rgba(164,0,25,.15);
      color:rgba(255,255,255,.92);
      font-weight:900;
      animation: pop .6s ease both;
    }
    @keyframes pop{from{transform:scale(.96);opacity:0} to{transform:scale(1);opacity:1}}
    .wa{
      position:fixed; right:18px; bottom:18px; z-index:999;
      display:flex;align-items:center;gap:10px;
      padding:14px 16px;border-radius:999px;
      background:linear-gradient(135deg, rgba(255,43,43,.95), rgba(164,0,25,.85));
      color:#fff;text-decoration:none;font-weight:950;
      box-shadow:0 14px 28px rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.12);
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

    <script>
btn.onclick = async () => {
  const text = code;

  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }

  btn.innerText="✅ Tersalin";
  setTimeout(()=>btn.innerText="Salin Email",1500);
};
</script>

    <div class="muted" style="margin-top:12px;">
      Jangan gunakan temp mail/number untuk pemulihan, gunakan email/nomor asli untuk pemulihan. Kalau masih ngeyel dan akun kena verif, saya tidak tanggung jawab
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
    .vbtn{background:#22c55e;border:none;color:#fff;padding:10px 12px;border-radius:12px;cursor:pointer;font-weight:950}
    .lbtn{display:inline-block;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:white;padding:10px 12px;border-radius:12px;text-decoration:none;font-weight:950}
    .act{min-width:260px;display:flex;flex-direction:column;align-items:flex-end;gap:8px}
    @media(max-width:740px){.row{flex-direction:column;align-items:flex-start}.act{align-items:flex-start;min-width:unset;width:100%}}
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
        feats = (PRODUCT_FEATS.get(pid) or p.get("features") or DEFAULT_FEATURES)
        feats_html = "".join([f'<div class="feat"><i>✓</i><span>{f}</span></div>' for f in feats])
        hot = '<span class="hot">🔥 TERLARIS</span>' if pid == "gemini" else ""
        disabled_attr = "disabled aria-disabled='true'" if stok <= 0 else ""
        disabled_btn = "disabled" if stok <= 0 else ""

        cards += f"""
          <div class="p reveal" data-product="{pid}" data-stock="{stok}">
            <div class="card-top">
              <div>
                <div class="ptitle">{p["name"]}</div>
                <div class="psub" id="stock-{pid}">{stok_txt}</div>
              </div>
              <div>{hot}</div>
            </div>

            <div class="price">Rp {rupiah(int(p["price"]))}<small>/ lisensi</small></div>
            <div class="feats">{feats_html}</div>

            <div class="buyrow">
              <div class="qtybox">
                <button class="qtybtn qty-minus" type="button" {disabled_btn}>-</button>
                <span class="qtyval">1</span>
                <button class="qtybtn qty-plus" type="button" {disabled_btn}>+</button>
              </div>

              <button class="btn primary buybtn" type="button" data-buy="{pid}" {disabled_attr}>
                Beli Sekarang
              </button>
            </div>

            <div class="note">{("Stok habis, tombol beli dinonaktifkan." if stok <= 0 else "Bayar QRIS → tunggu verifikasi → akun email terkirim secara otomatis.")}</div>
          </div>
        """
    html = _tpl_render(HOME_HTML, cards=cards, year=now_utc().year, logo=LOGO_IMAGE_URL)
    return HTMLResponse(html)

@app.get("/checkout/{product_id}")
def checkout(product_id: str, request: Request, qty: int = Query(1, ge=1, le=99)):
    """
    Buat order pending + nominal unik.
    FIX: setelah dibuat, redirect ke /pay/{order_id} supaya refresh tidak bikin order baru.
    Juga pakai cookie untuk lock order per produk (anti dobel).
    """
    if product_id not in PRODUCTS:
        return HTMLResponse("<h3>Produk tidak ditemukan</h3>", status_code=404)

    ip = _client_ip(request)
    if not _rate_limit_checkout(ip):
        return HTMLResponse("<h3>Terlalu banyak request</h3><p>Coba lagi beberapa menit.</p>", status_code=429)

    # lock by cookie: kalau masih ada pending order yang belum expired, pakai itu
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

    # cek stok realtime (berdasarkan voucher yang available)
    stock_map = get_stock_map()
    stock = int(stock_map.get(product_id, 0))
    if stock <= 0:
        return HTMLResponse("<h3>Stok habis</h3>", status_code=400)

    if qty > stock:
        qty = stock

    base_price = int(PRODUCTS[product_id]["price"])
    unique_code = random.randint(101, 999)
    # total = (harga * qty) + kode unik
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
    # cookie lock 15 menit
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

    html = _tpl_render(PAY_HTML, 
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

    badge = "#f59e0b" if st == "pending" else "#ef4444"
    # TTL seconds left
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))

    html = _tpl_render(STATUS_HTML, 
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
        <html><body style="font-family:Arial;background:#070c18;color:white;text-align:center;padding:40px">
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

    # kalau sudah paid dan voucher sudah ada
    if st == "paid" and vcode:
        return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)

    # kalau expired/cancelled
    if st == "cancelled":
        return HTMLResponse("<h3>Order sudah cancelled/expired</h3>", status_code=410)

    # assign voucher sesuai qty (sekali klik). fungsi ini juga akan set status order = paid.
    qty = int(order.get("qty") or 1)
    claim_vouchers_for_order(order_id, pid, qty)

    # redirect buyer ke voucher (atau halaman voucher akan bilang stok habis)
    return RedirectResponse(url=f"/voucher/{order_id}", status_code=303)
