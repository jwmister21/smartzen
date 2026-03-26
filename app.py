from flask import Flask, render_template, request, redirect, session, g
from datetime import datetime
import os
import re
import pdfplumber
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = "123"


def get_db():
    if "db" not in g:
        g.db = psycopg.connect(
            os.environ["DATABASE_URL"],
            row_factory=dict_row
        )
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

        cur.execute(
            "SELECT id FROM usuarios WHERE email = %s",
            ("admin@smartzen.com",)
        )
        admin = cur.fetchone()

        if not admin:
            cur.execute("""
                INSERT INTO usuarios (
                    nome, email, senha, cpf, telefone, nascimento, data_cadastro, is_admin
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                "Administrador",
                "admin@smartzen.com",
                "123456",
                "",
                "",
                "",
                datetime.now().strftime("%d/%m/%Y"),
                1
            ))

    db.commit()


@app.before_request
def before_request():
    init_db()


def limpar_valor_moeda(valor_str):
    if not valor_str:
        return 0.0

    valor_str = valor_str.replace("R$", "").replace(".", "").replace(",", ".").strip()

    try:
        return float(valor_str)
    except:
        return 0.0


def extrair_texto_pdf(caminho_pdf):
    texto = ""

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text()
            if texto_pagina:
                texto += texto_pagina + "\n"

    return texto


def classificar_oportunidade(parcela, origem):
    origem = (origem or "").lower()

    if "refin" in origem:
        return "Boa chance de refin"
    if "portabilidade" in origem:
        return "Boa chance de portar"
    if parcela >= 200:
        return "Boa chance de análise"
    if parcela > 0:
        return "Precisa análise"

    return "Sem vantagem aparente"


def calcular_novo_contrato(margem_livre, coeficiente=45.0):
    return round(margem_livre * coeficiente, 2)


def calcular_portabilidade_sem_troco(parcela_atual, reducao_percentual=0.12):
    nova_parcela = round(parcela_atual * (1 - reducao_percentual), 2)
    economia = round(parcela_atual - nova_parcela, 2)
    return nova_parcela, economia


def calcular_troco_estimado(parcela_atual, multiplicador=8):
    troco = round(parcela_atual * multiplicador, 2)
    return troco


def extrair_dados_extrato(texto):
    dados = {
        "nome": "",
        "beneficio": "",
        "situacao": "",
        "tipo_beneficio": "",
        "elegivel": False,
        "margem_livre": 0.0,
        "rmc": 0.0,
        "rcc": 0.0,
        "quantidade_contratos": 0,
        "contratos": [],
        "oportunidades": []
    }

    match_nome = re.search(r"Nome\s*:\s*(.+)", texto, re.IGNORECASE)
    if match_nome:
        dados["nome"] = match_nome.group(1).strip()

    match_beneficio = re.search(r"Benef[ií]cio\s*:\s*(\d+)", texto, re.IGNORECASE)
    if match_beneficio:
        dados["beneficio"] = match_beneficio.group(1).strip()

    match_situacao = re.search(r"Situa[cç][aã]o\s*:\s*(.+)", texto, re.IGNORECASE)
    if match_situacao:
        dados["situacao"] = match_situacao.group(1).strip()

    match_tipo = re.search(r"Esp[eé]cie\s*:\s*(.+)", texto, re.IGNORECASE)
    if match_tipo:
        dados["tipo_beneficio"] = match_tipo.group(1).strip()

    if re.search(r"eleg[ií]vel.*empr[eé]stimo", texto, re.IGNORECASE):
        dados["elegivel"] = True

    match_margem = re.search(
        r"Margem\s+dispon[ií]vel\s+para\s+empr[eé]stimo\s*[:\-]?\s*R\$\s*([\d\.\,]+)",
        texto,
        re.IGNORECASE
    )
    if match_margem:
        dados["margem_livre"] = limpar_valor_moeda(match_margem.group(1))

    match_rmc = re.search(
        r"RMC\s*[:\-]?\s*R\$\s*([\d\.\,]+)",
        texto,
        re.IGNORECASE
    )
    if match_rmc:
        dados["rmc"] = limpar_valor_moeda(match_rmc.group(1))

    match_rcc = re.search(
        r"RCC\s*[:\-]?\s*R\$\s*([\d\.\,]+)",
        texto,
        re.IGNORECASE
    )
    if match_rcc:
        dados["rcc"] = limpar_valor_moeda(match_rcc.group(1))

    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]

    for linha in linhas:
        parcelas = re.findall(r"R\$\s*([\d\.\,]+)", linha)

        bancos_possiveis = [
            "AGIBANK", "C6", "ZEMA", "SAFRA", "FACTA", "DAYCOVAL",
            "BRADESCO", "PAN", "BMG", "MERCANTIL", "ITAÚ", "BANRISUL",
            "CREFISA", "PARANA BANCO", "BANCO DO BRASIL", "CAIXA"
        ]

        banco_encontrado = ""
        for banco in bancos_possiveis:
            if banco.lower() in linha.lower():
                banco_encontrado = banco
                break

        match_contrato = re.search(r"\b\d{6,20}\b", linha)
        datas = re.findall(r"\d{2}/\d{2}/\d{4}", linha)

        if banco_encontrado or parcelas or match_contrato:
            parcela_valor = 0.0
            if parcelas:
                parcela_valor = limpar_valor_moeda(parcelas[-1])

            inicio = datas[0] if len(datas) > 0 else ""
            fim = datas[1] if len(datas) > 1 else ""

            linha_lower = linha.lower()
            if "portabilidade" in linha_lower:
                origem = "Portabilidade"
            elif "refin" in linha_lower:
                origem = "Refinanciamento"
            elif "averba" in linha_lower:
                origem = "Averbação nova"
            elif "migrado" in linha_lower:
                origem = "Migrado"
            else:
                origem = "Não identificado"

            contrato = {
                "banco": banco_encontrado if banco_encontrado else "Banco não identificado",
                "contrato": match_contrato.group() if match_contrato else f"Contrato {len(dados['contratos']) + 1}",
                "parcela": parcela_valor,
                "inicio": inicio,
                "fim": fim,
                "origem": origem,
                "oportunidade": classificar_oportunidade(parcela_valor, origem)
            }

            if contrato["parcela"] > 0 or banco_encontrado:
                nova_parcela, economia = calcular_portabilidade_sem_troco(contrato["parcela"])
                troco = calcular_troco_estimado(contrato["parcela"])

                contrato["nova_parcela"] = nova_parcela
                contrato["economia"] = economia
                contrato["troco_estimado"] = troco

                dados["contratos"].append(contrato)

    contratos_unicos = []
    vistos = set()

    for c in dados["contratos"]:
        chave = (c["banco"], c["contrato"], c["parcela"])
        if chave not in vistos:
            vistos.add(chave)
            contratos_unicos.append(c)

    dados["contratos"] = contratos_unicos
    dados["quantidade_contratos"] = len(contratos_unicos)

    if dados["margem_livre"] > 0:
        dados["oportunidades"].append("Você possui margem livre para novo contrato")

    if dados["quantidade_contratos"] > 0:
        dados["oportunidades"].append("Encontramos contratos para análise de portabilidade")

    if any("refin" in c["origem"].lower() or c["parcela"] >= 200 for c in dados["contratos"]):
        dados["oportunidades"].append("Alguns contratos podem ter chance de refinanciamento")

    if dados["rmc"] > 0 or dados["rcc"] > 0:
        dados["oportunidades"].append("Cartão indisponível no momento")

    return dados


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["POST"])
def login():
    email = request.form["email"]
    senha = request.form["senha"]

    db = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM usuarios WHERE email = %s AND senha = %s",
            (email, senha)
        )
        usuario = cur.fetchone()

    if usuario:
        session["usuario_id"] = usuario["id"]
        session["usuario"] = usuario["nome"]
        session["cpf"] = usuario["cpf"] or ""
        session["telefone"] = usuario["telefone"] or ""
        session["email"] = usuario["email"] or ""
        session["nascimento"] = usuario["nascimento"] or ""
        session["data_cadastro"] = usuario["data_cadastro"] or ""
        session["is_admin"] = bool(usuario["is_admin"])
        return redirect("/admin" if usuario["is_admin"] else "/dashboard")

    return "Login inválido"


@app.route("/cadastro")
def cadastro():
    return render_template("cadastro.html")


@app.route("/cadastrar", methods=["POST"])
def cadastrar():
    nome = request.form["nome"]
    email = request.form["email"]
    senha = request.form["senha"]
    cpf = request.form.get("cpf", "")
    telefone = request.form.get("telefone", "")
    nascimento = request.form.get("nascimento", "")
    data_cadastro = datetime.now().strftime("%d/%m/%Y")

    db = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM usuarios WHERE email = %s",
            (email,)
        )
        existente = cur.fetchone()

        if existente:
            return "Já existe um cadastro com esse e-mail."

        cur.execute("""
            INSERT INTO usuarios (
                nome, email, senha, cpf, telefone, nascimento, data_cadastro, is_admin
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            nome, email, senha, cpf, telefone, nascimento, data_cadastro, 0
        ))

    db.commit()
    return redirect("/")


