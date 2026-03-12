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

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "ganti-tokenmu")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("WARNING: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY belum di-set / tidak terbaca")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
app = FastAPI()


def _tpl_render(tpl, **kw) -> str:
    s = tpl.template if hasattr(tpl, "template") else str(tpl)
    for k, v in kw.items():
        s = s.replace(f"${{{k}}}", str(v))
        s = s.replace(f"${k}", str(v))
    return s


PRODUCTS = {
    "gemini": {
        "name": "Gemini AI Pro 3/4 Bulan",
        "price": 29_000,
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
    "Akses premium aktif",
    "Proses pembelian cepat",
    "Tampilan pembayaran jelas",
    "Bantuan admin tersedia",
]

PRODUCT_FEATS = {}
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "https://i.ibb.co.com/DDts2dZW/IMG-20260308-062026.jpg")
LOGO_IMAGE_URL = os.getenv("LOGO_IMAGE_URL", "https://i.ibb.co.com/3m2fyH71/Picsart-24-11-05-00-57-51-857.jpg")
WHATSAPP_URL = "https://wa.me/6281317391284"
ORDER_TTL_MINUTES = 15
RATE_WINDOW_SEC = 5 * 60
RATE_MAX_CHECKOUT = 6
_IP_BUCKET: Dict[str, list] = {}
_VISITOR_SESS: Dict[str, float] = {}
_VISITOR_BASE = 120


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


def get_sold_map() -> Dict[str, int]:
    sold = {pid: 0 for pid in PRODUCTS.keys()}
    try:
        res = supabase.table("vouchers").select("product_id,status").eq("status", "used").execute()
        for row in (res.data or []):
            pid = row.get("product_id")
            if pid in sold:
                sold[pid] += 1
    except Exception as e:
        print("[SOLD] err:", e)
    return sold


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
    supabase.table("orders").update({"status": "paid", "voucher_code": "\n".join(codes) if codes else None}).eq("id", order_id).execute()
    return codes


