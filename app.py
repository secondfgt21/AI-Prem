import os
import uuid
import random
import time
import json
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
        "name": "Gemini AI Pro 1 Tahun",
        "group": "gemini",
        "badge": "ðŸ”¥ TERLARIS",
        "price": 34_000,
        "features": [
            "Akses penuh Gemini AI Pro",
            "Google Drive 2TB",
            "Flow + 1.000 credit",
            "Akses Antigravity",
        ],
    },
    "gemini3b": {
        "name": "Gemini AI Pro 3 Bulan",
        "group": "gemini",
        "badge": "âš¡ FLEXIBLE",
        "price": 17_000,
        "features": [
            "Gemini AI Pro aktif 3 bulan",
            "Cocok untuk coba premium",
            "Akses cepat & stabil",
            "Aktivasi cepat",
        ],
    },
    "chatgpt": {
        "name": "ChatGPT Plus 1 Bulan",
        "group": "chatgpt",
        "badge": "ðŸ’Ž PREMIUM",
        "price": 14_000,
        "features": [
            "Akses model ChatGPT terbaru",
            "Respons lebih cepat & akurat",
            "Cocok untuk riset & coding",
            "Aktivasi cepat",
        ],
    },
}

PRODUCT_GROUPS = {
    "gemini": {
        "title": "Gemini AI Pro",
        "description": "Pilih varian Gemini yang paling cocok untuk kebutuhanmu.",
        "variants": ["gemini", "gemini3b"],
    },
    "chatgpt": {
        "title": "ChatGPT Plus",
        "description": "Akun premium siap pakai untuk kebutuhan harian, kerja, dan riset.",
        "variants": ["chatgpt"],
    },
}

QR_IMAGE_URL = os.getenv(
    "QR_IMAGE_URL",
    "https://i.ibb.co.com/FkNWgprm/IMG-20260308-062026.jpg",
)
LOGO_URL = os.getenv(
    "LOGO_URL",
    "https://i.ibb.co.com/3m2fyH71/Picsart-24-11-05-00-57-51-857.jpg",
)
WHATSAPP_URL = os.getenv("WHATSAPP_URL", "https://wa.me/6280000000000")
TELEGRAM_URL = os.getenv("TELEGRAM_URL", "https://t.me/impuraid")

ORDER_TTL_MINUTES = 15
RATE_WINDOW_SEC = 5 * 60
RATE_MAX_CHECKOUT = 6

_IP_BUCKET: Dict[str, list] = {}
_VISITOR_SESS: Dict[str, float] = {}
_VISITOR_BASE = 120
_ACTIVE_USERS_BASE = 187
_TODAY_SUCCESS_BASE = 46

FAQ_ITEMS = [
    ("Apakah akun yang dijual bergaransi?", "Ya. Semua produk yang dijual bergaransi."),
    ("Berapa lama proses verifikasi pembayaran?", "Biasanya 1â€“5 menit. Saat sedang ramai, proses bisa sedikit lebih lama."),
    ("Kenapa nominal transfer harus persis?", "Karena sistem menggunakan nominal unik untuk mencocokkan pembayaran. Jangan dibulatkan atau dikurangi."),
    ("Bagaimana cara cek status pesanan?", "Masuk ke menu Cek Order lalu masukkan Order ID kamu untuk melihat status terbaru."),
    ("Kalau ada kendala setelah pembelian bagaimana?", "Hubungi admin melalui WhatsApp atau Telegram yang tersedia di website agar dibantu lebih cepat."),
]


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


def voucher_lines(v: str | None) -> list[str]:
    txt = (v or "").strip()
    if not txt:
        return []
    return [line.strip() for line in txt.splitlines() if line.strip()]


def get_sales_stats() -> dict:
    sold = {pid: 0 for pid in PRODUCTS.keys()}
    total_success = 0
    try:
        res = supabase.table("orders").select("product_id,qty,status,created_at").execute()
        today = now_utc().date()
        today_success = 0
        for row in (res.data or []):
            st = (row.get("status") or "").lower()
            if st != "paid":
                continue
            qty = int(row.get("qty") or 1)
            pid = row.get("product_id")
            total_success += qty
            if pid in sold:
                sold[pid] += qty
            dt = _parse_dt(row.get("created_at", ""))
            if dt and dt.astimezone(timezone.utc).date() == today:
                today_success += qty
        return {
            "sold": sold,
            "total_success": total_success,
            "today_success": today_success,
            "active_users": _ACTIVE_USERS_BASE + total_success * 3,
        }
    except Exception as e:
        print("[STATS] err:", e)
        return {
            "sold": sold,
            "total_success": 0,
            "today_success": 0,
            "active_users": _ACTIVE_USERS_BASE,
        }


def build_faq_items() -> str:
    out = []
    for q, a in FAQ_ITEMS:
        out.append(
            f"<details class='faq-item reveal'><summary>{q}</summary><div class='faq-answer'>{a}</div></details>"
        )
    return "\n".join(out)


def build_home_cards(stock: dict, stats: dict) -> str:
    cards = []
    for group_id, group in PRODUCT_GROUPS.items():
        variants_html = []
        for pid in group["variants"]:
            p = PRODUCTS[pid]
            stok = int(stock.get(pid, 0))
            sold = int(stats["sold"].get(pid, 0))
            features = "".join(
                f"<div class='feature-chip'><span class='devil'>ðŸ˜ˆ</span><span>{feat}</span></div>" for feat in p["features"]
            )
            disabled_attr = "disabled aria-disabled='true'" if stok <= 0 else ""
            variants_html.append(
                f"""
                <div class="variant-panel" data-variant-panel="{pid}">
                    <div class="variant-meta-row">
                        <span class="pill badge">{p['badge']}</span>
                        <span class="pill">Terjual {sold}</span>
                        <span class="pill" id="stock-{pid}">Stok {stok}</span>
                    </div>
                    <div class="price-line">
                        <div class="price">Rp {rupiah(int(p['price']))}</div>
                        <div class="subprice">Nominal unik dibuat otomatis saat checkout</div>
                    </div>
                    <div class="feature-grid">{features}</div>
                    <div class="buyrow">
                        <div class="qtybox" data-qtybox="{pid}">
                            <button class="qtybtn" type="button" data-act="minus" data-pid="{pid}" {disabled_attr}>âˆ’</button>
                            <span class="qtyval" data-qty="{pid}">1</span>
                            <button class="qtybtn" type="button" data-act="plus" data-pid="{pid}" {disabled_attr}>+</button>
                        </div>
                        <button class="btn btn-buy glitch-btn" type="button" data-buy="{pid}" {disabled_attr}>Beli Sekarang</button>
                    </div>
                    <div class="micro-note">{('Stok habis.' if stok <= 0 else 'Bayar QRIS â†’ tunggu verifikasi â†’ akun dikirim otomatis setelah dicek admin')}</div>
                </div>
                """
            )

        selector = ""
        if len(group["variants"]) > 1:
            opts = "".join(
                f"<option value='{pid}'>{PRODUCTS[pid]['name']} â€” Rp {rupiah(int(PRODUCTS[pid]['price']))}</option>"
                for pid in group["variants"]
            )
            selector = f"""
            <label class="variant-selector-wrap">
                <span class="selector-label">Pilih Varian</span>
                <select class="variant-selector" data-group-selector="{group_id}">{opts}</select>
            </label>
            """

        cards.append(
            f"""
            <article class="product-card reveal" id="produk-{group_id}" data-group="{group_id}">
                <div class="card-head">
                    <div>
                        <div class="eyebrow">IMPURA CYBER STORE</div>
                        <h3>{group['title']}</h3>
                        <p>{group['description']}</p>
                    </div>
                    <div class="orb"></div>
                </div>
                {selector}
                <div class="variant-stack" data-group-stack="{group_id}">
                    {''.join(variants_html)}
                </div>
            </article>
            """
        )
    return "\n".join(cards)