@app.route("/dashboard")
def dashboard():
    if "usuario" not in session:
        return redirect("/")

    return render_template("dashboard.html", nome=session["usuario"])


@app.route("/analisar-extrato", methods=["GET", "POST"])
def analisar_extrato():
    if "usuario" not in session:
        return redirect("/")

    dados_extrato = None
    erro = None

    if request.method == "POST":
        arquivo = request.files.get("pdf")

        if not arquivo or arquivo.filename == "":
            erro = "Selecione um arquivo PDF."
            return render_template("analisar_extrato.html", dados_extrato=dados_extrato, erro=erro)

        if not arquivo.filename.lower().endswith(".pdf"):
            erro = "Envie apenas arquivo PDF."
            return render_template("analisar_extrato.html", dados_extrato=dados_extrato, erro=erro)

        pasta_upload = os.path.join("static", "uploads")
        os.makedirs(pasta_upload, exist_ok=True)

        caminho_arquivo = os.path.join(pasta_upload, arquivo.filename)
        arquivo.save(caminho_arquivo)

        try:
            texto = extrair_texto_pdf(caminho_arquivo)
            dados_extrato = extrair_dados_extrato(texto)
            dados_extrato["valor_novo_contrato"] = calcular_novo_contrato(
                dados_extrato["margem_livre"]
            )
        except Exception as e:
            erro = f"Erro ao analisar o PDF: {str(e)}"

    return render_template("analisar_extrato.html", dados_extrato=dados_extrato, erro=erro)