BASE_STYLE = Template(r'''
:root{
  --bg:#030304;
  --panel:rgba(10,10,13,.82);
  --text:#f5f7fb;
  --muted:rgba(255,255,255,.72);
  --red:#ff2a2a;
  --red-2:#9e0018;
  --neon:0 0 8px rgba(255,42,42,.38),0 0 18px rgba(255,42,42,.28),0 0 34px rgba(255,42,42,.16);
  --neon-border:0 0 0 1px rgba(255,255,255,.08),0 0 0 1px rgba(255,42,42,.18) inset,0 0 28px rgba(255,42,42,.18);
  --shadow:0 24px 70px rgba(0,0,0,.55);
  --shadow-soft:0 16px 36px rgba(0,0,0,.35);
  --radius:24px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;color:var(--text);background:radial-gradient(900px 520px at 10% -10%, rgba(255,0,51,.18), transparent 60%),radial-gradient(1000px 600px at 100% 0%, rgba(120,0,0,.18), transparent 55%),radial-gradient(900px 520px at 50% 100%, rgba(255,30,30,.07), transparent 55%),linear-gradient(180deg, #060608 0%, #020203 100%);min-height:100vh;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;background:linear-gradient(rgba(255,255,255,.016) 1px, transparent 1px);background-size:100% 4px;opacity:.18;pointer-events:none;z-index:20;mix-blend-mode:screen}
body:after{content:"";position:fixed;inset:0;background:radial-gradient(circle at center, transparent 40%, rgba(0,0,0,.28) 100%);pointer-events:none;z-index:19}
a{color:inherit}.wrap{width:min(1380px, calc(100vw - 24px)); margin:0 auto; position:relative; z-index:2}.glow-text{text-shadow:var(--neon)}
.panel{position:relative;overflow:hidden;border-radius:var(--radius);background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)), var(--panel);border:1px solid rgba(255,255,255,.08);box-shadow:var(--shadow)}
.panel:before{content:"";position:absolute;inset:0;background:radial-gradient(500px 220px at 0% 0%, rgba(255,40,40,.16), transparent 60%),radial-gradient(420px 220px at 100% 0%, rgba(158,0,24,.16), transparent 60%);pointer-events:none}
.panel.neon,.btn.primary,.menu-btn,.wa,.stat-badge,.copy-mini:hover{box-shadow:var(--neon-border), var(--shadow-soft)}
.site-header{position:sticky;top:0;z-index:1200;backdrop-filter:blur(16px);background:linear-gradient(180deg, rgba(5,5,7,.92), rgba(5,5,7,.7));border-bottom:1px solid rgba(255,255,255,.06)}
.header-inner{min-height:82px;display:flex;align-items:center;justify-content:space-between;gap:14px}.brand-row{display:flex;align-items:center;gap:12px;min-width:0}.menu-btn{width:46px;height:46px;border-radius:14px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:#fff;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;position:relative}.menu-btn span,.menu-btn:before,.menu-btn:after{content:"";display:block;width:18px;height:2px;background:#fff;border-radius:999px;position:absolute}.menu-btn span{transform:translateY(0)}.menu-btn:before{transform:translateY(-6px)}.menu-btn:after{transform:translateY(6px)}
.logo-shell{width:54px;height:54px;border-radius:999px;padding:3px;background:linear-gradient(135deg, #ff7777, #9e0018);box-shadow:var(--neon-border);position:relative;flex:0 0 auto}.logo{width:100%;height:100%;object-fit:cover;border-radius:999px;display:block}.brand-copy h1{margin:0;font-size:23px;letter-spacing:.4px}.brand-copy .tag{font-size:13px;color:var(--muted);min-height:19px}.nav-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}.pill{padding:11px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);font-size:12px;color:var(--muted)}.pill.cta{background:linear-gradient(135deg, var(--red), var(--red-2));color:#fff;font-weight:900;text-decoration:none}
.hero{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:18px;padding:22px 0 0}.heroL,.heroR,.p{padding:24px}.eyebrow,.stat-badge,.faq-tag{display:inline-flex;align-items:center;gap:8px;width:max-content;max-width:100%;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.035);border-radius:999px;padding:10px 14px;font-size:12px}.eyebrow .dot,.live-dot{width:8px;height:8px;border-radius:999px;background:linear-gradient(135deg,#ff7676,#9e0018);box-shadow:var(--neon)}
.title{font-size:clamp(32px,5vw,58px);line-height:1.02;margin:16px 0 10px;letter-spacing:-.05em;max-width:12ch}.title .accent{background:linear-gradient(180deg,#fff,#ff9b9b 120%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 10px rgba(255,42,42,.18))}.sub{font-size:15px;line-height:1.75;color:var(--muted);max-width:60ch}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:14px 18px;border-radius:16px;text-decoration:none;font-weight:900;font-size:14px;border:1px solid rgba(255,255,255,.1);cursor:pointer;position:relative;overflow:hidden;transition:transform .2s ease, box-shadow .2s ease, border-color .2s ease}.btn:hover{transform:translateY(-2px)}.btn.primary{background:linear-gradient(135deg,var(--red),var(--red-2));color:#fff;border-color:transparent}.btn.primary:hover:before{content:attr(data-glitch);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,var(--red),var(--red-2));text-shadow:-2px 0 #00fff0,2px 0 #ff00c7;animation:glitch .22s linear 1}.btn.ghost{background:rgba(255,255,255,.03);color:#fff}
@keyframes glitch{0%{clip-path:inset(0 0 75% 0);transform:translate(-2px,1px)}25%{clip-path:inset(40% 0 20% 0);transform:translate(2px,-1px)}50%{clip-path:inset(20% 0 45% 0);transform:translate(-1px,1px)}75%{clip-path:inset(70% 0 0 0);transform:translate(1px,-1px)}100%{clip-path:inset(0);transform:translate(0)}}
.hero-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:22px}.metric{padding:14px 12px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08)}.metric b{display:block;font-size:16px;margin-bottom:4px}.metric span{display:block;color:var(--muted);font-size:12px;line-height:1.45}
.step{display:grid;grid-template-columns:42px 1fr;gap:12px;padding:14px;margin-top:10px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08)}.num{width:42px;height:42px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-weight:950;background:linear-gradient(135deg, rgba(255,70,70,.18), rgba(120,0,0,.24));border:1px solid rgba(255,106,106,.18)}.step b{display:block;margin-bottom:4px}.step span{display:block;color:var(--muted);font-size:13px;line-height:1.55}
.section-head{display:flex;justify-content:space-between;align-items:end;gap:14px;margin:26px 0 14px}.section{font-size:19px;font-weight:950}.section-sub{font-size:13px;color:var(--muted)}.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.p{display:flex;flex-direction:column;transition:transform .22s ease, border-color .22s ease}.p:hover{transform:translateY(-5px);border-color:rgba(255,40,40,.26)}.card-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.ptitle{font-size:24px;margin:0 0 8px;font-weight:950;letter-spacing:-.03em}.psub,.sold-line,.note,.muted{font-size:13px;color:var(--muted)}.hot{display:inline-flex;align-items:center;gap:8px;font-size:11px;font-weight:950;color:#fff;background:linear-gradient(135deg, rgba(255,60,60,.96), rgba(120,0,0,.88));padding:8px 12px;border-radius:999px}.price{font-size:36px;font-weight:950;margin:18px 0 10px;letter-spacing:-.04em;line-height:1}.price small{font-size:14px;font-weight:700;color:var(--muted);margin-left:6px}.feats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:14px;flex:1}.feat{display:flex;align-items:flex-start;gap:10px;font-size:13px;color:#fff;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);padding:12px;border-radius:16px}.feat i{width:20px;height:20px;border-radius:999px;background:linear-gradient(135deg,var(--red),var(--red-2));display:inline-flex;align-items:center;justify-content:center;font-style:normal;font-size:11px;flex:0 0 auto;box-shadow:var(--neon)}
.buyrow{display:flex;gap:12px;align-items:center;flex-wrap:nowrap;margin-top:10px}.qtybox{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:10px 12px;flex:0 0 auto}.qtybtn{width:40px;height:40px;border-radius:14px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.03);color:#fff;font-weight:900;font-size:16px}.qtyval{min-width:28px;text-align:center;font-weight:900}.buybtn{flex:1;min-width:180px}.note{margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,.06);line-height:1.65}
.footer{margin-top:24px;color:rgba(255,255,255,.52);font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;border-top:1px solid rgba(255,255,255,.08);padding:16px 0 30px}.wa{position:fixed;right:18px;bottom:18px;z-index:1150;display:flex;align-items:center;gap:10px;padding:14px 18px;border-radius:999px;background:linear-gradient(135deg,var(--red),var(--red-2));color:#fff;text-decoration:none;font-weight:950;border:1px solid rgba(255,255,255,.12)}
.drawer-backdrop,.chat-backdrop{position:fixed;inset:0;display:none}.drawer-backdrop.show,.chat-backdrop.show{display:block}.drawer-backdrop,.chat-backdrop{background:rgba(0,0,0,.55);z-index:1250}.drawer{position:fixed;left:0;top:0;bottom:0;width:min(88vw,320px);transform:translateX(-100%);transition:transform .24s ease;z-index:1260;padding:18px;background:linear-gradient(180deg, rgba(8,8,11,.98), rgba(8,8,11,.94));border-right:1px solid rgba(255,255,255,.08)}.drawer.show{transform:translateX(0)}.drawer a{display:block;padding:14px 14px;border-radius:14px;margin-top:10px;text-decoration:none;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08)}
.chat-sheet{position:fixed;left:50%;bottom:18px;transform:translateX(-50%) translateY(16px);width:min(94vw,420px);opacity:0;transition:all .22s ease;z-index:1260;padding:18px}.chat-sheet.show{opacity:1;transform:translateX(-50%) translateY(0)}.contact-option{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px;border-radius:16px;text-decoration:none;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);margin-top:10px}
.copy-mini{display:inline-flex;align-items:center;gap:6px;padding:8px 10px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:#fff;font-size:12px;cursor:pointer}.typing{border-right:2px solid rgba(255,255,255,.86);white-space:nowrap;overflow:hidden;display:inline-block;max-width:100%}
.faq-list{display:grid;gap:12px}.faq-item{padding:16px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08)}.faq-item h3{margin:0 0 8px;font-size:16px}.faq-item p{margin:0;color:var(--muted);line-height:1.7;font-size:14px}
.lookup-box{width:min(560px,100%);margin:40px auto;padding:24px}.input{width:100%;background:rgba(255,255,255,.04);color:#fff;border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:14px 16px;font-size:15px;outline:none}.reveal{opacity:0;transform:translateY(16px);transition:opacity .55s ease, transform .55s ease}.reveal.show{opacity:1;transform:translateY(0)}
@media (max-width:1080px){.hero{grid-template-columns:1fr}}@media (max-width:760px){.header-inner{min-height:74px}.nav-actions{display:none}.heroL,.heroR,.p,.lookup-box{padding:18px}.hero-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.feats{grid-template-columns:1fr}.section-head{flex-direction:column;align-items:flex-start}}@media (max-width:520px){.wrap{width:min(1380px, calc(100vw - 18px))}.title{max-width:100%}.actions{flex-direction:column;align-items:stretch}.buyrow{flex-wrap:nowrap;align-items:stretch}.buybtn{flex:1;min-width:0}.qtybox{width:auto;flex:0 0 auto;padding:10px}.btn{width:100%}.buyrow .btn{width:auto}.grid,.hero-metrics{grid-template-columns:1fr}.wa{right:14px;bottom:14px}}
''')