HOME_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Impura â€” AI Premium Store</title>
  <meta name="description" content="Impura menyediakan berbagai layanan AI premium dengan tampilan cyber neon merah hitam, pembayaran QRIS, dan proses order cepat."/>
  <style>
    :root{
      color-scheme:dark;
      --bg:#050506;
      --bg-soft:#0a0a0d;
      --panel:#0f1015;
      --panel-2:rgba(255,255,255,.05);
      --line:rgba(255,75,75,.16);
      --text:#f6f7fb;
      --muted:#c9cfdb;
      --muted-2:#97a0b2;
      --red:#ff2d55;
      --red-2:#ff4b4b;
      --red-3:#ff1744;
      --glow:rgba(255,45,85,.38);
      --shadow:0 18px 80px rgba(0,0,0,.55),0 0 24px rgba(255,45,85,.16);
      --success:#2ee6a6;
      --warning:#ffd166;
      --radius:24px;
      --radius-sm:16px;
      --maxw:1320px;
    }
    *{box-sizing:border-box}
    html{scroll-behavior:smooth}
    body{
      margin:0;
      font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
      color:var(--text);
      background:
        radial-gradient(circle at top left, rgba(255,35,73,.22), transparent 33%),
        radial-gradient(circle at top right, rgba(255,75,75,.13), transparent 28%),
        radial-gradient(circle at bottom center, rgba(255,45,85,.10), transparent 35%),
        linear-gradient(180deg,#050506,#09090d 32%,#050506 100%);
      min-height:100vh;
      position:relative;
      overflow-x:hidden;
    }
    body::before{
      content:"";
      position:fixed;inset:0;pointer-events:none;z-index:2;
      background:repeating-linear-gradient(to bottom, rgba(255,255,255,.025) 0px, rgba(255,255,255,.025) 1px, transparent 3px, transparent 6px);
      mix-blend-mode:soft-light;
      opacity:.27;
    }
    body::after{
      content:"";
      position:fixed;inset:0;pointer-events:none;z-index:1;
      background:linear-gradient(120deg,transparent 0%,rgba(255,45,85,.03) 50%,transparent 100%);
      animation:bgMove 12s linear infinite;
    }
    @keyframes bgMove { from{transform:translateX(-6%)} to{transform:translateX(6%)} }
    a{color:inherit;text-decoration:none}
    img{max-width:100%;display:block}
    .container{max-width:var(--maxw);margin:0 auto;padding:0 20px}
    .sticky-header{
      position:sticky;top:0;z-index:100;
      backdrop-filter:blur(16px);
      background:rgba(5,5,8,.68);
      border-bottom:1px solid var(--line);
      box-shadow:0 14px 34px rgba(0,0,0,.22);
    }
    .header-inner{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:14px 0}
    .brand{display:flex;align-items:center;gap:12px;min-width:0}
    .menu-btn,.icon-btn,.copy-btn,.qtybtn,.zoom-fab{
      border:1px solid rgba(255,75,75,.18);
      background:rgba(255,255,255,.04);
      color:var(--text);
      border-radius:14px;
      cursor:pointer;
      transition:.25s ease;
    }
    .menu-btn{width:46px;height:46px;display:grid;place-items:center;box-shadow:0 0 0 1px rgba(255,45,85,.06), 0 0 22px rgba(255,45,85,.14)}
    .menu-btn:hover,.icon-btn:hover,.copy-btn:hover,.qtybtn:hover,.zoom-fab:hover{transform:translateY(-1px);box-shadow:0 0 26px rgba(255,45,85,.22)}
    .logo{
      width:48px;height:48px;border-radius:50%;object-fit:cover;
      border:2px solid rgba(255,75,75,.5);
      box-shadow:0 0 0 4px rgba(255,45,85,.08),0 0 26px rgba(255,45,85,.24);
    }
    .brand-text{min-width:0}
    .brand-text strong{display:block;font-size:1rem;letter-spacing:.04em;text-transform:uppercase;text-shadow:0 0 18px rgba(255,45,85,.34)}
    .brand-text span{display:block;color:var(--muted-2);font-size:.84rem}
    .header-actions{display:flex;gap:10px;align-items:center}
    .nav-chip{padding:11px 14px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--muted);display:none}

    .drawer-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.56);backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:.28s ease;z-index:120}
    .drawer{position:fixed;top:0;left:0;height:100vh;width:min(86vw,360px);background:rgba(10,10,14,.97);border-right:1px solid var(--line);box-shadow:30px 0 80px rgba(0,0,0,.5);transform:translateX(-105%);transition:.3s ease;z-index:130;padding:18px}
    .drawer.open{transform:translateX(0)}
    .drawer-backdrop.open{opacity:1;pointer-events:auto}
    .drawer-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
    .drawer-nav a{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-radius:16px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.03);margin-bottom:12px}
    .drawer-nav a:hover{border-color:rgba(255,75,75,.35);box-shadow:0 0 26px rgba(255,45,85,.16)}

    .hero{padding:34px 0 18px;position:relative}
    .hero-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:22px;align-items:stretch}
    .hero-card,.hero-side,.glass{background:linear-gradient(180deg, rgba(18,18,24,.93), rgba(10,10,14,.88));border:1px solid rgba(255,75,75,.16);border-radius:var(--radius);box-shadow:var(--shadow);position:relative;overflow:hidden}
    .hero-card{padding:30px}
    .hero-card::before,.hero-side::before,.product-card::before,.glass::before{
      content:"";position:absolute;inset:auto auto -24px -10px;width:160px;height:160px;border-radius:50%;background:radial-gradient(circle, rgba(255,45,85,.22), transparent 70%);filter:blur(8px)
    }
    .eyebrow{display:inline-flex;gap:8px;align-items:center;padding:9px 14px;border-radius:999px;background:rgba(255,45,85,.10);border:1px solid rgba(255,75,75,.24);color:#ffe0e5;text-transform:uppercase;letter-spacing:.16em;font-size:.72rem;box-shadow:0 0 22px rgba(255,45,85,.12)}
    .hero h1{font-size:clamp(2rem,4vw,4.4rem);line-height:1.02;margin:18px 0 14px;letter-spacing:-.04em;text-shadow:0 0 24px rgba(255,45,85,.22)}
    .hero .lead{font-size:1.02rem;color:var(--muted);max-width:760px;line-height:1.8}
    .typing-wrap{display:flex;align-items:center;gap:10px;color:#ffd5dc;font-weight:700;min-height:34px;text-shadow:0 0 14px rgba(255,45,85,.18)}
    .typing-cursor{display:inline-block;width:10px;height:1.2em;background:var(--red-2);box-shadow:0 0 16px rgba(255,45,85,.5);animation:blink 1s steps(1) infinite}
    @keyframes blink{50%{opacity:0}}
    .hero-actions{display:flex;flex-wrap:wrap;gap:14px;margin-top:22px}
    .btn{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:10px;padding:14px 18px;border:none;border-radius:16px;font-weight:800;letter-spacing:.02em;cursor:pointer;text-decoration:none;transition:.25s ease;overflow:hidden}
    .btn-primary{background:linear-gradient(135deg, var(--red-3), var(--red-2));color:#fff;box-shadow:0 0 0 1px rgba(255,255,255,.06), 0 0 28px rgba(255,45,85,.24), inset 0 0 16px rgba(255,255,255,.08)}
    .btn-secondary{background:rgba(255,255,255,.045);color:var(--text);border:1px solid rgba(255,255,255,.08)}
    .btn:hover{transform:translateY(-2px)}
    .glitch-btn:hover::before,.glitch-btn:hover::after{content:attr(data-label);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:inherit;animation:glitch .32s linear 2}
    .glitch-btn:hover::before{transform:translate(-2px,0);text-shadow:2px 0 #00e5ff}
    .glitch-btn:hover::after{transform:translate(2px,0);text-shadow:-2px 0 #ffea00}
    @keyframes glitch{
      0%{clip-path:inset(0 0 85% 0)}
      25%{clip-path:inset(18% 0 42% 0)}
      50%{clip-path:inset(46% 0 18% 0)}
      75%{clip-path:inset(70% 0 8% 0)}
      100%{clip-path:inset(0 0 85% 0)}
    }
    .stats-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:26px}
    .stat{padding:16px;border-radius:18px;background:rgba(255,255,255,.035);border:1px solid rgba(255,75,75,.14);box-shadow:inset 0 0 0 1px rgba(255,255,255,.025),0 0 24px rgba(255,45,85,.08)}
    .stat strong{display:block;font-size:1.5rem;text-shadow:0 0 16px rgba(255,45,85,.2)}
    .stat span{display:block;color:var(--muted-2);font-size:.9rem;margin-top:6px}

    .hero-side{padding:22px;display:grid;gap:14px}
    .mini-card{padding:18px;border-radius:20px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06)}
    .mini-card h3{margin:0 0 8px;font-size:1rem}
    .mini-list{display:grid;gap:10px}
    .mini-list div{display:flex;gap:10px;align-items:flex-start;color:var(--muted)}
    .devil{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:radial-gradient(circle at 30% 30%, #ff8ba3, var(--red));box-shadow:0 0 16px rgba(255,45,85,.3);font-size:.9rem;flex:0 0 26px}

    .section{padding:20px 0}
    .section-head{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:18px}
    .section-head h2{margin:0;font-size:clamp(1.45rem,2.4vw,2.4rem)}
    .section-head p{margin:0;color:var(--muted);max-width:740px;line-height:1.8}

    .products-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
    .product-card{position:relative;padding:24px;border-radius:28px;background:linear-gradient(180deg, rgba(18,18,24,.92), rgba(10,10,14,.9));border:1px solid rgba(255,75,75,.16);box-shadow:var(--shadow);overflow:hidden}
    .product-card:hover{transform:translateY(-4px);box-shadow:0 30px 100px rgba(0,0,0,.55),0 0 34px rgba(255,45,85,.18)}
    .card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:16px}
    .card-head h3{margin:8px 0 6px;font-size:1.45rem}
    .card-head p{margin:0;color:var(--muted);line-height:1.8}
    .orb{width:74px;height:74px;border-radius:50%;background:radial-gradient(circle at 30% 30%, rgba(255,255,255,.36), rgba(255,45,85,.24), transparent 70%);border:1px solid rgba(255,75,75,.22);box-shadow:0 0 40px rgba(255,45,85,.2)}
    .variant-selector-wrap{display:grid;gap:8px;margin-bottom:16px}
    .selector-label{font-size:.85rem;color:var(--muted-2)}
    .variant-selector{width:100%;background:#121318;color:var(--text);border:1px solid rgba(255,75,75,.18);border-radius:16px;padding:14px 16px;outline:none;box-shadow:0 0 0 1px rgba(255,45,85,.06)}
    .variant-panel{display:none;gap:14px}
    .variant-panel.active{display:grid;animation:fadeIn .28s ease}
    @keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
    .variant-meta-row{display:flex;flex-wrap:wrap;gap:8px}
    .pill{display:inline-flex;align-items:center;padding:9px 12px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid rgba(255,75,75,.16);color:#fbe6ea;font-size:.83rem;box-shadow:0 0 18px rgba(255,45,85,.08)}
    .badge{background:rgba(255,45,85,.14);border-color:rgba(255,75,75,.28)}
    .price-line{display:grid;gap:4px}
    .price{font-size:2rem;font-weight:900;letter-spacing:-.03em;color:#fff;text-shadow:0 0 24px rgba(255,45,85,.2)}
    .subprice{color:var(--muted-2);font-size:.92rem}
    .feature-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .feature-chip{display:flex;gap:10px;align-items:flex-start;padding:14px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.12)}
    .feature-chip span:last-child{color:var(--muted);line-height:1.6}
    .buyrow{display:flex;gap:12px;align-items:center;margin-top:4px;flex-wrap:wrap}
    .qtybox{display:flex;align-items:center;gap:10px;padding:8px;border-radius:16px;background:rgba(255,255,255,.035);border:1px solid rgba(255,75,75,.16)}
    .qtybtn{width:38px;height:38px;font-size:1.2rem}
    .qtyval{min-width:24px;text-align:center;font-weight:800}
    .btn-buy{padding:14px 20px;background:linear-gradient(135deg,#a8002a,var(--red-2));color:#fff;box-shadow:0 0 0 1px rgba(255,255,255,.06), 0 0 26px rgba(255,45,85,.22)}
    .btn[disabled]{opacity:.46;cursor:not-allowed;transform:none;box-shadow:none}
    .micro-note{font-size:.88rem;color:var(--muted-2)}

    .trust-grid,.faq-grid,.testi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
    .trust-card,.faq-item,.testi-card,.contact-card,.footer-stat{padding:18px;border-radius:22px;background:linear-gradient(180deg, rgba(17,17,22,.9), rgba(10,10,14,.88));border:1px solid rgba(255,75,75,.14);box-shadow:var(--shadow)}
    .trust-card h3,.testi-card h3{margin:12px 0 8px;font-size:1rem}
    .trust-card p,.testi-card p,.contact-card p{margin:0;color:var(--muted);line-height:1.8}
    .faq-grid{grid-template-columns:1fr}
    .faq-item summary{cursor:pointer;font-weight:800;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px}
    .faq-item summary::-webkit-details-marker{display:none}
    .faq-item summary::after{content:'+';font-size:1.2rem;color:#fff}
    .faq-item[open] summary::after{content:'âˆ’'}
    .faq-answer{margin-top:14px;color:var(--muted);line-height:1.8}
    .testi-card h3{display:flex;justify-content:space-between;gap:10px}

    .contact-grid{display:grid;grid-template-columns:1.08fr .92fr;gap:18px}
    .contact-actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:18px}

    .footer{padding:24px 0 70px}
    .footer-box{display:grid;gap:16px;padding:22px;border-radius:28px;background:linear-gradient(180deg, rgba(14,14,18,.95), rgba(7,7,10,.92));border:1px solid rgba(255,75,75,.14);box-shadow:var(--shadow)}
    .footer-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .footer p{margin:0;color:var(--muted)}

    .floating-cta{position:fixed;right:18px;bottom:18px;z-index:70;display:inline-flex;align-items:center;gap:10px;padding:14px 18px;border-radius:999px;background:linear-gradient(135deg,var(--red-3),var(--red-2));color:#fff;box-shadow:0 0 0 1px rgba(255,255,255,.08),0 0 26px rgba(255,45,85,.24)}

    .modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.62);backdrop-filter:blur(4px);opacity:0;pointer-events:none;transition:.25s ease;z-index:150}
    .modal{position:fixed;left:50%;top:50%;transform:translate(-50%,-45%) scale(.98);width:min(94vw,420px);background:#111217;border:1px solid rgba(255,75,75,.18);border-radius:26px;padding:22px;z-index:160;opacity:0;pointer-events:none;transition:.25s ease;box-shadow:0 20px 80px rgba(0,0,0,.56),0 0 28px rgba(255,45,85,.16)}
    .modal h3{margin:0 0 10px}
    .modal p{margin:0;color:var(--muted);line-height:1.8}
    .modal .stack{display:grid;gap:12px;margin-top:18px}
    .modal.open,.modal-backdrop.open{opacity:1;pointer-events:auto}
    .modal.open{transform:translate(-50%,-50%) scale(1)}

    .skeleton-overlay{position:fixed;inset:0;background:rgba(5,5,8,.95);backdrop-filter:blur(4px);z-index:200;display:none;place-items:center;padding:20px}
    .skeleton-box{width:min(96vw,980px);display:grid;gap:16px}
    .skeleton-card{height:88px;border-radius:24px;background:linear-gradient(90deg, rgba(255,255,255,.06) 25%, rgba(255,255,255,.14) 50%, rgba(255,255,255,.06) 75%);background-size:240% 100%;animation:shimmer 1.4s infinite}
    .skeleton-card.big{height:280px}
    @keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}

    .reveal{opacity:0;transform:translateY(22px);transition:opacity .65s ease, transform .65s ease}
    .reveal.show{opacity:1;transform:none}

    @media (max-width: 1080px){
      .hero-grid,.products-grid,.contact-grid,.trust-grid,.testi-grid{grid-template-columns:1fr}
      .stats-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
      .feature-grid{grid-template-columns:1fr}
    }
    @media (min-width: 900px){ .nav-chip{display:inline-flex} .container{padding:0 26px} }
    @media (max-width: 720px){
      .container{padding:0 16px}
      .hero-card,.hero-side,.product-card,.trust-card,.faq-item,.testi-card,.contact-card,.footer-box{border-radius:22px}
      .hero-card{padding:24px}
      .header-inner{padding:12px 0}
      .stats-grid,.footer-stats{grid-template-columns:1fr 1fr}
      .floating-cta{right:12px;left:12px;justify-content:center}
      .brand-text span{display:none}
    }
  </style>
</head>
<body>
  <div class="drawer-backdrop" id="drawerBackdrop"></div>
  <aside class="drawer" id="drawer">
    <div class="drawer-top">
      <div class="brand">
        <img src="$logo_url" alt="Logo Impura" class="logo"/>
        <div class="brand-text"><strong>Impura</strong><span>Cyber Premium Access</span></div>
      </div>
      <button class="icon-btn" id="closeDrawer" style="width:42px;height:42px">âœ•</button>
    </div>
    <nav class="drawer-nav">
      <a href="#beranda">Beranda <span>â†—</span></a>
      <a href="/cek-order">Cek Order <span>â†—</span></a>
      <a href="#hubungi-kami">Hubungi Kami <span>â†—</span></a>
      <a href="#faq">FAQ <span>â†—</span></a>
    </nav>
  </aside>

  <header class="sticky-header">
    <div class="container header-inner">
      <div class="brand">
        <button class="menu-btn" id="openDrawer" aria-label="Buka menu">â˜°</button>
        <img src="$logo_url" alt="Logo Impura" class="logo"/>
        <div class="brand-text"><strong>Impura</strong><span>â€¢ QRIS Verified</span></div>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary glitch-btn" data-label="Chat Admin" id="chatAdminBtn" type="button">Chat Admin</button>
      </div>
    </div>
  </header>

  <main>
    <section class="hero" id="beranda">
      <div class="container hero-grid">
        <div class="hero-card reveal show">
          <span class="eyebrow">Beli Akses AI Premium dengan proses cepat.</span>
          <div class="title"><span class="accent">Beli Akses AI Premium dengan proses cepat</span>.</div>
          <div class="typing-wrap"><span>âš¡</span><span id="typingText"></span><span class="typing-cursor"></span></div>
          <p class="lead">Pilih produk  bayar QRIS  tunggu verifikasi  sistem otomatis kirim akun email.</p>
          <div class="hero-actions">
            <a href="#produk" class="btn btn-primary glitch-btn" data-label="Lihat Produk">Lihat Produk</a>
            <a href="/cek-order" class="btn btn-secondary">Cek Status Order</a>
          </div>
          <div class="stats-grid">
            <div class="stat"><strong id="heroSold">$hero_sold</strong><span>Total produk terjual</span></div>
            <div class="stat"><strong id="heroActive">$hero_active</strong><span>Pengguna aktif</span></div>
          </div>
        </div>
        <div class="hero-side reveal">
          <div class="mini-card">
            <h3>Kenapa pilih Impura?</h3>
            <div class="mini-list">
              <div><span class="devil">ðŸ˜ˆ</span><span>Semua produk bergaransi.</span></div>
              <div><span class="devil">ðŸ˜ˆ</span><span>Support after sales, ketika produk bermasalah bisa chatt admin.</span></div>
              <div><span class="devil">ðŸ˜ˆ</span><span>Harga murah namun berkualitas</span></div>
            </div>
          </div>
          <div class="mini-card">
            <h3>Support & delivery</h3>
            <div class="mini-list">
              <div><span class="devil">ðŸ˜ˆ</span><span>Pilih WhatsApp atau Telegram langsung dari tombol chat admin.</span></div>
              <div><span class="devil">ðŸ˜ˆ</span><span>Cek status order mandiri menggunakan Order ID kapan pun.</span></div>
              <div><span class="devil">ðŸ˜ˆ</span><span>QRIS bisa diperbesar untuk membantu scan via galeri.</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="section" id="produk">
      <div class="container">
        <div class="section-head reveal">
          <div>
            <h2>Produk</h2>
          </div>
        </div>
        <div class="products-grid">
          $cards
        </div>
      </div>
    </section>

    <section class="section" id="keunggulan">
      <div class="container">
        <div class="section-head reveal">
          <div>
            <h2>Trust layer yang lebih kuat</h2>
            <p>Section ini membantu menaikkan kepercayaan user dengan menampilkan value proposition yang jelas, terpisah, dan mudah dibaca.</p>
          </div>
        </div>
        <div class="trust-grid">
          <div class="trust-card reveal"><span class="devil">ðŸ˜ˆ</span><h3>Pembayaran Aman</h3><p>Instruksi QRIS diperjelas agar user membayar sesuai nominal unik yang muncul.</p></div>
          <div class="trust-card reveal"><span class="devil">ðŸ˜ˆ</span><h3>Verifikasi Cepat</h3><p>Status order bisa dicek manual lewat halaman khusus tanpa harus chat admin dulu.</p></div>
          <div class="trust-card reveal"><span class="devil">ðŸ˜ˆ</span><h3>Responsif Mobile</h3><p>Desktop lebih full, mobile tetap rapih, sticky header selalu terlihat, dan feedback salin lebih terasa.</p></div>
          <div class="trust-card reveal"><span class="devil">ðŸ˜ˆ</span><h3>Cyber Premium UI</h3><p>Scanline, border glow, hover glitch, dan animasi modern membentuk identitas visual yang lebih kuat.</p></div>
        </div>
      </div>
    </section>

    

    <section class="section" id="faq">
      <div class="container">
        <div class="section-head reveal">
          <div>
            <h2>FAQ</h2>
            <p>Draft FAQ sudah disiapkan agar user bisa mendapat jawaban cepat tanpa harus bertanya ke admin untuk hal-hal dasar.</p>
          </div>
        </div>
        <div class="faq-grid">
          $faq_items
        </div>
      </div>
    </section>

    <section class="section" id="hubungi-kami">
      <div class="container contact-grid">
        <div class="contact-card reveal">
          <div class="section-head" style="margin-bottom:10px"><div><h2>Butuh bantuan cepat?</h2><p>Klik tombol chat admin di pojok kanan atas.</p></div></div>
        </div>
        <div class="contact-card reveal">
          <div class="section-head" style="margin-bottom:10px"><div><h2>Catatan penting pembayaran</h2></div></div>
          <p><strong style="color:#fff">Jangan membulatkan nominal transfer.</strong> Sistem membaca nominal unik sampai digit terakhir untuk mempercepat pencocokan pembayaran. Transfer harus persis sesuai angka yang tampil pada halaman bayar.</p>
        </div>
      </div>
    </section>
  </main>

  <footer class="footer">
    <div class="container">
      <div class="footer-box reveal show">
        <p>Â© $year Impura. All rights reserved.</p>
      </div>
    </div>
  </footer>

  <a href="#produk" class="floating-cta">ðŸ”¥ Beli Sekarang</a>

  <div class="modal-backdrop" id="modalBackdrop"></div>
  <div class="modal" id="chatModal">
    <h3>Hubungi Admin</h3>
    <p>Pilih channel yang ingin kamu gunakan untuk menghubungi admin Impura.</p>
    <div class="stack">
      <a class="btn btn-primary" href="$wa_url" target="_blank" rel="noopener">WhatsApp</a>
      <a class="btn btn-secondary" href="$telegram_url" target="_blank" rel="noopener">Telegram</a>
      <button class="btn btn-secondary" id="closeModalBtn" type="button">Tutup</button>
    </div>
  </div>

  <div class="skeleton-overlay" id="skeletonOverlay">
    <div class="skeleton-box">
      <div class="skeleton-card"></div>
      <div class="skeleton-card big"></div>
      <div class="skeleton-card"></div>
    </div>
  </div>

  <script>
    const PRODUCT_META = $product_json;
    const qtyMap = {};
    const typingTarget = document.getElementById('typingText');
    const typingMessage = 'Menyediakan Berbagai Layanan AI Premium';
    let typingIndex = 0;
    function runTyping(){
      if(!typingTarget) return;
      if(typingIndex <= typingMessage.length){
        typingTarget.textContent = typingMessage.slice(0, typingIndex);
        typingIndex += 1;
        setTimeout(runTyping, typingIndex < typingMessage.length ? 55 : 1200);
      } else {
        typingIndex = 0;
        setTimeout(runTyping, 600);
      }
    }
    runTyping();

    function animateCounter(el, end){
      if(!el) return;
      const finalVal = Number(end || 0);
      const start = performance.now();
      const duration = 900;
      const from = Number(String(el.textContent).replace(/\D/g,'')) || 0;
      function frame(now){
        const p = Math.min(1, (now - start) / duration);
        const value = Math.round(from + (finalVal - from) * (1 - Math.pow(1-p, 3)));
        el.textContent = value.toLocaleString('id-ID');
        if(p < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    }

    function setVariant(groupId, pid){
      document.querySelectorAll(`[data-group-stack="${groupId}"] .variant-panel`).forEach(el => el.classList.remove('active'));
      const panel = document.querySelector(`[data-variant-panel="${pid}"]`);
      if(panel) panel.classList.add('active');
      if(!qtyMap[pid]) qtyMap[pid] = 1;
    }

    document.querySelectorAll('[data-group-selector]').forEach(select => {
      const groupId = select.getAttribute('data-group-selector');
      setVariant(groupId, select.value);
      select.addEventListener('change', () => setVariant(groupId, select.value));
    });
    document.querySelectorAll('.product-card').forEach(card => {
      const groupId = card.dataset.group;
      if(!card.querySelector('[data-group-selector]')){
        const panel = card.querySelector('.variant-panel');
        if(panel) panel.classList.add('active');
      }
    });

    document.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pid = btn.dataset.pid;
        const act = btn.dataset.act;
        const meta = PRODUCT_META[pid] || {};
        const stock = Number(meta.stock || 0);
        let val = qtyMap[pid] || 1;
        if(act === 'minus') val = Math.max(1, val - 1);
        if(act === 'plus') val = Math.min(Math.max(stock, 1), val + 1);
        qtyMap[pid] = val;
        const target = document.querySelector(`[data-qty="${pid}"]`);
        if(target) target.textContent = String(val);
      });
    });

    function openDrawer(){ drawer.classList.add('open'); drawerBackdrop.classList.add('open'); document.body.style.overflow='hidden'; }
    function closeDrawer(){ drawer.classList.remove('open'); drawerBackdrop.classList.remove('open'); document.body.style.overflow=''; }
    const drawer = document.getElementById('drawer');
    const drawerBackdrop = document.getElementById('drawerBackdrop');
    document.getElementById('openDrawer').addEventListener('click', openDrawer);
    document.getElementById('closeDrawer').addEventListener('click', closeDrawer);
    drawerBackdrop.addEventListener('click', closeDrawer);
    document.querySelectorAll('.drawer-nav a').forEach(a => a.addEventListener('click', closeDrawer));

    const modal = document.getElementById('chatModal');
    const modalBackdrop = document.getElementById('modalBackdrop');
    function openModal(){ modal.classList.add('open'); modalBackdrop.classList.add('open'); }
    function closeModal(){ modal.classList.remove('open'); modalBackdrop.classList.remove('open'); }
    document.getElementById('chatAdminBtn').addEventListener('click', openModal);
    document.getElementById('chatAdminBtn2').addEventListener('click', openModal);
    document.getElementById('closeModalBtn').addEventListener('click', closeModal);
    modalBackdrop.addEventListener('click', closeModal);

    const skeleton = document.getElementById('skeletonOverlay');
    document.querySelectorAll('[data-buy]').forEach(btn => {
      btn.dataset.label = btn.textContent.trim();
      btn.addEventListener('click', () => {
        const pid = btn.dataset.buy;
        const qty = qtyMap[pid] || 1;
        skeleton.style.display = 'grid';
        setTimeout(() => {
          location.href = `/checkout/${pid}?qty=${qty}`;
        }, 650);
      });
    });

    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if(entry.isIntersecting) entry.target.classList.add('show'); });
    }, { threshold: .15 });
    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

    async function refreshStats(){
      try{
        const [statsRes, visRes, stockRes] = await Promise.all([
          fetch('/api/stats'),
          fetch('/api/visitors'),
          fetch('/api/stock')
        ]);
        const stats = await statsRes.json();
        const vis = await visRes.json();
        const stockData = await stockRes.json();
        if(stats.ok){
          animateCounter(document.getElementById('heroSold'), stats.total_success);
          animateCounter(document.getElementById('heroActive'), stats.active_users);
          animateCounter(document.getElementById('heroToday'), stats.today_success);
          animateCounter(document.getElementById('footerActive'), stats.active_users);
          animateCounter(document.getElementById('footerToday'), stats.today_success);
          Object.entries(stats.sold || {}).forEach(([pid, val]) => {
            const panel = document.querySelector(`[data-variant-panel="${pid}"] .pill:nth-child(2)`);
            if(panel) panel.textContent = `Terjual ${Number(val || 0).toLocaleString('id-ID')}`;
          });
        }
        if(vis.ok){ animateCounter(document.getElementById('heroVisitors'), vis.count); }
        if(stockData.ok){
          Object.entries(stockData.stock || {}).forEach(([pid, val]) => {
            const el = document.getElementById(`stock-${pid}`);
            if(el) el.textContent = `Stok ${Number(val || 0).toLocaleString('id-ID')}`;
          });
        }
      }catch(e){ console.log(e); }
    }
    refreshStats();
    setInterval(refreshStats, 18000);
  </script>
</body>
</html>""")

PAY_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Bayar Order â€” Impura</title>
  <style>
    :root{color-scheme:dark;--bg:#050506;--panel:#111217;--line:rgba(255,75,75,.18);--text:#f7f8fc;--muted:#c6cad4;--red:#ff2d55;--red2:#ff4b4b;--shadow:0 20px 70px rgba(0,0,0,.56),0 0 30px rgba(255,45,85,.14)}
    *{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at top left, rgba(255,45,85,.14), transparent 30%),linear-gradient(180deg,#050506,#0a0a0d);color:var(--text);min-height:100vh}
    body::before{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(to bottom, rgba(255,255,255,.02) 0, rgba(255,255,255,.02) 1px, transparent 3px, transparent 7px);opacity:.28}
    .container{max-width:1180px;margin:0 auto;padding:28px 20px 50px}
    .topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}
    .brand{display:flex;align-items:center;gap:12px}.logo{width:48px;height:48px;border-radius:50%;object-fit:cover;border:2px solid rgba(255,75,75,.4);box-shadow:0 0 26px rgba(255,45,85,.22)}
    .grid{display:grid;grid-template-columns:1fr .96fr;gap:18px}.card{background:linear-gradient(180deg, rgba(17,18,23,.94), rgba(10,10,14,.9));border:1px solid var(--line);border-radius:28px;padding:24px;box-shadow:var(--shadow)}
    .eyebrow{display:inline-flex;padding:8px 14px;border-radius:999px;background:rgba(255,45,85,.12);border:1px solid rgba(255,75,75,.26);font-size:.76rem;letter-spacing:.18em;text-transform:uppercase}
    h1{margin:16px 0 8px;font-size:clamp(1.8rem,3.2vw,3rem)} p{color:var(--muted);line-height:1.8;margin:0}
    .order-grid{display:grid;gap:12px;margin-top:20px}.row{display:flex;justify-content:space-between;gap:16px;padding:14px 16px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.12)}
    .label{color:#9ea5b7}.value{font-weight:800}.value.red{color:#fff;text-shadow:0 0 18px rgba(255,45,85,.24)}
    .warn{margin-top:16px;padding:16px 18px;border-radius:20px;background:rgba(255,45,85,.08);border:1px solid rgba(255,75,75,.18)}
    .warn strong{display:block;margin-bottom:6px;color:#fff}
    .copyline{display:flex;align-items:center;gap:8px;justify-content:flex-end;flex-wrap:wrap}
    .copy-btn,.btn,.zoom-fab{border:none;cursor:pointer;border-radius:14px;padding:12px 16px;font-weight:800;transition:.25s ease}
    .copy-btn{padding:8px 12px;background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,75,75,.18)}
    .btn{display:inline-flex;align-items:center;justify-content:center;text-decoration:none}
    .btn-primary{background:linear-gradient(135deg,#a8002a,var(--red2));color:#fff;box-shadow:0 0 26px rgba(255,45,85,.22)}
    .btn-secondary{background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,255,255,.08)}
    .qris-wrap{display:grid;gap:14px}.qris-frame{position:relative;padding:16px;border-radius:28px;background:#fff;border:2px solid rgba(255,75,75,.18);overflow:hidden}
    .qris-frame img{width:100%;border-radius:18px;cursor:zoom-in}.zoom-fab{position:absolute;right:16px;bottom:16px;background:rgba(17,18,23,.94);color:#fff;border:1px solid rgba(255,75,75,.18);padding:10px 14px}
    .tips{display:grid;gap:12px}.tip{display:flex;gap:10px;align-items:flex-start;padding:14px 16px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.12)}
    .tip .icon{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#ff6684,var(--red));box-shadow:0 0 16px rgba(255,45,85,.24)}
    .actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:18px}.timer{font-size:1.1rem;font-weight:800;color:#fff;text-shadow:0 0 18px rgba(255,45,85,.24)}
    .modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);opacity:0;pointer-events:none;transition:.25s ease}.modal{position:fixed;left:50%;top:50%;transform:translate(-50%,-46%) scale(.98);width:min(96vw,580px);background:#0e0f14;border:1px solid var(--line);border-radius:30px;padding:18px;opacity:0;pointer-events:none;transition:.25s ease;box-shadow:var(--shadow)}
    .modal img{width:100%;border-radius:20px}.modal.open,.modal-backdrop.open{opacity:1;pointer-events:auto}.modal.open{transform:translate(-50%,-50%) scale(1)}
    @media (max-width:960px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="brand"><img src="$logo_url" alt="Logo" class="logo"/><div><strong>Impura</strong><div style="color:#9ea5b7">Pembayaran QRIS</div></div></div>
      <a class="btn btn-secondary" href="/">â† Kembali</a>
    </div>
    <div class="grid">
      <section class="card">
        <span class="eyebrow">Nominal unik wajib persis</span>
        <h1>Bayar sekarang</h1>
        <p>Jangan membulatkan nominal transfer. Sistem membaca hingga digit terakhir agar pembayaran cepat terdeteksi. Gunakan nominal <strong style="color:#fff">sama persis</strong>.</p>
        <div class="order-grid">
          <div class="row"><span class="label">Produk</span><span class="value">$product_name</span></div>
          <div class="row"><span class="label">Jumlah</span><span class="value">$qty</span></div>
          <div class="row"><span class="label">Subtotal</span><span class="value">Rp $subtotal</span></div>
          <div class="row"><span class="label">Order ID</span><span class="copyline"><span class="value" id="orderIdVal">$order_id</span><button class="copy-btn" data-copy="$order_id">Copy</button></span></div>
          <div class="row"><span class="label">Nominal transfer</span><span class="copyline"><span class="value red" id="amountVal">Rp $total</span><button class="copy-btn" data-copy="$total_clean">Copy</button></span></div>
        </div>
        <div class="warn"><strong>Penting:</strong> nominal QRIS harus sama persis, jangan dibulatkan, jangan dikurangi, dan jangan menambah angka lain. Bila transfer tidak sesuai, verifikasi dapat terhambat.</div>
        <div class="actions">
          <a class="btn btn-primary" href="/status/$order_id">Cek Status Order</a>
        </div>
      </section>
      <section class="card qris-wrap">
        <div class="qris-frame">
          <img src="$qris" id="qrisImage" alt="QRIS Impura"/>
          <button class="zoom-fab" id="zoomBtn" type="button">Perbesar QR</button>
        </div>
        <div class="tips">
          <div class="tip"><span class="icon">ðŸ˜ˆ</span><div><strong>Simpan QR jika perlu</strong><div style="color:#c6cad4">Tekan lama pada gambar QR untuk menyimpan ke galeri, atau klik tombol <em>Perbesar QR</em> agar lebih mudah dipindai.</div></div></div>
          <div class="tip"><span class="icon">ðŸ˜ˆ</span><div><strong>Scan QRIS</strong><div style="color:#c6cad4">Buka aplikasi pembayaran, scan QRIS, lalu pastikan memasukan nominal yang sesuai dengan nominal transfer.</div></div></div>
          <div class="tip"><span class="icon">ðŸ˜ˆ</span><div><strong>Setelah bayar</strong><div style="color:#c6cad4">Masuk ke halaman status order untuk melihat apakah pesanan sudah diverifikasi.</div></div></div>
        </div>
      </section>
    </div>
  </div>
  <div class="modal-backdrop" id="backdrop"></div>
  <div class="modal" id="zoomModal">
    <img src="$qris" alt="QRIS Zoom"/>
    <div style="display:flex;justify-content:flex-end;margin-top:14px"><button class="btn btn-secondary" id="closeZoom" type="button">Tutup</button></div>
  </div>
  <script>
    let ttl = $ttl_sec;
    const ttlEl = document.getElementById('ttl');
    function fmt(sec){ const m = String(Math.floor(sec/60)).padStart(2,'0'); const s = String(sec%60).padStart(2,'0'); return `${m}:${s}`; }
    function tick(){
      ttlEl.textContent = fmt(Math.max(0, ttl));
      if(ttl <= 0){ location.href = '/status/$order_id'; return; }
      ttl -= 1;
    }
    tick(); setInterval(tick, 1000);

    function vibrate(){ if('vibrate' in navigator){ navigator.vibrate(20); } }
    document.querySelectorAll('[data-copy]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try{ await navigator.clipboard.writeText(btn.dataset.copy); btn.textContent = 'Copied'; vibrate(); setTimeout(()=>btn.textContent='Copy',1100);}catch(e){}
      });
    });
    const backdrop = document.getElementById('backdrop');
    const modal = document.getElementById('zoomModal');
    function openZoom(){ modal.classList.add('open'); backdrop.classList.add('open'); }
    function closeZoom(){ modal.classList.remove('open'); backdrop.classList.remove('open'); }
    document.getElementById('zoomBtn').addEventListener('click', openZoom);
    document.getElementById('qrisImage').addEventListener('click', openZoom);
    document.getElementById('closeZoom').addEventListener('click', closeZoom);
    backdrop.addEventListener('click', closeZoom);
  </script>
</body>
</html>""")