@app.route("/admin")
def admin():
    if "usuario" not in session:
        return redirect("/")

    if not session.get("is_admin"):
        return "Acesso negado"

    db = get_db()

    with db.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE is_admin = 0")
        clientes_reais = cur.fetchall()

        total_clientes = len(clientes_reais)

        cur.execute("SELECT COUNT(*) AS total FROM contratos")
        total_contratos = cur.fetchone()["total"]

        cur.execute(
            "SELECT COUNT(*) AS total FROM contratos WHERE status = %s",
            ("Em digitação",)
        )
        em_digitacao = cur.fetchone()["total"]

        cur.execute(
            "SELECT COUNT(*) AS total FROM contratos WHERE status = %s",
            ("Finalizado",)
        )
        finalizados = cur.fetchone()["total"]

        cur.execute("""
            SELECT
                c.*,
                u.cpf,
                u.telefone,
                u.email,
                u.nascimento,
                u.data_cadastro
            FROM contratos c
            LEFT JOIN usuarios u ON u.id = c.cliente_id
            ORDER BY c.id DESC
        """)
        contratos = cur.fetchall()

    return render_template(
        "admin.html",
        nome=session["usuario"],
        total_clientes=total_clientes,
        total_contratos=total_contratos,
        em_digitacao=em_digitacao,
        finalizados=finalizados,
        contratos=contratos
    )


