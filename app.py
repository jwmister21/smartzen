from flask import Flask, render_template, request, redirect, session, g
from datetime import datetime
import os
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
    app.run(debug=True)