STATUS_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Status Order â€” Impura</title>
  <style>
    :root{color-scheme:dark;--bg:#050506;--panel:#111217;--line:rgba(255,75,75,.18);--text:#f7f8fc;--muted:#c6cad4;--red:#ff2d55;--red2:#ff4b4b;--shadow:0 20px 70px rgba(0,0,0,.56),0 0 30px rgba(255,45,85,.14)}
    *{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at top left, rgba(255,45,85,.14), transparent 30%),linear-gradient(180deg,#050506,#0a0a0d);color:var(--text);min-height:100vh}
    body::before{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(to bottom, rgba(255,255,255,.02) 0, rgba(255,255,255,.02) 1px, transparent 3px, transparent 7px);opacity:.28}
    .container{max-width:860px;margin:0 auto;padding:28px 18px 50px}.card{background:linear-gradient(180deg, rgba(17,18,23,.94), rgba(10,10,14,.9));border:1px solid var(--line);border-radius:28px;padding:24px;box-shadow:var(--shadow)}
    .top{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:18px}.btn,.copy-btn{border:none;cursor:pointer;border-radius:14px;padding:12px 16px;font-weight:800;transition:.25s ease;text-decoration:none}
    .btn{display:inline-flex;align-items:center;justify-content:center}.btn-primary{background:linear-gradient(135deg,#a8002a,var(--red2));color:#fff;box-shadow:0 0 26px rgba(255,45,85,.22)}.btn-secondary{background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,255,255,.08)}
    h1{margin:0 0 8px;font-size:clamp(1.8rem,3vw,2.6rem)} p{margin:0;color:var(--muted);line-height:1.8}.grid{display:grid;gap:12px;margin-top:20px}.row{display:flex;justify-content:space-between;gap:14px;padding:15px 16px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.12)}
    .badge{display:inline-flex;align-items:center;padding:10px 14px;border-radius:999px;background:$badge;background:linear-gradient(135deg,$badge,rgba(255,255,255,.14));font-weight:900;box-shadow:0 0 18px rgba(255,45,85,.18)}
    .copyline{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}.copy-btn{padding:8px 12px;background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,75,75,.18)}
    .timer{font-size:1.1rem;font-weight:800;text-shadow:0 0 18px rgba(255,45,85,.24)}
  </style>
</head>
<body>
  <div class="container">
    <div class="top">
      <a class="btn btn-secondary" href="/">â† Kembali</a>
      <a class="btn btn-secondary" href="/cek-order">Cari Order Lain</a>
    </div>
    <div class="card">
      <h1>Status pesanan kamu</h1>
      <p>Gunakan halaman ini untuk memantau pesanan. Kamu juga bisa menyalin Order ID dan nominal transfer dalam satu klik.</p>
      <div style="margin-top:16px"><span class="badge">$st</span></div>
      <div class="grid">
        <div class="row"><span>Order ID</span><span class="copyline"><strong id="orderIdVal">$order_id</strong><button class="copy-btn" data-copy="$order_id">Copy</button></span></div>
        <div class="row"><span>Produk</span><strong>$product_name</strong></div>
        <div class="row"><span>Qty</span><strong>$qty</strong></div>
        <div class="row"><span>Nominal transfer</span><span class="copyline"><strong id="amountVal">Rp $amount</strong><button class="copy-btn" data-copy="$amount_clean">Copy</button></span></div>
        <div class="row"><span>Sisa waktu</span><span class="timer" id="ttl">00:00</span></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:20px">
        <a class="btn btn-primary" href="/pay/$order_id">Lihat Halaman Bayar</a>
        <a class="btn btn-secondary" href="$telegram_url" target="_blank" rel="noopener">Telegram Admin</a>
      </div>
    </div>
  </div>
  <script>
    let ttl = $ttl_sec;
    const ttlEl = document.getElementById('ttl');
    function fmt(sec){ const m = String(Math.floor(sec/60)).padStart(2,'0'); const s = String(sec%60).padStart(2,'0'); return `${m}:${s}`; }
    function tick(){ ttlEl.textContent = fmt(Math.max(0, ttl)); if(ttl<=0) return; ttl -= 1; }
    tick(); setInterval(tick,1000);
    function vibrate(){ if('vibrate' in navigator){ navigator.vibrate(20); } }
    document.querySelectorAll('[data-copy]').forEach(btn => btn.addEventListener('click', async()=>{ try{ await navigator.clipboard.writeText(btn.dataset.copy); btn.textContent='Copied'; vibrate(); setTimeout(()=>btn.textContent='Copy',1100);}catch(e){} }));
    async function poll(){
      try{
        const res = await fetch('/api/order/$order_id');
        const data = await res.json();
        if(data.ok){
          ttl = data.ttl_sec || 0;
          if(data.status === 'paid'){
            vibrate();
            location.href = '/voucher/$order_id';
          }
          if(data.status === 'cancelled'){
            location.reload();
          }
        }
      }catch(e){}
    }
    setInterval(poll, 5000);
  </script>
</body>
</html>""")

LOOKUP_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Cek Order â€” Impura</title>
  <style>
    :root{color-scheme:dark;--bg:#050506;--panel:#111217;--line:rgba(255,75,75,.18);--text:#f7f8fc;--muted:#c6cad4;--red:#ff2d55;--red2:#ff4b4b;--shadow:0 20px 70px rgba(0,0,0,.56),0 0 30px rgba(255,45,85,.14)}
    *{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at top left, rgba(255,45,85,.14), transparent 30%),linear-gradient(180deg,#050506,#0a0a0d);color:var(--text);min-height:100vh}
    body::before{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(to bottom, rgba(255,255,255,.02) 0, rgba(255,255,255,.02) 1px, transparent 3px, transparent 7px);opacity:.28}
    .container{max-width:760px;margin:0 auto;padding:28px 18px 50px}.card{background:linear-gradient(180deg, rgba(17,18,23,.94), rgba(10,10,14,.9));border:1px solid var(--line);border-radius:28px;padding:24px;box-shadow:var(--shadow)}
    .logo{width:58px;height:58px;border-radius:50%;object-fit:cover;border:2px solid rgba(255,75,75,.4);box-shadow:0 0 26px rgba(255,45,85,.22)}
    .top{display:flex;align-items:center;gap:14px;margin-bottom:18px}.eyebrow{display:inline-flex;padding:8px 14px;border-radius:999px;background:rgba(255,45,85,.12);border:1px solid rgba(255,75,75,.26);font-size:.76rem;letter-spacing:.18em;text-transform:uppercase}
    h1{margin:14px 0 8px;font-size:clamp(1.9rem,3vw,2.8rem)} p{margin:0;color:var(--muted);line-height:1.8}.form{display:grid;gap:14px;margin-top:22px}.input{width:100%;background:#121318;color:#fff;border:1px solid rgba(255,75,75,.18);border-radius:18px;padding:16px;outline:none;font-size:1rem}.btn{border:none;cursor:pointer;border-radius:16px;padding:14px 18px;font-weight:800;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}
    .btn-primary{background:linear-gradient(135deg,#a8002a,var(--red2));color:#fff;box-shadow:0 0 26px rgba(255,45,85,.22)}.btn-secondary{background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,255,255,.08)}
    .hint{padding:16px 18px;border-radius:18px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.12);margin-top:16px;color:var(--muted)}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="top"><img src="$logo_url" alt="Logo" class="logo"/><div><span class="eyebrow">Order Lookup</span><h1>Cek status pesanan dengan Order ID</h1></div></div>
      <p>Masukkan Order ID yang kamu dapat setelah checkout. Sistem akan langsung mengarahkan ke halaman status pesanan.</p>
      <form class="form" method="get" action="/cek-order">
        <input class="input" type="text" name="order_id" placeholder="Contoh: 1e4b0f4a-xxxx-xxxx-xxxx-xxxxxxxxxxxx" value="$existing_order_id" required/>
        <button class="btn btn-primary" type="submit">Cek Status</button>
      </form>
      <div class="hint">Tips: gunakan tombol Copy di halaman pembayaran agar Order ID mudah disalin tanpa salah.</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:18px">
        <a class="btn btn-secondary" href="/">Kembali ke Beranda</a>
        <a class="btn btn-secondary" href="$telegram_url" target="_blank" rel="noopener">Hubungi Admin</a>
      </div>
      $lookup_error
    </div>
  </div>
</body>
</html>""")

VOUCHER_HTML = Template(r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Detail Akun â€” Impura</title>
  <style>
    :root{color-scheme:dark;--bg:#050506;--panel:#111217;--line:rgba(255,75,75,.18);--text:#f7f8fc;--muted:#c6cad4;--red:#ff2d55;--red2:#ff4b4b;--shadow:0 20px 70px rgba(0,0,0,.56),0 0 30px rgba(255,45,85,.14)}
    *{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at top left, rgba(255,45,85,.14), transparent 30%),linear-gradient(180deg,#050506,#0a0a0d);color:var(--text);min-height:100vh}
    body::before{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(to bottom, rgba(255,255,255,.02) 0, rgba(255,255,255,.02) 1px, transparent 3px, transparent 7px);opacity:.28}
    .container{max-width:880px;margin:0 auto;padding:28px 18px 50px}.card{background:linear-gradient(180deg, rgba(17,18,23,.94), rgba(10,10,14,.9));border:1px solid var(--line);border-radius:28px;padding:24px;box-shadow:var(--shadow)}
    .code{margin-top:18px;padding:18px;border-radius:20px;background:rgba(255,255,255,.03);border:1px solid rgba(255,75,75,.14);white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:1rem;line-height:1.8}.btn{border:none;cursor:pointer;border-radius:16px;padding:14px 18px;font-weight:800;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.btn-primary{background:linear-gradient(135deg,#a8002a,var(--red2));color:#fff;box-shadow:0 0 26px rgba(255,45,85,.22)}.btn-secondary{background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,255,255,.08)}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1 style="margin:0 0 8px">Pembayaran berhasil âœ…</h1>
      <p style="margin:0;color:#c6cad4;line-height:1.8">Berikut detail akun untuk produk <strong style="color:#fff">$pid_name</strong>. Simpan data ini baik-baik.</p>
      <div class="code" id="voucherBox">$code</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:18px">
        <button class="btn btn-primary" id="copyVoucher" type="button">Salin email</button>
        <a class="btn btn-secondary" href="/">Kembali ke Beranda</a>
      </div>
      <div class="warn"><strong>Penting:</strong> Jangan gunakan temp mail/number untuk pemulihan, gunakan email/nomor asli untuk pemulihan. Kalau masih ngeyel dan akun kena verif, kami tidak tanggung jawab.</div>
    </div>
  </div>
  <script>
    document.getElementById('copyVoucher').addEventListener('click', async()=>{
      try{ await navigator.clipboard.writeText(document.getElementById('voucherBox').innerText); if('vibrate' in navigator){navigator.vibrate(25);} document.getElementById('copyVoucher').textContent='Tersalin'; setTimeout(()=>document.getElementById('copyVoucher').textContent='Salin Semua',1100);}catch(e){}
    });
  </script>
</body>
</html>""")

ADMIN_HTML = Template(r"""<!doctype html>
<html lang="id"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Admin Impura</title>
<style>
body{margin:0;font-family:Inter,system-ui,sans-serif;background:#08090c;color:#fff}.wrap{max-width:1100px;margin:0 auto;padding:26px 18px}.row{display:flex;justify-content:space-between;gap:12px;padding:18px;border-radius:18px;background:#111217;border:1px solid rgba(255,75,75,.18);margin-bottom:14px}.muted{color:#aab1be;font-size:.92rem}.vbtn,.lbtn{border:none;text-decoration:none;cursor:pointer;border-radius:14px;padding:12px 14px;font-weight:800;display:inline-flex;align-items:center;justify-content:center}.vbtn{background:linear-gradient(135deg,#a8002a,#ff4b4b);color:#fff}.lbtn{background:rgba(255,255,255,.05);color:#fff;border:1px solid rgba(255,255,255,.08)}
</style></head><body><div class="wrap"><h1>Admin Panel</h1>$items</div></body></html>""")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    stock = get_stock_map()
    stats = get_sales_stats()
    cards = build_home_cards(stock, stats)
    vis_count = _VISITOR_BASE + max(1, len(_VISITOR_SESS))
    html = _tpl_render(
        HOME_HTML,
        cards=cards,
        year=now_utc().year,
        faq_items=build_faq_items(),
        logo_url=LOGO_URL,
        wa_url=WHATSAPP_URL,
        telegram_url=TELEGRAM_URL,
        hero_sold=f"{stats['total_success']:,}",
        hero_active=f"{stats['active_users']:,}",
        hero_today=f"{max(stats['today_success'], _TODAY_SUCCESS_BASE + stats['today_success']):,}",
        hero_visitors=f"{vis_count:,}",
        product_json=json.dumps({pid: {"stock": int(stock.get(pid, 0)), "price": int(p["price"])} for pid, p in PRODUCTS.items()}),
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
    ins = supabase.table("orders").insert({
        "id": order_id,
        "product_id": product_id,
        "qty": int(qty),
        "unit": int(base_price),
        "amount_idr": int(total),
        "status": "pending",
        "created_at": created_at,
        "voucher_code": None,
    }).execute()
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
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))
    html = _tpl_render(
        PAY_HTML,
        product_name=product_name,
        qty=str(qty),
        unit=rupiah(unit),
        subtotal=rupiah(subtotal),
        total=rupiah(amount),
        total_clean=str(amount),
        qris=QR_IMAGE_URL,
        order_id=order_id,
        ttl=str(ORDER_TTL_MINUTES),
        ttl_sec=str(ttl_sec),
        logo_url=LOGO_URL,
        telegram_url=TELEGRAM_URL,
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
    created = _parse_dt(order.get("created_at", "")) or now_utc()
    ttl_sec = max(0, int(ORDER_TTL_MINUTES * 60 - (now_utc() - created).total_seconds()))
    html = _tpl_render(
        STATUS_HTML,
        qty=str(qty),
        amount=rupiah(amount),
        amount_clean=str(amount),
        st=st.upper(),
        badge=badge,
        order_id=order_id,
        ttl_sec=str(ttl_sec),
        product_name=PRODUCTS.get(pid, {}).get("name", pid),
        telegram_url=TELEGRAM_URL,
    )
    return HTMLResponse(html)


@app.get("/cek-order", response_class=HTMLResponse)
def lookup(order_id: Optional[str] = None):
    if order_id:
        return RedirectResponse(url=f"/status/{order_id.strip()}", status_code=302)
    html = _tpl_render(
        LOOKUP_HTML,
        logo_url=LOGO_URL,
        existing_order_id="",
        lookup_error="",
        telegram_url=TELEGRAM_URL,
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
        return HTMLResponse("<html><body style='font-family:Arial;background:#07070b;color:white;text-align:center;padding:40px'><h2>Akun Email</h2><p>Status: PAID âœ…</p><p style='opacity:.8'>Maaf, stok untuk produk ini sedang habis.</p></body></html>")
    html = _tpl_render(VOUCHER_HTML, pid_name=PRODUCTS.get(order.get("product_id"), {}).get("name", order.get("product_id")), code=code)
    return HTMLResponse(html)


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
    stats = get_sales_stats()
    return {
        "ok": True,
        "sold": stats["sold"],
        "total_success": stats["total_success"],
        "today_success": max(stats["today_success"], _TODAY_SUCCESS_BASE + stats["today_success"]),
        "active_users": stats["active_users"],
    }


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
    resp.set_cookie("vis_sid", sid, max_age=24*3600, httponly=True, samesite="lax")
    return resp


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
                action = f"<form method='post' action='/admin/verify/{oid}?token={token}' style='margin:0;'><button class='vbtn' type='submit'>VERIFIKASI + KIRIM VOUCHER</button></form><div class='muted'>Auto-cancel: {ORDER_TTL_MINUTES} menit</div>"
            elif st == "paid":
                label = f"Voucher: {vcode}" if vcode else "Voucher: (habis / belum ada)"
                action = f"<a class='lbtn' href='/voucher/{oid}'>Buka Akun Email</a><div class='muted'>{label}</div>"
            else:
                action = f"<div class='muted'>Status: {st.upper()}</div><a class='lbtn' href='/pay/{oid}'>Buka Pay</a>"
            items += f"<div class='row'><div><div><b>{PRODUCTS.get(pid, {}).get('name', pid)}</b> â€” Qty {qty} â€” Rp {rupiah(amt)}</div><div class='muted'>ID: {oid}</div><div class='muted'>{created}</div><div class='muted'>Status: {st}</div></div><div>{action}</div></div>"
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
