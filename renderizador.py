#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador.py — Conversor MIDI → MP3 com progresso persistente, múltiplos
formatos de soundfont e geração de arpejos matemáticos.

Uso rápido:
    python renderizador.py --listar
    python renderizador.py --status
    python renderizador.py --formato orquestra
    python renderizador.py --mid "001- Cristo meu Mestre.mid" --formato quarteto_cordas
    python renderizador.py --formato metais --arpejo --estilo-arpejo alternado
    python renderizador.py --reiniciar --formato orquestra
"""

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    import mido
except ImportError:
    print("ERRO: biblioteca 'mido' não encontrada.")
    print("Ative o venv e rode: pip install mido")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Configuração de caminhos
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
MID_DIR = BASE_DIR / "mid"
OUTPUT_DIR = BASE_DIR / "output"
SOUNDFONTS_DIR = BASE_DIR / "soundfonts"
DB_PATH = BASE_DIR / "progresso.db"

# ─────────────────────────────────────────────────────────────────────────────
# Descoberta dinâmica de SoundFonts
# ─────────────────────────────────────────────────────────────────────────────

def descobrir_soundfonts() -> dict[str, Path]:
    """
    Varre soundfonts/ e retorna um dict {nome: caminho} para cada .sf2 encontrado.
    O nome é o stem do arquivo (ex: 'GeneralUser GS' para 'GeneralUser GS.sf2').
    Nomes são normalizados para uso como flag de CLI (espaços → _, maiúsculas → minúsculas).
    """
    sf2s: dict[str, Path] = {}
    if SOUNDFONTS_DIR.exists():
        for sf2 in sorted(SOUNDFONTS_DIR.glob("*.sf2")):
            nome_cli = sf2.stem.replace(" ", "_").replace("-", "_")
            sf2s[nome_cli] = sf2
    return sf2s


# Carrega uma vez ao iniciar — reúsado em todo o script
SOUNDFONTS: dict[str, Path] = descobrir_soundfonts()

ESTILOS_ARPEJO = ["ascendente", "descendente", "alternado"]


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def configurar_log(verbose: bool = False) -> logging.Logger:
    nivel = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("mid2mp3")


# ─────────────────────────────────────────────────────────────────────────────
# Banco de dados SQLite
# ─────────────────────────────────────────────────────────────────────────────

def abrir_banco() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS renders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo_mid     TEXT NOT NULL,
            formato         TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pendente',
            output_mp3      TEXT,
            data_criacao    TEXT NOT NULL,
            data_conclusao  TEXT,
            erro_msg        TEXT,
            UNIQUE(arquivo_mid, formato)
        )
    """)
    conn.commit()
    return conn


def registrar_pendente(conn: sqlite3.Connection, arquivo_mid: str, formato: str):
    conn.execute("""
        INSERT INTO renders (arquivo_mid, formato, status, data_criacao)
        VALUES (?, ?, 'pendente', ?)
        ON CONFLICT(arquivo_mid, formato) DO NOTHING
    """, (arquivo_mid, formato, datetime.now().isoformat()))
    conn.commit()


def marcar_concluido(conn: sqlite3.Connection, arquivo_mid: str, formato: str, output_mp3: str):
    conn.execute("""
        UPDATE renders SET status='concluido', output_mp3=?, data_conclusao=?, erro_msg=NULL
        WHERE arquivo_mid=? AND formato=?
    """, (output_mp3, datetime.now().isoformat(), arquivo_mid, formato))
    conn.commit()


def marcar_erro(conn: sqlite3.Connection, arquivo_mid: str, formato: str, msg: str):
    conn.execute("""
        UPDATE renders SET status='erro', erro_msg=?, data_conclusao=?
        WHERE arquivo_mid=? AND formato=?
    """, (msg, datetime.now().isoformat(), arquivo_mid, formato))
    conn.commit()


def ja_concluido(conn: sqlite3.Connection, arquivo_mid: str, formato: str) -> bool:
    row = conn.execute("""
        SELECT status FROM renders WHERE arquivo_mid=? AND formato=?
    """, (arquivo_mid, formato)).fetchone()
    return row is not None and row["status"] == "concluido"


def reiniciar_banco(conn: sqlite3.Connection, formato: str | None = None):
    if formato:
        conn.execute("UPDATE renders SET status='pendente', data_conclusao=NULL, erro_msg=NULL WHERE formato=?", (formato,))
    else:
        conn.execute("UPDATE renders SET status='pendente', data_conclusao=NULL, erro_msg=NULL")
    conn.commit()


def status_banco(conn: sqlite3.Connection, formato: str | None = None) -> dict:
    q = "SELECT status, COUNT(*) as n FROM renders"
    params: tuple = ()
    if formato:
        q += " WHERE formato=?"
        params = (formato,)
    q += " GROUP BY status"
    rows = conn.execute(q, params).fetchall()
    totais: dict = {"concluido": 0, "pendente": 0, "erro": 0}
    for r in rows:
        totais[r["status"]] = r["n"]
    return totais


# ─────────────────────────────────────────────────────────────────────────────
# Listagem de arquivos MIDI
# ─────────────────────────────────────────────────────────────────────────────

def listar_midis() -> list[Path]:
    """Retorna todos os .mid da pasta MID_DIR, excluindo arquivos ocultos do macOS."""
    if not MID_DIR.exists():
        return []
    arquivos = sorted(
        p for p in MID_DIR.iterdir()
        if p.suffix.lower() == ".mid" and not p.name.startswith("._")
    )
    return arquivos


# ─────────────────────────────────────────────────────────────────────────────
# Arpejos matemáticos
# ─────────────────────────────────────────────────────────────────────────────

def _notas_do_acorde(notas_ativas: set[int]) -> list[int]:
    """Retorna a lista ordenada de notas ativas."""
    return sorted(notas_ativas)


def _gerar_sequencia_arpejo(
    notas: list[int],
    estilo: str,
    idx_acorde: int,
    n_divisoes: int = 0,
) -> list[int]:
    """
    Gera exatamente n_divisoes notas arpejadas.

    n_divisoes = numerador do compasso (ts_num):
      - 4/4  -> n_divisoes=4  (quartenario: 4 pulsos)
      - 3/4  -> n_divisoes=3  (ternario: 3 pulsos)
      - 6/8  -> n_divisoes=6  (ou 2, dependendo da interpretacao)
    Se n_divisoes=0, usa o numero de notas disponivel (comportamento legado).

    Expansao:
      - nota unica -> expande em oitavas para ter material suficiente
    Ajuste de tamanho:
      - len < n_divisoes -> cicla as notas (wrap-around)
      - len > n_divisoes -> toma os primeiros n_divisoes
    """
    if not notas:
        return []

    notas_asc = sorted(set(notas))

    # Expande nota unica em oitavas para ter pelo menos 3 opcoes
    if len(notas_asc) == 1:
        n = notas_asc[0]
        candidatos: list[int] = []
        if n - 12 >= 0:   candidatos.append(n - 12)
        candidatos.append(n)
        if n + 12 <= 127: candidatos.append(n + 12)
        if n + 24 <= 127: candidatos.append(n + 24)
        notas_asc = candidatos

    # Sequencia base no estilo escolhido
    if estilo == "descendente":
        base = list(reversed(notas_asc))
    elif estilo == "alternado":
        base = notas_asc if idx_acorde % 2 == 0 else list(reversed(notas_asc))
    else:  # ascendente (padrao)
        base = notas_asc[:]

    # Ajustar para exatamente n_divisoes notas
    n = n_divisoes if n_divisoes > 0 else len(base)
    if n <= 0:
        return base

    # Cicla/trunca para atingir exatamente n notas
    return [base[i % len(base)] for i in range(n)]

