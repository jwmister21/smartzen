import pdfplumber
import re


def normalizar_valor(valor_str):
    if not valor_str:
        return 0.0

    valor_str = valor_str.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(valor_str)
    except:
        return 0.0


def extrair_contratos_extrato(caminho_pdf, debug=False):
    contratos = []

    with pdfplumber.open(caminho_pdf) as pdf:
        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto_pagina = pagina.extract_text() or ""

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

            # Cada bloco começa assim no seu PDF:
            # 110022 3367 121 - BANCO AGIBANK SA ...
            # 010122 273268 626 - BANCO C6 CONSIGNADO SA ...
            #
            # Então aqui a gente pega:
            # [6 dígitos] [4 a 6 dígitos] [código banco] - [resto do bloco]
            #
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

                # banco: tenta pegar do começo do bloco até "Ativo"
                banco_match = re.search(r'^(.*?)\s+Ativo\b', resto, re.IGNORECASE)
                if banco_match:
                    banco_nome = banco_match.group(1).strip()
                else:
                    # fallback: pega até a primeira data MM/AAAA
                    banco_match = re.search(r'^(.*?)\s+\d{2}/\d{4}', resto, re.IGNORECASE)
                    banco_nome = banco_match.group(1).strip() if banco_match else "Banco não identificado"

                banco = f"{codigo_banco} - {banco_nome}"

                # dados principais: início / fim / qtd parcelas / valor parcela
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

                contratos.append({
                    "contrato": contrato,
                    "banco": banco,
                    "inicio": inicio,
                    "fim": fim,
                    "qtd_parcelas": qtd_parcelas,
                    "valor_parcela": valor_parcela,
                    "valor_parcela_texto": valor_parcela_texto,
                    "bloco_original": bloco
                })

    # remove duplicados
    unicos = {}
    for c in contratos:
        unicos[c["contrato"]] = c

    return list(unicos.values())
