#!/usr/bin/env python3
import argparse
import re
import sys
import unicodedata
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERRO: PyMuPDF não está instalado.")
    print("Instale com: pip install pymupdf")
    sys.exit(1)


def limpar_linha(linha: str) -> str:
    linha = linha.replace("\u00a0", " ")
    linha = re.sub(r"\s+", " ", linha)
    return linha.strip()


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = texto.strip("-")
    return texto[:90] or "sem-titulo"


def extrair_linhas_pdf(pdf_path: Path) -> list[str]:
    doc = fitz.open(pdf_path)
    linhas = []

    for pagina_num, page in enumerate(doc, start=1):
        texto = page.get_text("text", sort=True)
        for linha in texto.splitlines():
            linha = limpar_linha(linha)
            if linha:
                linhas.append(linha)

    return linhas


def detectar_inicio_hino(linha: str):
    """
    Tenta detectar início de hino em PDFs de letras.

    Exemplos aceitos:
    Hino 001 - Título
    001 - Título
    1. Título
    1 Título
    HINO 1 Título
    """
    linha = limpar_linha(linha)

    padroes = [
        r"^HINO\s+0*(\d{1,3})\s*[-–—.:]\s*(.+)$",
        r"^HINO\s+0*(\d{1,3})\s+(.+)$",
        r"^0*(\d{1,3})\s*[-–—.:]\s*(.+)$",
        r"^0*(\d{1,3})\s+(.+)$",
    ]

    for padrao in padroes:
        m = re.match(padrao, linha, re.IGNORECASE)
        if not m:
            continue

        numero = int(m.group(1))
        titulo = limpar_linha(m.group(2))

        if not (1 <= numero <= 480):
            continue

        # Evita confundir número de página/estrofe com título
        if len(titulo) < 3:
            continue

        # Evita pegar linhas que claramente parecem letra/estrofe numerada
        if titulo.lower().startswith(("em ", "de ", "do ", "da ", "dos ", "das ", "e ")):
            continue

        return numero, titulo

    return None


def separar_hinos(linhas: list[str]) -> list[dict]:
    hinos = []
    atual = None
    numeros_vistos = set()

    for linha in linhas:
        inicio = detectar_inicio_hino(linha)

        if inicio:
            numero, titulo = inicio

            # Se o mesmo número aparecer de novo, provavelmente é número de página ou repetição;
            # só aceita se ainda não foi iniciado.
            if numero not in numeros_vistos:
                if atual:
                    hinos.append(atual)

                atual = {
                    "numero": numero,
                    "titulo": titulo,
                    "linhas": [],
                }
                numeros_vistos.add(numero)
                continue

        if atual:
            # Remove rodapés/cabeçalhos comuns
            baixa = linha.lower()
            ignorar = [
                "congregação cristã no brasil",
                "congregacao crista no brasil",
                "hinário",
                "hinario",
                "hinos de louvores",
                "súplicas a deus",
                "suplicas a deus",
            ]

            if baixa in ignorar:
                continue

            atual["linhas"].append(linha)

    if atual:
        hinos.append(atual)

    return hinos


def salvar_txts(hinos: list[dict], pasta_saida: Path) -> int:
    pasta_saida.mkdir(parents=True, exist_ok=True)

    total = 0

    for hino in hinos:
        numero = hino["numero"]
        titulo = hino["titulo"]

        nome = f"hino-{numero:03d}-{slugify(titulo)}.txt"
        caminho = pasta_saida / nome

        conteudo = []
        conteudo.append(f"Hino {numero:03d} - {titulo}")
        conteudo.append("")

        linhas_validas = [l for l in hino["linhas"] if l.strip()]
        conteudo.extend(linhas_validas)

        caminho.write_text("\n".join(conteudo).strip() + "\n", encoding="utf-8")
        total += 1

    return total


def salvar_debug(linhas: list[str], pasta_saida: Path):
    pasta_saida.mkdir(parents=True, exist_ok=True)

    debug_path = pasta_saida / "_debug_texto_extraido_do_pdf.txt"
    debug_path.write_text("\n".join(linhas), encoding="utf-8")

    amostra_path = pasta_saida / "_debug_primeiras_200_linhas.txt"
    amostra_path.write_text("\n".join(linhas[:200]), encoding="utf-8")

    return debug_path, amostra_path


def main():
    parser = argparse.ArgumentParser(
        description="Extrai letras de um PDF de letras da CCB e gera um TXT por hino."
    )
    parser.add_argument("pdf", help="Caminho do PDF de letras.")
    parser.add_argument("saida", help="Pasta onde os TXT serão gerados.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Gera arquivos de debug com o texto bruto extraído do PDF."
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    pasta_saida = Path(args.saida).expanduser().resolve()

    print("PDF informado:", pdf_path)
    print("Pasta de saída:", pasta_saida)

    if not pdf_path.exists():
        print("")
        print("ERRO: PDF não encontrado.")
        print("Confira o caminho do arquivo PDF.")
        sys.exit(1)

    linhas = extrair_linhas_pdf(pdf_path)
    print("Total de linhas extraídas do PDF:", len(linhas))

    if not linhas:
        print("")
        print("ERRO: Não foi possível extrair texto do PDF.")
        print("Esse PDF pode ser imagem/escaneado. Nesse caso precisa de OCR.")
        pasta_saida.mkdir(parents=True, exist_ok=True)
        sys.exit(1)

    hinos = separar_hinos(linhas)
    print("Hinos detectados:", len(hinos))

    if args.debug or len(hinos) == 0:
        debug_path, amostra_path = salvar_debug(linhas, pasta_saida)
        print("Arquivo de debug gerado:", debug_path)
        print("Amostra de debug gerada:", amostra_path)

    if not hinos:
        print("")
        print("Nenhum hino foi detectado automaticamente.")
        print("Abra o arquivo _debug_primeiras_200_linhas.txt e veja como o PDF escreve os títulos.")
        print("Depois ajuste o padrão de detecção conforme o formato real do seu PDF.")
        sys.exit(1)

    total = salvar_txts(hinos, pasta_saida)

    print("")
    print("Concluído.")
    print("Arquivos TXT gerados:", total)
    print("Veja com:")
    print(f"ls \"{pasta_saida}\" | head")
    print("")
    print("Exemplo para abrir um arquivo:")
    print(f"cat \"{pasta_saida}\"/hino-001-*.txt")


if __name__ == "__main__":
    main()