HOME_HTML = Template(r'''<!doctype html>
<html lang="id"><head><meta charset="utf-8"/><script async src="https://www.googletagmanager.com/gtag/js?id=G-YGSFDD04M4"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-YGSFDD04M4');</script><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Impura</title><style>'''+BASE_STYLE.template+r'''</style></head>
<body>
<div id="drawerBackdrop" class="drawer-backdrop"></div>
<aside id="drawer" class="drawer panel neon"><div class="eyebrow"><span class="dot"></span> Menu Navigasi</div><a href="/">Beranda</a><a href="/cek-order">Cek Order</a><a href="/faq">FAQ</a></aside>
<div id="chatBackdrop" class="chat-backdrop"></div>
<div id="chatSheet" class="chat-sheet panel neon"><div class="glow-text" style="font-weight:950;font-size:18px">Chat Admin</div><div class="muted" style="margin-top:6px"></div><a class="contact-option" href="$whatsapp" target="_blank" rel="noreferrer"><span>WhatsApp</span><strong>Chat sekarang</strong></a></div>

<header class="site-header"><div class="wrap header-inner"><div class="brand-row"><button id="menuBtn" class="menu-btn" aria-label="Buka menu"><span></span></button><div class="logo-shell"><img class="logo" src="$logo" alt="Logo Impura"/></div><div class="brand-copy"><h1 class="glow-text">Impura.ID</h1><div class="tag"><span id="typingText" class="typing"></span></div></div></div></header>
<div class="wrap">
<div class="hero"><div class="panel neon heroL reveal"><div class="eyebrow"><span class="dot"></span> Harga termurah se-Indonesia</div><div class="title"><span class="accent">Beli Akses AI Premium dengan proses cepat</span>.</div><div class="sub">Pilih produk → bayar QRIS → tunggu verifikasi → sistem otomatis kirim akun email.</div><div class="actions"><a class="btn primary" data-glitch="Lihat Produk" href="#produk">Lihat Produk</a><a class="btn ghost" href="/faq">Lihat FAQ</a></div><div class="hero-metrics"><div class="metric"><b>QRIS</b><span>Pembayaran cepat dan bisa menggunakan semua e-wallet dan bank.</span></div><div class="metric"><b>Realtime</b><span>Statistik terjual tampil langsung di kartu produk.</span></div><div class="metric"><b>Private</b><span>Benefit untuk sendiri, tidak berbagi benefit dengan orang lain.</span></div><div class="metric"><b>Support</b><span>Admin bisa dihubungi langsung via WhatsApp.</span></div></div></div><div class="panel neon heroR reveal" id="cara"><div class="section glow-text" style="font-size:16px">Cara beli (3 langkah)</div><div class="step"><div class="num">1</div><div><b>Pilih produk</b><span>Klik tombol beli pada produk yang diinginkan, lalu atur jumlah pembelian.</span></div></div><div class="step"><div class="num">2</div><div><b>Bayar QRIS sesuai nominal unik</b><span>Jangan dibulatkan. Tiga digit terakhir adalah kode verifikasi otomatis.</span></div></div><div class="step"><div class="num">3</div><div><b>Cek status order</b><span>User bisa cek status hanya dengan memasukkan Order ID tanpa perlu chat admin terlebih dahulu.</span></div></div></div></div>
<div class="section-head reveal"><div class="section glow-text" id="produk">Produk tersedia</div><div class="section-sub">Total produk terjual: $total_sold 🔥</div></div><div class="grid">$cards</div>
<div class="section-head reveal" id="testimoni"><div class="section glow-text">Keunggulan Impura</div><div class="section-sub">Kami khusus menjual AI Premium berkualitas.</div></div>
<div class="grid"><div class="panel p reveal"><div class="faq-tag"><span class="dot"></span> Garansi</div><div class="note" style="margin-top:12px;border-top:none;padding-top:0">Semya produk yang kami jual memiliki garansi, jadi ketika produk bermasalah kalian bisa klaim garansi dengan s&k berlaku.</div></div><div class="panel p reveal"><div class="faq-tag"><span class="dot"></span> Private</div><div class="note" style="margin-top:12px;border-top:none;padding-top:0">Semua produk kami dijamin Private bukan sharing dan bukan via invite keluarga yang mana benefitnya dipakai rame-rame.</div></div><div class="panel p reveal"><div class="faq-tag"><span class="dot"></span> Proses cepat</div><div class="note" style="margin-top:12px;border-top:none;padding-top:0">Setelah membeli kalian bisa langsung pakai langsung.</div></div></div>
<div class="footer" id="hubungi"><div>© $year impura.id</div></div></div>
<a id="chatAdminBtn" class="wa" href="$whatsapp" target="_blank" rel="noreferrer">💬 Chat Admin</a>
<script>
const TYPE_TEXT="Menyediakan Berbagai Layanan AI Premium";(function(){const el=document.getElementById("typingText");if(!el) return;let i=0;let deleting=false;function loop(){el.textContent=TYPE_TEXT.slice(0,i);if(!deleting&&i<TYPE_TEXT.length){i++;setTimeout(loop,58);return;}if(!deleting&&i===TYPE_TEXT.length){deleting=true;setTimeout(loop,1200);return;}if(deleting&&i>0){i--;setTimeout(loop,28);return;}deleting=false;setTimeout(loop,420);}loop();})();
async function refreshStats(){try{const r=await fetch('/api/stats',{cache:'no-store'});const j=await r.json();if(!j||!j.ok) return;for(const pid in j.stock){const stockEl=document.getElementById('stock-'+pid);const soldEl=document.getElementById('sold-'+pid);if(stockEl) stockEl.textContent='Stok: '+j.stock[pid]+' tersedia';if(soldEl) soldEl.textContent='Terjual real: '+(j.sold[pid]||0)+' akun';const card=document.querySelector('.p[data-product="'+pid+'"]');if(card){card.setAttribute('data-stock',j.stock[pid]);syncCard(card);}}const totalEl=document.getElementById('totalSoldFooter');if(totalEl) totalEl.textContent=j.total_sold||0;}catch(e){}}
function syncCard(card){const stock=parseInt(card.getAttribute('data-stock')||'0',10)||0;const qtyEl=card.querySelector('.qtyval');const minus=card.querySelector('.qty-minus');const plus=card.querySelector('.qty-plus');const buy=card.querySelector('.buybtn');let qty=parseInt((qtyEl&&qtyEl.textContent)||'1',10)||1;if(stock<=0){qty=1;if(qtyEl) qtyEl.textContent='1';if(minus) minus.disabled=true;if(plus) plus.disabled=true;if(buy){buy.disabled=true;buy.setAttribute('aria-disabled','true');}return;}qty=Math.max(1,Math.min(qty,stock));if(qtyEl) qtyEl.textContent=String(qty);if(minus) minus.disabled=qty<=1;if(plus) plus.disabled=qty>=stock;if(buy){buy.disabled=false;buy.removeAttribute('aria-disabled');}}
function showSkeletonAndGo(url){window.location.href=url;}
(function bind(){document.querySelectorAll('.p[data-product]').forEach(card=>{syncCard(card);const minus=card.querySelector('.qty-minus');const plus=card.querySelector('.qty-plus');const buy=card.querySelector('.buybtn');if(minus){minus.addEventListener('click',()=>{const q=card.querySelector('.qtyval');q.textContent=String((parseInt(q.textContent||'1',10)||1)-1);syncCard(card);});}if(plus){plus.addEventListener('click',()=>{const q=card.querySelector('.qtyval');q.textContent=String((parseInt(q.textContent||'1',10)||1)+1);syncCard(card);});}if(buy){buy.addEventListener('click',()=>{if(buy.disabled) return;const pid=buy.getAttribute('data-buy');const qty=parseInt(card.querySelector('.qtyval').textContent||'1',10)||1;showSkeletonAndGo('/checkout/'+encodeURIComponent(pid)+'?qty='+encodeURIComponent(qty));});}});})();
(function reveal(){const io=new IntersectionObserver((entries)=>entries.forEach((e)=>{if(e.isIntersecting)e.target.classList.add('show');}),{threshold:.14});document.querySelectorAll('.reveal').forEach((el)=>io.observe(el));})();
(function drawer(){const btn=document.getElementById('menuBtn');const drawer=document.getElementById('drawer');const back=document.getElementById('drawerBackdrop');function close(){drawer.classList.remove('show');back.classList.remove('show');}btn.addEventListener('click',()=>{drawer.classList.add('show');back.classList.add('show');});back.addEventListener('click',close);drawer.querySelectorAll('a').forEach(a=>a.addEventListener('click',close));})();
(function chatSheet(){const btn=document.getElementById('chatAdminBtn');const back=document.getElementById('chatBackdrop');const sheet=document.getElementById('chatSheet');function close(){back.classList.remove('show');sheet.classList.remove('show');}btn.addEventListener('click',(e)=>{e.preventDefault();back.classList.add('show');sheet.classList.add('show');});back.addEventListener('click',close);})();
refreshStats();setInterval(refreshStats,12000);
</script></body></html>''')

