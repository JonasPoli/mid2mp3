#!/usr/bin/env python3
import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path


HEADER_RE = re.compile(r"^\s*#\s*(.*)$")


def normalize_dashes(text: str) -> str:
    return (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:90] or "sem-titulo"


def parse_header(line: str, last_hino_num: int | None = None):
    """
    Aceita formatos como:
    # Hino 1 – Cristo, Meu Mestre
    # Hino151 – Se, De Cristo...
    # Hino – 100 – Todos Juntos...
    # 323 – Oh! Não Busques Ansioso
    # Coro 1 – ALELUIA!
    """
    m = HEADER_RE.match(line)
    if not m:
        return None

    raw = normalize_dashes(m.group(1).strip())
    raw = re.sub(r"\s+", " ", raw)

    # Caso normal: Hino/Coro + número + título
    m2 = re.match(
        r"^(Hino|Coro)\s*(?:-\s*)?0*(\d{1,3})\s*(?:-\s*)?(.*)$",
        raw,
        flags=re.IGNORECASE,
    )
    if m2:
        tipo = m2.group(1).lower()
        numero = int(m2.group(2))
        titulo = m2.group(3).strip(" -")
        return tipo, numero, titulo

    # Caso sem a palavra Hino: # 323 – Título
    m3 = re.match(r"^0*(\d{1,3})\s*(?:-\s*)?(.*)$", raw)
    if m3:
        numero = int(m3.group(1))
        titulo = m3.group(2).strip(" -")

        # Como os hinos vão até 480, se aparecer só número nessa faixa,
        # tratamos como hino.
        if 1 <= numero <= 480:
            return "hino", numero, titulo

    return None


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    title = title.strip(" -")
    return title


def fix_hyphenated_line_breaks(text: str) -> str:
    """
    Corrige quebras como:
    enga-
    nador
    para:
    enganador
    """
    return re.sub(
        r"([A-Za-zÀ-ÿ])-\n\s*([a-zà-ÿ])",
        r"\1\2",
        text,
    )


def clean_line(line: str) -> str:
    line = line.replace("\u00a0", " ")
    line = line.replace("__", "")
    line = line.replace("_", "")
    line = re.sub(r"\s+", " ", line).strip()

    if not line:
        return ""

    # Corrige "1.Faz" -> "1. Faz"
    line = re.sub(r"^(\d+)\s*[.)-]\s*", r"\1. ", line)

    # Padroniza coro/final
    if re.fullmatch(r"coro[:.]?", line, flags=re.IGNORECASE):
        return "Coro:"
    if re.fullmatch(r"final[:.]?", line, flags=re.IGNORECASE):
        return "Final:"

    # Espaços estranhos antes de pontuação
    line = re.sub(r"\s+([,;:.!?])", r"\1", line)

    # Vírgula duplicada ou ponto duplicado muito comum em OCR
    line = re.sub(r"\.{3,}", "...", line)
    line = line.replace("..", ".")

    # Correção comum do arquivo
    line = line.replace(" ,", ",")

    return line


def normalize_body(body: str, max_blank_lines: int = 1, line_marker: str = "") -> str:
    body = fix_hyphenated_line_breaks(body)

    cleaned = []
    blank_count = 0

    for raw_line in body.splitlines():
        line = clean_line(raw_line)

        if not line:
            blank_count += 1
            if blank_count <= max_blank_lines and cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        blank_count = 0

        if line_marker:
            # Não marca cabeçalhos internos como Coro/Final/estrofe numerada
            if line in {"Coro:", "Final:"} or re.match(r"^\d+\.\s*$", line):
                cleaned.append(line)
            else:
                cleaned.append(f"{line} {line_marker}".rstrip())
        else:
            cleaned.append(line)

    # Remove vazios no começo/fim
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return "\n".join(cleaned)


def parse_items(text: str):
    lines = text.splitlines()
    items = []

    current = None
    body_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        header = parse_header(line)

        if header:
            if current:
                current["body"] = "\n".join(body_lines).strip()
                items.append(current)

            tipo, numero, titulo = header

            # Junta continuação de título em linhas seguintes até a primeira linha vazia.
            # Exemplo:
            # # Hino 29 – Senhor Jesus, Tu És o Meu
            # Rochedo
            j = i + 1
            continuation = []
            while j < len(lines):
                nxt = lines[j].strip()

                if not nxt:
                    break

                if parse_header(lines[j]):
                    break

                continuation.append(nxt)
                j += 1

            if continuation:
                titulo = " ".join([titulo] + continuation)

            current = {
                "tipo": tipo,
                "numero": numero,
                "titulo": clean_title(titulo),
            }

            body_lines = []
            i = j
            continue

        if current:
            body_lines.append(line)

        i += 1

    if current:
        current["body"] = "\n".join(body_lines).strip()
        items.append(current)

    return items


