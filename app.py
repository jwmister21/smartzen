from flask import Flask, render_template, request, redirect, session, g, jsonify
from datetime import datetime
from calendar import monthrange
import os
import re
import pdfplumber
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = "123"

# =========================
# BANCO DE DADOS
# =========================
def get_db():
    if "db" not in g:
        # O Render usa DATABASE_URL, se não existir ele tenta local
        db_url = os.environ.get("DATABASE_URL", "postgresql://usuario:senha@localhost/smartzen")
        g.db = psycopg.connect(db_url, row_factory=dict_row)
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL,
                cpf TEXT,
                telefone TEXT,
                nascimento TEXT,
                data_cadastro TEXT,
                is_admin INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contratos (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER NOT NULL REFERENCES usuarios(id),
                cliente TEXT NOT NULL,
                tipo TEXT,
                parcela TEXT,
                valor TEXT,
                saldo_devedor TEXT,
                banco_origem TEXT,
                banco_destino TEXT,
                status TEXT
            )
        """)
        # Cria admin padrão se não existir
        cur.execute("SELECT id FROM usuarios WHERE email = %s", ("admin@smartzen.com",))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO usuarios (nome, email, senha, is_admin, data_cadastro)
                VALUES (%s, %s, %s, %s, %s)
            """, ("Administrador", "admin@smartzen.com", "123456", 1, datetime.now().strftime("%d/%m/%Y")))
    db.commit()

@app.before_request
def before_request():
    init_db()

# =========================
# FUNÇÕES DE APOIO E PDF
# =========================
def limpar_valor_moeda(valor_str):
    if not valor_str: return 0.0
    valor_str = str(valor_str).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".").strip()
    try: return float(valor_str)
    except: return 0.0

def formatar_moeda(valor):
    try: valor = float(valor)
    except: valor = 0.0
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def extrair_texto_pdf(caminho_pdf):
    texto = ""
    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            # Ajuste de tolerância para evitar texto "escadeado"
            txt = pagina.extract_text(layout=True, x_tolerance=2, y_tolerance=2)
            if txt: texto += txt + "\n"
    return texto

def normalizar_texto_inss(texto):
    if not texto: return ""
    texto = texto.upper()
    # "Solda" palavras que o PDF quebra ao meio
    texto = re.sub(r"AGIBAN\s*\n\s*K", "AGIBANK", texto)
    texto = re.sub(r"PORTABILI\s*\n\s*DADE", "PORTABILIDADE", texto)
    texto = re.sub(r"AVERBAÇ\s*\n\s*ÃO", "AVERBAÇÃO", texto)
    return texto

def calcular_prazo_restante(fim_desc):
    try:
        dt_fim = datetime.strptime(fim_desc, "%m/%Y")
        hoje = datetime.now()
        meses = (dt_fim.year - hoje.year) * 12 + (dt_fim.month - hoje.month)
        return max(0, meses)
    except: return 0

def calcular_saldo_devedor_previsto(parcela, prazo, taxa=0.0189):
    if parcela <= 0 or prazo <= 0: return 0.0
    saldo = parcela * (1 - (1 + taxa) ** (-prazo)) / taxa
    return round(saldo, 2)

def extrair_dados_extrato(texto):
    texto_norm = normalizar_texto_inss(texto)
    dados = {
        "nome": "Cliente não identificado", "beneficio": "", "situacao": "",
        "margem_livre": 0.0, "rmc": 0.0, "rcc": 0.0, "contratos": [], "cartoes": [],
        "oportunidades": [], "valor_novo_contrato": 0.0
    }

    # Regex Nome
    m_nome = re.search(r"HISTÓRICO DE\s*EMPRÉSTIMO CONSIGNADO\s*([A-Z ]+)", texto_norm)
    if m_nome: dados["nome"] = m_nome.group(1).strip()

    # Regex Margens (Exemplo simplificado para capturar valores)
    valores = re.findall(r"R\$\s*([\d\.,]+)", texto_norm)
    if len(valores) >= 3:
        dados["margem_livre"] = limpar_valor_moeda(valores[0])
        dados["valor_novo_contrato"] = round(dados["margem_livre"] * 45, 2)
    
    if dados["margem_livre"] > 0:
        dados["oportunidades"].append("Margem disponível para novo empréstimo")

    # Aqui você pode adicionar as suas lógicas de busca de contratos (extrair_contratos_bancarios)
    return dados

# =========================
# ROTAS FLASK
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login():
    email, senha = request.form["email"], request.form["senha"]
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE email = %s AND senha = %s", (email, senha))
        user = cur.fetchone()
    if user:
        session.update({"usuario_id": user["id"], "usuario": user["nome"], "is_admin": bool(user["is_admin"])})
        return redirect("/admin" if user["is_admin"] else "/dashboard")
    return "Login inválido"

@app.route("/admin")
def admin():
    if not session.get("is_admin"): return redirect("/")
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE is_admin = 0")
        clientes = cur.fetchall()
        cur.execute("SELECT * FROM contratos ORDER BY id DESC")
        contratos = cur.fetchall()
    return render_template("admin.html", nome=session["usuario"], total_clientes=len(clientes), contratos=contratos)

@app.route("/dashboard")
def dashboard():
    if "usuario" not in session: return redirect("/")
    return render_template("dashboard.html", nome=session["usuario"])

@app.route("/analisar-extrato", methods=["GET", "POST"])
def analisar_extrato():
    if "usuario" not in session: return redirect("/")
    dados, erro = None, None
    if request.method == "POST":
        file = request.files.get("pdf")
        if file and file.filename.lower().endswith(".pdf"):
            os.makedirs("static/uploads", exist_ok=True)
            path = os.path.join("static/uploads", file.filename)
            file.save(path)
            try:
                raw_text = extrair_texto_pdf(path)
                dados = extrair_dados_extrato(raw_text)
            except Exception as e: erro = f"Erro na leitura: {str(e)}"
    return render_template("analisar_extrato.html", dados_extrato=dados, erro=erro, formatar_moeda=formatar_moeda)

@app.route("/contratos")
def contratos_view():
    if "usuario" not in session: return redirect("/")
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM contratos WHERE cliente_id = %s", (session["usuario_id"],))
        meus_contratos = cur.fetchall()
    return render_template("contratos.html", andamento=meus_contratos, nome=session["usuario"])

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
