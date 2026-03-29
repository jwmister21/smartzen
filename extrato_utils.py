import pdfplumber
import re
from datetime import datetime


def normalizar_valor(valor_str):
    if not valor_str:
        return 0.0

    valor_str = str(valor_str).replace(" ", "").replace(".", "").replace(",", ".").strip()

    try:
        return float(valor_str)
    except Exception:
        return 0.0


def calcular_prazo_restante(fim):
    """
    Recebe fim no formato MM/AAAA
    e calcula quantos meses faltam a partir de hoje.
    """
    try:
        hoje = datetime.now()
        data_fim = datetime.strptime(fim, "%m/%Y")

        meses = (data_fim.year - hoje.year) * 12 + (data_fim.month - hoje.month)

        if meses <= 0:
            return 0

        return meses
    except Exception:
        return 0


def calcular_saldo_devedor_estimado(parcela, prazo_restante, taxa=0.022):
    """
    Estima saldo devedor pela fórmula do valor presente da série:
    PV = PMT * (1 - (1+i)^-n) / i

    taxa padrão: 2,2% ao mês
    """
    try:
        parcela = float(parcela)
        prazo_restante = int(prazo_restante)

        if parcela <= 0 or prazo_restante <= 0:
            return 0.0

        if taxa <= 0:
            return round(parcela * prazo_restante, 2)

        saldo = parcela * (1 - (1 + taxa) ** (-prazo_restante)) / taxa
        return round(saldo, 2)

    except Exception:
        return 0.0


def calcular_com_troco(parcela, saldo_devedor, taxa_nova=0.022):
    """
    Regra que você pediu:
    valor liberado estimado = parcela / 0.022
    troco estimado = valor liberado estimado - saldo devedor
    """
    try:
        parcela = float(parcela)
        saldo_devedor = float(saldo_devedor)

        if parcela <= 0:
            return {
                "valor_liberado_estimado": 0.0,
                "troco_estimado": 0.0
            }

        valor_liberado_estimado = parcela / taxa_nova
        troco_estimado = valor_liberado_estimado - saldo_devedor

        if troco_estimado < 0:
            troco_estimado = 0.0

        return {
            "valor_liberado_estimado": round(valor_liberado_estimado, 2),
            "troco_estimado": round(troco_estimado, 2)
        }

    except Exception:
        return {
            "valor_liberado_estimado": 0.0,
            "troco_estimado": 0.0
        }


def calcular_sem_troco(saldo_devedor, prazo_restante, taxa_reducao=0.015):
    """
    Recalcula a parcela com taxa menor, mantendo o mesmo saldo e prazo restante.
    taxa_reducao padrão: 1,5% ao mês
    """
    try:
        saldo_devedor = float(saldo_devedor)
        prazo_restante = int(prazo_restante)

        if saldo_devedor <= 0 or prazo_restante <= 0:
            return {
                "nova_parcela_reduzida": 0.0,
                "economia_mensal": 0.0
            }

        if taxa_reducao <= 0:
            nova_parcela = saldo_devedor / prazo_restante
        else:
            nova_parcela = saldo_devedor * taxa_reducao / (1 - (1 + taxa_reducao) ** (-prazo_restante))

        return {
            "nova_parcela_reduzida": round(nova_parcela, 2)
        }

    except Exception:
        return {
            "nova_parcela_reduzida": 0.0
        }


