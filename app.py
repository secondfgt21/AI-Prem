from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

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
            }}
            img {{
                margin-top:15px;
                border-radius:10px;
            }}
        </style>
    </head>
    <body>

        <div class="box">
            <h2>Pembayaran QRIS</h2>
            <p>Produk: <b>{product_id}</b></p>

            <p>Silakan scan QRIS:</p>

            <img src="LINK_QRIS_KAMU" width="220"><br><br>

            <p style="opacity:.7;">
            Setelah bayar, tunggu verifikasi otomatis.
            </p>

        </div>

    </body>
    </html>
    """