def aplicar_arpejo_na_trilha(
    midi_in: mido.MidiFile,
    estilo: str = "ascendente",
    patch_gm: int | None = None,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Adiciona uma NOVA trilha de arpejo ao MidiFile sem alterar as originais.

    Estratégia:
      1. Detecta a trilha de som mais grave (via mediana das notas).
      2. Gera uma nova trilha com as notas arpejadas/expandidas em oitavas.
      3. A nova trilha usa um canal MIDI livre (não usado pelas outras trilhas).
      4. Velocidade da nova trilha = 75% da velocidade original.
      5. Se patch_gm informado, insere program_change no canal novo.
      6. As trilhas originais são mantidas INTACTAS.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    if len(midi_in.tracks) < 2:
        log.warning("MIDI tem apenas 1 trilha; arpejo ignorado.")
        return midi_in

    # ── Detectar trilha com som mais grave ────────────────────────────────
    def mediana_notas(t: mido.MidiTrack) -> float:
        ns = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if not ns:
            return float("inf")
        ns.sort()
        meio = len(ns) // 2
        return (ns[meio - 1] + ns[meio]) / 2 if len(ns) % 2 == 0 else float(ns[meio])

    ultima_trilha_idx = len(midi_in.tracks) - 1
    medianas = [(i, mediana_notas(t)) for i, t in enumerate(midi_in.tracks)]
    com_notas = [(i, m) for i, m in medianas if m < float("inf")]

    if com_notas:
        trilha_alvo_idx, med_alvo = min(com_notas, key=lambda x: (x[1], -x[0]))
        if trilha_alvo_idx != ultima_trilha_idx:
            log.info(
                "  ♩  Trilha mais grave: #%d (mediana=%.1f) "
                "[diferente da última #%d (mediana=%.1f)]",
                trilha_alvo_idx, med_alvo,
                ultima_trilha_idx,
                next((m for i, m in medianas if i == ultima_trilha_idx), float("nan")),
            )
        else:
            log.debug("  ♩  Trilha mais grave: #%d (mediana=%.1f)", trilha_alvo_idx, med_alvo)
    else:
        log.warning("Nenhuma trilha com notas; usando a última.")
        trilha_alvo_idx = ultima_trilha_idx

    trilha_original = midi_in.tracks[trilha_alvo_idx]
    ticks_por_beat = midi_in.ticks_per_beat

    # ── Mapear todas as mudanças de compasso ao longo do MIDI ─────────────
    # Cada time_signature meta-msg define o compasso a partir de um tick absoluto.
    # Musicas com compassos alternantes (ex: 4/4 → 3/4 → 4/4) têm vários desses.
    ts_changes: list[tuple[int, int, int]] = []   # (tick_abs, ts_num, ts_den)
    for trilha in midi_in.tracks:
        tick = 0
        for msg in trilha:
            tick += msg.time
            if msg.type == "time_signature":
                ts_changes.append((tick, msg.numerator, msg.denominator))

    ts_changes.sort(key=lambda x: x[0])
    if not ts_changes or ts_changes[0][0] > 0:
        ts_changes.insert(0, (0, 4, 4))   # padrão GM: 4/4 desde o início

    def ts_em_tick(tick: int) -> tuple[int, int]:
        """Retorna (ts_num, ts_den) vigente no tick dado (busca binária)."""
        lo, hi = 0, len(ts_changes) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if ts_changes[mid][0] <= tick:
                lo = mid
            else:
                hi = mid - 1
        return ts_changes[lo][1], ts_changes[lo][2]

    # Log dos compassos encontrados
    compassos_unicos = sorted({(num, den) for _, num, den in ts_changes})
    if len(compassos_unicos) == 1:
        num0, den0 = compassos_unicos[0]
        log.info("  ♩  Compasso: %d/%d (uniforme) → %d pulsos por acorde", num0, den0, num0)
    else:
        descricao = " / ".join(f"{n}/{d}" for n, d in compassos_unicos)
        log.info("  ♩  Compassos alternantes detectados: %s → pulsos por acorde variam", descricao)

    # ── Descobrir canal MIDI livre ────────────────────────────────────────
    canais_usados: set[int] = set()
    for trilha in midi_in.tracks:
        for msg in trilha:
            if hasattr(msg, "channel"):
                canais_usados.add(msg.channel)
    # Canal 9 é percussão GM — não usa
    canal_livre = next(
        (c for c in range(16) if c != 9 and c not in canais_usados),
        15,  # fallback: canal 15
    )
    log.info(
        "  ♪  Nova trilha de arpejo: canal MIDI %d | patch %s",
        canal_livre,
        f"GM {patch_gm}" if patch_gm is not None else "original (sem alteração)",
    )

    # ── Passo 1: eventos absolutos da trilha alvo ─────────────────────────
    eventos_abs: list[tuple[int, mido.Message]] = []
    tick_atual = 0
    for msg in trilha_original:
        tick_atual += msg.time
        eventos_abs.append((tick_atual, msg))

    if not eventos_abs:
        return midi_in

    # ── Passo 2: agrupar notas em grupos (acorde / nota única) ───────────
    notas_ativas: dict[int, int] = {}
    grupos: list[dict] = []

    for tick, msg in eventos_abs:
        if msg.type == "note_on" and msg.velocity > 0:
            notas_ativas[msg.note] = tick
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            if msg.note in notas_ativas:
                t_ini = notas_ativas.pop(msg.note)
                duracao = tick - t_ini
                grupo_existente = next(
                    (g for g in grupos if abs(g["tick_inicio"] - t_ini) <= 2), None
                )
                if grupo_existente:
                    grupo_existente["notas"].append((msg.note, duracao))
                else:
                    grupos.append({"tick_inicio": t_ini, "notas": [(msg.note, duracao)]})


    # ── Passo 3: calcular fim de cada grupo ──────────────────────────────────
    for grupo in grupos:
        if grupo["notas"]:
            grupo["tick_fim"] = grupo["tick_inicio"] + max(d for _, d in grupo["notas"])
        else:
            grupo["tick_fim"] = grupo["tick_inicio"]

    # ── Passo 4: gerar eventos arpejados com volume 75% ──────────────────────
    VEL_SCALE  = 0.75
    VEL_FILL   = 0.45   # fill de silencio: mais suave
    BASE_VEL   = 80
    MIN_GAP    = ticks_por_beat * 2   # silencio minimo para ativar o fill (minima)

    novos_eventos: list[tuple[int, mido.Message]] = []

    for idx_acorde, grupo in enumerate(grupos):
        tick_ini = grupo["tick_inicio"]
        notas_raw = grupo["notas"]
        notas_sorted = sorted(notas_raw, key=lambda x: x[0])
        duracao_total = max(d for _, d in notas_sorted) if notas_sorted else ticks_por_beat

        # Compasso vigente NESTE acorde (pode variar ao longo da musica)
        ts_num_local, _ = ts_em_tick(tick_ini)
        sequencia = _gerar_sequencia_arpejo(
            [n for n, _ in notas_sorted], estilo, idx_acorde, n_divisoes=ts_num_local
        )
        # Divide o tempo do acorde igualmente entre ts_num_local pulsos
        duracao_nota = max(1, duracao_total // ts_num_local)
        vel = max(1, int(BASE_VEL * VEL_SCALE))  # ~60

        for i, nota in enumerate(sequencia):
            t_on  = tick_ini + i * duracao_nota
            t_off = t_on + duracao_nota
            novos_eventos.append((t_on,  mido.Message("note_on",  channel=canal_livre, note=nota, velocity=vel, time=0)))
            novos_eventos.append((t_off, mido.Message("note_off", channel=canal_livre, note=nota, velocity=0,   time=0)))

    # ── Passo 5: preencher silencias com ping-pong ───────────────────────────
    vel_fill = max(1, int(BASE_VEL * VEL_FILL))  # ~36
    ultimo_grupo_notas: list[int] = []

    for i, grupo in enumerate(grupos):
        notas_grupo = sorted([n for n, _ in grupo["notas"]])
        if notas_grupo:
            ultimo_grupo_notas = notas_grupo

        tick_proximo = grupos[i + 1]["tick_inicio"] if i + 1 < len(grupos) else None
        if tick_proximo is None:
            continue

        gap = tick_proximo - grupo["tick_fim"]
        if gap < MIN_GAP or not ultimo_grupo_notas:
            continue

        notas_sorted_g = sorted(grupo["notas"], key=lambda x: x[0])
        duracao_total_g = max(d for _, d in notas_sorted_g) if notas_sorted_g else ticks_por_beat

        tick_cursor = grupo["tick_fim"]
        ciclo_idx   = 0

        while tick_cursor + 1 <= tick_proximo:
            # Compasso vigente NO INSTANTE do fill (pode mudar durante o silencio)
            ts_fill, _ = ts_em_tick(tick_cursor)
            dur_fill_nota = max(1, duracao_total_g // ts_fill)
            dur_ciclo = dur_fill_nota * ts_fill

            if dur_ciclo == 0 or tick_cursor + dur_ciclo > tick_proximo:
                break

            # Gera exatamente ts_fill notas por ciclo, alternando direcao
            seq_fill = _gerar_sequencia_arpejo(
                ultimo_grupo_notas,
                "ascendente" if ciclo_idx % 2 == 0 else "descendente",
                ciclo_idx,
                n_divisoes=ts_fill,
            )
            for j, nota in enumerate(seq_fill):
                t_on  = tick_cursor + j * dur_fill_nota
                t_off = t_on + dur_fill_nota
                novos_eventos.append((t_on,  mido.Message("note_on",  channel=canal_livre, note=nota, velocity=vel_fill, time=0)))
                novos_eventos.append((t_off, mido.Message("note_off", channel=canal_livre, note=nota, velocity=0,        time=0)))
            tick_cursor += dur_ciclo
            ciclo_idx   += 1

        if ciclo_idx > 0:
            log.debug("  fill: grupo %d, %d ciclos, gap=%d ticks", i, ciclo_idx, gap)

    # ── Passo 6: montar nova trilha ───────────────────────────────────────────
    novos_eventos.sort(key=lambda x: x[0])
    nova_trilha = mido.MidiTrack()
    nova_trilha.name = (trilha_original.name or "baixo") + "_arpejo"

    if patch_gm is not None:
        nova_trilha.append(
            mido.Message("program_change", channel=canal_livre, program=patch_gm, time=0)
        )
    else:
        for msg in trilha_original:
            if msg.type == "program_change":
                nova_trilha.append(
                    mido.Message("program_change", channel=canal_livre,
                                 program=msg.program, time=0)
                )
                break

    tick_prev = 0
    for tick_abs, msg in novos_eventos:
        delta = tick_abs - tick_prev
        nova_trilha.append(msg.copy(time=delta))
        tick_prev = tick_abs

    nova_trilha.append(mido.MetaMessage("end_of_track", time=0))

    # ── Passo 7: novo MidiFile = originais + nova trilha ──────────────────────
    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)
    for trilha in midi_in.tracks:
        novo_midi.tracks.append(trilha)
    novo_midi.tracks.append(nova_trilha)

    log.info(
        "  ✔  Trilha de arpejo adicionada (canal %d, vel ~%d/127, fill ~%d/127, patch %s)",
        canal_livre, vel, vel_fill,
        str(patch_gm) if patch_gm is not None else "original",
    )
    return novo_midi



# ─────────────────────────────────────────────────────────────────────────────
# Forçar patch GM em todas as trilhas
# ─────────────────────────────────────────────────────────────────────────────

# Instrumentos GM mais usados (programa 0-indexed)
GM_PATCHES: dict[str, int] = {
    "piano":          0,
    "cravo":          6,
    "caixa_de_musica": 9,  # GM #10
    "glockenspiel":   9,
    "vibrafone":      11,
    "marimba":        12,
    "xilofone":       13,
    "sinos":          14,
    "celesta":        8,
    "cordas":         48,
    "pizzicato":      45,
    "harpa":          46,
    "orgao":          16,
    "flauta":         73,
    "trompete":       56,
    "trombone":       57,
    "tuba":           58,
    "coro":           52,
    "orquestra":      48,
}


def aplicar_patch(
    midi_in: mido.MidiFile,
    patch: int,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Substitui todos os program_change do MIDI pelo patch especificado (0-127).
    Mantém o canal 9 intacto (percussão GM não muda de patch).
    Se uma trilha não tiver program_change, insere um logo no início.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    patch = max(0, min(127, patch))
    log.debug("Forçando patch GM %d em todas as trilhas.", patch)

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        tem_program_change = False
        canais_vistos: set[int] = set()

        for msg in trilha:
            if hasattr(msg, "channel") and msg.channel == 9:
                # Canal 9 = percussão GM — não altera
                nova_trilha.append(msg)
            elif msg.type == "program_change":
                # Substitui pelo patch desejado
                nova_trilha.append(msg.copy(program=patch))
                canais_vistos.add(msg.channel)
                tem_program_change = True
            else:
                # Para note_on/note_off sem program_change anterior,
                # insere um antes da primeira nota
                if (
                    not tem_program_change
                    and msg.type == "note_on"
                    and hasattr(msg, "channel")
                    and msg.channel != 9
                    and msg.channel not in canais_vistos
                ):
                    nova_trilha.append(
                        mido.Message("program_change", channel=msg.channel,
                                     program=patch, time=0)
                    )
                    canais_vistos.add(msg.channel)
                nova_trilha.append(msg)

        novo_midi.tracks.append(nova_trilha)

    return novo_midi


# ─────────────────────────────────────────────────────────────────────────────
# Ajuste de velocidade (BPM)
# ─────────────────────────────────────────────────────────────────────────────

def aplicar_velocidade(
    midi_in: mido.MidiFile,
    porcentagem: float,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Ajusta a velocidade de reprodução do MIDI escalando todos os set_tempo.

    porcentagem = 100  → velocidade original
    porcentagem = 80   → 20% mais lento  (multiplica µs/beat por 100/80)
    porcentagem = 150  → 50% mais rápido (multiplica µs/beat por 100/150)

    Limites: 10%–1000% para evitar valores inválidos.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    porcentagem = max(10.0, min(1000.0, porcentagem))
    fator = 100.0 / porcentagem  # >1 = mais lento, <1 = mais rápido

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    alterados = 0

    for trilha in midi_in.tracks:
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        for msg in trilha:
            if msg.type == "set_tempo":
                novo_tempo = max(1, int(round(msg.tempo * fator)))
                bpm_orig = 60_000_000 / msg.tempo
                bpm_novo = 60_000_000 / novo_tempo
                log.debug(
                    "  tempo: %dµs/beat (%.1f BPM) → %dµs/beat (%.1f BPM)",
                    msg.tempo, bpm_orig, novo_tempo, bpm_novo,
                )
                nova_trilha.append(msg.copy(tempo=novo_tempo))
                alterados += 1
            else:
                nova_trilha.append(msg)
        novo_midi.tracks.append(nova_trilha)

    if alterados == 0:
        # Nenhum set_tempo encontrado — insere na primeira trilha
        tempo_padrao = 500_000  # 120 BPM
        novo_tempo = max(1, int(round(tempo_padrao * fator)))
        novo_midi.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=novo_tempo, time=0))
        log.debug("  set_tempo inserido manualmente: %dµs/beat", novo_tempo)

    bpm_resultado = 60_000_000 / (novo_tempo if alterados == 0 else int(round(
        _tempo_do_midi(midi_in) * fator)))
    log.info(
        "  ⏱  Velocidade: %.0f%% → %.1f BPM (original: %.1f BPM)",
        porcentagem, bpm_resultado, 60_000_000 / _tempo_do_midi(midi_in),
    )
    return novo_midi


def aplicar_transposicao(
    midi_in: mido.MidiFile,
    semitons: int,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Transpoe todas as notas do MIDI em N semitons.

    semitons > 0  -> sobe (ex: +12 = uma oitava acima)
    semitons < 0  -> desce (ex: -12 = uma oitava abaixo)
    semitons = 0  -> nenhuma alteracao

    Canal 9 (percussao GM) nao e transposto.
    Notas fora do intervalo 0-127 apos transposicao sao descartadas.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    if semitons == 0:
        return midi_in

    sentido = "cima" if semitons > 0 else "baixo"
    log.info(
        "  Transposicao: %+d semitons (%s)",
        semitons, sentido,
    )

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    descartadas = 0

    for trilha in midi_in.tracks:
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        for msg in trilha:
            # Nao transpoe percussao (canal 9) nem meta-mensagens
            if msg.type in ("note_on", "note_off") and getattr(msg, "channel", 9) != 9:
                nova_nota = msg.note + semitons
                if 0 <= nova_nota <= 127:
                    nova_trilha.append(msg.copy(note=nova_nota))
                else:
                    # Nota ficaria fora do alcance MIDI — descarta
                    descartadas += 1
                    if msg.type == "note_off" or msg.velocity == 0:
                        pass  # silencio automatico
            else:
                nova_trilha.append(msg)
        novo_midi.tracks.append(nova_trilha)

    if descartadas:
        log.warning("  %d nota(s) descartada(s) por ficarem fora do alcance MIDI (0-127)", descartadas)

    return novo_midi

def _tempo_do_midi(midi_in: mido.MidiFile) -> int:
    """
    Retorna o tempo (µs/beat) do primeiro set_tempo encontrado no arquivo.
    Se não houver, assume 120 BPM = 500 000 µs/beat.
    """
    for trilha in midi_in.tracks:
        for msg in trilha:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500_000  # 120 BPM


def aplicar_desincronismo(
    midi_in: mido.MidiFile,
    delay_segundos: float = 0.1,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica desincronismo nota-a-nota no MIDI.

    Trilha 0  - sempre intocada (referencia)
    Demais    - cada nota (note_on + seu note_off correspondente) recebe
                aleatoriamente um atraso de delay_segundos OU 2x delay_segundos.
                A escolha e independente para cada nota.
    """
    import random

    if log is None:
        log = logging.getLogger("mid2mp3")

    if len(midi_in.tracks) < 2:
        log.warning("MIDI tem apenas 1 trilha; desincronismo ignorado.")
        return midi_in

    tempo_us       = _tempo_do_midi(midi_in)
    ticks_por_beat = midi_in.ticks_per_beat
    ticks_por_seg  = ticks_por_beat * 1_000_000 / tempo_us

    delay1 = max(1, int(round(delay_segundos       * ticks_por_seg)))
    delay2 = max(1, int(round(delay_segundos * 2.0 * ticks_por_seg)))

    log.info(
        "  Desincronismo por nota: %.2fs (%d ticks) ou %.2fs (%d ticks) aleatorio",
        delay_segundos, delay1, delay_segundos * 2, delay2,
    )

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 e trilhas de arpejo -> sempre intocadas
        eh_arpejo = "_arpejo" in (trilha.name or "").lower()
        if idx_trilha == 0 or eh_arpejo:
            novo_midi.tracks.append(trilha)
            motivo = "trilha 0 (referência)" if idx_trilha == 0 else "trilha de arpejo"
            log.debug("  Trilha %d '%s' -> intocada (%s)", idx_trilha, trilha.name, motivo)
            continue

        # Converter para ticks absolutos
        eventos: list[list] = []
        tick = 0
        for msg in trilha:
            tick += msg.time
            eventos.append([tick, msg])

        # Aplicar atraso aleatorio por nota
        notas_ativas: dict[tuple[int, int], int] = {}
        rng = random.Random(idx_trilha)   # semente por trilha -> reproducivel
        notas_afetadas = 0

        for ev in eventos:
            msg = ev[1]
            ch  = getattr(msg, "channel", 0)

            if msg.type == "note_on" and msg.velocity > 0:
                offset = rng.choice([delay1, delay2])
                notas_ativas[(msg.note, ch)] = offset
                ev[0] += offset
                notas_afetadas += 1

            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.note, ch)
                if key in notas_ativas:
                    ev[0] += notas_ativas.pop(key)

        log.debug(
            "  Trilha %d '%s' -> %d notas deslocadas aleatoriamente",
            idx_trilha, trilha.name, notas_afetadas,
        )

        # Reordenar e converter de volta para delta-ticks
        eventos.sort(key=lambda e: e[0])
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name

        tick_prev = 0
        for tick_abs, msg in eventos:
            delta = max(0, tick_abs - tick_prev)
            nova_trilha.append(msg.copy(time=delta))
            tick_prev = tick_abs

        novo_midi.tracks.append(nova_trilha)

    return novo_midi



# ─────────────────────────────────────────────────────────────────────────────
# Conversão MIDI → MP3
# ─────────────────────────────────────────────────────────────────────────────

def gerar_mp3(
    caminho_mid: Path,
    caminho_sf2: Path,
    caminho_mp3: Path,
    dry_run: bool = False,
    usar_arpejo: bool = False,
    estilo_arpejo: str = "ascendente",
    delay_desincronismo: float = 0.0,
    patch_gm: int | None = None,
    velocidade: float = 100.0,
    semitons: int = 0,
    log: logging.Logger | None = None,
) -> None:
    """
    Converte um arquivo MIDI em MP3 usando FluidSynth + FFmpeg.

    Ordem de processamento:
      1. semitons     -- transpoe notas
      2. velocidade   -- ajusta BPM
      3. patch_gm     -- forca instrumento GM
      4. usar_arpejo  -- adiciona trilha de arpejo
      5. desincronismo -- atrasa trilhas progressivamente
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mid_para_converter = caminho_mid

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # ── Transposicao de semitons ───────────────────────────────────────────
        if semitons != 0:
            log.info("  Transponiendo %+d semitons", semitons)
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_transp = aplicar_transposicao(midi_base, semitons, log)
                    mid_para_converter = tmp_path / f"transp_{caminho_mid.name}"
                    midi_transp.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao transpor (%s); usando original.", e)

        # ── Arpejo ────────────────────────────────────────────────────────
        if usar_arpejo:
            log.info("  ♫  Aplicando arpejo '%s' em: %s", estilo_arpejo, caminho_mid.name)
            if not dry_run:
                try:
                    midi_original = mido.MidiFile(str(mid_para_converter))
                    midi_arpejado = aplicar_arpejo_na_trilha(midi_original, estilo_arpejo, patch_gm, log)
                    mid_para_converter = tmp_path / f"arpejo_{caminho_mid.name}"
                    midi_arpejado.save(str(mid_para_converter))
                    log.debug("  MIDI arpejado salvo em: %s", mid_para_converter)
                except Exception as e:
                    log.warning("  Falha ao aplicar arpejo (%s); usando original.", e)
                    mid_para_converter = caminho_mid

        # ── Velocidade ────────────────────────────────────────────────
        if velocidade != 100.0:
            log.info("  🎵  Ajustando velocidade para %.0f%%", velocidade)
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_vel = aplicar_velocidade(midi_base, velocidade, log)
                    mid_para_converter = tmp_path / f"vel_{caminho_mid.name}"
                    midi_vel.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao ajustar velocidade (%s); usando original.", e)

        # ── Patch GM ───────────────────────────────────────────────
        if patch_gm is not None:
            log.info("  🎹  Forçando patch GM %d (%s) em todas as trilhas.",
                     patch_gm,
                     next((k for k, v in GM_PATCHES.items() if v == patch_gm), str(patch_gm)))
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_patched = aplicar_patch(midi_base, patch_gm, log)
                    mid_para_converter = tmp_path / f"patch_{caminho_mid.name}"
                    midi_patched.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao aplicar patch (%s); usando original.", e)

        # ── Desincronismo ─────────────────────────────────────────────────
        if delay_desincronismo > 0:
            log.info("  ⏱  Aplicando desincronismo de %.3fs/faixa em: %s",
                     delay_desincronismo, caminho_mid.name)
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_desync = aplicar_desincronismo(midi_base, delay_desincronismo, log)
                    mid_para_converter = tmp_path / f"desync_{caminho_mid.name}"
                    midi_desync.save(str(mid_para_converter))
                    log.debug("  MIDI dessincronizado salvo em: %s", mid_para_converter)
                except Exception as e:
                    log.warning("  Falha ao aplicar desincronismo (%s); usando original.", e)

        # ── FluidSynth: MIDI → WAV ─────────────────────────────────────────
        arquivo_wav = tmp_path / (caminho_mp3.stem + ".wav")
        cmd_fluid = [
            "fluidsynth",
            "-F", str(arquivo_wav),
            "-O", "s16",
            "-T", "wav",
            "-g", "5",        # gain: default=0.2 é muito baixo; 5 = volume normal
            "--quiet",
            str(caminho_sf2),
            str(mid_para_converter),
        ]
        log.debug("  FluidSynth: %s", " ".join(cmd_fluid))
        if not dry_run:
            resultado = subprocess.run(cmd_fluid, capture_output=True, text=True)
            if resultado.returncode != 0:
                raise RuntimeError(f"FluidSynth falhou:\n{resultado.stderr}")

        # ── FFmpeg: WAV → MP3 ──────────────────────────────────────────────
        cmd_ffmpeg = [
            "ffmpeg",
            "-y",
            "-i", str(arquivo_wav),
            "-af", "loudnorm=I=-14:TP=-1:LRA=11",  # normalização de volume (EBU R128)
            "-q:a", "0",
            "-map_metadata", "-1",
            "-loglevel", "error",
            str(caminho_mp3),
        ]
        log.debug("  FFmpeg: %s", " ".join(cmd_ffmpeg))
        if not dry_run:
            resultado = subprocess.run(cmd_ffmpeg, capture_output=True, text=True)
            if resultado.returncode != 0:
                raise RuntimeError(f"FFmpeg falhou:\n{resultado.stderr}")

        # ── Salvar MIDI processado para análise ────────────────────────────
        # Sempre que o MIDI foi modificado (arpejo/patch/desync), salva uma
        # cópia .mid na mesma pasta do MP3 para inspeção em editor de música.
        if not dry_run and mid_para_converter != caminho_mid:
            caminho_mid_out = caminho_mp3.with_suffix(".mid")
            import shutil
            shutil.copy2(str(mid_para_converter), str(caminho_mid_out))
            log.info("  📄  MIDI processado: %s", caminho_mid_out.name)

    log.info("  ✓  Gerado: %s", caminho_mp3.name)


# ─────────────────────────────────────────────────────────────────────────────
# Comandos de interface
# ─────────────────────────────────────────────────────────────────────────────

def cmd_opcoes():
    """Imprime referência completa de todos os parâmetros e seus valores."""
    sep = "─" * 70
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║            mid2mp3 — Referência de Parâmetros                       ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    # ── SoundFonts ────────────────────────────────────────────────────────
    print()
    print("┌─ --formato  (SoundFont a usar)")
    sf2s = descobrir_soundfonts()
    if sf2s:
        for nome, caminho in sf2s.items():
            tam = caminho.stat().st_size
            tam_str = f"{tam/1_048_576:.1f} MB" if tam >= 1_048_576 else f"{tam/1024:.0f} KB"
            print(f"│  {nome:<35} ← {caminho.name} ({tam_str})")
    else:
        print(f"│  (nenhum .sf2 em {SOUNDFONTS_DIR} — baixe um e coloque lá)")
    print(sep)

    # ── Instrumentos GM ────────────────────────────────────────────────────
    print()
    print("┌─ --instrumento  (força instrumento GM em todas as trilhas)")
    col = 0
    linha = "│  "
    for nome, patch in sorted(GM_PATCHES.items()):
        token = f"{nome} (patch {patch})"
        if len(linha) + len(token) + 2 > 72:
            print(linha)
            linha = "│  "
        linha += token + "   "
    if linha.strip("│").strip():
        print(linha)
    print(f"│  Ou use --patch-numero 0-127 para qualquer patch GM.")
    print(sep)

    # ── Arpejos ────────────────────────────────────────────────────────────
    print()
    print("┌─ --arpejo  (adiciona trilha de arpejo na voz mais grave)")
    print("│  --estilo-arpejo:")
    estilos = {
        "ascendente" : "grave → agudo (padrão)",
        "descendente": "agudo → grave",
        "alternado"  : "ascendente nos compassos pares, descendente nos ímpares",
    }
    for e, desc in estilos.items():
        print(f"│    {e:<15} — {desc}")
    print(sep)

    # ── Desincronismo ──────────────────────────────────────────────────────
    print()
    print("┌─ --desincronismo  (atraso progressivo entre trilhas)")
    print("│  Trilha 0 → sem atraso")
    print("│  Trilha 1 → +Xs  |  Trilha 2 → +2Xs  |  ...")
    print("│  --delay-faixa SEGUNDOS   (padrão: 0.1)")
    print(sep)

    # ── Controle de arquivos ───────────────────────────────────────────────
    print()
    print("┌─ Seleção de arquivos")
    midis = listar_midis()
    print(f"│  --mid ARQUIVO.mid    (processa apenas um arquivo)")
    if midis:
        print(f"│  Arquivos disponíveis em mid/:")
        for f in midis:
            print(f"│    {f.name}")
    else:
        print(f"│  (nenhum .mid em {MID_DIR})")
    print(sep)

    # ── Controle de progresso ─────────────────────────────────────────────
    print()
    print("┌─ Progresso e execução")
    print("│  --continuar     pula arquivos já concluídos (padrão)")
    print("│  --reiniciar     força reprocessamento de tudo")
    print("│  --dry-run       simula sem gerar arquivos")
    print("│  --verbose / -v  saída detalhada")
    print(sep)

    # ── Informação ────────────────────────────────────────────────────────
    print()
    print("┌─ Comandos informativos")
    print("│  --soundfonts  lista SoundFonts disponíveis")
    print("│  --listar      lista MIDIs e status no banco")
    print("│  --status      resumo do banco (concluídos/erros)")
    print("│  --opcoes / ?  esta tela")
    print(sep)

    # ── Exemplo completo ──────────────────────────────────────────────────
    print()
    print("┌─ Exemplo completo — todos os parâmetros opcionais")
    sf_ex = next(iter(sf2s.keys())) if sf2s else "<formato>"
    print(f"│  python renderizador.py \\")
    print(f"│    --mid \"001- Cristo meu Mestre.mid\" \\")
    print(f"│    --formato {sf_ex} \\")
    print(f"│    --instrumento caixa_de_musica \\")      # força patch GM (nome)
    print(f"│    --patch-numero 9 \\")                   # ou patch direto 0-127
    print(f"│    --arpejo \\")                           # ativa arpejo no baixo
    print(f"│    --estilo-arpejo ascendente \\")         # ascendente|descendente|alternado
    print(f"│    --desincronismo \\")                    # ativa desync por nota
    print(f"│    --delay-faixa 0.1 \\")                 # valor base do desync (s)
    print(f"│    --velocidade 100 \\")                   # % da velocidade (80=lento, 120=rápido)
    print(f"│    --semitons 0 \\")                       # transposição (+12=oitava acima, -12=abaixo)
    print(f"│    --reiniciar \\")                        # força reprocessamento
    print(f"│    --dry-run \\")                          # simula sem gerar arquivo
    print(f"│    --verbose")                             # saída detalhada
    print()
    print("│  Notas:")
    print("│    • --instrumento e --patch-numero se sobrepõem (instrumento tem prioridade)")
    print("│    • --delay-faixa só tem efeito com --desincronismo")
    print("│    • --estilo-arpejo só tem efeito com --arpejo")
    print("│    • --continuar (padrão) e --reiniciar são mutuamente exclusivos")
    print(sep)
    print()



def cmd_soundfonts(log: logging.Logger):
    """Lista todos os SoundFonts disponíveis na pasta soundfonts/."""
    sf2s = descobrir_soundfonts()
    if not sf2s:
        print(f"\nNenhum .sf2 encontrado em: {SOUNDFONTS_DIR}")
        print("Baixe SoundFonts em https://www.generaluser.us/ e coloque nessa pasta.\n")
        return

    print(f"\n{'Nome (use em --formato)':<35} {'Arquivo':<40} {'Tamanho'}")
    print("─" * 85)
    for nome, caminho in sf2s.items():
        tamanho = caminho.stat().st_size
        if tamanho >= 1_048_576:
            tam_str = f"{tamanho/1_048_576:.1f} MB"
        else:
            tam_str = f"{tamanho/1024:.0f} KB"
        print(f"{nome:<35} {caminho.name:<40} {tam_str}")
    print(f"\nTotal: {len(sf2s)} soundfont(s) em {SOUNDFONTS_DIR}\n")


def cmd_listar(conn: sqlite3.Connection, formato: str, log: logging.Logger):
    midis = listar_midis()
    if not midis:
        log.error("Nenhum arquivo .mid encontrado em: %s", MID_DIR)
        return

    print(f"\n{'#':<5} {'Arquivo':<60} {'Status':<12}")
    print("─" * 80)
    for i, mid in enumerate(midis, 1):
        row = conn.execute(
            "SELECT status FROM renders WHERE arquivo_mid=? AND formato=?",
            (mid.name, formato)
        ).fetchone()
        status = row["status"] if row else "—"
        simbolo = {"concluido": "✓", "erro": "✗", "pendente": "…"}.get(status, "—")
        print(f"{i:<5} {mid.name:<60} {simbolo} {status}")
    print(f"\nTotal: {len(midis)} arquivo(s) | Formato: {formato}\n")


def cmd_status(conn: sqlite3.Connection, formato: str | None, log: logging.Logger):
    totais = status_banco(conn, formato)
    total = sum(totais.values())
    print(f"\n{'─'*40}")
    print(f"  Banco: {DB_PATH.name}  |  Formato: {formato or 'todos'}")
    print(f"{'─'*40}")
    print(f"  ✓ Concluídos : {totais['concluido']:>5}")
    print(f"  … Pendentes  : {totais['pendente']:>5}")
    print(f"  ✗ Erros      : {totais['erro']:>5}")
    print(f"{'─'*40}")
    print(f"  Total        : {total:>5}")
    print()


def cmd_processar(
    conn: sqlite3.Connection,
    midis: list[Path],
    formato: str,
    caminho_sf2: Path,
    dry_run: bool,
    usar_arpejo: bool,
    estilo_arpejo: str,
    delay_desincronismo: float,
    patch_gm: int | None,
    velocidade: float,
    semitons: int,
    reiniciar: bool,
    log: logging.Logger,
):
    if reiniciar:
        log.info("⚠  Reiniciando progresso para o formato '%s'…", formato)
        reiniciar_banco(conn, formato)

    sufixo_arpejo = f"_arpejo_{estilo_arpejo}" if usar_arpejo else ""
    sufixo_desync = f"_desync{delay_desincronismo:.2f}s".replace('.', '') if delay_desincronismo > 0 else ""
    sufixo_patch  = f"_patch{patch_gm}" if patch_gm is not None else ""
    sufixo_vel    = f"_vel{int(velocidade)}pct" if velocidade != 100.0 else ""
    sufixo_semi   = f"_semi{semitons:+d}" if semitons != 0 else ""
    pasta_saida = OUTPUT_DIR / f"{formato}{sufixo_arpejo}{sufixo_desync}{sufixo_patch}{sufixo_vel}{sufixo_semi}"

    total = len(midis)
    concluidos = 0
    pulados = 0
    erros = 0

    for idx, caminho_mid in enumerate(midis, 1):
        nome_mid = caminho_mid.name
        nome_mp3 = caminho_mid.stem + f"_{formato}{sufixo_arpejo}{sufixo_desync}{sufixo_patch}{sufixo_vel}{sufixo_semi}.mp3"
        caminho_mp3 = pasta_saida / nome_mp3

        # Registra no banco se ainda não existe
        registrar_pendente(conn, nome_mid, formato)

        if not reiniciar and ja_concluido(conn, nome_mid, formato):
            log.debug("[%d/%d] Pulado (já concluído): %s", idx, total, nome_mid)
            pulados += 1
            continue

        log.info("[%d/%d] %s", idx, total, nome_mid)

        if dry_run:
            log.info("  (dry-run) Simularia: %s → %s", nome_mid, nome_mp3)
            concluidos += 1
            continue

        pasta_saida.mkdir(parents=True, exist_ok=True)

        try:
            gerar_mp3(
                caminho_mid=caminho_mid,
                caminho_sf2=caminho_sf2,
                caminho_mp3=caminho_mp3,
                dry_run=False,
                usar_arpejo=usar_arpejo,
                estilo_arpejo=estilo_arpejo,
                delay_desincronismo=delay_desincronismo,
                patch_gm=patch_gm,
                velocidade=velocidade,
                semitons=semitons,
                log=log,
            )
            marcar_concluido(conn, nome_mid, formato, str(caminho_mp3))
            concluidos += 1
        except Exception as exc:
            log.error("  ✗  Erro em '%s': %s", nome_mid, exc)
            marcar_erro(conn, nome_mid, formato, str(exc))
            erros += 1

    print()
    print(f"{'─'*50}")
    print(f"  Formato      : {formato}")
    if usar_arpejo:
        print(f"  Arpejo       : {estilo_arpejo}")
    if delay_desincronismo > 0:
        print(f"  Desincronismo: {delay_desincronismo:.3f}s por faixa")
    if patch_gm is not None:
        nome_patch = next((k for k, v in GM_PATCHES.items() if v == patch_gm), str(patch_gm))
        print(f"  Instrumento  : patch GM {patch_gm} ({nome_patch})")
    if velocidade != 100.0:
        print(f"  Velocidade   : {velocidade:.0f}%")
    if semitons != 0:
        sentido = "acima" if semitons > 0 else "abaixo"
        print(f"  Transposicao : {semitons:+d} semitons ({sentido})")
    print(f"  Processados  : {concluidos}")
    print(f"  Pulados      : {pulados}")
    print(f"  Erros        : {erros}")
    print(f"  Saída em     : {pasta_saida}")
    print(f"{'─'*50}")
    if dry_run:
        print("  (modo dry-run — nenhum arquivo foi gerado)")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI principal
# ─────────────────────────────────────────────────────────────────────────────

def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renderizador.py",
        description=(
            "Conversor MIDI → MP3 com banco de progresso, múltiplos formatos de\n"
            "soundfont e geração de arpejos matemáticos na última trilha (baixo).\n\n"
            "SoundFonts necessários (baixe e coloque em soundfonts/):\n"
            "  https://musical-artifacts.com/artifacts?tags=soundfont\n"
            "  https://www.generaluser.us/ (GeneralUser GS)\n"
            "  https://member.keymusician.com/Member/FluidR3_GM/index.html"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Formato de soundfont (dinâmico)
    nomes_disponiveis = sorted(SOUNDFONTS.keys())
    parser.add_argument(
        "--formato",
        choices=nomes_disponiveis if nomes_disponiveis else None,
        default=nomes_disponiveis[0] if nomes_disponiveis else None,
        metavar="FORMATO",
        help=(
            "Nome do SoundFont a usar (veja --soundfonts para a lista completa). "
            f"Padrão: {nomes_disponiveis[0] if nomes_disponiveis else 'nenhum'}"
        ),
    )

    # Arquivo único
    parser.add_argument(
        "--mid",
        metavar="ARQUIVO.mid",
        help="Processa apenas este arquivo MIDI (nome sem o caminho completo).",
    )

    # Controle de progresso
    grupo_progresso = parser.add_mutually_exclusive_group()
    grupo_progresso.add_argument(
        "--continuar",
        action="store_true",
        default=True,
        help="(Padrão) Pula arquivos já concluídos.",
    )
    grupo_progresso.add_argument(
        "--reiniciar",
        action="store_true",
        help="Força o reprocessamento de todos os arquivos, mesmo os já concluídos.",
    )

    # Arpejos
    parser.add_argument(
        "--arpejo",
        action="store_true",
        help="Ativa arpejos na trilha de som mais grave (detectada automaticamente).",
    )
    parser.add_argument(
        "--estilo-arpejo",
        choices=ESTILOS_ARPEJO,
        default="ascendente",
        metavar="ESTILO",
        help=f"Estilo do arpejo: {', '.join(ESTILOS_ARPEJO)}. Padrão: ascendente",
    )

    # Instrumento GM (sobrescreve os program_change do MIDI)
    nomes_gm = ", ".join(sorted(GM_PATCHES.keys()))
    parser.add_argument(
        "--instrumento",
        metavar="NOME",
        help=(
            f"Força um instrumento GM em todas as trilhas. Opções: {nomes_gm}. "
            "Use junto com --soundfonts para ver efeitos."
        ),
    )
    parser.add_argument(
        "--patch-numero",
        type=int,
        metavar="0-127",
        help="Força um número de patch GM diretamente (0=Piano, 9=Caixa de Música, etc.). "
             "Sobreposto por --instrumento se ambos informados.",
    )

    # Desincronismo de trilhas
    parser.add_argument(
        "--desincronismo",
        action="store_true",
        help=(
            "Ativa o desincronismo progressivo entre trilhas. "
            "Trilha 0 fica no tempo 0; cada trilha seguinte é atrasada "
            "em --delay-faixa segundos a mais que a anterior."
        ),
    )
    parser.add_argument(
        "--delay-faixa",
        type=float,
        default=0.1,
        metavar="SEGUNDOS",
        help="Atraso em segundos por faixa no modo --desincronismo. Padrão: 0.1",
    )

    # Velocidade de reprodução
    parser.add_argument(
        "--velocidade",
        type=float,
        default=100.0,
        metavar="PORCENTAGEM",
        help=(
            "Ajusta a velocidade de reprodução em %%. "
            "100 = original | 80 = 20%% mais lento | 150 = 50%% mais rápido. "
            "Intervalo válido: 10–1000. Padrão: 100"
        ),
    )

    # Transposicao de semitons
    parser.add_argument(
        "--semitons",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Transpoe todas as notas N semitons para cima (positivo) ou para baixo (negativo). "
            "Ex: --semitons 12 = uma oitava acima | --semitons -5 = 5 semitons abaixo. "
            "Percussao (canal 9) nao e afetada. Padrao: 0"
        ),
    )

    parser.add_argument(
        "--soundfonts",
        action="store_true",
        help="Lista todos os SoundFonts disponíveis em soundfonts/ e encerra.",
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="Lista todos os MIDIs com status do banco e encerra.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Exibe resumo do banco de progresso e encerra.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula toda a execução sem gerar nenhum arquivo.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Saída mais detalhada (nível DEBUG).",
    )

    parser.add_argument(
        "--opcoes",
        action="store_true",
        help="Exibe referência completa de todos os parâmetros e valores possíveis.",
    )

    return parser


def main():
    # Atalho: `python renderizador.py ?` ou sem argumentos → mostra opções
    import sys as _sys
    if len(_sys.argv) == 1 or (len(_sys.argv) == 2 and _sys.argv[1] == "?"):
        cmd_opcoes()
        return

    parser = construir_parser()
    args = parser.parse_args()

    log = configurar_log(args.verbose)

    # ── Abrir banco ────────────────────────────────────────────────────────
    conn = abrir_banco()

    # ── Comandos informativos ────────────────────────────────────────────
    if getattr(args, "opcoes", False):
        cmd_opcoes()
        conn.close()
        return

    if args.soundfonts:
        cmd_soundfonts(log)
        conn.close()
        return

    if args.listar:
        cmd_listar(conn, args.formato, log)
        conn.close()
        return

    if args.status:
        cmd_status(conn, args.formato if not args.reiniciar else None, log)
        conn.close()
        return

    # ── Validar soundfont ────────────────────────────────────────────
    # Recarrega para garantir estado fresco (ex: usuário adicionou sf2 após iniciar)
    soundfonts_atuais = descobrir_soundfonts()
    if args.formato is None:
        log.error(
            "Nenhum SoundFont encontrado em: %s\n"
            "Baixe um .sf2 e coloque lá. Veja: https://www.generaluser.us/\n"
            "Depois rode: python renderizador.py --soundfonts",
            SOUNDFONTS_DIR,
        )
        sys.exit(1)
    if args.formato not in soundfonts_atuais:
        log.error(
            "SoundFont '%s' não encontrado.\n"
            "Rode  python renderizador.py --soundfonts  para ver as opções.",
            args.formato,
        )
        sys.exit(1)
    caminho_sf2 = soundfonts_atuais[args.formato]
    if not args.dry_run and not caminho_sf2.exists():
        log.error(
            "SoundFont não encontrado: %s\n"
            "Baixe um arquivo .sf2 e coloque em: %s\n"
            "Sugestão: https://www.generaluser.us/",
            caminho_sf2,
            SOUNDFONTS_DIR,
        )
        sys.exit(1)
    elif args.dry_run and not caminho_sf2.exists():
        log.warning("(dry-run) SoundFont ausente: %s", caminho_sf2)

    # ── Resolver lista de MIDIs ────────────────────────────────────────────
    if args.mid:
        caminho_especifico = MID_DIR / args.mid
        if not caminho_especifico.exists():
            log.error("Arquivo não encontrado: %s", caminho_especifico)
            sys.exit(1)
        midis = [caminho_especifico]
    else:
        midis = listar_midis()
        if not midis:
            log.error("Nenhum arquivo .mid encontrado em: %s", MID_DIR)
            sys.exit(1)

    # ── Resolver instrumento GM ────────────────────────────────────────────
    patch_gm: int | None = None
    if args.instrumento:
        nome = args.instrumento.lower()
        if nome not in GM_PATCHES:
            log.error(
                "Instrumento '%s' desconhecido. Opções: %s",
                args.instrumento, ", ".join(sorted(GM_PATCHES.keys()))
            )
            sys.exit(1)
        patch_gm = GM_PATCHES[nome]
        log.info("Instrumento: %s (patch GM %d)", nome, patch_gm)
    elif args.patch_numero is not None:
        patch_gm = max(0, min(127, args.patch_numero))
        log.info("Patch GM manual: %d", patch_gm)

    log.info("mid2mp3 | Formato: %s | Arquivos: %d | Arpejo: %s%s | Desincronismo: %s | Instrumento: %s | Velocidade: %.0f%%",
             args.formato,
             len(midis),
             "sim" if args.arpejo else "não",
             f" ({args.estilo_arpejo})" if args.arpejo else "",
             f"{args.delay_faixa}s/faixa" if args.desincronismo else "não",
             f"patch {patch_gm}" if patch_gm is not None else "original",
             args.velocidade)

    if args.dry_run:
        log.info("⚠  Modo DRY-RUN ativado — nenhum arquivo será gerado.")

    # ── Processar ──────────────────────────────────────────────────────────
    cmd_processar(
        conn=conn,
        midis=midis,
        formato=args.formato,
        caminho_sf2=caminho_sf2,
        dry_run=args.dry_run,
        usar_arpejo=args.arpejo,
        estilo_arpejo=args.estilo_arpejo,
        delay_desincronismo=args.delay_faixa if args.desincronismo else 0.0,
        patch_gm=patch_gm,
        velocidade=args.velocidade,
        semitons=args.semitons,
        reiniciar=args.reiniciar,
        log=log,
    )

    conn.close()


if __name__ == "__main__":
    main()