PAY_HTML = Template(r'''<!doctype html><html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Pembayaran QRIS</title><style>'''+BASE_STYLE.template+r'''body{display:flex;align-items:center;justify-content:center;padding:24px}.box{width:min(560px,100%);padding:22px;text-align:center}.total{font-size:42px;font-weight:950;color:#fff;margin:12px 0;text-shadow:var(--neon)}.qris{margin:16px auto 8px;width:min(360px,100%);background:linear-gradient(180deg, rgba(8,8,10,.98), rgba(18,0,0,.98));border-radius:24px;padding:10px;border:1px solid rgba(255,52,52,.75);box-shadow:0 0 0 1px rgba(0,0,0,.92) inset, 0 0 0 3px rgba(255,0,34,.18), 0 0 26px rgba(255,0,34,.28), 0 14px 30px rgba(0,0,0,.45)}.qris img{width:100%;height:auto;display:block;border-radius:18px;background:#080808}.oid{margin-top:14px;padding:14px;border:1px dashed rgba(255,255,255,.2);border-radius:16px;word-break:break-all}.row{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}.warn{margin-top:14px;border-radius:18px;padding:14px;background:rgba(255,43,43,.08);border:1px solid rgba(255,43,43,.22);color:#fff;line-height:1.65}.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:1000;background:rgba(0,0,0,.62);border:1px solid rgba(255,255,255,.12);color:#fff;padding:12px 14px;border-radius:14px;opacity:0;pointer-events:none;transition:opacity .2s ease, transform .2s ease}.toast.show{opacity:1;transform:translateX(-50%) translateY(-6px)}</style></head><body><div class="box panel neon"><div class="eyebrow"><span class="dot"></span> Pembayaran QRIS</div><h1 class="glow-text" style="margin:14px 0 8px">$product_name</h1><div class="muted">Jumlah: <b>$qty</b></div><div style="margin-top:14px">Total transfer</div><div class="total">Rp $total</div><div class="warn"><b>WAJIB transfer sesuai nominal unik hingga 3 digit terakhir.</b><br/>Jangan dibulatkan, jangan dilebihkan, dan jangan dikurangi karena sistem verifikasi membaca nominal ini secara persis.</div><div style="margin-top:14px">Scan QRIS</div><div class="qris"><img src="$qris" alt="QRIS"/></div><div class="oid">Order ID:<br/><b>$order_id</b><br/><button class="copy-mini" onclick="copyText('$order_id','Order ID berhasil disalin')">Salin Order ID</button></div><div class="row" style="margin-top:14px"><a class="btn" href="/status/$order_id">Cek Status</a><a class="btn ghost" href="/cek-order">Cari Order via ID</a><a class="btn ghost" href="/">Kembali</a></div><div class="muted" style="margin-top:12px">Catatan: order akan otomatis <b>cancel</b> jika belum dibayar dalam $ttl menit.</div></div><a id="chatAdminBtn" class="wa" href="$whatsapp" target="_blank" rel="noreferrer">💬 Chat Admin</a><div id="toast" class="toast">Pembayaran berhasil diverifikasi ✅ Mengarahkan...</div><div id="copyToast" class="toast">Tersalin</div><script>function vibe(){try{if(navigator.vibrate) navigator.vibrate(35);}catch(e){}}async function copyText(v,msg){try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(v);}else{const ta=document.createElement('textarea');ta.value=v;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();}const t=document.getElementById('copyToast');t.textContent=msg||'Tersalin';t.classList.add('show');vibe();setTimeout(()=>t.classList.remove('show'),1300);}catch(e){}}async function poll(){try{const r=await fetch('/api/order/$order_id',{cache:'no-store'});const j=await r.json();if(!j||!j.ok) return;if(j.status==='paid'){const t=document.getElementById('toast');t.classList.add('show');vibe();setTimeout(()=>{window.location.href='/voucher/$order_id';},700);}if(j.status==='cancelled'){window.location.href='/status/$order_id';}}catch(e){}}setInterval(poll,2000);poll();document.getElementById('chatAdminBtn').addEventListener('click',function(e){e.preventDefault();window.open('$whatsapp','_blank');});</script></body></html>''')

