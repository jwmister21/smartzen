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


# =========================
# FUNÇÕES AUXILIARES
# =========================
def limpar_valor_moeda(valor_str):
    if valor_str is None:
        return 0.0

    valor_str = str(valor_str).strip()
    if not valor_str:
        return 0.0

    valor_str = valor_str.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".").strip()

    try:
        return float(valor_str)
    except Exception:
        return 0.0


def formatar_moeda(valor):
    try:
        valor = float(valor)
    except Exception:
        valor = 0.0

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def limpar_nome_arquivo(nome):
    return re.sub(r"[^a-zA-Z0-9._-]", "_", nome)


def extrair_texto_pdf(caminho_pdf):
    texto = ""

    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text()
            if texto_pagina:
                texto += texto_pagina + "\n"

    return texto


def mes_ano_para_data(mes_ano):
    try:
        dt = datetime.strptime(mes_ano, "%m/%Y")
        ultimo_dia = monthrange(dt.year, dt.month)[1]
        return dt.replace(day=ultimo_dia)
    except Exception:
        return None


def meses_entre_datas(data_inicial, data_final):
    if not data_inicial or not data_final:
        return 0

    return max(
        0,
        (data_final.year - data_inicial.year) * 12 + (data_final.month - data_inicial.month)
    )


def calcular_prazo_restante(fim_desconto):
    data_fim = mes_ano_para_data(fim_desconto)
    if not data_fim:
        return 0

    hoje = datetime.now()
    meses = meses_entre_datas(hoje, data_fim)

    if meses == 0 and data_fim >= hoje:
        return 1

    return meses


def calcular_saldo_devedor_previsto(parcela, prazo_restante, taxa_mensal=0.0189):
    parcela = float(parcela or 0)
    prazo_restante = int(prazo_restante or 0)

    if parcela <= 0 or prazo_restante <= 0:
        return 0.0

    if taxa_mensal <= 0:
        return round(parcela * prazo_restante, 2)

    saldo = parcela * (1 - (1 + taxa_mensal) ** (-prazo_restante)) / taxa_mensal
    return round(saldo, 2)


def calcular_novo_contrato(margem_livre, coeficiente=45.0):
    return round(float(margem_livre or 0) * coeficiente, 2)


def calcular_portabilidade_sem_troco(parcela_atual, reducao_percentual=0.12):
    parcela_atual = float(parcela_atual or 0)
    nova_parcela = round(parcela_atual * (1 - reducao_percentual), 2)
    economia = round(parcela_atual - nova_parcela, 2)
    return nova_parcela, economia


def calcular_troco_estimado(parcela_atual, multiplicador=8):
    parcela_atual = float(parcela_atual or 0)
    return round(parcela_atual * multiplicador, 2)


def calcular_saque_cartao(limite_cartao, percentual=0.70):
    limite_cartao = float(limite_cartao or 0)
    return round(limite_cartao * percentual, 2)


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


