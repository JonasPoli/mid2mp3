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
    python renderizador.py --formato metais --arpejo --estilo-arpejo sacro
    python renderizador.py --reiniciar --formato orquestra
"""

import argparse
import logging
import os
import re
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
            if sf2.name.startswith("."):
                continue
            nome_cli = sf2.stem.replace(" ", "_").replace("-", "_")
            sf2s[nome_cli] = sf2
    return sf2s


# Carrega uma vez ao iniciar — reúsado em todo o script
SOUNDFONTS: dict[str, Path] = descobrir_soundfonts()

ESTILOS_ARPEJO = ["ascendente", "descendente", "alternado", "sacro"]


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

def aplicar_arpejo_sacro(
    midi_in: mido.MidiFile,
    patch_gm: int | None = None,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Gera uma trilha de arpejo sacro inteligente baseada nas quatro vozes (SATB).
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  ♫  Iniciando geração de Arpejo Sacro Inteligente (SATB)...")

    # 1. Identificar trilhas com notas
    trilhas_com_notas = []
    for idx, t in enumerate(midi_in.tracks):
        contem_notas = any(m.type == "note_on" and m.velocity > 0 for m in t)
        if contem_notas:
            trilhas_com_notas.append((idx, t))

    if not trilhas_com_notas:
        log.warning("Nenhuma trilha com notas encontrada para o arpejo sacro.")
        return midi_in

    # Classificar as trilhas por tom (do mais agudo ao mais grave)
    def mediana_notas(t: mido.MidiTrack) -> float:
        ns = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if not ns:
            return 60.0
        ns.sort()
        return ns[len(ns) // 2]

    trilhas_ordenadas = sorted(trilhas_com_notas, key=lambda x: mediana_notas(x[1]), reverse=True)
    num_vozes = len(trilhas_ordenadas)
    log.info("  ♩  Vozes detectadas para arpejo sacro: %d", num_vozes)

    # Mapeamento SATB robusto
    if num_vozes == 1:
        s_track = c_track = t_track = b_track = trilhas_ordenadas[0][1]
    elif num_vozes == 2:
        s_track = c_track = trilhas_ordenadas[0][1]
        t_track = b_track = trilhas_ordenadas[1][1]
    elif num_vozes == 3:
        s_track = trilhas_ordenadas[0][1]
        c_track = t_track = trilhas_ordenadas[1][1]
        b_track = trilhas_ordenadas[2][1]
    else:
        s_track = trilhas_ordenadas[0][1]
        c_track = trilhas_ordenadas[1][1]
        t_track = trilhas_ordenadas[2][1]
        b_track = trilhas_ordenadas[-1][1]

    # Obter notas absolutas de cada voz
    def obter_notas_absolutas(track: mido.MidiTrack) -> list[dict]:
        notas = []
        notas_ativas = {}  # note -> (start_tick, velocity)
        tick_atual = 0
        for msg in track:
            tick_atual += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = (tick_atual, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    start_tick, velocity = notas_ativas.pop(msg.note)
                    notas.append({
                        "note": msg.note,
                        "start": start_tick,
                        "end": tick_atual,
                        "velocity": velocity
                    })
        for note, (start_tick, velocity) in notas_ativas.items():
            notas.append({
                "note": note,
                "start": start_tick,
                "end": tick_atual,
                "velocity": velocity
            })
        return sorted(notas, key=lambda x: x["start"])

    soprano_notas = obter_notas_absolutas(s_track)
    contralto_notas = obter_notas_absolutas(c_track)
    tenor_notas = obter_notas_absolutas(t_track)
    baixo_notas = obter_notas_absolutas(b_track)

    # 2. Descobrir canal MIDI livre
    canais_usados: set[int] = set()
    for trilha in midi_in.tracks:
        for msg in trilha:
            if hasattr(msg, "channel"):
                canais_usados.add(msg.channel)
    canal_livre = next(
        (c for c in range(16) if c != 9 and c not in canais_usados),
        15,
    )

    # 3. Mapear time signature changes
    ts_changes: list[tuple[int, int, int]] = []
    for trilha in midi_in.tracks:
        tick = 0
        for msg in trilha:
            tick += msg.time
            if msg.type == "time_signature":
                ts_changes.append((tick, msg.numerator, msg.denominator))
    ts_changes.sort(key=lambda x: x[0])
    if not ts_changes or ts_changes[0][0] > 0:
        ts_changes.insert(0, (0, 4, 4))

    def ts_em_tick(tick: int) -> tuple[int, int]:
        lo, hi = 0, len(ts_changes) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if ts_changes[mid][0] <= tick:
                lo = mid
            else:
                hi = mid - 1
        return ts_changes[lo][1], ts_changes[lo][2]

    # 4. Calcular limites de compassos
    ticks_por_beat = midi_in.ticks_per_beat
    max_tick = 0
    for track in midi_in.tracks:
        t = 0
        for msg in track:
            t += msg.time
        if t > max_tick:
            max_tick = t

    limites_compassos = []
    tick_atual = 0
    while tick_atual < max_tick:
        num, den = ts_em_tick(tick_atual)
        len_measure = int(ticks_por_beat * 4 * num / den)
        if len_measure <= 0:
            len_measure = ticks_por_beat * 4
        limites_compassos.append((tick_atual, tick_atual + len_measure, num, den))
        tick_atual += len_measure

    # 5. Helpers para buscar notas das vozes
    def obter_nota_voz_no_tick(vozes_notas: list[dict], tick: int, m_start: int, m_end: int) -> int | None:
        active = [n for n in vozes_notas if n["start"] <= tick < n["end"]]
        if active:
            return active[0]["note"]
        in_measure = [n for n in vozes_notas if m_start <= n["start"] < m_end]
        if in_measure:
            in_measure.sort(key=lambda x: abs(x["start"] - tick))
            return in_measure[0]["note"]
        active_start = [n for n in vozes_notas if n["start"] <= m_start < n["end"]]
        if active_start:
            return active_start[0]["note"]
        if vozes_notas:
            vozes_notas_sorted = sorted(vozes_notas, key=lambda x: abs(x["start"] - tick))
            return vozes_notas_sorted[0]["note"]
        return None

    def obter_primeira_nota_do_baixo(baixo_notas: list[dict], m_start: int, m_end: int) -> int | None:
        in_measure = [n for n in baixo_notas if m_start <= n["start"] < m_end]
        if in_measure:
            in_measure.sort(key=lambda x: x["start"])
            return in_measure[0]["note"]
        active_at_start = [n for n in baixo_notas if n["start"] <= m_start < n["end"]]
        if active_at_start:
            return active_at_start[0]["note"]
        before = [n for n in baixo_notas if n["end"] <= m_start]
        if before:
            before.sort(key=lambda x: x["end"], reverse=True)
            return before[0]["note"]
        if baixo_notas:
            return baixo_notas[0]["note"]
        return None

    # 6. Gerar eventos
    novos_eventos = []
    import random
    rng = random.Random(42)

    for idx_m, (m_start, m_end, num, den) in enumerate(limites_compassos):
        # Lift pedal (0) e press pedal (127) para humanizar sustain por compasso
        novos_eventos.append((m_start, mido.Message("control_change", channel=canal_livre, control=64, value=0, time=0)))
        novos_eventos.append((m_start + 5, mido.Message("control_change", channel=canal_livre, control=64, value=127, time=0)))

        D = int(num * (8 / den))
        if D <= 0:
            D = 8
        duracao_nota = int((m_end - m_start) / D)
        if duracao_nota <= 0:
            duracao_nota = 1

        primeira_nota_baixo = obter_primeira_nota_do_baixo(baixo_notas, m_start, m_end)

        # Escolher padrão de vozes
        if D == 8:
            pattern = ['B', 'T', 'A', 'T', 'S', 'T', 'A', 'T']
        elif D == 6:
            pattern = ['B', 'T', 'A', 'S', 'A', 'T']
        elif D == 4:
            pattern = ['B', 'T', 'A', 'T']
        else:
            base = ['B', 'T', 'A', 'S', 'A', 'T']
            pattern = [base[idx % len(base)] for idx in range(D)]

        for i in range(D):
            t_on = m_start + i * duracao_nota
            t_off = t_on + duracao_nota

            role = pattern[i]

            # Buscar notas correspondentes no tick
            note_B = obter_nota_voz_no_tick(baixo_notas, t_on, m_start, m_end)
            note_T = obter_nota_voz_no_tick(tenor_notas, t_on, m_start, m_end)
            note_A = obter_nota_voz_no_tick(contralto_notas, t_on, m_start, m_end)
            note_S = obter_nota_voz_no_tick(soprano_notas, t_on, m_start, m_end)

            if note_B is None: note_B = 48
            if note_T is None: note_T = note_B + 12
            if note_A is None: note_A = note_T + 4
            if note_S is None: note_S = note_A + 5

            # Mapeia nota
            if role == 'B':
                if i == 0 and primeira_nota_baixo is not None:
                    note = primeira_nota_baixo
                else:
                    note = note_B
            elif role == 'T':
                note = note_T
            elif role == 'A':
                note = note_A
            elif role == 'S':
                note = note_S

            # Dinâmica (velocity)
            if role == 'B':
                vel = rng.randint(55, 75)
            elif role == 'T':
                vel = rng.randint(35, 55)
            elif role == 'A':
                vel = rng.randint(35, 55)
            elif role == 'S':
                vel = rng.randint(30, 45)

            # Transição suave no final do compasso
            if i == D - 1 and idx_m + 1 < len(limites_compassos):
                next_m_start, next_m_end = limites_compassos[idx_m + 1][:2]
                next_bass = obter_primeira_nota_do_baixo(baixo_notas, next_m_start, next_m_end)
                if next_bass is not None:
                    best_note = note
                    min_diff = abs(note - next_bass)
                    for octave_shift in [-24, -12, 12, 24]:
                        shifted = note + octave_shift
                        if 0 <= shifted <= 127:
                            diff = abs(shifted - next_bass)
                            if diff < min_diff:
                                min_diff = diff
                                best_note = shifted
                    note = best_note

            novos_eventos.append((t_on, mido.Message("note_on", channel=canal_livre, note=note, velocity=vel, time=0)))
            novos_eventos.append((t_off, mido.Message("note_off", channel=canal_livre, note=note, velocity=0, time=0)))

    novos_eventos.sort(key=lambda x: x[0])
    nova_trilha = mido.MidiTrack()
    nova_trilha.name = "Arpejo Sacro Inteligente"

    # Definir instrumento (patch)
    if patch_gm is not None:
        program = patch_gm
    else:
        program = 0
        for msg in b_track:
            if msg.type == "program_change":
                program = msg.program
                break

    nova_trilha.append(
        mido.Message("program_change", channel=canal_livre, program=program, time=0)
    )

    tick_prev = 0
    for tick_abs, msg in novos_eventos:
        delta = tick_abs - tick_prev
        nova_trilha.append(msg.copy(time=delta))
        tick_prev = tick_abs

    nova_trilha.append(mido.MetaMessage("end_of_track", time=0))

    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)
    for trilha in midi_in.tracks:
        novo_midi.tracks.append(trilha)
    novo_midi.tracks.append(nova_trilha)

    log.info(
        "  ✔  Trilha de arpejo sacro adicionada (canal %d, patch %d)",
        canal_livre,
        program
    )
    return novo_midi

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

    if estilo == "sacro":
        return aplicar_arpejo_sacro(midi_in, patch_gm, log)

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



def aplicar_arranjo(
    midi_in: mido.MidiFile,
    tipo: str,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Cria um arranjo específico duplicando e distribuindo as vozes
    em famílias de instrumentos de acordo com o tipo escolhido.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  🎻  Aplicando arranjo estilo '%s'...", tipo)

    # 1. Identificar quais trilhas possuem notas de fato
    trilhas_com_notas = []
    for idx, t in enumerate(midi_in.tracks):
        contem_notas = any(m.type == "note_on" and m.velocity > 0 for m in t)
        if contem_notas:
            trilhas_com_notas.append((idx, t))

    if not trilhas_com_notas:
        log.warning("Nenhuma trilha com notas para arranjar.")
        return midi_in

    # Classificar as trilhas por tom (do mais agudo ao mais grave)
    def mediana_notas(t: mido.MidiTrack) -> float:
        ns = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if not ns:
            return 60.0
        ns.sort()
        return ns[len(ns) // 2]

    trilhas_ordenadas = sorted(trilhas_com_notas, key=lambda x: mediana_notas(x[1]), reverse=True)
    num_vozes = len(trilhas_ordenadas)
    log.info("  Vozes detectadas para arranjo: %d", num_vozes)

    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)

    # Mantém a trilha 0 (tempo, assinaturas, compassos, etc.)
    if len(midi_in.tracks) > 0:
        trilha_tempo = mido.MidiTrack()
        for msg in midi_in.tracks[0]:
            if msg.type not in ("note_on", "note_off"):
                trilha_tempo.append(msg.copy())
        novo_midi.tracks.append(trilha_tempo)

    # Definir as configurações de instrumentos para cada tipo de arranjo
    config_arranjos = {
        "cordas": {
            "camadas": [
                {"patch": 40, "vol": 1.0, "nome": "Violino"},      # Soprano
                {"patch": 40, "vol": 0.95, "nome": "Violino II"},  # Alto
                {"patch": 41, "vol": 0.9, "nome": "Viola"},        # Tenor
                {"patch": 42, "vol": 0.85, "nome": "Cello"},       # Baixo
            ]
        },
        "metais": {
            "camadas": [
                {"patch": 56, "vol": 0.7, "nome": "Trompete"},
                {"patch": 60, "vol": 0.75, "nome": "Trompa"},
                {"patch": 57, "vol": 0.7, "nome": "Trombone"},
                {"patch": 58, "vol": 0.65, "nome": "Tuba"},
            ]
        },
        "orgaos": {
            "camadas": [
                {"patch": 19, "vol": 0.85, "nome": "Órgão de Igreja"},
                {"patch": 20, "vol": 0.85, "nome": "Harmônio/Reed"},
                {"patch": 16, "vol": 0.75, "nome": "Órgão Drawbar"},
                {"patch": 18, "vol": 0.7, "nome": "Órgão de Rock"},
            ]
        },
        "pianos": {
            "camadas": [
                {"patch": 0, "vol": 0.8, "nome": "Piano de Cauda"},
                {"patch": 1, "vol": 0.75, "nome": "Piano Brilhante"},
                {"patch": 4, "vol": 0.7, "nome": "Piano Elétrico 1"},
                {"patch": 5, "vol": 0.65, "nome": "Piano Elétrico 2"},
            ]
        },
        "classico_1": {
            "camadas": [
                {"patch": 73, "vol": 0.8, "nome": "Flauta"},
                {"patch": 68, "vol": 0.8, "nome": "Oboé"},
                {"patch": 46, "vol": 0.75, "nome": "Harpa"},
                {"patch": 42, "vol": 0.7, "nome": "Violoncelo"},
            ]
        },
        "sintetizado": {
            "camadas": [
                {"patch": 80, "vol": 0.75, "nome": "Square Lead"},
                {"patch": 62, "vol": 0.7, "nome": "Synth Brass 1"},
                {"patch": 50, "vol": 0.75, "nome": "Synth Strings 1"},
                {"patch": 38, "vol": 0.7, "nome": "Synth Bass 1"},
            ]
        },
        "combinacao_3": {
            "camadas": [
                {"patch": 71, "vol": 0.85, "nome": "Clarinete"},
                {"patch": 45, "vol": 0.8, "nome": "Pizzicato Strings"},
                {"patch": 69, "vol": 0.8, "nome": "Corne Inglês"},
                {"patch": 70, "vol": 0.75, "nome": "Fagote"},
            ]
        },
        "combinacao_4": {
            "camadas": [
                {"patch": 8, "vol": 0.85, "nome": "Celesta"},
                {"patch": 52, "vol": 0.8, "nome": "Coro Aahs"},
                {"patch": 54, "vol": 0.75, "nome": "Voz Sintética"},
                {"patch": 49, "vol": 0.7, "nome": "Cordas Ensemble 2"},
            ]
        },
        "combinacao_5": {
            "camadas": [
                {"patch": 21, "vol": 0.8, "nome": "Acordeão"},
                {"patch": 24, "vol": 0.8, "nome": "Violão de Nylon"},
                {"patch": 22, "vol": 0.75, "nome": "Gaita"},
                {"patch": 32, "vol": 0.7, "nome": "Baixo Acústico"},
            ]
        },
        "orgao_igreja": {
            "camadas": [
                {"patch": 19, "vol": 0.85, "nome": "Órgão de Igreja I"},
                {"patch": 19, "vol": 0.85, "nome": "Órgão de Igreja II"},
                {"patch": 19, "vol": 0.85, "nome": "Órgão de Igreja III"},
                {"patch": 19, "vol": 0.85, "nome": "Órgão de Igreja IV"},
            ]
        },
        "orgao_reed": {
            "camadas": [
                {"patch": 20, "vol": 0.85, "nome": "Harmônio I"},
                {"patch": 20, "vol": 0.85, "nome": "Harmônio II"},
                {"patch": 20, "vol": 0.85, "nome": "Harmônio III"},
                {"patch": 20, "vol": 0.85, "nome": "Harmônio IV"},
            ]
        },
        "orquestra_sacra_1": {
            "camadas": [
                {"patch": 73, "vol": 0.85, "nome": "Flauta"},
                {"patch": 71, "vol": 0.80, "nome": "Clarinete"},
                {"patch": 60, "vol": 0.75, "nome": "Trompa"},
                {"patch": 42, "vol": 0.85, "nome": "Violoncelo"},
            ]
        },
        "orquestra_sacra_2": {
            "camadas": [
                {"patch": 40, "vol": 0.85, "nome": "Violino"},
                {"patch": 68, "vol": 0.80, "nome": "Oboé"},
                {"patch": 41, "vol": 0.80, "nome": "Viola"},
                {"patch": 70, "vol": 0.85, "nome": "Fagote"},
            ]
        },
        "orquestra_suave": {
            "camadas": [
                {"patch": 68, "vol": 0.75, "nome": "Oboé"},
                {"patch": 71, "vol": 0.75, "nome": "Clarinete"},
                {"patch": 41, "vol": 0.75, "nome": "Viola"},
                {"patch": 42, "vol": 0.80, "nome": "Violoncelo"},
            ]
        }
    }

    # Tratamento especial para orquestra completa (contendo tudo acima)
    if tipo == "orquestra_completa":
        familias_orq = {
            "cordas":   [48, 48, 48, 48],  # String Ensemble
            "metais":   [56, 60, 57, 58],  # Trumpet, Horn, Trombone, Tuba
            "paletas":  [73, 68, 71, 70],  # Flute, Oboe, Clarinet, Bassoon
            "piano":    [0, 0, 0, 0],      # Piano
            "orgao":    [19, 19, 19, 19],  # Church Organ
        }
        
        canais_disponiveis = [c for c in range(16) if c != 9]
        ch_idx = 0
        
        for idx_pos, (idx_orig, trilha_orig) in enumerate(trilhas_ordenadas):
            for nome_fam, patches in familias_orq.items():
                patch = patches[idx_pos % len(patches)]
                canal = canais_disponiveis[ch_idx % len(canais_disponiveis)]
                ch_idx += 1
                
                vol_mult = 0.55 if nome_fam in ("metais", "piano") else 0.7
                nome_trilha = f"Voz {idx_pos+1} - {nome_fam.capitalize()}"
                
                trilha_gerada = _criar_trilha_orquestrada(
                    trilha_orig, canal, patch, nome_trilha, volume_mult=vol_mult
                )
                novo_midi.tracks.append(trilha_gerada)
        return novo_midi

    if tipo not in config_arranjos:
        log.warning("Tipo de arranjo '%s' desconhecido. Ignorando.", tipo)
        return midi_in

    camadas = config_arranjos[tipo]["camadas"]
    canais_disponiveis = [c for c in range(16) if c != 9]

    for idx_pos, (idx_orig, trilha_orig) in enumerate(trilhas_ordenadas):
        config_camada = camadas[idx_pos % len(camadas)]
        canal = canais_disponiveis[idx_pos % len(canais_disponiveis)]
        
        trilha_gerada = _criar_trilha_orquestrada(
            trilha_orig,
            canal,
            config_camada["patch"],
            f"Voz {idx_pos+1} - {config_camada['nome']}",
            volume_mult=config_camada["vol"]
        )
        novo_midi.tracks.append(trilha_gerada)

    return novo_midi


def _criar_trilha_orquestrada(
    trilha_orig: mido.MidiTrack,
    canal: int,
    patch: int,
    nome: str,
    volume_mult: float = 1.0,
) -> mido.MidiTrack:
    nova_trilha = mido.MidiTrack()
    nova_trilha.name = nome
    nova_trilha.append(mido.Message("program_change", channel=canal, program=patch, time=0))
    
    for msg in trilha_orig:
        if msg.type in ("note_on", "note_off"):
            vel = msg.velocity
            if msg.type == "note_on" and vel > 0:
                vel = max(1, min(127, int(vel * volume_mult)))
            nova_trilha.append(msg.copy(channel=canal, velocity=vel))
        elif msg.type == "program_change":
            pass
        elif not msg.is_meta:
            if hasattr(msg, "channel"):
                nova_trilha.append(msg.copy(channel=canal))
            else:
                nova_trilha.append(msg.copy())
        else:
            if msg.type == "end_of_track":
                nova_trilha.append(msg.copy())
                
    return nova_trilha



def aplicar_humanizar_cordas(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica humanização (micro-dinâmicas de velocity e articulação de legato)
    nas notas da trilha para reduzir a sensação robótica do MIDI.
    """
    import random
    if log is None:
        log = logging.getLogger("mid2mp3")
    log.info("  🎻  Humanizando articulação de cordas (legato e micro-dinâmicas)...")
    
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    rng = random.Random(42)  # Semente fixa para ser determinístico/reproduzível
    
    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (tempo) e trilha de arpejo -> sempre intocadas
        eh_arpejo = "_arpejo" in (trilha.name or "").lower()
        if idx_trilha == 0 or eh_arpejo:
            novo_midi.tracks.append(trilha)
            continue
            
        # 1. Converter eventos da trilha para ticks absolutos
        eventos_abs = []
        tick = 0
        for msg in trilha:
            tick += msg.time
            eventos_abs.append([tick, msg])
            
        # 2. Aplicar micro-dinâmicas (variação de velocity)
        for ev in eventos_abs:
            msg = ev[1]
            if msg.type == "note_on" and msg.velocity > 0:
                var = rng.randint(-7, 7)
                msg.velocity = max(1, min(127, msg.velocity + var))
                
        # 3. Aplicar Legato (sobreposição suave de notas consecutivas no mesmo canal)
        por_canal = {}
        for i, ev in enumerate(eventos_abs):
            msg = ev[1]
            if hasattr(msg, "channel") and msg.channel != 9:  # Ignora percussão
                por_canal.setdefault(msg.channel, []).append(i)
                
        for canal, indices in por_canal.items():
            notas_ativas = {}
            for idx_ev in indices:
                tick_abs, msg = eventos_abs[idx_ev]
                if msg.type == "note_on" and msg.velocity > 0:
                    notas_ativas[msg.note] = (tick_abs, idx_ev)
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    if msg.note in notas_ativas:
                        t_on, idx_on = notas_ativas.pop(msg.note)
                        duracao = tick_abs - t_on
                        
                        # Encontrar se existe uma próxima nota iniciando em breve neste canal
                        proxima_nota = None
                        for idx_prox in indices:
                            if idx_prox > idx_ev:
                                t_prox, msg_prox = eventos_abs[idx_prox]
                                if msg_prox.type == "note_on" and msg_prox.velocity > 0:
                                    proxima_nota = (t_prox, msg_prox)
                                    break
                        
                        if proxima_nota:
                            t_prox, msg_prox = proxima_nota
                            distancia = t_prox - tick_abs
                            
                            # Se a distância entre o fim desta nota e o início da próxima for menor que 30 ticks
                            if distancia <= 30:
                                # Legato: estende a nota atual em 15% da sua duração (máximo de 40 ticks)
                                extensao = max(5, min(40, int(duracao * 0.15)))
                                ev[0] = t_prox + extensao
                                
        # 4. Reordenar e converter de volta para delta-time
        eventos_abs.sort(key=lambda e: e[0])
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        
        tick_prev = 0
        for tick_abs, msg in eventos_abs:
            delta = max(0, tick_abs - tick_prev)
            nova_trilha.append(msg.copy(time=delta))
            tick_prev = tick_abs
            
        novo_midi.tracks.append(nova_trilha)
        
    return novo_midi