STATUS_HTML = Template(r'''<!doctype html><html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Status Order</title><style>'''+BASE_STYLE.template+r'''body{display:flex;align-items:center;justify-content:center;padding:24px}.box{width:min(620px,100%);padding:22px;text-align:center}.grid2{margin-top:16px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.mini{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:18px;padding:12px}.mini .t{font-size:12px;color:var(--muted)}.mini .v{font-size:22px;font-weight:950;margin-top:4px}.status-pill{display:inline-flex;align-items:center;gap:8px;padding:10px 14px;border-radius:999px;font-weight:950;background:linear-gradient(135deg, rgba(255,43,43,.96), rgba(164,0,25,.85));margin-top:12px}.spin{width:14px;height:14px;border:2px solid rgba(255,255,255,.22);border-top-color:rgba(255,255,255,.9);border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.kv{margin-top:12px;display:grid;gap:10px}.kvrow{display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:12px 14px}.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:1000;background:rgba(0,0,0,.62);border:1px solid rgba(255,255,255,.12);color:#fff;padding:12px 14px;border-radius:14px;opacity:0;pointer-events:none;transition:opacity .2s ease, transform .2s ease}.toast.show{opacity:1;transform:translateX(-50%) translateY(-6px)}@media(max-width:520px){.grid2{grid-template-columns:1fr}.kvrow{flex-direction:column;align-items:flex-start}}</style></head><body><div class="box panel neon"><div class="eyebrow"><span class="dot"></span> Status Order</div><h1 class="glow-text" style="margin:14px 0 8px">Pantau Order</h1><div class="kv"><div class="kvrow"><div><div class="muted">Produk</div><b>$pid</b></div></div><div class="kvrow"><div><div class="muted">Jumlah</div><b>$qty</b></div></div><div class="kvrow"><div><div class="muted">Nominal</div><b>Rp $amount</b></div><button class="copy-mini" onclick="copyText('Rp $amount','Nominal berhasil disalin')">Salin Nominal</button></div><div class="kvrow"><div><div class="muted">Order ID</div><b>$order_id</b></div><button class="copy-mini" onclick="copyText('$order_id','Order ID berhasil disalin')">Salin Order ID</button></div></div><div class="status-pill"><span id="st">$st</span> <span class="spin"></span></div><div class="grid2"><div class="mini"><div class="t">Countdown verifikasi</div><div class="v" id="cd">--:--</div></div><div class="mini"><div class="t">Auto cek</div><div class="v" id="tick">2s</div></div></div><div class="muted" style="margin-top:14px">Halaman ini akan otomatis redirect ke akun email setelah verifikasi. Jika sudah bayar tapi lama, hubungi admin dari tombol chat.</div></div><a id="chatAdminBtn" class="wa" href="$whatsapp" target="_blank" rel="noreferrer">💬 Chat Admin</a><div id="toast" class="toast">Akun email berhasil dikirim ✅ Mengarahkan...</div><div id="copyToast" class="toast">Tersalin</div><script>let ttl=$ttl_sec;let every=2;document.getElementById('tick').textContent=every+'s';function vibe(){try{if(navigator.vibrate) navigator.vibrate(35);}catch(e){}}async function copyText(v,msg){try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(v);}else{const ta=document.createElement('textarea');ta.value=v;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();}const t=document.getElementById('copyToast');t.textContent=msg||'Tersalin';t.classList.add('show');vibe();setTimeout(()=>t.classList.remove('show'),1300);}catch(e){}}function fmt(sec){sec=Math.max(0,sec|0);const m=(sec/60)|0;const s=sec%60;return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');}function updateCd(){document.getElementById('cd').textContent=fmt(ttl);ttl=Math.max(0,ttl-1);}setInterval(updateCd,1000);updateCd();async function poll(){try{const r=await fetch('/api/order/$order_id',{cache:'no-store'});const j=await r.json();if(!j||!j.ok) return;if(j.status==='paid'){const t=document.getElementById('toast');t.classList.add('show');vibe();setTimeout(()=>{window.location.href='/voucher/$order_id';},700);return;}if(j.status==='cancelled'){document.getElementById('st').textContent='CANCELLED';return;}if(typeof j.ttl_sec==='number') ttl=j.ttl_sec;}catch(e){}}setInterval(poll,every*1000);poll();document.getElementById('chatAdminBtn').addEventListener('click',function(e){e.preventDefault();window.open('$whatsapp','_blank');});</script></body></html>''')

