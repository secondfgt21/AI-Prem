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
    <body style="font-family:sans-serif; text-align:center; padding-top:40px;">
        <h2>Checkout</h2>
        <p>Produk: {product_id}</p>

        <form method="post" action="/order">
            <input type="hidden" name="product_id" value="{product_id}">
            <input name="buyer_name" placeholder="Nama" required><br><br>
            <input name="buyer_contact" placeholder="WhatsApp / Telegram" required><br><br>
            <button type="submit">Lanjut Bayar</button>
        </form>
    </body>
    </html>
    """


# ======================
# PROSES ORDER
# ======================
@app.post("/order", response_class=HTMLResponse)
def order(
    product_id: str = Form(...),
    buyer_name: str = Form(...),
    buyer_contact: str = Form(...)
):
    return f"""
    <html>
    <body style="font-family:sans-serif;text-align:center;padding-top:40px;">
        <h2>Pesanan berhasil dibuat ✅</h2>

        <p><b>Produk:</b> {product_id}</p>
        <p><b>Nama:</b> {buyer_name}</p>

        <h3>Silakan Bayar via QRIS</h3>

        <img src="https://ibb.co.com/LDBnFg3C" width="220"><br><br>

        <p>Setelah bayar, kirim bukti ke WhatsApp:</p>

        <a href="https://wa.me/6281317391284">
            <button style="padding:10px 18px;font-size:16px;">
                Kirim Bukti Pembayaran
            </button>
        </a>

        <p style="margin-top:20px;font-size:13px;color:gray;">
        Admin akan verifikasi & mengirim voucher otomatis.
        </p>
    </body>
    </html>
    """