def aplicar_pedal_sustain(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Simula um pianista usando o pedal de sustain (CC 64).
    Para cada canal ativo:
    - Detecta o início de novos acordes/notas.
    - Libera o pedal (CC 64 = 0) no início do acorde para limpar a ressonância.
    - Pressiona o pedal (CC 64 = 127) logo após o ataque das notas para sustentar a harmonia.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  🎹  [Equinox Pianos] Gerando automação inteligente de pedal de sustain (CC 64)...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Ignora trilha 0 e percussão
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        # 1. Converter eventos da trilha para ticks absolutos
        eventos_abs = []
        tick_atual = 0
        for msg in trilha:
            tick_atual += msg.time
            eventos_abs.append([tick_atual, msg])

        # 2. Encontrar todos os ticks onde novas notas iniciam por canal
        notas_por_canal = {}
        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                ch = getattr(msg, "channel", 0)
                if ch != 9:
                    notas_por_canal.setdefault(ch, []).append(tick_abs)

        pedais_para_inserir = []

        for ch, ticks in notas_por_canal.items():
            if not ticks:
                continue
            
            # Agrupar ticks próximos (tolerância de 20 ticks) para identificar início de acordes
            ticks.sort()
            inicio_acordes = []
            ultimo_tick = -9999
            for t in ticks:
                if t - ultimo_tick > 20:
                    inicio_acordes.append(t)
                    ultimo_tick = t

            # Para cada início de acorde, aplicar o pedal
            for i, t_acorde in enumerate(inicio_acordes):
                # Libera o pedal no início do acorde (limpa a ressonância anterior)
                pedais_para_inserir.append((t_acorde, mido.Message("control_change", channel=ch, control=64, value=0, time=0)))
                
                # Pressiona o pedal logo em seguida (atraso de 40 ticks ou 10% da nota)
                t_press = t_acorde + 40
                
                # Evita passar do próximo acorde
                if i + 1 < len(inicio_acordes):
                    t_prox = inicio_acordes[i + 1]
                    if t_press >= t_prox:
                        t_press = t_acorde + (t_prox - t_acorde) // 2

                pedais_para_inserir.append((t_press, mido.Message("control_change", channel=ch, control=64, value=127, time=0)))

            # Zera o pedal no final da trilha
            if inicio_acordes:
                t_final = eventos_abs[-1][0]
                pedais_para_inserir.append((t_final, mido.Message("control_change", channel=ch, control=64, value=0, time=0)))

        if pedais_para_inserir:
            # 3. Adicionar as mensagens de pedal
            for tick_abs, msg in pedais_para_inserir:
                eventos_abs.append([tick_abs, msg])

            # 4. Reordenar e converter de volta para delta-time
            eventos_abs.sort(key=lambda x: x[0])
            nova_trilha = mido.MidiTrack()
            nova_trilha.name = trilha.name
            
            tick_prev = 0
            for tick_abs, msg in eventos_abs:
                delta = max(0, tick_abs - tick_prev)
                nova_trilha.append(msg.copy(time=delta))
                tick_prev = tick_abs
                
            novo_midi.tracks.append(nova_trilha)
        else:
            novo_midi.tracks.append(trilha)

    return novo_midi


def aplicar_humanizacao_voicing_e_roll(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica Voicing (melodia mais alta, vozes internas mais suaves) e Micro-Roll (pequeno atraso
    progressivo entre as notas de um mesmo acorde) para simular o toque de um pianista.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    def mapear_velocidade(vel_original: int, v_min: int, v_max: int) -> int:
        scale = (v_max - v_min) / 126.0
        return int(v_min + (vel_original - 1) * scale)

    log.info("  🎹  [Equinox Pianos] Humanizando dinâmica de vozes (voicing) e micro-atraso de acordes (roll)...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    import random
    rng = random.Random(42)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Ignora trilha 0 e arpejo
        eh_arpejo = "_arpejo" in (trilha.name or "").lower()
        if idx_trilha == 0 or eh_arpejo:
            novo_midi.tracks.append(trilha)
            continue

        # 1. Converter eventos da trilha para ticks absolutos
        eventos_abs = []
        tick_atual = 0
        for msg in trilha:
            tick_atual += msg.time
            eventos_abs.append([tick_atual, msg])

        # 2. Agrupar as notas que iniciam aproximadamente no mesmo tick (acordes)
        notas_on_ativas = {}
        acordes = {} # tick_abs -> list of indices in eventos_abs for note_on

        for idx, (tick_abs, msg) in enumerate(eventos_abs):
            if msg.type == "note_on" and msg.velocity > 0:
                # Agrupa no tick aproximado (tolerância de 15 ticks)
                tick_grupo = next((t for t in acordes if abs(t - tick_abs) <= 15), None)
                if tick_grupo is None:
                    tick_grupo = tick_abs
                    acordes[tick_grupo] = []
                acordes[tick_grupo].append(idx)
                notas_on_ativas[msg.note] = idx

        # 3. Aplicar Voicing e Roll para cada grupo de acorde
        for tick_grupo, indices in acordes.items():
            if len(indices) < 2:
                # Nota isolada: aplica apenas variação dinâmica leve e sem roll
                idx = indices[0]
                msg = eventos_abs[idx][1]
                var = rng.randint(-5, 5)
                msg.velocity = max(1, min(127, msg.velocity + var))
                continue

            # Coletar notas e seus valores de nota para ordenar por altura
            notas_grupo = []
            for idx in indices:
                msg = eventos_abs[idx][1]
                notas_grupo.append((idx, msg.note, msg.velocity))

            # Ordena por altura da nota (da mais grave para a mais aguda)
            notas_grupo.sort(key=lambda x: x[1])

            # Aplicar Voicing de acordo com equinox.md:
            # - Soprano (mais aguda): 65 a 88
            # - Baixo (mais grave): 50 a 72
            # - Vozes internas (Contralto: 45 a 65, Tenor: 42 a 62)
            num_notas = len(notas_grupo)
            for pos, (idx, note, vel) in enumerate(notas_grupo):
                msg = eventos_abs[idx][1]
                if pos == num_notas - 1:
                    # Soprano (mais aguda)
                    nova_vel = mapear_velocidade(vel, 65, 88)
                elif pos == 0:
                    # Baixo (mais grave)
                    nova_vel = mapear_velocidade(vel, 50, 72)
                else:
                    # Vozes internas
                    if num_notas == 3:
                        # Baixo (0), Contralto (1), Soprano (2)
                        nova_vel = mapear_velocidade(vel, 45, 65)
                    elif num_notas >= 4:
                        # Baixo (0), Tenor (1), Contralto (2), Soprano (3)
                        if pos == 1:
                            nova_vel = mapear_velocidade(vel, 42, 62)
                        else:
                            nova_vel = mapear_velocidade(vel, 45, 65)
                    else:
                        nova_vel = mapear_velocidade(vel, 45, 65)
                
                # Adiciona variação humana aleatória leve (+/- 4)
                nova_vel = max(1, min(127, nova_vel + rng.randint(-4, 4)))
                msg.velocity = nova_vel

            # Aplicar Micro-Roll:
            # Atraso progressivo da nota mais grave para a mais aguda (8 ticks por nota de atraso)
            for pos, (idx, note, vel) in enumerate(notas_grupo):
                atraso = pos * 8
                if atraso > 0:
                    # Atrasar o note_on
                    eventos_abs[idx][0] += atraso
                    
                    # Atrasar o correspondente note_off para manter a duração intacta
                    msg_on = eventos_abs[idx][1]
                    # Procuramos o note_off correspondente após este note_on
                    for idx_off in range(idx + 1, len(eventos_abs)):
                        tick_off, msg_off = eventos_abs[idx_off]
                        if (msg_off.type == "note_off" or (msg_off.type == "note_on" and msg_off.velocity == 0)) and msg_off.note == msg_on.note:
                            eventos_abs[idx_off][0] += atraso
                            break

        # 4. Reordenar e converter de volta para delta-time
        eventos_abs.sort(key=lambda x: x[0])
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        
        tick_prev = 0
        for tick_abs, msg in eventos_abs:
            delta = max(0, tick_abs - tick_prev)
            nova_trilha.append(msg.copy(time=delta))
            tick_prev = tick_abs
            
        novo_midi.tracks.append(nova_trilha)

    return novo_midi


def aplicar_modelo_piano(
    midi_in: mido.MidiFile,
    modelo: str,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica o modelo de piano selecionado (Steinway ou Yamaha, padrão ou estéreo L/R)
    inserindo comandos de Bank Select (CC 0) e Program Change corretos.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    modelos = {
        "steinway":    {"bank": 0, "program": 0, "desc": "Steinway D Concert Grand"},
        "yamaha":      {"bank": 0, "program": 1, "desc": "Yamaha C7 Concert Grand"},
        "steinway_lr": {"bank": 1, "program": 0, "desc": "Steinway D (Concert Stereo L/R)"},
        "yamaha_lr":   {"bank": 1, "program": 1, "desc": "Yamaha C7 (Concert Stereo L/R)"},
    }

    if modelo not in modelos:
        log.warning("Modelo de piano '%s' desconhecido. Usando 'steinway_lr'.", modelo)
        modelo = "steinway_lr"

    config = modelos[modelo]
    log.info("  🎹  [Equinox Pianos] Selecionando modelo: %s (Banco %d, Programa %d)...", 
             config["desc"], config["bank"], config["program"])

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (tempo) -> intocada
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        tem_program_change = False
        canais_vistos = set()

        for msg in trilha:
            if hasattr(msg, "channel") and msg.channel == 9:
                nova_trilha.append(msg)
            elif msg.type == "program_change":
                ch = msg.channel
                # Insere Bank Select (CC 0) antes do program change para mudar o banco no FluidSynth
                nova_trilha.append(mido.Message("control_change", channel=ch, control=0, value=config["bank"], time=0))
                nova_trilha.append(msg.copy(program=config["program"]))
                canais_vistos.add(ch)
                tem_program_change = True
            else:
                if (
                    not tem_program_change
                    and msg.type == "note_on"
                    and hasattr(msg, "channel")
                    and msg.channel != 9
                    and msg.channel not in canais_vistos
                ):
                    ch = msg.channel
                    # Injeta Bank Select + Program Change antes da primeira nota
                    nova_trilha.append(mido.Message("control_change", channel=ch, control=0, value=config["bank"], time=0))
                    nova_trilha.append(mido.Message("program_change", channel=ch, program=config["program"], time=0))
                    canais_vistos.add(ch)
                nova_trilha.append(msg)

        novo_midi.tracks.append(nova_trilha)

    return novo_midi


def aplicar_mapeamento_aaviolin(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Mapeia os patches GM para os patches específicos do aaviolin.sf2 (0 a 7).
    Analisa a velocidade/duração das notas para escolher entre:
    - 0: Violin (Standard)
    - 1: Fast Violin (Notas rápidas)
    - 3: Slow Violin (Notas longas com vibrato natural)
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  🎻  [AAViolin] Mapeando instrumentos para articulações (Fast/Slow/Standard)...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (tempo) -> intocada
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        total_notas = 0
        notas_curtas = 0
        notas_longas = 0
        
        # Para calcular durações, precisamos acumular os delta-times
        tick_acumulado = 0
        notas_ativas = {}
        for msg in trilha:
            tick_acumulado += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = tick_acumulado
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    t_on = notas_ativas.pop(msg.note)
                    duracao = tick_acumulado - t_on
                    total_notas += 1
                    if duracao < ticks_por_beat * 0.5:
                        notas_curtas += 1
                    elif duracao > ticks_por_beat * 1.2:
                        notas_longas += 1

        # Escolher o preset do AAViolin com base na predominância
        preset_escolhido = 0 # Violin Standard
        if total_notas > 0:
            pct_curtas = notas_curtas / total_notas
            pct_longas = notas_longas / total_notas
            if pct_curtas > 0.5:
                preset_escolhido = 1 # Fast Violin
                log.debug("    Trilha %d: predominância de notas rápidas (%.1f%%) -> Fast Violin (preset 1)", idx_trilha, pct_curtas * 100)
            elif pct_longas > 0.4:
                preset_escolhido = 3 # Slow Violin
                log.debug("    Trilha %d: predominância de notas locais/longas (%.1f%%) -> Slow Violin (preset 3)", idx_trilha, pct_longas * 100)
            else:
                log.debug("    Trilha %d: misto -> Standard Violin (preset 0)", idx_trilha)
        
        # Criar nova trilha substituindo os program_changes pelo preset escolhido
        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        tem_program_change = False
        canais_vistos = set()

        for msg in trilha:
            if hasattr(msg, "channel") and msg.channel == 9:
                nova_trilha.append(msg)
            elif msg.type == "program_change":
                nova_trilha.append(msg.copy(program=preset_escolhido))
                canais_vistos.add(msg.channel)
                tem_program_change = True
            else:
                if (
                    not tem_program_change
                    and msg.type == "note_on"
                    and hasattr(msg, "channel")
                    and msg.channel != 9
                    and msg.channel not in canais_vistos
                ):
                    nova_trilha.append(
                        mido.Message("program_change", channel=msg.channel,
                                     program=preset_escolhido, time=0)
                    )
                    canais_vistos.add(msg.channel)
                nova_trilha.append(msg)

        novo_midi.tracks.append(nova_trilha)

    return novo_midi


def aplicar_vibrato_humanizado(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Insere mensagens de Control Change (CC 1 - Modulation Wheel) para criar vibrato natural
    de forma progressiva (delayed vibrato) em notas sustentadas.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  🎻  [AAViolin] Aplicando vibrato humanizado progressivo em notas sustentadas...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat
    MIN_DURACAO_VIBRATO = int(ticks_por_beat * 0.75) # Apenas notas com duração >= 3/4 de tempo

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Ignora trilha 0 e arpejo
        eh_arpejo = "_arpejo" in (trilha.name or "").lower()
        if idx_trilha == 0 or eh_arpejo:
            novo_midi.tracks.append(trilha)
            continue

        # 1. Converter eventos da trilha para ticks absolutos
        eventos_abs = []
        tick_atual = 0
        for msg in trilha:
            tick_atual += msg.time
            eventos_abs.append([tick_atual, msg])

        # 2. Identificar notas ativas e suas durações
        notas_ativas = {} # note -> (tick_on, index_on, channel)
        vibratos_para_inserir = [] # list of (tick_abs, msg)

        for i, ev in enumerate(eventos_abs):
            tick_abs, msg = ev
            if msg.type == "note_on" and msg.velocity > 0:
                ch = getattr(msg, "channel", 0)
                if ch != 9: # Ignora percussão
                    notas_ativas[msg.note] = (tick_abs, i, ch)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    t_on, idx_on, ch = notas_ativas.pop(msg.note)
                    duracao = tick_abs - t_on
                    
                    if duracao >= MIN_DURACAO_VIBRATO:
                        # Criar curvas de vibrato progressivo (Modulation Wheel CC 1)
                        t_start = t_on
                        t_end = tick_abs
                        
                        vibratos_para_inserir.append((t_start, mido.Message("control_change", channel=ch, control=1, value=0, time=0)))
                        
                        t_ramp1 = t_start + int(duracao * 0.15)
                        vibratos_para_inserir.append((t_ramp1, mido.Message("control_change", channel=ch, control=1, value=15, time=0)))
                        
                        t_ramp2 = t_start + int(duracao * 0.30)
                        vibratos_para_inserir.append((t_ramp2, mido.Message("control_change", channel=ch, control=1, value=45, time=0)))
                        
                        t_ramp3 = t_start + int(duracao * 0.45)
                        vibratos_para_inserir.append((t_ramp3, mido.Message("control_change", channel=ch, control=1, value=65, time=0)))
                        
                        t_fade = t_end - max(5, int(duracao * 0.05))
                        vibratos_para_inserir.append((t_fade, mido.Message("control_change", channel=ch, control=1, value=0, time=0)))

        if vibratos_para_inserir:
            # 3. Adicionar as mensagens de vibrato aos eventos absolutos
            for tick_abs, msg in vibratos_para_inserir:
                eventos_abs.append([tick_abs, msg])

            # 4. Reordenar todos os eventos por tick absoluto
            eventos_abs.sort(key=lambda x: x[0])

            # 5. Converter de volta para delta-time
            nova_trilha = mido.MidiTrack()
            nova_trilha.name = trilha.name
            
            tick_prev = 0
            for tick_abs, msg in eventos_abs:
                delta = max(0, tick_abs - tick_prev)
                nova_trilha.append(msg.copy(time=delta))
                tick_prev = tick_abs
                
            novo_midi.tracks.append(nova_trilha)
        else:
            novo_midi.tracks.append(trilha)

    return novo_midi


def aplicar_mapeamento_crisis(
    midi_in: mido.MidiFile,
    modelo: str,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica o mapeamento de bancos específicos do CrisisGeneralMidi301.sf2.
    - 'expressiva' (Banco 1): usa as articulações lentas/expressivas.
    - 'sinfonica' (Banco 2): usa as articulações alternativas/sinfônicas.
    - 'padrao' (Banco 0): usa o banco GM padrão.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    config_bancos = {
        "padrao": 0,
        "expressiva": 1,
        "sinfonica": 2,
    }

    if modelo not in config_bancos:
        log.warning("Modelo do Crisis '%s' desconhecido. Usando 'expressiva'.", modelo)
        modelo = "expressiva"

    target_bank = config_bancos[modelo]
    log.info("  🎹  [Crisis GM] Mapeando instrumentos para o modelo: %s (Banco %d)...", modelo, target_bank)

    # Lista de programas suportados em cada banco do Crisis
    # Banco 1 (Slow/Expressive):
    banco_1_programs = {
        14,  # Church Bell 01
        29,  # Overdriven Gtr 18
        30,  # Distorted Gtr 18
        40,  # Slow Violin
        41,  # Slow Viola
        42,  # Cello Slow
        43,  # Contrabass Slow
        48,  # String Ensemble 3
        52,  # Choir Aahs Slow
        56,  # Trumpet Slow
        57,  # Trombone Slow
        58,  # Tuba Slow
        60,  # French Horns Slow
        61,  # Brass Section Slow
        68,  # Oboe Slow
        70,  # Bassoon Slow
        71,  # Clarinet Slow
        72,  # Piccolo Slow
        75,  # Recorder Slow
        78,  # Whistle Slow
        79,  # Ocarina Slow
    }

    # Banco 2 (Alternative/Symphonic):
    banco_2_programs = {
        29,  # Overdriven 2Amp
        30,  # Distorted 2Amp
        40,  # Violin 2
        41,  # Viola 2
        42,  # Cello 2
        43,  # Contrabass 2
        52,  # Choir Aahs 2
        56,  # Trumpet 2
        57,  # Trombone 2
        58,  # Tuba 2
        60,  # French Horns 2
        61,  # Brass Section 2
        68,  # Oboe 2
        70,  # Bassoon 2
        71,  # Clarinet 2
        72,  # Piccolo 2
        73,  # Flute 2
        78,  # Whistle Solo 01
        79,  # Ocarina 2
    }

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (tempo) -> intocada
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        nova_trilha = mido.MidiTrack()
        nova_trilha.name = trilha.name
        
        # Guardamos o patch atual de cada canal para saber o que mapear
        canal_patch = {}
        
        for msg in trilha:
            if msg.type == "program_change":
                ch = msg.channel
                prog = msg.program
                canal_patch[ch] = prog
                
                # Só mudamos o banco para canais que não sejam de percussão (9)
                if ch != 9:
                    use_bank = 0
                    if target_bank == 1 and prog in banco_1_programs:
                        use_bank = 1
                    elif target_bank == 2 and prog in banco_2_programs:
                        use_bank = 2
                    
                    if use_bank > 0:
                        # Injeta Bank Select (CC 0)
                        nova_trilha.append(mido.Message("control_change", channel=ch, control=0, value=use_bank, time=0))
                
                nova_trilha.append(msg)
            else:
                if msg.type == "note_on" and hasattr(msg, "channel") and msg.channel != 9:
                    ch = msg.channel
                    if ch not in canal_patch:
                        canal_patch[ch] = 0
                nova_trilha.append(msg)

        novo_midi.tracks.append(nova_trilha)

    return novo_midi


def aplicar_expressao_sopros(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica dinâmica de expressão (CC 11) em trilhas de sopros (metais e palhetas/madeiras)
    para criar crescendos/decrescendos e dar a sensação de respiração/articulação realista.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  💨  [Crisis GM] Aplicando dinâmica de expressão realista (CC 11) em sopros...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Ignora trilha 0 e percussão
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        # 1. Converter eventos da trilha para ticks absolutos
        eventos_abs = []
        tick_atual = 0
        for msg in trilha:
            tick_atual += msg.time
            eventos_abs.append([tick_atual, msg])

        # 2. Identificar se é uma trilha de sopro e qual o canal associado
        eh_sopro = False
        canal_sopro = None
        for _, msg in eventos_abs:
            if msg.type == "program_change":
                prog = msg.program
                ch = msg.channel
                if ch != 9 and 56 <= prog <= 79:  # Brass, Reeds, Pipes
                    eh_sopro = True
                    canal_sopro = ch
                    break

        if not eh_sopro or canal_sopro is None:
            # Não é sopro, passa a trilha intacta
            novo_midi.tracks.append(trilha)
            continue

        # 3. Encontrar nota-ons e seus respectivos nota-offs para calcular durações
        notas_ativas = {} # note_number -> tick_start
        notas_duracao = [] # list of (tick_start, tick_end, duration)

        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                ch = getattr(msg, "channel", 0)
                if ch == canal_sopro:
                    notas_ativas[msg.note] = tick_abs
            elif (msg.type == "note_off") or (msg.type == "note_on" and msg.velocity == 0):
                ch = getattr(msg, "channel", 0)
                if ch == canal_sopro:
                    if msg.note in notas_ativas:
                        t_start = notas_ativas.pop(msg.note)
                        t_end = tick_abs
                        dur = t_end - t_start
                        if dur > 0:
                            notas_duracao.append((t_start, t_end, dur))

        # 4. Inserir eventos de CC 11
        eventos_cc11 = []
        limiar_ticks = int(ticks_por_beat * 0.75) # 3/4 de tempo

        for t_start, t_end, dur in notas_duracao:
            if dur >= limiar_ticks:
                # Swell de expressão:
                # - Início suave (85)
                # - Pico de sopro (120) no primeiro quarto da nota
                # - Sustentação natural (105) no terceiro quarto da nota
                # - Decaimento no final antes da liberação (75)
                eventos_cc11.append((t_start + 1, mido.Message("control_change", channel=canal_sopro, control=11, value=85, time=0)))
                eventos_cc11.append((t_start + int(dur * 0.25), mido.Message("control_change", channel=canal_sopro, control=11, value=120, time=0)))
                eventos_cc11.append((t_start + int(dur * 0.75), mido.Message("control_change", channel=canal_sopro, control=11, value=105, time=0)))
                eventos_cc11.append((t_end - int(dur * 0.05), mido.Message("control_change", channel=canal_sopro, control=11, value=75, time=0)))

        if eventos_cc11:
            t_final = eventos_abs[-1][0]
            eventos_cc11.append((t_final, mido.Message("control_change", channel=canal_sopro, control=11, value=127, time=0)))

            for tick_abs, msg in eventos_cc11:
                eventos_abs.append([tick_abs, msg])

            eventos_abs.sort(key=lambda x: (x[0], 0 if x[1].type == "control_change" else 1))
            nova_trilha = mido.MidiTrack()
            nova_trilha.name = trilha.name

            tick_prev = 0
            for tick_abs, msg in eventos_abs:
                delta = max(0, tick_abs - tick_prev)
                nova_trilha.append(msg.copy(time=delta))
                tick_prev = tick_abs

            novo_midi.tracks.append(nova_trilha)
        else:
            novo_midi.tracks.append(trilha)

    return novo_midi


def aplicar_vibrato_strings_crisis(
    midi_in: mido.MidiFile,
    log: logging.Logger | None = None,
) -> mido.MidiFile:
    """
    Aplica vibrato progressivo (CC 1 Modulation Wheel) em trilhas de cordas longas (violino, viola, cello, contrabaixo, ensemble)
    para o Crisis General MIDI, simulando a expressão dramática de uma orquestra de cordas real.
    """
    if log is None:
        log = logging.getLogger("mid2mp3")

    log.info("  🎻  [Crisis GM] Aplicando vibrato progressivo (CC 1) nas cordas...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat
    MIN_DURACAO_VIBRATO = int(ticks_por_beat * 0.75)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        eh_arpejo = "_arpejo" in (trilha.name or "").lower()
        if idx_trilha == 0 or eh_arpejo:
            novo_midi.tracks.append(trilha)
            continue

        eventos_abs = []
        tick_atual = 0
        for msg in trilha:
            tick_atual += msg.time
            eventos_abs.append([tick_atual, msg])

        eh_cordas = False
        canal_cordas = None
        for _, msg in eventos_abs:
            if msg.type == "program_change":
                prog = msg.program
                ch = msg.channel
                if ch != 9 and (40 <= prog <= 43 or prog in (48, 49)):
                    eh_cordas = True
                    canal_cordas = ch
                    break

        if not eh_cordas or canal_cordas is None:
            novo_midi.tracks.append(trilha)
            continue

        notas_ativas = {}
        vibratos_para_inserir = []

        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                if msg.channel == canal_cordas:
                    notas_ativas[msg.note] = tick_abs
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.channel == canal_cordas:
                    if msg.note in notas_ativas:
                        t_on = notas_ativas.pop(msg.note)
                        duracao = tick_abs - t_on
                        
                        if duracao >= MIN_DURACAO_VIBRATO:
                            vibratos_para_inserir.append((t_on, mido.Message("control_change", channel=canal_cordas, control=1, value=0, time=0)))
                            vibratos_para_inserir.append((t_on + int(duracao * 0.2), mido.Message("control_change", channel=canal_cordas, control=1, value=15, time=0)))
                            vibratos_para_inserir.append((t_on + int(duracao * 0.4), mido.Message("control_change", channel=canal_cordas, control=1, value=40, time=0)))
                            vibratos_para_inserir.append((t_on + int(duracao * 0.6), mido.Message("control_change", channel=canal_cordas, control=1, value=60, time=0)))
                            vibratos_para_inserir.append((tick_abs - max(5, int(duracao * 0.05)), mido.Message("control_change", channel=canal_cordas, control=1, value=0, time=0)))

        if vibratos_para_inserir:
            t_final = eventos_abs[-1][0]
            vibratos_para_inserir.append((t_final, mido.Message("control_change", channel=canal_cordas, control=1, value=0, time=0)))

            for tick_abs, msg in vibratos_para_inserir:
                eventos_abs.append([tick_abs, msg])

            eventos_abs.sort(key=lambda x: (x[0], 0 if x[1].type == "control_change" else 1))

            nova_trilha = mido.MidiTrack()
            nova_trilha.name = trilha.name
            
            tick_prev = 0
            for tick_abs, msg in eventos_abs:
                delta = max(0, tick_abs - tick_prev)
                nova_trilha.append(msg.copy(time=delta))
                tick_prev = tick_abs
                
            novo_midi.tracks.append(nova_trilha)
        else:
            novo_midi.tracks.append(trilha)

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
    "orgao":          16,   # Drawbar Organ (Hammond)
    "orgao_igreja":   19,   # Church Organ  ← CCB / coral litúrgico
    "orgao_percussao": 17,  # Percussive Organ
    "orgao_rock":     18,   # Rock Organ
    "orgao_reed":     20,   # Reed Organ (harmônio)
    "flauta":         73,
    "trompete":       56,
    "trombone":       57,
    "tuba":           58,
    "coro":           52,
    "orquestra":      48,
    "quarteto_cordas": 48,  # Mapeia para Strings/Cordas para renderizar as vozes com cordas
    "metais":         61,  # GM Brass Section
    "brass":          61,  # GM Brass Section
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
    usar_orquestra: bool = False,
    humanizar_cordas: bool = False,
    arranjo: str | None = None,
    piano_modelo: str | None = None,
    crisis_modelo: str | None = None,
) -> None:
    """
    Converte um arquivo MIDI em MP3 usando FluidSynth + FFmpeg.

    Ordem de processamento:
      1. semitons     -- transpoe notas
      2. usar_orquestra -- cria arranjo de orquestra completa
      3. velocidade   -- ajusta BPM
      4. patch_gm     -- forca instrumento GM
      5. usar_arpejo  -- adiciona trilha de arpejo
      6. desincronismo -- atrasa trilhas progressivamente
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

        # ── Arranjo de Instrumentos ────────────────────────────────────────────
        if usar_orquestra and not arranjo:
            arranjo = "orquestra_completa"

        if arranjo:
            patch_gm = None  # Ignora patch_gm para não anular o arranjo
            log.info("  🎻  Aplicando arranjo '%s' em: %s", arranjo, caminho_mid.name)
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_arr = aplicar_arranjo(midi_base, arranjo, log)
                    mid_para_converter = tmp_path / f"arr_{arranjo}_{caminho_mid.name}"
                    midi_arr.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao aplicar arranjo '%s' (%s); usando original.", arranjo, e)

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

        # ── AAViolin Specific Mappings and Vibrato ─────────────────────────
        if "aaviolin" in caminho_sf2.stem.lower():
            log.info("  🎻  [AAViolin] Detectado soundfont AAViolin. Aplicando otimizações...")
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    midi_aav = aplicar_mapeamento_aaviolin(midi_base, log)
                    midi_aav = aplicar_vibrato_humanizado(midi_aav, log)
                    mid_para_converter = tmp_path / f"aav_{caminho_mid.name}"
                    midi_aav.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao aplicar otimizações do AAViolin (%s); usando anterior.", e)

        # ── Equinox Grand Pianos Specific Mappings and humanizations ────────
        if "equinox" in caminho_sf2.stem.lower():
            log.info("  🎹  [Equinox Pianos] Detectado soundfont Equinox Grand Pianos. Aplicando humanizações...")
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    # 1. Seleciona o modelo de piano
                    midi_eq = aplicar_modelo_piano(midi_base, piano_modelo or "steinway_lr", log)
                    # 2. Aplica voicing dinâmico e micro-atraso de acordes
                    midi_eq = aplicar_humanizacao_voicing_e_roll(midi_eq, log)
                    # 3. Aplica automação inteligente do pedal de sustain
                    midi_eq = aplicar_pedal_sustain(midi_eq, log)
                    mid_para_converter = tmp_path / f"eq_{caminho_mid.name}"
                    midi_eq.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao aplicar otimizações do Equinox Pianos (%s); usando anterior.", e)

        # ── Crisis General Midi Specific Mappings and humanizations ────────
        if "crisis" in caminho_sf2.stem.lower():
            log.info("  🎹  [Crisis GM] Detectado soundfont Crisis General Midi. Aplicando humanizações...")
            if not dry_run:
                try:
                    midi_base = mido.MidiFile(str(mid_para_converter))
                    # 1. Mapeamento de canais/bancos baseado no modelo
                    midi_cri = aplicar_mapeamento_crisis(midi_base, crisis_modelo or "expressiva", log)
                    # 2. Dinâmica de sopros (expressão CC 11)
                    midi_cri = aplicar_expressao_sopros(midi_cri, log)
                    # 3. Vibrato de cordas (CC 1)
                    midi_cri = aplicar_vibrato_strings_crisis(midi_cri, log)
                    mid_para_converter = tmp_path / f"cri_{caminho_mid.name}"
                    midi_cri.save(str(mid_para_converter))
                except Exception as e:
                    log.warning("  Falha ao aplicar otimizações do Crisis GM (%s); usando anterior.", e)

        # ── FluidSynth: MIDI → WAV ─────────────────────────────────────────
        arquivo_wav = tmp_path / (caminho_mp3.stem + ".wav")
        cmd_fluid = [
            "fluidsynth",
            "-F", str(arquivo_wav),
            "-O", "float",    # float 32-bit: headroom ilimitado, evita clipping em qualquer soundfont
            "-T", "wav",
            "-g", "0.8",      # gain moderado; loudnorm normaliza o volume final de qualquer forma
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
            "-af",
            # 1) alimiter: limitador de pico transparente operando em float
            #    attack=1ms captura transientes rápidos; limit=0.891 ≈ -1 dBFS
            # 2) loudnorm: normaliza loudness para -14 LUFS (EBU R128), True Peak -1
            "alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false,"
            "loudnorm=I=-14:TP=-1:LRA=11",
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
        "sacro"      : "quatro vozes (SATB) inteligente (padrão)",
        "ascendente" : "grave → agudo",
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
    print(f"│    --estilo-arpejo sacro \\")              # sacro|ascendente|descendente|alternado
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
    usar_orquestra: bool = False,
    humanizar_cordas: bool = False,
    arranjo: str | None = None,
    piano_modelo: str | None = None,
    crisis_modelo: str | None = None,
):
    if reiniciar:
        log.info("⚠  Reiniciando progresso para o formato '%s'…", formato)
        reiniciar_banco(conn, formato)

    if usar_orquestra and not arranjo:
        arranjo = "orquestra_completa"

    sufixo_arranjo = f"_arranjo_{arranjo}" if arranjo else ""
    sufixo_humanizar = "_humanizado" if humanizar_cordas else ""
    sufixo_arpejo = f"_arpejo_{estilo_arpejo}" if usar_arpejo else ""
    sufixo_desync = f"_desync{delay_desincronismo:.2f}s".replace('.', '') if delay_desincronismo > 0 else ""
    sufixo_patch  = f"_patch{patch_gm}" if patch_gm is not None else ""
    sufixo_vel    = f"_vel{int(velocidade)}pct" if velocidade != 100.0 else ""
    sufixo_semi   = f"_semi{semitons:+d}" if semitons != 0 else ""
    sufixo_piano  = f"_piano_{piano_modelo}" if (piano_modelo and "equinox" in formato.lower()) else ""
    sufixo_crisis = f"_crisis_{crisis_modelo}" if (crisis_modelo and "crisis" in formato.lower()) else ""
    pasta_saida = OUTPUT_DIR / f"{formato}{sufixo_arranjo}{sufixo_humanizar}{sufixo_arpejo}{sufixo_desync}{sufixo_patch}{sufixo_vel}{sufixo_semi}{sufixo_piano}{sufixo_crisis}"

    if not dry_run:
        pasta_saida.mkdir(parents=True, exist_ok=True)
        import json
        
        def limpar_para_json(obj):
            if isinstance(obj, dict):
                return {k: limpar_para_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [limpar_para_json(x) for x in obj]
            elif isinstance(obj, Path):
                return str(obj)
            else:
                return obj

        arquivo_json = pasta_saida / "parametros.json"
        parametros = {
            "geracao": 1,
            "parametros_recebidos": {
                "formato": formato,
                "caminho_sf2": str(caminho_sf2),
                "dry_run": dry_run,
                "usar_arpejo": usar_arpejo,
                "estilo_arpejo": estilo_arpejo,
                "delay_desincronismo": delay_desincronismo,
                "patch_gm": patch_gm,
                "velocidade": velocidade,
                "semitons": semitons,
                "usar_orquestra": usar_orquestra,
                "humanizar_cordas": humanizar_cordas,
                "arranjo": arranjo,
                "piano_modelo": piano_modelo,
                "crisis_modelo": crisis_modelo,
            },
            "configuracao_interna": {
                "soundfonts_disponiveis": {k: str(v) for k, v in SOUNDFONTS.items()},
                "gm_patches": GM_PATCHES,
                "estilos_arpejo_disponiveis": ESTILOS_ARPEJO
            },
            "data_processamento": datetime.now().isoformat()
        }
        with open(arquivo_json, "w", encoding="utf-8") as f_json:
            json.dump(limpar_para_json(parametros), f_json, indent=4, ensure_ascii=False)
        log.info("Parâmetros do processo salvos em parametros.json")

    total = len(midis)
    concluidos = 0
    pulados = 0
    erros = 0

    for idx, caminho_mid in enumerate(midis, 1):
        nome_mid = caminho_mid.name
        match_hino = re.search(r'\d+', caminho_mid.stem)
        if match_hino:
            num_hino = int(match_hino.group(0))
            nome_mp3 = f"{num_hino:03d}.mp3"
        else:
            nome_mp3 = caminho_mid.stem + ".mp3"
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
                usar_orquestra=usar_orquestra,
                humanizar_cordas=humanizar_cordas,
                arranjo=arranjo,
                piano_modelo=piano_modelo,
                crisis_modelo=crisis_modelo,
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
    if arranjo:
        print(f"  Arranjo      : {arranjo}")
    if humanizar_cordas:
        print("  Humanizado   : sim (legato, velocity e dinâmica)")
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
        default="sacro",
        metavar="ESTILO",
        help=f"Estilo do arpejo: {', '.join(ESTILOS_ARPEJO)}. Padrão: sacro",
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
    parser.add_argument(
        "--orquestra",
        action="store_true",
        help="Cria um arranjo de orquestra completa (cordas, metais, paletas e piano) multiplicando as vozes.",
    )
    parser.add_argument(
        "--arranjo",
        choices=["cordas", "metais", "orgaos", "orquestra_completa", "pianos", "classico_1", "sintetizado", "combinacao_3", "combinacao_4", "combinacao_5", "orgao_igreja", "orgao_reed", "orquestra_sacra_1", "orquestra_sacra_2", "orquestra_suave"],
        metavar="TIPO",
        help="Aplica um arranjo específico distribuindo as vozes.",
    )
    parser.add_argument(
        "--piano-modelo",
        choices=["steinway", "yamaha", "steinway_lr", "yamaha_lr"],
        default="steinway_lr",
        metavar="MODELO",
        help="Seleciona o modelo de piano para Equinox Grand Pianos: steinway, yamaha, steinway_lr, yamaha_lr. Padrão: steinway_lr",
    )
    parser.add_argument(
        "--crisis-modelo",
        choices=["padrao", "expressiva", "sinfonica"],
        default="expressiva",
        metavar="MODELO",
        help="Seleciona o modelo de orquestração para Crisis General Midi: padrao, expressiva, sinfonica. Padrão: expressiva",
    )
    parser.add_argument(
        "--humanizar-cordas",
        action="store_true",
        help="Aplica humanização (micro-dinâmicas de velocity e legato) nas trilhas de cordas.",
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
        path_arg = Path(args.mid)
        if path_arg.exists():
            caminho_especifico = path_arg
        elif (MID_DIR / args.mid).exists():
            caminho_especifico = MID_DIR / args.mid
        else:
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

    arranjo = args.arranjo
    if args.orquestra and not arranjo:
        arranjo = "orquestra_completa"

    log.info("mid2mp3 | Formato: %s | Arquivos: %d | Arpejo: %s%s | Desincronismo: %s | Instrumento: %s | Velocidade: %.0f%% | Arranjo: %s | Modelo Piano: %s | Modelo Crisis: %s | Humanizado: %s",
             args.formato,
             len(midis),
             "sim" if args.arpejo else "não",
             f" ({args.estilo_arpejo})" if args.arpejo else "",
             f"{args.delay_faixa}s/faixa" if args.desincronismo else "não",
             f"patch {patch_gm}" if patch_gm is not None else "original",
             args.velocidade,
             arranjo if arranjo else "não",
             args.piano_modelo,
             args.crisis_modelo,
             "sim" if args.humanizar_cordas else "não")

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
        usar_orquestra=args.orquestra,
        humanizar_cordas=args.humanizar_cordas,
        arranjo=arranjo,
        piano_modelo=args.piano_modelo,
        crisis_modelo=args.crisis_modelo,
    )

    conn.close()


if __name__ == "__main__":
    main()