VOUCHER_HTML = Template(r'''<!doctype html><html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Akun Akses</title><style>'''+BASE_STYLE.template+r'''body{display:flex;align-items:center;justify-content:center;padding:24px}.box{width:min(620px,100%);padding:22px;text-align:center}.code{margin:16px auto 12px;background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.12);padding:16px 14px;border-radius:16px;font-size:18px;font-weight:950;letter-spacing:.3px;word-break:break-all;white-space:pre-wrap}.success{display:inline-flex;align-items:center;gap:10px;margin-top:12px;padding:10px 12px;border-radius:999px;background:rgba(34,197,94,.10);border:1px solid rgba(34,197,94,.22);font-weight:900}.row{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;z-index:1000;background:rgba(0,0,0,.62);border:1px solid rgba(255,255,255,.12);color:#fff;padding:12px 14px;border-radius:14px;opacity:0;pointer-events:none;transition:opacity .2s ease, transform .2s ease}</style></head><body><div class="box panel neon"><div class="eyebrow"><span class="dot"></span> Akun Email</div><h1 class="glow-text" style="margin:14px 0 8px">Akses Berhasil Dikirim</h1><div class="muted">Status: <b>PAID ✅</b></div><div class="muted">Produk: <b>$pid</b></div><div class="success">✅ Akun email berhasil dikirim</div><div class="code" id="vcode">$code</div><div class="row"><button class="btn primary" data-glitch="Salin Email" id="copyVoucherBtn">Salin Email</button><a class="btn ghost" href="/">Kembali ke Beranda</a></div><div class="muted" style="margin-top:12px">Gunakan email atau nomor asli untuk pemulihan. Jangan gunakan temp mail atau temp number untuk recovery.</div></div><a id="chatAdminBtn" class="wa" href="$whatsapp" target="_blank" rel="noreferrer">💬 Chat Admin</a><div id="copyToast" class="toast">Tersalin</div><script>function vibe(){try{if(navigator.vibrate) navigator.vibrate(35);}catch(e){}}document.getElementById('copyVoucherBtn').onclick=async()=>{const text=document.getElementById('vcode').innerText;try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);}else{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();}const btn=document.getElementById('copyVoucherBtn');btn.innerText='✅ Tersalin';vibe();const t=document.getElementById('copyToast');t.textContent='Akun email berhasil disalin';t.style.opacity='1';t.style.transform='translateX(-50%) translateY(-6px)';setTimeout(()=>{btn.innerText='Salin Email';t.style.opacity='0';t.style.transform='translateX(-50%)';},1500);}catch(e){}};document.getElementById('chatAdminBtn').addEventListener('click',function(e){e.preventDefault();window.open('$whatsapp','_blank');});</script></body></html>''')

