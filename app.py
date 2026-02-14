from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import random

app = FastAPI()

# ======================
# HALAMAN UTAMA (LANDING)
# ======================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head>
        <title>AI Premium Store</title>
        <style>
            body{
                font-family: Arial;
                background:#0f172a;
                color:white;
                text-align:center;
                padding:40px;
            }
            .card{
                background:#1e293b;
                padding:25px;
                border-radius:15px;
                max-width:350px;
                margin:auto;
                box-shadow:0 10px 25px rgba(0,0,0,0.4);
            }
            .btn{
                display:inline-block;
                background:#22c55e;
                border:none;
                padding:12px 20px;
                color:white;
                font-size:16px;
                border-radius:8px;
                cursor:pointer;
                margin-top:15px;
                text-decoration:none;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>AI Premium</h1>
            <p>Gemini AI Pro 1 Tahun</p>
            <h2>Rp 30.000</h2>
            <p>Stok: 12 tersedia</p>

            <a href="/checkout/gemini" class="btn">
                Beli Sekarang
            </a>
        </div>
    </body>
    </html>
    """


# ======================
# HALAMAN CHECKOUT
# ======================
@app.get("/checkout/{product_id}", response_class=HTMLResponse)
def checkout(product_id: str):
    harga = 30000
    kode_unik = random.randint(101, 999)
    total = harga + kode_unik

    return f"""
    <html>
    <head>
        <title>Pembayaran</title>
        <style>
            body {{
                font-family: Arial;
                text-align:center;
                background:#0f172a;
                color:white;
                padding-top:40px;
            }}
            .box {{
                background:#1e293b;
                padding:25px;
                border-radius:15px;
                display:inline-block;
                max-width:340px;
            }}
            img {{
                margin-top:15px;
                border-radius:10px;
                background:white;
                padding:8px;
            }}
            .total {{
                font-size:28px;
                font-weight:bold;
                color:#22c55e;
                margin-top:10px;
            }}
            .note {{
                opacity:.7;
                font-size:13px;
            }}
        </style>
    </head>
    <body>

        <div class="box">
            <h2>Pembayaran QRIS</h2>
            <p>Produk: <b>{product_id}</b></p>

            <p>Total transfer:</p>
            <div class="total">Rp {total:,}</div>

            <p class="note">
            termasuk kode unik untuk verifikasi otomatis
            </p>

            <p>Scan QRIS:</p>

            <img src="https://i.postimg.cc/qRkr7LcJ/Kode-QRIS-WARUNG-MAKMUR-ABADI-CIANJUR-(1).png" width="220">

            <p class="note" style="margin-top:15px;">
            Setelah bayar, tunggu verifikasi otomatis.
            </p>
        </div>

    </body>
    </html>
    """
