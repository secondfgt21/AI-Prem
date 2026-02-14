from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

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
            button{
                background:#22c55e;
                border:none;
                padding:12px 20px;
                color:white;
                font-size:16px;
                border-radius:8px;
                cursor:pointer;
                margin-top:15px;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>AI Premium</h1>
            <p>Gemini AI Pro 1 Tahun</p>
            <h2>Rp 30.000</h2>
            <p>Stok: 12 tersedia</p>
            <button onclick="alert('Fitur order segera aktif')">Beli Sekarang</button>
        </div>
    </body>
    </html>
    """