FAQ_HTML = Template(r'''<!doctype html><html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>FAQ Impura</title><style>'''+BASE_STYLE.template+r'''body{padding-bottom:40px}.faq-wrap{padding:22px 0 40px}</style></head><body><header class="site-header"><div class="wrap header-inner"><div class="brand-row"><a class="menu-btn" href="/"><span></span></a><div class="logo-shell"><img class="logo" src="$logo" alt="Logo"/></div><div class="brand-copy"><h1 class="glow-text">FAQ Impura.ID</h1><div class="tag">Pertanyaan yang paling sering ditanyakan user</div></div></div><div class="nav-actions"><a class="pill cta" href="/cek-order">Cek Order</a></div></div></header><div class="wrap faq-wrap"><div class="panel neon lookup-box" style="width:min(920px,100%)"><div class="faq-list">$faq_items</div></div></div></body></html>''')

LOOKUP_HTML = Template(r'''<!doctype html><html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Cek Order</title><style>'''+BASE_STYLE.template+r'''body{padding-bottom:40px}</style></head><body><header class="site-header"><div class="wrap header-inner"><div class="brand-row"><a class="menu-btn" href="/"><span></span></a><div class="logo-shell"><img class="logo" src="$logo" alt="Logo"/></div><div class="brand-copy"><h1 class="glow-text">Cek Status Pesanan</h1><div class="tag">Masukkan Order ID untuk melihat status pesanan</div></div></div></div></header><div class="wrap"><div class="panel neon lookup-box"><div class="eyebrow"><span class="dot"></span> Lookup Order</div><h2 style="margin:14px 0 8px">Cek status hanya dengan Order ID</h2><div class="muted">Masukkan Order ID yang kamu dapat saat checkout, lalu tekan tombol cek.</div><form onsubmit="event.preventDefault(); goCheck();" style="margin-top:16px; display:grid; gap:12px"><input id="oidInput" class="input" placeholder="Contoh: 123e4567-e89b-12d3-a456-426614174000" autocomplete="off"/><button class="btn primary" data-glitch="Cek Status" type="submit">Cek Status</button></form><div class="muted" style="margin-top:12px">Tip: kamu bisa salin-tempel Order ID dari halaman pembayaran atau halaman status order.</div></div></div><script>function goCheck(){const v=(document.getElementById('oidInput').value||'').trim();if(!v){alert('Masukkan Order ID terlebih dahulu');return;}window.location.href='/status/'+encodeURIComponent(v);}</script></body></html>''')

ADMIN_HTML = Template(r'''<!doctype html><html lang="id"><head><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Admin Panel</title><style>body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial;background:#070c18;color:#fff;padding:20px}.box{max-width:980px;margin:0 auto}.row{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);padding:14px;border-radius:16px;margin-bottom:10px;display:flex;gap:12px;align-items:center;justify-content:space-between;backdrop-filter: blur(10px)}.muted{opacity:.75;font-size:12px;word-break:break-all}.vbtn{background:#22c55e;border:none;color:#fff;padding:10px 12px;border-radius:12px;cursor:pointer;font-weight:950}.lbtn{display:inline-block;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:white;padding:10px 12px;border-radius:12px;text-decoration:none;font-weight:950}.act{min-width:260px;display:flex;flex-direction:column;align-items:flex-end;gap:8px}@media(max-width:740px){.row{flex-direction:column;align-items:flex-start}.act{align-items:flex-start;min-width:unset;width:100%}}</style></head><body><div class="box"><h2 style="margin:0 0 10px;">Admin Panel</h2><div style="opacity:.75;margin-bottom:12px;">Klik tombol untuk verifikasi + otomatis assign akun email lalu redirect ke halaman akun email.</div>$items</div></body></html>''')

FAQ_ITEMS = [
    ("Bagaimana cara membeli produk di Impura?", "Pilih produk, klik beli, bayar QRIS sesuai nominal unik, lalu simpan Order ID untuk cek status. Setelah pembayaran diverifikasi, akun email akan tampil otomatis."),
    ("Kenapa nominal transfer tidak boleh dibulatkan?", "Karena sistem membaca nominal unik sampai 3 digit terakhir untuk membantu verifikasi. Jika dibulatkan, pembayaran bisa terlambat terdeteksi atau perlu konfirmasi manual."),
    ("Bagaimana cara cek status pesanan?", "Buka halaman Cek Order, masukkan Order ID, lalu sistem akan menampilkan status terbaru order kamu secara otomatis."),
    ("Kalau sudah bayar tapi status belum berubah bagaimana?", "Tunggu beberapa saat sambil tetap membuka halaman status. Jika masih pending, hubungi admin lewat WhatsApp atau Telegram dan kirim Order ID kamu."),
    ("Apakah stok produk tampil real-time?", "Ya. Halaman produk menampilkan stok tersedia dan jumlah produk terjual yang diperbarui berkala dari sistem order."),
    ("Berapa lama order aktif sebelum expired?", "Order akan otomatis dibatalkan jika belum dibayar dalam 15 menit, jadi sebaiknya langsung selesaikan pembayaran setelah checkout."),
    ("Apa akun bergaransi?", "Setiap akun mendapatkan garansi 1 (satu) bulan / sampai event berakhir dan garansi hanya diganti dengan akun lain, tidak menerima garansi uang kembali."),
]