def save_items(items, out_dir: Path, no_title: bool, max_blank_lines: int, line_marker: str):
    out_dir.mkdir(parents=True, exist_ok=True)

    index_rows = []

    for item in items:
        tipo = item["tipo"]
        numero = item["numero"]
        titulo = item["titulo"]
        body = normalize_body(
            item["body"],
            max_blank_lines=max_blank_lines,
            line_marker=line_marker,
        )

        prefix = "hino" if tipo == "hino" else "coro"
        filename = f"{prefix}-{numero:03d}-{slugify(titulo)}.txt"
        path = out_dir / filename

        content_lines = []

        if not no_title:
            label = "Hino" if tipo == "hino" else "Coro"
            content_lines.append(f"{label} {numero:03d} - {titulo}")
            content_lines.append("")

        content_lines.append(body)

        path.write_text("\n".join(content_lines).strip() + "\n", encoding="utf-8")

        index_rows.append({
            "tipo": tipo,
            "numero": numero,
            "titulo": titulo,
            "arquivo": filename,
        })

    return index_rows


def save_index(index_rows, out_dir: Path):
    csv_path = out_dir / "_indice.csv"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["tipo", "numero", "titulo", "arquivo"])
        writer.writeheader()
        writer.writerows(index_rows)

    return csv_path


def print_report(items, out_dir: Path, csv_path: Path):
    hinos = sorted([i["numero"] for i in items if i["tipo"] == "hino"])
    coros = sorted([i["numero"] for i in items if i["tipo"] == "coro"])

    missing_hinos = [n for n in range(1, 481) if n not in hinos]

    print("")
    print("Concluído.")
    print(f"Hinos detectados: {len(hinos)}")
    print(f"Coros detectados: {len(coros)}")
    print(f"Total de TXT gerados: {len(items)}")
    print(f"Pasta de saída: {out_dir}")
    print(f"Índice CSV: {csv_path}")

    if missing_hinos:
        print("")
        print("ATENÇÃO: hinos não encontrados no arquivo fonte:")
        print(", ".join(str(n) for n in missing_hinos))

    print("")
    print("Veja os primeiros arquivos com:")
    print(f'ls "{out_dir}" | head')
    print("")
    print("Abra um hino com:")
    print(f'cat "{out_dir}"/hino-001-*.txt')


def main():
    parser = argparse.ArgumentParser(
        description="Separa um TXT completo do Hinário CCB em um arquivo .txt por hino/coro."
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Caminho do TXT completo, ex: docs/725350581-Letras-Ccb-Completo.txt",
    )
    parser.add_argument(
        "--out-dir",
        default="source/letras_youtube",
        help="Pasta onde os TXT separados serão gerados.",
    )
    parser.add_argument(
        "--no-title",
        action="store_true",
        help="Não escreve o título no começo de cada TXT.",
    )
    parser.add_argument(
        "--line-marker",
        default="",
        help="Opcional: adiciona um marcador no final de cada linha, ex: '|'.",
    )
    parser.add_argument(
        "--max-blank-lines",
        type=int,
        default=1,
        help="Quantidade máxima de linhas em branco consecutivas.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    print("Arquivo fonte:", input_path)
    print("Pasta de saída:", out_dir)

    if not input_path.exists():
        print("")
        print("ERRO: arquivo TXT não encontrado.")
        print("Dica: rode find docs -iname '*Letras*Ccb*txt' para localizar o arquivo.")
        sys.exit(1)

    text = input_path.read_text(encoding="utf-8", errors="replace")
    items = parse_items(text)

    if not items:
        print("")
        print("ERRO: nenhum hino/coro foi detectado.")
        print("Confira se o arquivo possui cabeçalhos como '# Hino 1 – Título'.")
        sys.exit(1)

    index_rows = save_items(
        items=items,
        out_dir=out_dir,
        no_title=args.no_title,
        max_blank_lines=args.max_blank_lines,
        line_marker=args.line_marker,
    )
    csv_path = save_index(index_rows, out_dir)

    print_report(items, out_dir, csv_path)


if __name__ == "__main__":
    main()