@app.route("/conta")
def conta():
    if "usuario" not in session:
        return redirect("/")

    return render_template(
        "conta.html",
        nome=session.get("usuario", "Cliente"),
        cpf=session.get("cpf", ""),
        telefone=session.get("telefone", ""),
        email=session.get("email", ""),
        nascimento=session.get("nascimento", ""),
        data_cadastro=session.get("data_cadastro", "")
    )


@app.route("/contratar", methods=["POST"])
def contratar():
    if "usuario" not in session:
        return redirect("/")

    db = get_db()

    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO contratos (
                cliente_id, cliente, tipo, parcela, valor,
                saldo_devedor, banco_origem, banco_destino, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            session["usuario_id"],
            session["usuario"],
            request.form.get("tipo", ""),
            request.form.get("parcela", ""),
            request.form.get("valor", ""),
            request.form.get("saldo_devedor", ""),
            request.form.get("banco_origem", ""),
            request.form.get("banco_destino", "A definir"),
            request.form.get("status", "Em digitação")
        ))

    db.commit()
    return redirect("/contratos")


@app.route("/atualizar-status", methods=["POST"])
def atualizar_status():
    if "usuario" not in session:
        return {"ok": False, "erro": "Não autenticado"}, 401

    if not session.get("is_admin"):
        return {"ok": False, "erro": "Acesso negado"}, 403

    contrato_id = request.form.get("contrato_id")
    novo_status = request.form.get("status")

    db = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM contratos WHERE id = %s",
            (contrato_id,)
        )
        contrato = cur.fetchone()

        if not contrato:
            return {"ok": False, "erro": "Contrato não encontrado"}, 404

        cur.execute(
            "UPDATE contratos SET status = %s WHERE id = %s",
            (novo_status, contrato_id)
        )

    db.commit()
    return {"ok": True, "novo_status": novo_status}


@app.route("/contratos")
def contratos_view():
    if "usuario" not in session:
        return redirect("/")

    db = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM contratos WHERE cliente_id = %s ORDER BY id DESC",
            (session["usuario_id"],)
        )
        meus_contratos = cur.fetchall()

    andamento = [c for c in meus_contratos if c["status"] != "Finalizado"]
    finalizados = [c for c in meus_contratos if c["status"] == "Finalizado"]

    tem_portabilidade_andamento = any(
        "Portabilidade" in (c["tipo"] or "") for c in andamento
    )

    return render_template(
        "contratos.html",
        andamento=andamento,
        finalizados=finalizados,
        nome=session["usuario"],
        tem_portabilidade_andamento=tem_portabilidade_andamento
    )


@app.route("/novo-contrato/<cliente>")
def novo_contrato(cliente):
    if "usuario" not in session:
        return redirect("/")

    if not session.get("is_admin"):
        return "Acesso negado"

    return render_template("novo_contrato.html", cliente=cliente)


@app.route("/salvar-novo-contrato", methods=["POST"])
def salvar_novo_contrato():
    if "usuario" not in session:
        return redirect("/")

    if not session.get("is_admin"):
        return "Acesso negado"

    cliente_nome = request.form.get("cliente", "")
    db = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM usuarios WHERE nome = %s",
            (cliente_nome,)
        )
        usuario = cur.fetchone()

        if not usuario:
            return "Cliente não encontrado."

        cur.execute("""
            INSERT INTO contratos (
                cliente_id, cliente, tipo, parcela, valor,
                saldo_devedor, banco_origem, banco_destino, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            usuario["id"],
            cliente_nome,
            request.form.get("tipo", ""),
            request.form.get("parcela", ""),
            request.form.get("valor", ""),
            request.form.get("saldo_devedor", ""),
            request.form.get("banco_origem", ""),
            request.form.get("banco_destino", "A definir"),
            request.form.get("status", "Em digitação")
        ))

    db.commit()
    return redirect("/admin")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, port=5001)