@app.get("/", response_class=HTMLResponse)
def home():
    stock = get_stock_map()
    sold = get_sold_map()
    total_sold = sum(sold.values())
    cards = ""
    for pid, p in PRODUCTS.items():
        stok = int(stock.get(pid, 0))
        sold_qty = int(sold.get(pid, 0))
        feats = (PRODUCT_FEATS.get(pid) or p.get("features") or DEFAULT_FEATURES)
        feats_html = "".join([f'<div class="feat"><i>✓</i><span>{f}</span></div>' for f in feats])
        hot = '<span class="hot">🔥 TERLARIS</span>' if pid == "gemini" else '<span class="stat-badge"><span class="dot"></span> LIVE</span>'
        disabled_attr = "disabled aria-disabled='true'" if stok <= 0 else ""
        disabled_btn = "disabled" if stok <= 0 else ""
        cards += f'<div class="panel neon p reveal" data-product="{pid}" data-stock="{stok}"><div class="card-top"><div><div class="ptitle glow-text">{p["name"]}</div><div class="psub" id="stock-{pid}">Stok: {stok} tersedia</div><div class="sold-line" id="sold-{pid}" style="margin-top:6px">Terjual real: {sold_qty} akun</div></div><div>{hot}</div></div><div class="price">Rp {rupiah(int(p["price"]))}<small>/ Akun</small></div><div class="feats">{feats_html}</div><div class="buyrow"><div class="qtybox"><button class="qtybtn qty-minus" type="button" {disabled_btn}>-</button><span class="qtyval">1</span><button class="qtybtn qty-plus" type="button" {disabled_btn}>+</button></div><button class="btn primary buybtn" data-glitch="Beli Sekarang" type="button" data-buy="{pid}" {disabled_attr}>Beli Sekarang</button></div><div class="note">{"Stok habis, tombol beli dinonaktifkan." if stok <= 0 else "Bayar QRIS → tunggu verifikasi → akun email terkirim otomatis. Simpan Order ID untuk cek status kapan saja."}</div></div>'
    html = _tpl_render(HOME_HTML, cards=cards, year=now_utc().year, logo=LOGO_IMAGE_URL, total_sold=total_sold, whatsapp=WHATSAPP_URL)
    return HTMLResponse(html)

@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/faq", response_class=HTMLResponse)
def faq_page():
    faq_items = "".join([f'<div class="faq-item"><h3>{q}</h3><p>{a}</p></div>' for q, a in FAQ_ITEMS])
    return HTMLResponse(_tpl_render(FAQ_HTML, faq_items=faq_items, logo=LOGO_IMAGE_URL))

@app.get("/cek-order", response_class=HTMLResponse)
def cek_order_page():
    return HTMLResponse(_tpl_render(LOOKUP_HTML, logo=LOGO_IMAGE_URL))

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
    stock = int(get_stock_map().get(product_id, 0))
    if stock <= 0:
        return HTMLResponse("<h3>Stok habis</h3>", status_code=400)
    if qty > stock:
        qty = stock
    base_price = int(PRODUCTS[product_id]["price"])
    unique_code = random.randint(101, 999)
    total = (base_price * int(qty)) + unique_code
    order_id = str(uuid.uuid4())
    ins = supabase.table("orders").insert({"id": order_id, "product_id": product_id, "qty": int(qty), "unit": int(base_price), "amount_idr": int(total), "status": "pending", "created_at": now_utc().isoformat(), "voucher_code": None}).execute()
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
    product_name = PRODUCTS.get(pid, {}).get("name", pid)
    return HTMLResponse(_tpl_render(PAY_HTML, product_name=product_name, qty=str(qty), total=rupiah(amount), qris=QR_IMAGE_URL, order_id=order_id, ttl=ORDER_TTL_MINUTES, whatsapp=WHATSAPP_URL))

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
    pid = PRODUCTS.get(order.get("product_id", ""), {}).get("name", order.get("product_id", ""))
    qty = int(order.get("qty") or 1)
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))
    return HTMLResponse(_tpl_render(STATUS_HTML, pid=pid, qty=str(qty), amount=rupiah(amount), st=st.upper(), order_id=order_id, ttl_sec=str(ttl_sec), whatsapp=WHATSAPP_URL))

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
        return HTMLResponse("<html><body style='font-family:Arial;background:#070c18;color:white;text-align:center;padding:40px'><h2>Akun Email</h2><p>Status: PAID ✅</p><p style='opacity:.8'>Maaf, stok untuk produk ini sedang habis.</p></body></html>")
    return HTMLResponse(_tpl_render(VOUCHER_HTML, pid=PRODUCTS.get(order.get("product_id"), {}).get("name", order.get("product_id")), code=code, whatsapp=WHATSAPP_URL))

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

@app.get("/api/stats")
def api_stats():
    stock = get_stock_map()
    sold = get_sold_map()
    return {"ok": True, "stock": stock, "sold": sold, "total_sold": sum(sold.values())}

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

@app.get("/admin", response_class=HTMLResponse)
def admin(token: Optional[str] = None):
    if not require_admin(token):
        return HTMLResponse("<h3>Unauthorized</h3>", status_code=401)
    res = supabase.table("orders").select("id,product_id,qty,unit,amount_idr,status,created_at,voucher_code").order("created_at", desc=True).limit(80).execute()
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
                action = f"<form method='post' action='/admin/verify/{oid}?token={token}' style='margin:0;'><button class='vbtn' type='submit'>VERIFIKASI + KIRIM VOUCHER</button></form><div class='muted'>Auto-cancel: {ORDER_TTL_MINUTES} menit</div>"
            elif st == "paid":
                label = f"Voucher: {vcode}" if vcode else "Voucher: (habis / belum ada)"
                action = f"<a class='lbtn' href='/voucher/{oid}'>Buka Akun Email</a><div class='muted'>{label}</div>"
            else:
                action = f"<div class='muted'>Status: {st.upper()}</div><a class='lbtn' href='/pay/{oid}'>Buka Pay</a>"
            items += f"<div class='row'><div class='col'><div><b>{pid}</b> — Qty {qty} — Rp {rupiah(amt)}</div><div class='muted'>ID: {oid}</div><div class='muted'>{created}</div><div class='muted'>Status: {st}</div></div><div class='act'>{action}</div></div>"
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
