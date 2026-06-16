#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extrai letras de arquivos MuseScore (.mscx/.mscz) e gera arquivos TXT separados.

Pensado para projetos com arquivos como:
  docs/ccb-hinario-5-do/do/musescore/xml/hino-90.mscx

Saídas principais:
  - Um .txt por hino/coro em --txt-dir
  - Quebra inteligente: só corta em vírgula/ponto quando a linha chega perto do tamanho médio
  - Opcionalmente CSV/JSON com os dados extraídos
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, asdict
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET


@dataclass
class ItemLetra:
    tipo: str
    numero: int
    titulo: str
    compositor: str
    estrofes: List[str]
    coro: str
    arquivo: str
    avisos: List[str]

    @property
    def letra_completa(self) -> str:
        partes: List[str] = []
        for i, estrofe in enumerate(self.estrofes, start=1):
            partes.append(f"{i}.\n{estrofe}".strip())
        if self.coro:
            partes.append(f"Coro:\n{self.coro}".strip())
        return "\n\n".join(partes).strip()


def texto_xml(el: Optional[ET.Element]) -> str:
    """Extrai texto de tags MuseScore, tratando <sym>lyricsElision</sym> como espaço."""
    if el is None:
        return ""

    partes: List[str] = []

    def walk(node: ET.Element):
        if node.text:
            partes.append(node.text)
        for child in list(node):
            if child.tag == "sym":
                sym_name = (child.text or "").strip()
                if sym_name.startswith("lyricsElision"):
                    partes.append(" ")
                # outros símbolos musicais são ignorados
            else:
                walk(child)
            if child.tail:
                partes.append(child.tail)

    walk(el)
    txt = unescape("".join(partes))
    txt = txt.replace("\u00a0", " ")
    txt = txt.replace("\u200b", "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def abrir_xml_musescore(path: Path) -> ET.Element:
    if path.suffix.lower() == ".mscz":
        with zipfile.ZipFile(path, "r") as z:
            candidatos = [n for n in z.namelist() if n.lower().endswith(".mscx")]
            if not candidatos:
                raise ValueError(f"MSCZ sem .mscx interno: {path}")
            with z.open(candidatos[0]) as f:
                return ET.parse(f).getroot()
    return ET.parse(path).getroot()


def limpar_nome_arquivo(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    texto = re.sub(r"-+", "-", texto).strip("-")
    return texto[:80] or "sem-titulo"


def limpar_titulo(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()
    return texto


def detectar_tipo_numero(path: Path, root: ET.Element) -> Tuple[str, int]:
    nome = path.stem.lower()
    m = re.search(r"(hino|coro)[-_ ]?(\d+)", nome)
    if m:
        return m.group(1), int(m.group(2))

    # fallback: tenta achar texto numérico em Instrument Name (Part)
    for text_el in root.findall(".//Text"):
        style = texto_xml(text_el.find("style"))
        if style == "Instrument Name (Part)":
            t = texto_xml(text_el.find("text"))
            if t.isdigit():
                return "hino", int(t)

    return "hino", 0


def obter_metadados(path: Path, root: ET.Element) -> Tuple[str, int, str, str]:
    tipo, numero = detectar_tipo_numero(path, root)
    titulo = ""
    compositor = ""

    for text_el in root.findall(".//Text"):
        style = texto_xml(text_el.find("style"))
        val = texto_xml(text_el.find("text"))
        if not val:
            continue
        if style == "Title" and not titulo:
            titulo = limpar_titulo(val)
        elif style == "Composer" and not compositor:
            compositor = limpar_titulo(val)
        elif style == "Instrument Name (Part)" and numero == 0 and val.isdigit():
            numero = int(val)

    if not titulo:
        titulo = path.stem

    return tipo, numero, titulo, compositor


def remover_prefixo_estrofe(token: str) -> str:
    # Remove "1. ", "2. ", etc. no começo da primeira sílaba/primeira palavra.
    return re.sub(r"^\s*\d+\s*[\.)]\s*", "", token).strip()


def juntar_tokens_lyric(tokens: List[Tuple[str, str]]) -> str:
    """Junta sílabas do MuseScore em texto corrido."""
    palavras: List[str] = []
    atual = ""

    for raw, syllabic in tokens:
        txt = remover_prefixo_estrofe(raw)
        if not txt:
            continue

        syllabic = (syllabic or "single").strip().lower()

        if syllabic in {"begin", "middle"}:
            atual += txt
            continue

        if syllabic == "end":
            atual += txt
            palavras.append(atual)
            atual = ""
            continue

        # single/ausente
        if atual:
            palavras.append(atual)
            atual = ""
        palavras.append(txt)

    if atual:
        palavras.append(atual)

    texto = " ".join(palavras)

    # Ajustes de pontuação e espaços
    texto = texto.replace(" ’", "’")
    texto = texto.replace(" '", "'")
    texto = re.sub(r"\s+([,;.!?])", r"\1", texto)
    texto = re.sub(r"([¿¡])\s+", r"\1", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def extrair_letras(root: ET.Element) -> Tuple[List[str], str, List[str]]:
    """
    Extrai estrofes e coro a partir do Staff 1.
    Antes do StaffText "Coro", Lyrics são estrofes agrupadas por <no>.
    Depois do StaffText "Coro", Lyrics entram como coro.
    """
    avisos: List[str] = []

    # Em arquivos MuseScore há Staff de definição dentro de <Part> e Staff real dentro de <Score>.
    # Precisamos do Staff que contém <Measure>. Preferimos id=1, pois é onde ficam as letras.
    staff = None
    for candidato in root.findall(".//Staff[@id='1']"):
        if candidato.findall("Measure"):
            staff = candidato
            break

    if staff is None:
        for candidato in root.findall(".//Staff"):
            if candidato.findall("Measure"):
                staff = candidato
                avisos.append("Não encontrei Staff id=1 com medidas; usei o primeiro Staff com Measure encontrado.")
                break

    if staff is None:
        return [], "", ["Nenhum Staff com Measure encontrado no arquivo."]

    secoes: Dict[str, Dict[int, List[Tuple[str, str]]]] = {
        "estrofe": {},
        "coro": {0: []},
    }
    secao_atual = "estrofe"

    # Percorre os Measures na ordem do arquivo.
    for measure in staff.findall("Measure"):
        for child in list(measure):
            # Detecta marcador de coro.
            if child.tag in {"StaffText", "SystemText"}:
                t = texto_xml(child.find("text"))
                if re.search(r"\bcoro\b", t, flags=re.I):
                    secao_atual = "coro"
                continue

            if child.tag != "Chord":
                continue

            for lyr in child.findall("Lyrics"):
                txt = texto_xml(lyr.find("text"))
                if not txt:
                    continue

                # Ignora letras invisíveis ou marcadores estranhos se existirem
                if txt.strip().lower() == "coro":
                    secao_atual = "coro"
                    continue

                no_el = lyr.find("no")
                no = 0
                if no_el is not None and (no_el.text or "").strip().isdigit():
                    no = int((no_el.text or "0").strip())

                syllabic = texto_xml(lyr.find("syllabic")) or "single"

                if secao_atual == "coro":
                    # Chorus normalmente usa no 0; se houver no, ainda juntamos tudo no coro.
                    secoes["coro"].setdefault(0, []).append((txt, syllabic))
                else:
                    secoes["estrofe"].setdefault(no, []).append((txt, syllabic))

    estrofes: List[str] = []
    for no in sorted(secoes["estrofe"].keys()):
        texto = juntar_tokens_lyric(secoes["estrofe"][no])
        if texto:
            estrofes.append(texto)

    coro = juntar_tokens_lyric(secoes["coro"].get(0, []))

    if not estrofes and not coro:
        avisos.append("Não consegui extrair letras deste arquivo.")

    return estrofes, coro, avisos


def dividir_em_segmentos_pontuados(texto: str, delimitadores: str) -> List[str]:
    """
    Divide o texto em segmentos terminados por pontuação, mantendo o delimitador.

    Importante: esta função NÃO decide a quebra de linha. Ela apenas cria
    pontos possíveis de corte, como vírgula, ponto e vírgula, ponto final etc.
    A quebra real é feita por agrupar_segmentos_por_tamanho(), que só corta
    quando a linha já chegou perto do tamanho médio do hinário.
    """
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    if not delimitadores:
        return [texto]

    escaped = re.escape(delimitadores)
    partes = re.findall(rf"[^ {escaped}][^{escaped}]*[{escaped}]?|[{escaped}]", texto)

    segmentos: List[str] = []
    buffer = ""

    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue

        if len(parte) == 1 and parte in delimitadores:
            buffer = (buffer + parte).strip()
            if buffer:
                segmentos.append(buffer)
                buffer = ""
            continue

        if buffer:
            segmentos.append(buffer.strip())
        buffer = parte

        if parte[-1:] in delimitadores:
            segmentos.append(buffer.strip())
            buffer = ""

    if buffer.strip():
        segmentos.append(buffer.strip())

    return [s for s in segmentos if s.strip()] or [texto]


def quebrar_por_palavras(texto: str, hard_max_len: int) -> List[str]:
    """Quebra de emergência para trecho muito longo sem pontuação adequada."""
    texto = texto.strip()
    if not texto:
        return []
    if hard_max_len <= 0 or len(texto) <= hard_max_len:
        return [texto]

    palavras = texto.split()
    linhas: List[str] = []
    atual = ""

    for palavra in palavras:
        if not atual:
            atual = palavra
        elif len(atual) + 1 + len(palavra) <= hard_max_len:
            atual += " " + palavra
        else:
            if atual:
                linhas.append(atual)
            atual = palavra

    if atual:
        linhas.append(atual)

    return linhas


def agrupar_segmentos_por_tamanho(
    segmentos: List[str],
    target_len: int,
    hard_max_len: int,
) -> List[str]:
    """
    Agrupa segmentos pontuados em linhas.

    Regra principal:
    - NÃO quebra em toda vírgula/ponto.
    - Acumula texto até chegar perto de target_len.
    - Quando chega em target_len, usa a próxima pontuação como corte natural.
    - Se ficar grande demais sem pontuação, usa hard_max_len como limite de segurança.
    """
    if target_len <= 0:
        target_len = 42
    if hard_max_len <= 0:
        hard_max_len = max(target_len + 14, int(target_len * 1.35))
    if hard_max_len < target_len:
        hard_max_len = target_len

    linhas: List[str] = []
    atual = ""

    for segmento in segmentos:
        segmento = segmento.strip()
        if not segmento:
            continue

        # Se um segmento sozinho já passa muito do limite, quebra por palavras.
        if not atual and len(segmento) > hard_max_len:
            linhas.extend(quebrar_por_palavras(segmento, hard_max_len))
            continue

        candidato = segmento if not atual else f"{atual} {segmento}"

        # Enquanto não atingiu o tamanho médio, não corta só porque apareceu vírgula/ponto.
        if len(candidato) < target_len:
            atual = candidato
            continue

        # Se atingiu o tamanho médio e ainda cabe no limite máximo, fecha a linha aqui.
        if len(candidato) <= hard_max_len:
            linhas.append(candidato.strip())
            atual = ""
            continue

        # Se passou do limite máximo, fecha a linha anterior e começa outra.
        if atual:
            linhas.append(atual.strip())
            if len(segmento) > hard_max_len:
                linhas.extend(quebrar_por_palavras(segmento, hard_max_len))
                atual = ""
            else:
                atual = segmento
        else:
            linhas.extend(quebrar_por_palavras(segmento, hard_max_len))
            atual = ""

    if atual.strip():
        linhas.append(atual.strip())

    return linhas


def formatar_texto_em_linhas(
    texto: str,
    max_line_len: int,
    delimitadores: str,
    line_marker: str,
    hard_max_line_len: int = 0,
) -> List[str]:
    # max_line_len foi mantido para compatibilidade com os comandos antigos,
    # mas agora significa "tamanho médio alvo", não corte obrigatório.
    target_len = max_line_len
    hard_max_len = hard_max_line_len or max(target_len + 14, int(target_len * 1.35))

    segmentos = dividir_em_segmentos_pontuados(texto, delimitadores)
    linhas_base = agrupar_segmentos_por_tamanho(segmentos, target_len, hard_max_len)

    linhas: List[str] = []
    for linha in linhas_base:
        linha = linha.strip()
        if not linha:
            continue
        if line_marker:
            linha = f"{linha} {line_marker}".rstrip()
        linhas.append(linha)

    return linhas


def escrever_txt(
    item: ItemLetra,
    txt_dir: Path,
    max_line_len: int,
    delimitadores: str,
    line_marker: str,
    repeat_chorus: bool,
    include_header: bool,
    hard_max_line_len: int = 0,
) -> Path:
    txt_dir.mkdir(parents=True, exist_ok=True)

    nome = f"{item.tipo}-{item.numero:03d}-{limpar_nome_arquivo(item.titulo)}.txt"
    destino = txt_dir / nome

    linhas: List[str] = []
    tipo_label = "Hino" if item.tipo == "hino" else "Coro"

    if include_header:
        if item.numero:
            linhas.append(f"{tipo_label} {item.numero:03d} - {item.titulo}")
        else:
            linhas.append(f"{tipo_label} - {item.titulo}")
        linhas.append("")

    for idx, estrofe in enumerate(item.estrofes, start=1):
        linhas.append(f"{idx}.")
        linhas.extend(formatar_texto_em_linhas(estrofe, max_line_len, delimitadores, line_marker, hard_max_line_len))
        linhas.append("")

        if repeat_chorus and item.coro:
            linhas.append("Coro:")
            linhas.extend(formatar_texto_em_linhas(item.coro, max_line_len, delimitadores, line_marker, hard_max_line_len))
            linhas.append("")

    if item.coro and not repeat_chorus:
        linhas.append("Coro:")
        linhas.extend(formatar_texto_em_linhas(item.coro, max_line_len, delimitadores, line_marker, hard_max_line_len))
        linhas.append("")

    destino.write_text("\n".join(linhas).rstrip() + "\n", encoding="utf-8")
    return destino


def listar_arquivos(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    arquivos = []
    for ext in ("*.mscx", "*.mscz"):
        arquivos.extend(input_path.rglob(ext))
    return sorted(arquivos, key=lambda p: (p.stem.lower().split("-")[0], numero_no_nome(p), p.name.lower()))


def numero_no_nome(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else 0


def extrair_item(path: Path) -> ItemLetra:
    root = abrir_xml_musescore(path)
    tipo, numero, titulo, compositor = obter_metadados(path, root)
    estrofes, coro, avisos = extrair_letras(root)
    return ItemLetra(
        tipo=tipo,
        numero=numero,
        titulo=titulo,
        compositor=compositor,
        estrofes=estrofes,
        coro=coro,
        arquivo=str(path),
        avisos=avisos,
    )


def salvar_csv(items: List[ItemLetra], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "tipo",
                "numero",
                "titulo",
                "compositor",
                "letra_completa",
                "coro",
                "estrofes_json",
                "arquivo",
                "avisos",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow({
                "tipo": item.tipo,
                "numero": item.numero,
                "titulo": item.titulo,
                "compositor": item.compositor,
                "letra_completa": item.letra_completa,
                "coro": item.coro,
                "estrofes_json": json.dumps(item.estrofes, ensure_ascii=False),
                "arquivo": item.arquivo,
                "avisos": "; ".join(item.avisos),
            })


def salvar_json(items: List[ItemLetra], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for item in items:
        d = asdict(item)
        d["letra_completa"] = item.letra_completa
        data.append(d)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extrai letras de arquivos MuseScore .mscx/.mscz.")
    parser.add_argument("--input", required=True, help="Arquivo .mscx/.mscz ou pasta com arquivos MuseScore.")
    parser.add_argument("--txt-dir", default=None, help="Pasta onde serão gerados os TXT separados.")
    parser.add_argument("--out", default=None, help="CSV principal de saída. Ex: source/hinario5_letras.csv")
    parser.add_argument("--json", default=None, help="Opcional: caminho do JSON de saída.")
    parser.add_argument("--skip-csv", action="store_true", help="Não gera CSV, apenas TXT/JSON se informados.")
    parser.add_argument("--max-line-len", type=int, default=42, help="Tamanho médio alvo antes de aceitar corte em pontuação. Padrão: 42, estimado pelos PDFs enviados.")
    parser.add_argument("--hard-max-line-len", type=int, default=58, help="Limite de segurança quando não houver pontuação adequada. Padrão: 58.")
    parser.add_argument("--line-marker", default="|", help="Marcador no fim de cada linha. Use '' para não usar. Padrão: |")
    parser.add_argument("--break-delimiters", default=",;.!?", help="Caracteres que forçam quebra de frase. Padrão: ,;.!?")
    parser.add_argument("--no-repeat-chorus", action="store_true", help="Não repete o coro após cada estrofe; coloca o coro uma vez no final.")
    parser.add_argument("--no-header", action="store_true", help="Não inclui cabeçalho com número/título no TXT.")

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERRO: input não encontrado: {input_path}", file=sys.stderr)
        return 1

    arquivos = listar_arquivos(input_path)
    if not arquivos:
        print(f"ERRO: nenhum .mscx/.mscz encontrado em: {input_path}", file=sys.stderr)
        return 1

    items: List[ItemLetra] = []
    erros: List[str] = []

    for path in arquivos:
        try:
            item = extrair_item(path)
            items.append(item)
        except Exception as e:
            erros.append(f"{path}: {e}")

    items.sort(key=lambda i: (0 if i.tipo == "hino" else 1, i.numero, i.titulo))

    if args.txt_dir:
        txt_dir = Path(args.txt_dir)
        for item in items:
            escrever_txt(
                item=item,
                txt_dir=txt_dir,
                max_line_len=args.max_line_len,
                delimitadores=args.break_delimiters,
                line_marker=args.line_marker,
                repeat_chorus=not args.no_repeat_chorus,
                include_header=not args.no_header,
                hard_max_line_len=args.hard_max_line_len,
            )

    if not args.skip_csv:
        out_path = Path(args.out or "source/hinario5_letras.csv")
        salvar_csv(items, out_path)

    if args.json:
        salvar_json(items, Path(args.json))

    print(f"Arquivos MuseScore lidos: {len(arquivos)}")
    print(f"Itens extraídos: {len(items)}")
    if args.txt_dir:
        print(f"TXT gerados em: {args.txt_dir}")
    if not args.skip_csv:
        print(f"CSV gerado em: {args.out or 'source/hinario5_letras.csv'}")
    if args.json:
        print(f"JSON gerado em: {args.json}")

    sem_letra = [i for i in items if not i.estrofes and not i.coro]
    if sem_letra:
        print(f"Atenção: {len(sem_letra)} arquivo(s) sem letra detectada.")

    if erros:
        print("\nErros:", file=sys.stderr)
        for erro in erros[:20]:
            print(f"- {erro}", file=sys.stderr)
        if len(erros) > 20:
            print(f"... e mais {len(erros) - 20} erro(s).", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