def extrair_contratos_extrato(caminho_pdf, debug=False):
    contratos = []

    with pdfplumber.open(caminho_pdf) as pdf:
        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto_pagina = pagina.extract_text() or ""

            # Só processa página com contratos bancários
            if "EMPRÉSTIMOS BANCÁRIOS" not in texto_pagina:
                continue

            palavras = pagina.extract_words(
                use_text_flow=True,
                x_tolerance=2,
                y_tolerance=3
            )

            texto_limpo = " ".join(p["text"] for p in palavras)
            texto_limpo = re.sub(r"\s+", " ", texto_limpo).strip()

            if debug:
                print(f"\n===== PÁGINA {numero_pagina} =====")
                print(texto_limpo[:5000])
                print("===== FIM TEXTO =====\n")

            blocos = re.findall(
                r'(\d{6}\s+\d{4,6}\s+\d{3}\s*-\s+.*?)(?=(?:\d{6}\s+\d{4,6}\s+\d{3}\s*-\s+)|(?:\*Contratos)|$)',
                texto_limpo,
                re.IGNORECASE
            )

            if debug:
                print(f"BLOCOS ENCONTRADOS NA PÁGINA {numero_pagina}: {len(blocos)}")

            for bloco in blocos:
                bloco = re.sub(r"\s+", " ", bloco).strip()

                cabecalho = re.match(
                    r'(\d{6})\s+(\d{4,6})\s+(\d{3})\s*-\s+(.*)',
                    bloco,
                    re.IGNORECASE
                )

                if not cabecalho:
                    continue

                parte1 = cabecalho.group(1)
                parte2 = cabecalho.group(2)
                codigo_banco = cabecalho.group(3)
                resto = cabecalho.group(4)

                contrato = f"{parte1}{parte2}"

                banco_match = re.search(r'^(.*?)\s+Ativo\b', resto, re.IGNORECASE)
                if banco_match:
                    banco_nome = banco_match.group(1).strip()
                else:
                    banco_match = re.search(r'^(.*?)\s+\d{2}/\d{4}', resto, re.IGNORECASE)
                    banco_nome = banco_match.group(1).strip() if banco_match else "Banco não identificado"

                banco = f"{codigo_banco} - {banco_nome}"

                dados_match = re.search(
                    r'(\d{2}/\d{4})\s+(\d{2}/\d{4})\s+(\d{2,3})\s+R\$\s*([\d\.,]+)',
                    bloco,
                    re.IGNORECASE
                )

                if not dados_match:
                    if debug:
                        print("SEM DADOS:", bloco)
                    continue

                inicio = dados_match.group(1)
                fim = dados_match.group(2)
                qtd_parcelas = int(dados_match.group(3))
                valor_parcela_texto = dados_match.group(4)
                valor_parcela = normalizar_valor(valor_parcela_texto)

                prazo_restante = calcular_prazo_restante(fim)

                saldo_devedor_estimado = calcular_saldo_devedor_estimado(
                    valor_parcela,
                    prazo_restante,
                    taxa=0.022
                )

                dados_com_troco = calcular_com_troco(
                    valor_parcela,
                    saldo_devedor_estimado,
                    taxa_nova=0.022
                )

                dados_sem_troco = calcular_sem_troco(
                    saldo_devedor_estimado,
                    prazo_restante,
                    taxa_reducao=0.015
                )

                nova_parcela_reduzida = dados_sem_troco["nova_parcela_reduzida"]
                economia_mensal = round(max(0, valor_parcela - nova_parcela_reduzida), 2)

                contratos.append({
                    "contrato": contrato,
                    "banco": banco,
                    "inicio": inicio,
                    "fim": fim,
                    "qtd_parcelas": qtd_parcelas,
                    "valor_parcela": valor_parcela,
                    "valor_parcela_texto": valor_parcela_texto,
                    "prazo_restante": prazo_restante,
                    "saldo_devedor_estimado": saldo_devedor_estimado,
                    "valor_liberado_estimado": dados_com_troco["valor_liberado_estimado"],
                    "troco_estimado": dados_com_troco["troco_estimado"],
                    "nova_parcela_reduzida": nova_parcela_reduzida,
                    "economia_mensal": economia_mensal,
                    "bloco_original": bloco
                })

    # remove duplicados
    unicos = {}
    for c in contratos:
        unicos[c["contrato"]] = c

    return list(unicos.values())