def normalizar_texto_inss(texto):
    if not texto:
        return ""

    texto = texto.upper()

    substituicoes = {
        "AGIBAN\nK": "AGIBANK",
        "CONSIG\nNADO": "CONSIGNADO",
        "AVERBAÇ\nÃO": "AVERBAÇÃO",
        "AVERBAÇ AO": "AVERBAÇÃO",
        "PORTABILI\nDADE": "PORTABILIDADE",
        "REFINAN\nCIAMENT\nO": "REFINANCIAMENTO",
        "REFINAN\nCIAMENTO": "REFINANCIAMENTO",
        "MIGRADO\nDO\nCONTRATO": "MIGRADO DO CONTRATO",
        "A\nTIVO": "ATIVO",
        "CFI S A": "CFI SA",
        "S A": " SA",
        "20/03/2\n3": "20/03/23",
        "13/03/2\n3": "13/03/23",
        "01/03/2\n3": "01/03/23",
        "19/01/2\n3": "19/01/23",
        "04/12/2\n1": "04/12/21",
        "03/12/2\n1": "03/12/21",
        "02/12/2\n1": "02/12/21",
        "R$3.429 ,32": "R$3.429,32",
        "R$1.032 ,34": "R$1.032,34",
        "R$93 ,73": "R$93,73",
        "R$56 ,99": "R$56,99",
        "R$31 ,51": "R$31,51",
        "R$73 ,45": "R$73,45",
    }

    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)

    texto = re.sub(r"(\d{6})\s*\n\s*(\d{4,6})", r"\1\2", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{2,}", "\n", texto)

    return texto


def extrair_margens(texto, dados):
    padrao_modalidades = re.search(
        r"MARGEM CONSIGNÁVEL\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*"
        r"MARGEM UTILIZADA\*?\*?\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*"
        r"MARGEM RESERVADA\s*R\$\s*([\d\.,]+)",
        texto,
        re.IGNORECASE | re.DOTALL
    )

    if padrao_modalidades:
        margem_consignavel_emprestimo = limpar_valor_moeda(padrao_modalidades.group(1))
        margem_utilizada_emprestimo = limpar_valor_moeda(padrao_modalidades.group(4))
        dados["margem_livre"] = round(margem_consignavel_emprestimo - margem_utilizada_emprestimo, 2)

    match_emprestimo = re.search(
        r"EMPRÉSTIMOS\s*RMC\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*R\$\s*([\d\.,]+)\s*MARGEM DISPONÍVEL",
        texto,
        re.IGNORECASE | re.DOTALL
    )
    if match_emprestimo:
        dados["margem_livre"] = limpar_valor_moeda(match_emprestimo.group(2))

    match_rmc = re.search(
        r"RMC.*?R\$\s*([\d\.,]+).*?R\$\s*([\d\.,]+).*?R\$\s*([\d\.,]+)",
        texto,
        re.IGNORECASE | re.DOTALL
    )
    if match_rmc:
        dados["rmc"] = limpar_valor_moeda(match_rmc.group(1))

    match_rcc = re.search(
        r"RCC.*?R\$\s*([\d\.,]+).*?R\$\s*([\d\.,]+).*?R\$\s*([\d\.,]+)",
        texto,
        re.IGNORECASE | re.DOTALL
    )
    if match_rcc:
        dados["rcc"] = limpar_valor_moeda(match_rcc.group(1))

    return dados


def identificar_origem_bloco(bloco_upper):
    if "PORTABILIDADE" in bloco_upper:
        return "Portabilidade"
    if "REFINANCIAMENTO" in bloco_upper:
        return "Refinanciamento"
    if "AVERBAÇÃO NOVA" in bloco_upper or "AVERBACAO NOVA" in bloco_upper:
        return "Averbação nova"
    if "MIGRADO DO CONTRATO" in bloco_upper:
        return "Migrado"
    return "Não identificado"


def extrair_contratos_bancarios(texto_normalizado):
    contratos = []

    if "EMPRÉSTIMOS BANCÁRIOS" not in texto_normalizado:
        return contratos

    parte = texto_normalizado.split("EMPRÉSTIMOS BANCÁRIOS", 1)[1]

    if "CARTÃO DE CRÉDITO" in parte:
        parte = parte.split("CARTÃO DE CRÉDITO", 1)[0]

    parte = re.sub(r"[ \t]+", " ", parte)
    parte = re.sub(r"\n{2,}", "\n", parte)

    blocos = re.split(r"(?=\b\d{10,12}\b)", parte)

    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue

        match_contrato = re.match(r"^(\d{10,12})\b", bloco)
        if not match_contrato:
            continue

        contrato_numero = match_contrato.group(1)

        if "ATIVO" not in bloco.upper():
            continue

        match_banco = re.search(
            r"\b(\d{3}\s*-\s*(?:BANCO\s+)?[A-Z0-9ÇÁÉÍÓÚÃÕ\.\- ]+?(?:SA|S A))\b",
            bloco,
            re.IGNORECASE
        )
        banco = match_banco.group(1).strip() if match_banco else "Banco não identificado"
        banco = re.sub(r"\s+", " ", banco).replace(" S A", " SA").strip()

        datas_mes = re.findall(r"\b\d{2}/\d{4}\b", bloco)
        inicio = datas_mes[0] if len(datas_mes) >= 1 else ""
        fim = datas_mes[1] if len(datas_mes) >= 2 else ""

        match_qtde = re.search(
            r"\b(\d{2}/\d{4})\s+(\d{2}/\d{4})\s+(\d{1,3})\s+R\$",
            bloco
        )
        qtde_parcelas = int(match_qtde.group(3)) if match_qtde else 0

        valores = re.findall(r"R\$\s*([\d\.,]+)", bloco)

        if len(valores) == 0:
            continue

        parcela = limpar_valor_moeda(valores[0]) if len(valores) >= 1 else 0.0
        valor_emprestado = limpar_valor_moeda(valores[1]) if len(valores) >= 2 else 0.0
        valor_liberado = limpar_valor_moeda(valores[2]) if len(valores) >= 3 else 0.0
        iof = limpar_valor_moeda(valores[3]) if len(valores) >= 4 else 0.0
        valor_pago = limpar_valor_moeda(valores[-1]) if len(valores) >= 5 else 0.0

        if parcela <= 0:
            continue

        origem = identificar_origem_bloco(bloco.upper())
        prazo_restante = calcular_prazo_restante(fim)
        saldo_previo = calcular_saldo_devedor_previsto(parcela, prazo_restante)
        nova_parcela, economia = calcular_portabilidade_sem_troco(parcela)
        troco = calcular_troco_estimado(parcela)

        contratos.append({
            "tipo_registro": "emprestimo",
            "banco": banco,
            "contrato": contrato_numero,
            "status": "Ativo",
            "inicio": inicio,
            "fim": fim,
            "qtde_parcelas": qtde_parcelas,
            "prazo_restante": prazo_restante,
            "parcela": parcela,
            "valor_emprestado": valor_emprestado,
            "valor_liberado": valor_liberado,
            "iof": iof,
            "valor_pago": valor_pago,
            "origem": origem,
            "saldo_devedor_previsto": saldo_previo,
            "nova_parcela": nova_parcela,
            "economia": economia,
            "troco_estimado": troco,
            "oportunidade": classificar_oportunidade(parcela, origem)
        })

    unicos = []
    vistos = set()

    for c in contratos:
        chave = (c["contrato"], c["banco"], c["parcela"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(c)

    return unicos


def extrair_blocos_cartao(texto_normalizado, marcador_inicio, marcador_fim=None):
    blocos = []

    if marcador_inicio not in texto_normalizado:
        return blocos

    partes = texto_normalizado.split(marcador_inicio)

    for i in range(1, len(partes)):
        trecho = partes[i]
        bloco = trecho

        if marcador_fim and marcador_fim in bloco:
            bloco = bloco.split(marcador_fim, 1)[0]

        for parada in [
            "EMPRÉSTIMOS BANCÁRIOS",
            "RESUMO",
            "OBSERVAÇÕES",
            "OUTROS DESCONTOS"
        ]:
            if parada in bloco:
                bloco = bloco.split(parada, 1)[0]

        bloco = bloco.strip()
        if bloco:
            blocos.append(bloco)

    return blocos


def montar_cartao_do_bloco(bloco, tipo_cartao):
    contrato = re.search(r"\b(\d{8,12})\b", bloco)
    banco = re.search(r"\b(\d{3}\s*-\s*(?:BANCO\s+)?[A-Z0-9ÇÁÉÍÓÚÃÕ\.\- ]+?(?: SA| S A))\b", bloco)
    valores = re.findall(r"R\$\s*([\d\.,]+)", bloco)

    if not contrato:
        return None

    banco_formatado = "Banco não identificado"
    if banco:
        banco_formatado = re.sub(r"\s+", " ", banco.group(1)).replace(" S A", " SA").strip()

    limite_cartao = limpar_valor_moeda(valores[0]) if len(valores) >= 1 else 0.0
    reservado_atualizado = limpar_valor_moeda(valores[1]) if len(valores) >= 2 else 0.0

    valor_maximo_utilizavel = limite_cartao
    saque_maximo_estimado = calcular_saque_cartao(limite_cartao)

    return {
        "tipo_registro": "cartao",
        "tipo_cartao": tipo_cartao,
        "contrato": contrato.group(1),
        "banco": banco_formatado,
        "limite_cartao": limite_cartao,
        "reservado_atualizado": reservado_atualizado,
        "valor_maximo_utilizavel": valor_maximo_utilizavel,
        "saque_maximo_estimado": saque_maximo_estimado
    }


def extrair_cartoes(texto_normalizado):
    cartoes_lista = []

    blocos_rmc = extrair_blocos_cartao(
        texto_normalizado,
        "CARTÃO DE CRÉDITO - RMC",
        "CARTÃO DE CRÉDITO - RCC"
    )

    for bloco in blocos_rmc:
        cartao = montar_cartao_do_bloco(bloco, "RMC")
        if cartao:
            cartoes_lista.append(cartao)

    blocos_rcc = extrair_blocos_cartao(
        texto_normalizado,
        "CARTÃO DE CRÉDITO - RCC",
        None
    )

    for bloco in blocos_rcc:
        cartao = montar_cartao_do_bloco(bloco, "RCC")
        if cartao:
            cartoes_lista.append(cartao)

    # remove duplicados
    unicos = []
    vistos = set()

    for c in cartoes_lista:
        chave = (c["tipo_cartao"], c["contrato"], c["banco"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(c)

    return unicos


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
        "quantidade_cartoes": 0,
        "contratos": [],
        "cartoes": [],
        "oportunidades": [],
        "valor_novo_contrato": 0.0
    }

    match_nome = re.search(
        r"HISTÓRICO DE\s*EMPRÉSTIMO CONSIGNADO\s*([A-ZÁÉÍÓÚÃÕÇ ]+)",
        texto,
        re.IGNORECASE
    )
    if match_nome:
        dados["nome"] = match_nome.group(1).strip()

    match_beneficio = re.search(r"N[ºO] BENEF[ÍI]CIO:\s*([\d\.\-]+)", texto, re.IGNORECASE)
    if match_beneficio:
        dados["beneficio"] = match_beneficio.group(1).strip()

    match_situacao = re.search(r"SITUAÇÃO:\s*([A-Z]+)", texto, re.IGNORECASE)
    if match_situacao:
        dados["situacao"] = match_situacao.group(1).strip()

    match_tipo = re.search(r"BENEF[ÍI]CIO\s*([A-ZÁÉÍÓÚÃÕÇ ]+)\s*N[ºO] BENEF[ÍI]CIO", texto, re.IGNORECASE)
    if match_tipo:
        dados["tipo_beneficio"] = match_tipo.group(1).strip()

    if re.search(r"ELEGÍVEL PARA EMPRÉSTIMOS|ELEGIVEL PARA EMPRESTIMOS", texto, re.IGNORECASE):
        dados["elegivel"] = True

    dados = extrair_margens(texto, dados)

    texto_normalizado = normalizar_texto_inss(texto)

    contratos = extrair_contratos_bancarios(texto_normalizado)
    cartoes = extrair_cartoes(texto_normalizado)

    dados["contratos"] = contratos
    dados["cartoes"] = cartoes
    dados["quantidade_contratos"] = len(contratos)
    dados["quantidade_cartoes"] = len(cartoes)
    dados["valor_novo_contrato"] = calcular_novo_contrato(dados["margem_livre"])

    if dados["margem_livre"] > 0:
        dados["oportunidades"].append("Você possui margem livre para novo contrato")

    if dados["quantidade_contratos"] > 0:
        dados["oportunidades"].append("Encontramos contratos para análise de portabilidade")

    if any("refin" in (c["origem"] or "").lower() or c["parcela"] >= 200 for c in contratos):
        dados["oportunidades"].append("Alguns contratos podem ter chance de refinanciamento")

    if dados["quantidade_cartoes"] > 0:
        dados["oportunidades"].append("Cliente possui cartão consignado ativo")

    

    return dados


# =========================
# ROTAS
# =========================
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
            return render_template("analisar_extrato.html", dados_extrato=dados_extrato, erro=erro, formatar_moeda=formatar_moeda)

        if not arquivo.filename.lower().endswith(".pdf"):
            erro = "Envie apenas arquivo PDF."
            return render_template("analisar_extrato.html", dados_extrato=dados_extrato, erro=erro, formatar_moeda=formatar_moeda)

        pasta_upload = os.path.join("static", "uploads")
        os.makedirs(pasta_upload, exist_ok=True)

        nome_seguro = limpar_nome_arquivo(arquivo.filename)
        caminho_arquivo = os.path.join(pasta_upload, nome_seguro)
        arquivo.save(caminho_arquivo)

        try:
            texto = extrair_texto_pdf(caminho_arquivo)
            dados_extrato = extrair_dados_extrato(texto)
        except Exception as e:
            erro = f"Erro ao analisar o PDF: {str(e)}"

    return render_template(
        "analisar_extrato.html",
        dados_extrato=dados_extrato,
        erro=erro,
        formatar_moeda=formatar_moeda
    )


@app.route("/admin")
def admin():
    if "usuario" not in session:
        return redirect("/")

    if not session.get("is_admin"):
        return "Acesso negado"

    db = get_db()

    with db.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE is_admin = 0 ORDER BY id DESC")
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

    tipo = request.form.get("tipo", "")
    parcela = request.form.get("parcela", "")
    valor = request.form.get("valor", "")
    saldo_devedor = request.form.get("saldo_devedor", "")
    banco_origem = request.form.get("banco_origem", "")
    banco_destino = request.form.get("banco_destino", "A definir")
    status = request.form.get("status", "Em digitação")

    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO contratos (
                cliente_id, cliente, tipo, parcela, valor,
                saldo_devedor, banco_origem, banco_destino, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            session["usuario_id"],
            session["usuario"],
            tipo,
            parcela,
            valor,
            saldo_devedor,
            banco_origem,
            banco_destino,
            status
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


@app.route("/api/calcular-saldo-previo", methods=["POST"])
def api_calcular_saldo_previo():
    if "usuario" not in session:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401

    parcela = limpar_valor_moeda(request.form.get("parcela", "0"))
    prazo_restante = int(request.form.get("prazo_restante", "0") or 0)

    saldo = calcular_saldo_devedor_previsto(parcela, prazo_restante)

    return jsonify({
        "ok": True,
        "saldo_devedor_previsto": saldo,
        "saldo_devedor_previsto_formatado": formatar_moeda(saldo)
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
