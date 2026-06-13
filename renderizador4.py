#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador4.py — Geração 4: Humanizador MIDI Sacro Avançado
Consolida o melhor das gerações 1, 2 e 3:
  • Arpejo Sacro v3: detecção de inversões de acorde (arpejo.md seção 12),
    fill de silêncios (ping-pong, herdado do v1) e transição suave entre compassos
  • Detecção de estrutura do hino (Intro / Corpo / Final) com dinâmica progressiva
  • Presets dual-SoundFont — cada grupo de vozes com seu próprio timbre e SF2
  • Banco SQLite de progresso — processa pasta mid/ em lote sem re-renderizar
  • Humanização refinada por papel vocal E por seção do hino
  • 6 presets premium novos

Uso:
    python3 renderizador4.py --mid "003- Faz-nos ouvir Tua voz.mid" --preset equinox_sacro
    python3 renderizador4.py --listar-presets
    python3 renderizador4.py --status
    python3 renderizador4.py --preset coro_e_orgao          # processa toda pasta mid/
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
BASE_DIR       = Path(__file__).parent.resolve()
MID_DIR        = BASE_DIR / "mid"
OUTPUT_DIR     = BASE_DIR / "output4"
SOUNDFONTS_DIR = BASE_DIR / "soundfonts"
DB_PATH        = BASE_DIR / "progresso4.db"

# ─────────────────────────────────────────────────────────────────────────────
# Descoberta dinâmica de SoundFonts
# ─────────────────────────────────────────────────────────────────────────────
def descobrir_soundfonts() -> dict[str, Path]:
    """Varre soundfonts/ e retorna dict {nome_normalizado: caminho}."""
    sf2s: dict[str, Path] = {}
    if SOUNDFONTS_DIR.exists():
        for sf2 in sorted(SOUNDFONTS_DIR.glob("*.sf2")):
            if sf2.name.startswith("."):
                continue
            nome_cli = sf2.stem.replace(" ", "_").replace("-", "_").lower()
            sf2s[nome_cli] = sf2
    return sf2s

SOUNDFONTS = descobrir_soundfonts()

def obter_sf2(nome: str) -> Path:
    """Busca fuzzy por SoundFont; levanta FileNotFoundError se não achar."""
    nome_norm = nome.replace(" ", "_").replace("-", "_").lower()
    for k, v in SOUNDFONTS.items():
        if nome_norm in k or k in nome_norm:
            return v
    # Busca direta pelo nome do arquivo
    for ext in ("", ".sf2"):
        caminho = SOUNDFONTS_DIR / f"{nome}{ext}"
        if caminho.exists() and not caminho.name.startswith("."):
            return caminho
    # Fallback de emergência: qualquer sf2 disponível
    todas = [v for v in SOUNDFONTS.values()]
    if todas:
        return todas[0]
    raise FileNotFoundError(f"SoundFont '{nome}' não encontrada em {SOUNDFONTS_DIR}")

# ─────────────────────────────────────────────────────────────────────────────
# Configuração do Logger
# ─────────────────────────────────────────────────────────────────────────────
def configurar_log(verbose: bool = False) -> logging.Logger:
    nivel = logging.DEBUG if verbose else logging.INFO
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout
    )
    return logging.getLogger("renderizador4")

# ─────────────────────────────────────────────────────────────────────────────
# Banco de Dados SQLite de Progresso (herdado do v1, adaptado)
# ─────────────────────────────────────────────────────────────────────────────
def abrir_banco() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS renders4 (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo_mid     TEXT NOT NULL,
            preset          TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pendente',
            output_mp3      TEXT,
            data_criacao    TEXT NOT NULL,
            data_conclusao  TEXT,
            erro_msg        TEXT,
            UNIQUE(arquivo_mid, preset)
        )
    """)
    conn.commit()
    return conn

def registrar_pendente(conn: sqlite3.Connection, arquivo_mid: str, preset: str):
    conn.execute("""
        INSERT INTO renders4 (arquivo_mid, preset, status, data_criacao)
        VALUES (?, ?, 'pendente', ?)
        ON CONFLICT(arquivo_mid, preset) DO NOTHING
    """, (arquivo_mid, preset, datetime.now().isoformat()))
    conn.commit()

def marcar_concluido(conn: sqlite3.Connection, arquivo_mid: str, preset: str, output_mp3: str):
    conn.execute("""
        UPDATE renders4 SET status='concluido', output_mp3=?, data_conclusao=?
        WHERE arquivo_mid=? AND preset=?
    """, (output_mp3, datetime.now().isoformat(), arquivo_mid, preset))
    conn.commit()

def marcar_erro(conn: sqlite3.Connection, arquivo_mid: str, preset: str, msg: str):
    conn.execute("""
        UPDATE renders4 SET status='erro', erro_msg=?, data_conclusao=?
        WHERE arquivo_mid=? AND preset=?
    """, (msg, datetime.now().isoformat(), arquivo_mid, preset))
    conn.commit()

def ja_concluido(conn: sqlite3.Connection, arquivo_mid: str, preset: str) -> bool:
    row = conn.execute(
        "SELECT status FROM renders4 WHERE arquivo_mid=? AND preset=?",
        (arquivo_mid, preset)
    ).fetchone()
    return row is not None and row["status"] == "concluido"

# ─────────────────────────────────────────────────────────────────────────────
# Detecção Inteligente SATB
# ─────────────────────────────────────────────────────────────────────────────
def detectar_vozes_satb(midi: mido.MidiFile, log: logging.Logger) -> dict[str, int]:
    """
    Detecta as vozes SATB:
    1. Por nome da trilha (soprano/sop/s, alto/a, tenor/t, baixo/bass/b)
    2. Fallback: ordena por mediana de notas (mais aguda = soprano)
    Gera log com estatísticas de cada trilha (n, mín, máx, mediana).
    """
    log.info("🔍 Detectando vozes SATB...")
    trilhas_com_notas = []

    for idx, t in enumerate(midi.tracks):
        notas = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if notas:
            nome = t.name.strip() if t.name else f"Trilha {idx}"
            mediana = sorted(notas)[len(notas) // 2]
            trilhas_com_notas.append({
                "index": idx, "name": nome,
                "notes_count": len(notas),
                "min_note": min(notas), "max_note": max(notas),
                "median_note": mediana
            })

    if not trilhas_com_notas:
        log.warning("Nenhuma trilha com notas encontrada!")
        return {"soprano": 0, "contralto": 0, "tenor": 0, "baixo": 0}

    for tm in trilhas_com_notas:
        log.info(f"  Trilha #{tm['index']} ('{tm['name']}'): "
                 f"{tm['notes_count']} notas, Mín={tm['min_note']}, "
                 f"Máx={tm['max_note']}, Mediana={tm['median_note']}")

    # 1. Mapeamento por nome
    vozes_mapeadas: dict[str, int] = {}
    papeis = {
        "soprano":   ["soprano", "sop", "s", "cantus", "melody", "melodia", "vocal"],
        "contralto": ["contralto", "alto", "a"],
        "tenor":     ["tenor", "ten", "t"],
        "baixo":     ["baixo", "bass", "b"]
    }
    trilhas_restantes = trilhas_com_notas[:]
    for papel, termos in papeis.items():
        for tm in list(trilhas_restantes):
            nome_l = tm["name"].lower()
            if any(re.search(rf"\b{termo}\b", nome_l) or nome_l == termo for termo in termos):
                vozes_mapeadas[papel] = tm["index"]
                trilhas_restantes.remove(tm)
                break

    if len(vozes_mapeadas) == 4:
        log.info(f"✓ SATB mapeado por nome das trilhas: {vozes_mapeadas}")
        return vozes_mapeadas

    # 2. Fallback por mediana (mais aguda = soprano)
    trilhas_restantes.sort(key=lambda x: x["median_note"], reverse=True)
    for papel in ["soprano", "contralto", "tenor", "baixo"]:
        if papel not in vozes_mapeadas and trilhas_restantes:
            vozes_mapeadas[papel] = trilhas_restantes.pop(0)["index"]

    # Fallbacks de emergência (MIDI com poucas trilhas)
    fallback = trilhas_com_notas[0]["index"] if trilhas_com_notas else 0
    for papel in ["soprano", "contralto", "tenor", "baixo"]:
        if papel not in vozes_mapeadas:
            vozes_mapeadas[papel] = vozes_mapeadas.get("soprano", fallback)

    log.info(f"✓ SATB mapeado por mediana: {vozes_mapeadas}")
    return vozes_mapeadas

# ─────────────────────────────────────────────────────────────────────────────
# Utilitários de Tempo / Ticks
# ─────────────────────────────────────────────────────────────────────────────
def obter_tempo_midi(midi: mido.MidiFile) -> int:
    """Retorna tempo em µs/beat do primeiro set_tempo (padrão: 120 BPM)."""
    for track in midi.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500_000

def ms_para_ticks(ms: float, ticks_por_beat: int, tempo_us: int) -> int:
    """Converte milissegundos em ticks MIDI."""
    return int(round((ms / 1000.0) * (ticks_por_beat * 1_000_000 / tempo_us)))

def para_ticks_absolutos(track: mido.MidiTrack) -> list:
    """Converte trilha (delta times) para lista de [tick_absoluto, msg]."""
    eventos = []
    tick_acum = 0
    for msg in track:
        tick_acum += msg.time
        eventos.append([tick_acum, msg.copy()])
    return eventos

def para_trilha(eventos_abs: list) -> mido.MidiTrack:
    """Reconverte lista de [tick_abs, msg] para MidiTrack com delta times."""
    eventos_abs.sort(key=lambda x: x[0])
    track = mido.MidiTrack()
    tick_prev = 0
    for tick_abs, msg in eventos_abs:
        delta = max(0, tick_abs - tick_prev)
        msg.time = delta
        track.append(msg)
        tick_prev = tick_abs
    return track

def obter_max_tick(midi: mido.MidiFile) -> int:
    """Retorna o tick absoluto máximo do MIDI."""
    max_tick = 0
    for t in midi.tracks:
        tick = 0
        for msg in t:
            tick += msg.time
        max_tick = max(max_tick, tick)
    return max_tick

def obter_limites_compassos(midi: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """
    Calcula (start, end, numerador, denominador) para cada compasso.
    Suporta compassos alternantes ao longo do MIDI.
    """
    ts_changes: list[tuple[int, int, int]] = []
    for t in midi.tracks:
        tick = 0
        for msg in t:
            tick += msg.time
            if msg.type == "time_signature":
                ts_changes.append((tick, msg.numerator, msg.denominator))
    ts_changes.sort(key=lambda x: x[0])
    if not ts_changes or ts_changes[0][0] > 0:
        ts_changes.insert(0, (0, 4, 4))  # padrão GM: 4/4

    def ts_em_tick(tick: int) -> tuple[int, int]:
        lo, hi = 0, len(ts_changes) - 1
        while lo < hi:
            mid_i = (lo + hi + 1) // 2
            if ts_changes[mid_i][0] <= tick:
                lo = mid_i
            else:
                hi = mid_i - 1
        return ts_changes[lo][1], ts_changes[lo][2]

    max_tick = obter_max_tick(midi)
    ticks_por_beat = midi.ticks_per_beat
    limites: list[tuple[int, int, int, int]] = []
    tick_atual = 0
    while tick_atual < max_tick:
        num, den = ts_em_tick(tick_atual)
        len_measure = int(ticks_por_beat * 4 * num / den)
        if len_measure <= 0:
            len_measure = ticks_por_beat * 4
        limites.append((tick_atual, tick_atual + len_measure, num, den))
        tick_atual += len_measure
    return limites

# ─────────────────────────────────────────────────────────────────────────────
# Detecção de Estrutura do Hino (Intro / Corpo / Final) — NOVO na v4
# ─────────────────────────────────────────────────────────────────────────────
def detectar_estrutura_hino(
    midi: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger
) -> dict[str, tuple[int, int]]:
    """
    Identifica automaticamente as 3 seções do hino por densidade de notas:
      - intro:  início com densidade abaixo da média (< 75%)
      - corpo:  seção principal, mais densa e expressiva
      - final:  encerramento com densidade caindo novamente

    Retorna: {'intro': (t0, t1), 'corpo': (t1, t2), 'final': (t2, t3)}
    Usado pela humanização para aplicar dinâmica diferente por seção.
    """
    ticks_por_beat = midi.ticks_per_beat
    janela = ticks_por_beat * 8  # ~2 compassos em 4/4

    # Coleta todos os note_on de todas as vozes SATB
    todos_events: list[int] = []
    for idx in vozes.values():
        if idx < len(midi.tracks):
            tick_acum = 0
            for msg in midi.tracks[idx]:
                tick_acum += msg.time
                if msg.type == "note_on" and msg.velocity > 0:
                    todos_events.append(tick_acum)

    if not todos_events:
        max_t = obter_max_tick(midi)
        return {"intro": (0, 0), "corpo": (0, max_t), "final": (max_t, max_t)}

    todos_events.sort()
    t_max = todos_events[-1]

    # Calcula densidade por janela deslizante
    janelas_density: list[tuple[int, int]] = []
    t = 0
    while t < t_max + janela:
        count = sum(1 for ev in todos_events if t <= ev < t + janela)
        janelas_density.append((t, count))
        t += janela

    densidades = [c for _, c in janelas_density]
    media = sum(densidades) / max(len(densidades), 1)
    limiar = media * 0.75  # 75% da densidade média

    # Intro: janelas iniciais com densidade abaixo do limiar
    intro_end = 0
    for t_j, c in janelas_density:
        if c >= limiar:
            intro_end = t_j
            break

    # Final: janelas finais com densidade abaixo do limiar
    final_start = t_max + janela
    for t_j, c in reversed(janelas_density):
        if c >= limiar:
            final_start = t_j + janela
            break

    # Garante que corpo exista
    if final_start <= intro_end:
        final_start = int(t_max * 0.85)

    t_fim_total = t_max + janela * 2
    log.info(f"📐 Estrutura do hino detectada:")
    log.info(f"   Intro:  tick 0 → {intro_end}")
    log.info(f"   Corpo:  tick {intro_end} → {final_start}")
    log.info(f"   Final:  tick {final_start} → {t_fim_total}")

    return {
        "intro":  (0, intro_end),
        "corpo":  (intro_end, final_start),
        "final":  (final_start, t_fim_total),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Humanização Avançada v4
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_humanizacao_v4(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    estrutura: dict[str, tuple[int, int]],
    preset_cfg: dict,
    seed: int,
    log: logging.Logger
) -> mido.MidiFile:
    """
    Humanização refinada com três camadas:
    1. Micro-timing (delays por papel vocal) — Monotônicos/Interpolados por onsets para evitar notas comidas/gaps
    2. Velocities orgânicas variando por papel E por seção do hino:
         intro: mais suave; corpo: expressivo; final: diminuendo gradual
    3. Dinâmica de frase (curva parabólica: 90%→108%→82% por bloco de 2 compassos) - APLICADO APENAS A PIANOS
    """
    log.info(f"🎻 Humanizando (v4, seed={seed})...")
    rng = random.Random(seed)

    tempo_us      = obter_tempo_midi(midi_in)
    ticks_por_beat = midi_in.ticks_per_beat

    # Identifica se é piano para aplicar dinâmica de bloco/frase
    is_piano = False
    if "soundfont" in preset_cfg and ("piano" in preset_cfg["soundfont"].lower() or "equinox" in preset_cfg["soundfont"].lower()):
        is_piano = True
    elif "grupos" in preset_cfg:
        for g in preset_cfg["grupos"].values():
            if "soundfont" in g and ("piano" in g["soundfont"].lower() or "equinox" in g["soundfont"].lower()):
                is_piano = True
                break

    # Velocities por papel e seção
    vel_cfg: dict[str, dict[str, tuple[int, int]]] = {
        "soprano":   {"intro": (62, 78),  "corpo": (72, 90),  "final": (58, 74)},
        "contralto": {"intro": (44, 58),  "corpo": (50, 68),  "final": (40, 56)},
        "tenor":     {"intro": (42, 56),  "corpo": (48, 66),  "final": (38, 54)},
        "baixo":     {"intro": (50, 65),  "corpo": (58, 76),  "final": (46, 63)},
    }

    # Escala global de velocity do preset
    vel_scale = preset_cfg.get("velocity_scale", 1.0)

    def obter_secao(tick: int) -> str:
        if tick < estrutura["intro"][1]:
            return "intro"
        if tick >= estrutura["final"][0]:
            return "final"
        return "corpo"

    # 1. Coleta todos os ticks de início de notas (onsets) nas trilhas de vozes e eventos para detecção de frase
    onsets_set = {0}
    todos_eventos_notas = []
    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0 or "arpejo" in (trilha.name or "").lower():
            continue
        tick_abs = 0
        for msg in trilha:
            tick_abs += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                onsets_set.add(tick_abs)
                todos_eventos_notas.append({"type": "note_on", "tick": tick_abs})
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                todos_eventos_notas.append({"type": "note_off", "tick": tick_abs})
    
    max_tick = obter_max_tick(midi_in)
    onsets_set.add(max_tick)
    onset_ticks = sorted(list(onsets_set))

    # Detectar fins de frase globais por silêncio
    todos_eventos_notas.sort(key=lambda x: x["tick"])
    silence_threshold = int(ticks_por_beat * 1.5)
    phrase_ends = set()
    ultima_nota_fim = 0
    for i, ev in enumerate(todos_eventos_notas):
        if ev["type"] == "note_on":
            if ev["tick"] - ultima_nota_fim > silence_threshold and i > 0:
                phrase_ends.add(ultima_nota_fim)
        else:
            ultima_nota_fim = max(ultima_nota_fim, ev["tick"])
    phrase_ends.add(ultima_nota_fim)

    def eh_fim_de_frase(t: int, onset_ticks: list[int], phrase_ends: set[int]) -> bool:
        for pe in sorted(list(phrase_ends)):
            if pe >= t:
                outros_onsets = [ot for ot in onset_ticks if t < ot <= pe]
                if not outros_onsets:
                    return True
                break
        return False

    humanizacao_tipo = preset_cfg.get("humanizacao", "padrao")
    
    if humanizacao_tipo == "nova":
        limites_compassos = obter_limites_compassos(midi_in)
        # Pré-computa os delays para cada compasso
        delays_por_compasso = []
        for _ in limites_compassos:
            delays_base = [0.05, 0.1, 0.2]
            rng.shuffle(delays_base)
            delays_por_compasso.append({
                "soprano": 0.0,
                "contralto": delays_base[0],
                "tenor": delays_base[1],
                "baixo": delays_base[2]
            })
            
        def obter_delay_interpolado(t_abs: int, papel_vocal: str) -> float:
            if not limites_compassos:
                return 0.0
            import bisect
            m_starts = [m[0] for m in limites_compassos]
            idx = bisect.bisect_right(m_starts, t_abs) - 1
            idx = max(0, min(len(delays_por_compasso) - 1, idx))
            return delays_por_compasso[idx][papel_vocal]
    else:
        # 2. Gera delays limitados por slope (dD/ds <= 0.75) para cada onset e cada voz
        delays_por_onset = []
        prev_delays = {
            "soprano": 0.0,
            "contralto": 0.0,
            "tenor": 0.0,
            "baixo": 0.0
        }
        
        ticks_por_seg = ticks_por_beat * 1_000_000.0 / tempo_us
        
        for idx, t in enumerate(onset_ticks):
            # Atraso aleatório de 0 a 150ms (0 a 0.15s) no corpo e 0 a 200ms (0 a 0.20s) em fim de frase para pianos solos.
            # Para outros instrumentos (órgãos, orquestras, metais, synths, caixinha de música e híbridos),
            # limitamos a no máximo 20ms (0.02s) para manter o sincronismo impecável.
            if is_piano:
                limite_delay = 0.20 if eh_fim_de_frase(t, onset_ticks, phrase_ends) else 0.15
            else:
                limite_delay = 0.02

            if idx == 0:
                cur_delays = {
                    "soprano":   rng.uniform(0.0, limite_delay),
                    "contralto": rng.uniform(0.0, limite_delay),
                    "tenor":     rng.uniform(0.0, limite_delay),
                    "baixo":     rng.uniform(0.0, limite_delay),
                }
            else:
                dt_ticks = t - onset_ticks[idx - 1]
                ds = dt_ticks / ticks_por_seg
                max_change = 0.75 * ds
                
                cur_delays = {}
                for papel_vocal in ["soprano", "contralto", "tenor", "baixo"]:
                    d_prev = prev_delays[papel_vocal]
                    target = rng.uniform(0.0, limite_delay)
                    
                    min_val = d_prev - max_change
                    max_val = d_prev + max_change
                    clipped = max(min_val, min(max_val, target))
                    cur_delays[papel_vocal] = clipped
                    
            delays_por_onset.append(cur_delays)
            prev_delays = cur_delays

        def obter_delay_interpolado(t_abs: int, papel_vocal: str) -> float:
            if not onset_ticks:
                return 0.0
            if t_abs <= onset_ticks[0]:
                return delays_por_onset[0][papel_vocal]
            if t_abs >= onset_ticks[-1]:
                return delays_por_onset[-1][papel_vocal]
            
            import bisect
            idx = bisect.bisect_right(onset_ticks, t_abs) - 1
            t_start = onset_ticks[idx]
            t_next = onset_ticks[idx + 1]
            d_start = delays_por_onset[idx][papel_vocal]
            d_next = delays_por_onset[idx + 1][papel_vocal]
            
            return d_start + (d_next - d_start) * (t_abs - t_start) / (t_next - t_start)

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=ticks_por_beat)
    idx_para_voz = {idx: voz for voz, idx in vozes.items()}

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (meta/tempo) e trilha de arpejo passam intactas
        if idx_trilha == 0 or "arpejo" in (trilha.name or "").lower():
            novo_midi.tracks.append(trilha)
            continue

        papel = idx_para_voz.get(idx_trilha, "tenor")
        eventos_abs = para_ticks_absolutos(trilha)

        # ── Passo 1: Delay interpolado + velocity por seção ──────────────────
        for ev in eventos_abs:
            tick_abs, msg = ev
            if msg.type == "note_on" and msg.velocity > 0:
                secao = obter_secao(tick_abs)
                v_min, v_max = vel_cfg[papel][secao]

                # Velocity orgânica: usa beta distribution para centrar em (v_min+v_max)/2
                vel_base = int(v_min + (v_max - v_min) * rng.betavariate(2.5, 2.5))
                msg.velocity = max(1, min(127, int(vel_base * vel_scale)))

                delay_sec = obter_delay_interpolado(tick_abs, papel)
                delay_ticks = ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = tick_abs + delay_ticks

            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                delay_sec = obter_delay_interpolado(tick_abs, papel)
                delay_ticks = ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = tick_abs + delay_ticks

        # ── Passo 2: Dinâmica de frase por blocos de 2 compassos (Apenas para Pianos) ──
        if is_piano:
            bloco_ticks = ticks_por_beat * 8
            note_ons_sorted = sorted(
                [ev for ev in eventos_abs if ev[1].type == "note_on" and ev[1].velocity > 0],
                key=lambda x: x[0]
            )
            if note_ons_sorted:
                t_cursor = note_ons_sorted[0][0]
                t_fim_ev = note_ons_sorted[-1][0]
                while t_cursor <= t_fim_ev:
                    t_bloco_fim = t_cursor + bloco_ticks
                    for ev in eventos_abs:
                        tick_abs, msg = ev
                        if (t_cursor <= tick_abs < t_bloco_fim
                                and msg.type == "note_on" and msg.velocity > 0):
                            pos_rel = (tick_abs - t_cursor) / bloco_ticks
                            # Parábola com pico em ~55%: cresce suavemente e decai
                            fator = 0.90 + 0.36 * pos_rel - 0.44 * (pos_rel ** 2)
                            msg.velocity = max(1, min(127, int(msg.velocity * fator)))
                    t_cursor += bloco_ticks

        nova_trilha = para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Pedal de Sustain Inteligente (CC64) — refinado do v2
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_pedal_sustain(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger
) -> mido.MidiFile:
    """
    Aplica CC64 sincronizado com as mudanças do Baixo:
    - Liga +30 ticks após cada ataque do baixo
    - Solta -12 ticks antes do próximo ataque (evita embolamento harmônico)
    - Sempre desliga no final
    """
    log.info("🎹 Aplicando pedal de sustain inteligente (CC64)...")

    baixo_idx = vozes.get("baixo", 0)
    baixo_trilha = midi_in.tracks[baixo_idx] if baixo_idx < len(midi_in.tracks) else midi_in.tracks[0]

    eventos_baixo = para_ticks_absolutos(baixo_trilha)
    mudancas_baixo = sorted(
        tick for tick, msg in eventos_baixo
        if msg.type == "note_on" and msg.velocity > 0
    )

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        eventos_abs = para_ticks_absolutos(trilha)
        canais = {msg.channel for _, msg in eventos_abs
                  if hasattr(msg, "channel") and msg.channel != 9}

        if not canais:
            novo_midi.tracks.append(trilha)
            continue

        canal = list(canais)[0]
        pedal_events = []

        for i, t_baixo in enumerate(mudancas_baixo):
            t_on = t_baixo + 30
            pedal_events.append((t_on, mido.Message(
                "control_change", channel=canal, control=64, value=127, time=0)))
            if i + 1 < len(mudancas_baixo):
                t_prox = mudancas_baixo[i + 1]
                t_off = max(t_on + 5, t_prox - 12)
                pedal_events.append((t_off, mido.Message(
                    "control_change", channel=canal, control=64, value=0, time=0)))

        if mudancas_baixo and eventos_abs:
            pedal_events.append((eventos_abs[-1][0], mido.Message(
                "control_change", channel=canal, control=64, value=0, time=0)))

        for t_ev, msg in pedal_events:
            eventos_abs.append([t_ev, msg])

        nova_trilha = para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Expressão CC11 e Vibrato CC1 — ciente da seção do hino (NOVO na v4)
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_expressao_vibrato(
    midi_in: mido.MidiFile,
    estrutura: dict[str, tuple[int, int]],
    log: logging.Logger,
    cc11: bool = True,
    cc1: bool  = True
) -> mido.MidiFile:
    """
    Injeta CC11 (expressão) e CC1 (vibrato progressivo) em notas longas (> 1 semínima).
    Os valores de CC11 variam por seção do hino:
      - intro:  expressão menor (60→90→75→50)
      - corpo:  expressão plena (75→115→95→60)
      - final:  expressão fechando (55→85→70→40)
    CC1 (vibrato): começa em 0, cresce após 28% da duração, fecha no final.
    """
    log.info("🎻 Injetando CC11 (expressão) e CC1 (vibrato) por seção...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat
    # Vibrato apenas em notas com duração > 1 semínima
    limiar_vibrato = int(ticks_por_beat * 1.05)

    # Valores de CC11: (start, peak, sustain, end) por seção
    cc11_table = {
        "intro": (60, 90,  75, 50),
        "corpo": (75, 115, 95, 60),
        "final": (55, 85,  70, 40),
    }

    def obter_secao(tick: int) -> str:
        if tick < estrutura["intro"][1]:
            return "intro"
        if tick >= estrutura["final"][0]:
            return "final"
        return "corpo"

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        eventos_abs = para_ticks_absolutos(trilha)
        canais = {msg.channel for _, msg in eventos_abs
                  if hasattr(msg, "channel") and msg.channel != 9}

        if not canais:
            novo_midi.tracks.append(trilha)
            continue

        canal = list(canais)[0]

        # Ignora trilhas de órgão, piano ou percussão cromática (program < 21)
        eh_excluido = False
        for _, msg in eventos_abs:
            if msg.type == "program_change" and msg.program < 21:
                eh_excluido = True
                break
        if eh_excluido:
            novo_midi.tracks.append(trilha)
            continue

        # Mapeia todos os intervalos de notas para detecção de legato/overlap
        notas_eventos = []
        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                notas_eventos.append({"type": "start", "tick": tick_abs, "note": msg.note})
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                notas_eventos.append({"type": "end", "tick": tick_abs, "note": msg.note})
        
        intervalos = []
        notas_ativas_map = {}
        for ev in sorted(notas_eventos, key=lambda x: x["tick"]):
            if ev["type"] == "start":
                notas_ativas_map[ev["note"]] = ev["tick"]
            else:
                if ev["note"] in notas_ativas_map:
                    t_on = notas_ativas_map.pop(ev["note"])
                    intervalos.append({"start": t_on, "end": ev["tick"], "note": ev["note"]})
        intervalos.sort(key=lambda x: x["start"])

        novos_ccs: list[tuple[int, mido.Message]] = []
        for i, intr in enumerate(intervalos):
            t_on = intr["start"]
            t_off = intr["end"]
            duracao = t_off - t_on
            
            # Verifica se há uma nota começando logo em seguida
            tem_proxima = False
            if i + 1 < len(intervalos):
                tem_proxima = (intervalos[i + 1]["start"] <= t_off + 50)
            
            if duracao >= limiar_vibrato:
                secao = obter_secao(t_on)
                v_s, v_p, v_sus, v_e = cc11_table[secao]
                # Nota longa: aplica curva de expressão suave (usando valores da seção)
                if cc11:
                    # Inicia em v_s
                    novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=11, value=v_s, time=0)))
                    # Sobe para v_p no pico (18% da duração)
                    novos_ccs.append((t_on + int(duracao * 0.18), mido.Message("control_change", channel=canal, control=11, value=v_p, time=0)))
                    # Sustenta em v_sus (82% da duração)
                    novos_ccs.append((t_on + int(duracao * 0.82), mido.Message("control_change", channel=canal, control=11, value=v_sus, time=0)))
                    # Se não tem próxima nota logo em seguida, faz um fade-out para v_e
                    if not tem_proxima:
                        novos_ccs.append((max(t_on + 1, t_off - 4), mido.Message("control_change", channel=canal, control=11, value=v_e, time=0)))
                
                if cc1:
                    # Vibrato progressivo
                    novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=1, value=0, time=0)))
                    novos_ccs.append((t_on + int(duracao * 0.28), mido.Message("control_change", channel=canal, control=1, value=12, time=0)))
                    novos_ccs.append((t_on + int(duracao * 0.58), mido.Message("control_change", channel=canal, control=1, value=55, time=0)))
                    novos_ccs.append((max(t_on + 1, t_off - 8), mido.Message("control_change", channel=canal, control=1, value=0, time=0)))
            else:
                # Nota curta: apenas garante reset de expressão para v_s da seção
                if cc11:
                    secao = obter_secao(t_on)
                    v_s, _, _, _ = cc11_table[secao]
                    novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=11, value=v_s, time=0)))

        for t_cc, msg_cc in novos_ccs:
            eventos_abs.append([t_cc, msg_cc])

        nova_trilha = para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Arpejo Sacro Inteligente v3 — PRINCIPAL NOVIDADE da v4
# Detecção de inversões + fill de silêncios + transição entre compassos
# Baseado em: docs/arpejo.md seções 2, 7, 8, 11, 12
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_arpejo_sacro_v3(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    preset_cfg: dict,
    log: logging.Logger
) -> mido.MidiFile:
    """
    Gera trilha de arpejo sacro seguindo as diretrizes do arpejo.md:

    DETECÇÃO DE INVERSÕES (arpejo.md seção 12):
      Coleta os pitch classes (0-11) das 4 vozes no compasso.
      O arpejo começa na nota REAL do baixo (que pode ser uma inversão),
      mas usa as notas da harmonia real identificada pelas 4 vozes.
      Exemplo: B=Mi4, T=Dó4, A=Sol4, S=Dó5 → harmonia={0,4,7}(Dó maior com Mi no baixo)
      Arpejo usa {Mi, Sol, Dó}, não confunde com Mi maior.

    TRANSIÇÃO ENTRE COMPASSOS (arpejo.md seção 7):
      A última nota de cada compasso é escolhida para ser a mais próxima
      do baixo do próximo compasso (preparação suave).

    FILL DE SILÊNCIOS (v1 refinado, arpejo.md seção 8 "Camada 4 – respiração"):
      Pausas > 2 beats recebem notas ping-pong muito suaves do pool harmônico,
      preenchendo o espaço sem poluir a harmonia.

    Padrões (arpejo.md seção 11):
      4/4: B→T→A→T→S→T→A→T  (8 colcheias)
      3/4: B→T→A→S→A→T       (6 colcheias)
      2/4: B→A→S→A            (4 colcheias)
    """
    log.info("♫ Aplicando Arpejo Sacro v3 (inversões + fill + transição)...")

    ticks_por_beat = midi_in.ticks_per_beat
    rng = random.Random(2024)
    vel_scale = preset_cfg.get("velocity_scale", 1.0)

    # Extrai notas absolutas de cada voz
    def obter_notas_abs(track_idx: int) -> list[dict]:
        if track_idx >= len(midi_in.tracks):
            return []
        notas: list[dict] = []
        notas_ativas: dict[int, tuple[int, int]] = {}
        tick_acum = 0
        for msg in midi_in.tracks[track_idx]:
            tick_acum += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = (tick_acum, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    start, vel = notas_ativas.pop(msg.note)
                    notas.append({"note": msg.note, "start": start,
                                  "end": tick_acum, "velocity": vel})
        return sorted(notas, key=lambda x: x["start"])

    s_notas = obter_notas_abs(vozes["soprano"])
    a_notas = obter_notas_abs(vozes["contralto"])
    t_notas = obter_notas_abs(vozes["tenor"])
    b_notas = obter_notas_abs(vozes["baixo"])

    # Escolhe canal MIDI livre
    canais_usados: set[int] = set()
    for t in midi_in.tracks:
        for msg in t:
            if hasattr(msg, "channel"):
                canais_usados.add(msg.channel)
    canal = next((c for c in range(16) if c != 9 and c not in canais_usados), 15)

    limites_compassos = obter_limites_compassos(midi_in)

    def nota_no_tick(notas_lista: list[dict], tick: int,
                     m_start: int, m_end: int) -> int | None:
        """Retorna a nota da voz mais próxima do tick dentro do compasso."""
        ativas = [n for n in notas_lista if n["start"] <= tick < n["end"]]
        if ativas:
            return ativas[0]["note"]
        no_compasso = [n for n in notas_lista if m_start <= n["start"] < m_end]
        if no_compasso:
            no_compasso.sort(key=lambda x: abs(x["start"] - tick))
            return no_compasso[0]["note"]
        return None

    def detectar_pool_harmonico(
        m_start: int, m_end: int
    ) -> tuple[int | None, list[int]]:
        """
        Detecta o pool de notas harmônicas reais do compasso.
        INVERSÃO: o pool é construído a partir dos pitch classes de TODAS as 4 vozes.
        O baixo real é retornado separadamente para ser o ponto de partida do arpejo.
        """
        n_B = nota_no_tick(b_notas, m_start, m_start, m_end)
        n_T = nota_no_tick(t_notas, m_start, m_start, m_end)
        n_A = nota_no_tick(a_notas, m_start, m_start, m_end)
        n_S = nota_no_tick(s_notas, m_start, m_start, m_end)

        # Fallbacks se alguma voz estiver vazia
        if n_B is None: n_B = 48
        if n_T is None: n_T = n_B + 12
        if n_A is None: n_A = n_T + 5
        if n_S is None: n_S = n_A + 7

        # Pool harmônico: pitch classes das 4 vozes, expandidos em oitavas úteis
        pitch_classes = {n_B % 12, n_T % 12, n_A % 12, n_S % 12}
        pool: list[int] = []
        for pc in sorted(pitch_classes):
            for oitava in range(3, 7):           # MIDI oitavas 3-6 (aprox. 36-83)
                nota = pc + oitava * 12
                if 36 <= nota <= 84:
                    pool.append(nota)
        pool.sort()

        # Garante nota real do baixo no pool
        if n_B not in pool:
            pool.append(n_B)
            pool.sort()

        return n_B, pool, n_T, n_A, n_S

    # ── Geração dos eventos de arpejo ────────────────────────────────────────
    novos_eventos: list[tuple[int, mido.Message]] = []

    for idx_m, (m_start, m_end, num, den) in enumerate(limites_compassos):
        n_B_real, pool, n_T, n_A, n_S = detectar_pool_harmonico(m_start, m_end)

        if not pool:
            continue

        if preset_cfg.get("arpejo_tipo") == "simples":
            # 3 notas para compassos compostos, 4 para simples
            is_compound = (den == 8) or (num in (6, 9, 12)) or (num == 3 and den == 8)
            D = 3 if is_compound else 4
            duracao_nota = int((m_end - m_start) / D)
            if duracao_nota <= 0:
                continue
            if D == 3:
                pattern = ['B', 'T', 'A']
            else:
                pattern = ['B', 'T', 'A', 'S']
        else:
            # Divide o compasso em colcheias (arpejo.md seção 3)
            D = int(num * (8 / den))
            if D <= 0:
                D = 8
            duracao_nota = int((m_end - m_start) / D)
            if duracao_nota <= 0:
                continue

            # Padrão de vozes (arpejo.md seção 11)
            if num == 3:
                pattern = ['B', 'T', 'A', 'S', 'A', 'T']
            elif num == 2:
                pattern = ['B', 'A', 'S', 'A']
            else:  # 4/4
                pattern = ['B', 'T', 'A', 'T', 'S', 'T', 'A', 'T']

            # Ajusta o padrão ao número real de divisões
            if len(pattern) != D:
                pattern = [pattern[i % len(pattern)] for i in range(D)]

        # Próximo baixo (para transição suave — arpejo.md seção 7)
        proximo_baixo: int | None = None
        if idx_m + 1 < len(limites_compassos):
            nm_start, nm_end = limites_compassos[idx_m + 1][:2]
            proximo_baixo = nota_no_tick(b_notas, nm_start, nm_start, nm_end)

        for i, role in enumerate(pattern):
            t_on  = m_start + i * duracao_nota
            t_off = t_on + duracao_nota

            # Nota base pelo papel
            nota_base = {"B": n_B_real, "T": n_T, "A": n_A, "S": n_S}.get(role, n_B_real)
            if nota_base is None:
                nota_base = n_B_real or 48

            # Busca a nota harmônica mais próxima no pool (detecção de inversão)
            # Isso garante que usamos a harmonia real, não apenas a nota MIDI direta
            if pool:
                nota = min(pool, key=lambda x: abs(x - nota_base))
            else:
                nota = nota_base

            # Última nota do compasso → prepara próximo baixo (transição suave)
            if i == D - 1 and proximo_baixo is not None and pool:
                nota = min(pool, key=lambda x: abs(x - proximo_baixo))

            # Velocity do arpejo (arpejo.md seção 6: mais suave que as vozes)
            if role == 'B':
                vel = rng.randint(55, 72)    # baixo tem mais presença no arpejo
            elif role == 'S':
                vel = rng.randint(30, 45)    # notas próximas ao soprano: mais leves
            else:
                vel = rng.randint(36, 54)    # internas: médias

            vel = max(1, min(127, int(vel * vel_scale)))

            novos_eventos.append((t_on, mido.Message(
                "note_on",  channel=canal, note=nota, velocity=vel, time=0)))
            novos_eventos.append((t_off, mido.Message(
                "note_off", channel=canal, note=nota, velocity=0,   time=0)))

    # ── Fill de silêncios entre frases (herdado do v1, arpejo.md seção 8) ───
    if preset_cfg.get("fill_silencio", False) and limites_compassos:
        MIN_GAP = ticks_por_beat * 2   # pausa mínima para ativar o fill
        VEL_FILL = 32                   # velocidade muito suave para o fill

        for idx_m in range(len(limites_compassos) - 1):
            m_start, m_end = limites_compassos[idx_m][:2]
            nm_start = limites_compassos[idx_m + 1][0]
            gap = nm_start - m_end

            if gap < MIN_GAP:
                continue

            _, pool_fill, _, _, _ = detectar_pool_harmonico(m_start, m_end)
            if not pool_fill:
                continue

            # Seleciona notas alternadas do pool (espaçadas) para o ping-pong
            notas_fill_asc  = pool_fill[::2][:4] or pool_fill[:2]
            notas_fill_desc = list(reversed(notas_fill_asc))
            sequencia_fill  = notas_fill_asc + notas_fill_desc

            dur_fill = max(1, gap // max(len(sequencia_fill), 1))
            cursor = m_end

            for j, nota_f in enumerate(sequencia_fill):
                if cursor + dur_fill > nm_start:
                    break
                vel_f = max(1, min(127, VEL_FILL + rng.randint(-4, 4)))
                novos_eventos.append((cursor, mido.Message(
                    "note_on",  channel=canal, note=nota_f, velocity=vel_f, time=0)))
                novos_eventos.append((cursor + dur_fill, mido.Message(
                    "note_off", channel=canal, note=nota_f, velocity=0, time=0)))
                cursor += dur_fill

    novos_eventos.sort(key=lambda x: x[0])

    # Patch do arpejo: harpa para meditativo, piano para os demais
    if "meditativo" in preset_cfg.get("descricao", "").lower():
        patch_arpejo = 46   # Harp (GM)
    elif "musicbox" in preset_cfg.get("descricao", "").lower():
        patch_arpejo = 10   # Music Box (GM)
    else:
        patch_arpejo = 0    # Acoustic Grand Piano (GM)

    # Monta trilha de arpejo
    nova_trilha = mido.MidiTrack()
    nova_trilha.name = "Arpejo Sacro Inteligente v3"
    nova_trilha.append(mido.Message("program_change", channel=canal, program=patch_arpejo, time=0))
    nova_trilha.append(mido.Message("control_change", channel=canal, control=7,  value=68, time=0))
    nova_trilha.append(mido.Message("control_change", channel=canal, control=10, value=64, time=0))

    tick_prev = 0
    for tick_abs, msg in novos_eventos:
        delta = max(0, tick_abs - tick_prev)
        msg.time = delta
        nova_trilha.append(msg)
        tick_prev = tick_abs

    nova_trilha.append(mido.MetaMessage("end_of_track", time=0))

    # Retorna MIDI com trilha de arpejo adicionada ao final
    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)
    for t in midi_in.tracks:
        novo_midi.tracks.append(t)
    novo_midi.tracks.append(nova_trilha)

    log.info(f"  ✓ Arpejo v3 adicionado (canal {canal}, patch {patch_arpejo})")
    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Definição dos 6 Presets Premium v4
# ─────────────────────────────────────────────────────────────────────────────
PRESETS_V4: dict[str, dict] = {

    "equinox_sacro": {
        "descricao": "Piano Equinox premium com arpejo sacro v3 + fill de silêncios",
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "pan": 60, "vol": 80,  "canal": 1},
            "tenor":     {"patch": 0, "pan": 58, "vol": 78,  "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 90,  "canal": 3},
        },
        "pedal": True, "expressao": False, "vibrato": False,
        "arpejo": True,  "fill_silencio": True,
        "reverb_fs": "0.50", "gain_fs": "0.75",
    },

    "coro_e_orgao": {
        "descricao": "Coral Mellotron (S+A) + Órgão Timbres of Heaven (T+B) com CC11",
        "modo": "hibrido",
        "grupos": {
            "coro": {
                "vozes": ["soprano", "contralto"],
                "soundfont": "Mellotron.sf2",
                "patches_especificos": {"soprano": 52, "contralto": 52},  # Choir Aahs
                "vol": 90,
                "pan": {"soprano": 68, "contralto": 58},
            },
            "orgao": {
                "vozes": ["tenor", "baixo"],
                "soundfont": "Timbres_of_Heaven.sf2",
                "patches_especificos": {"tenor": 19, "baixo": 19},        # Church Organ
                "vol": 88,
                "pan": {"tenor": 50, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
    },

    "violino_e_piano": {
        "descricao": "Violino solo aaviolin (S) + Piano Equinox (A+T+B) com vibrato",
        "modo": "hibrido",
        "grupos": {
            "violino": {
                "vozes": ["soprano"],
                "soundfont": "aaviolin.sf2",
                "patches_especificos": {"soprano": 0},
                "vol": 108,
                "pan": {"soprano": 70},
            },
            "piano": {
                "vozes": ["contralto", "tenor", "baixo"],
                "soundfont": "Equinox_Grand_Pianos.sf2",
                "patches_especificos": {"contralto": 0, "tenor": 0, "baixo": 0},
                "vol": 82,
                "pan": {"contralto": 58, "tenor": 54, "baixo": 64},
            },
        },
        "pedal": True, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.45", "gain_fs": "0.80",
    },

    "orquestra_completa": {
        "descricao": "Orquestra sacra Crisis: Flauta+Oboé (sopros) + Viola+Cello (cordas) + pad",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 100, "canal": 0},  # Flute
            "contralto": {"patch": 68, "pan": 56, "vol": 88,  "canal": 1},  # Oboe
            "tenor":     {"patch": 41, "pan": 48, "vol": 85,  "canal": 2},  # Viola
            "baixo":     {"patch": 42, "pan": 64, "vol": 95,  "canal": 3},  # Cello
        },
        "pad_strings": True,  # adiciona pad de String Ensemble dobrando a harmonia
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
    },

    "meditativo": {
        "descricao": "Piano suave MuseScore com arpejo de harpa e muito reverb — para oração",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 68, "vol": 82, "canal": 0},
            "contralto": {"patch": 0, "pan": 60, "vol": 65, "canal": 1},
            "tenor":     {"patch": 0, "pan": 58, "vol": 62, "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 72, "canal": 3},
        },
        "pedal": True, "expressao": False, "vibrato": False,
        "arpejo": True,  "fill_silencio": True,
        "reverb_fs": "0.65", "gain_fs": "0.65",
        "velocity_scale": 0.82,  # reduz globalmente para soar mais delicado
    },

    "musicbox_arpejado": {
        "descricao": "Caixinha de música com arpejo sacro v3 completo e reverb generoso",
        "modo": "simples",
        "soundfont": "MusicBox.sf2",
        "vozes": {
            "soprano":   {"patch": 10, "pan": 70, "vol": 92, "canal": 0},
            "contralto": {"patch": 10, "pan": 58, "vol": 62, "canal": 1},
            "tenor":     {"patch": 10, "pan": 54, "vol": 60, "canal": 2},
            "baixo":     {"patch": 10, "pan": 64, "vol": 75, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": True,  "fill_silencio": True,
        "reverb_fs": "0.70", "gain_fs": "0.65",
        "velocity_scale": 0.78,
    },

    # === NOVAS VERSÕES DE ÓRGÃOS (Geração 2) ===
    "01_orgao_igreja_musescore": {
        "descricao": "Orgao de Igreja MuseScore - Timbre Unico",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 19, "pan": 58, "vol": 82, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "02_orgao_reed_musescore": {
        "descricao": "Harmonio Reed MuseScore - Timbre Unico",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 20, "pan": 68, "vol": 92, "canal": 0},
            "contralto": {"patch": 20, "pan": 60, "vol": 82, "canal": 1},
            "tenor":     {"patch": 20, "pan": 58, "vol": 80, "canal": 2},
            "baixo":     {"patch": 20, "pan": 64, "vol": 88, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "03_orgao_igreja_timbres": {
        "descricao": "Orgao de Igreja Timbres of Heaven - Timbre Unico",
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 19, "pan": 58, "vol": 82, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "04_orgao_reed_timbres": {
        "descricao": "Harmonio Reed Timbres of Heaven - Timbre Unico",
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 20, "pan": 68, "vol": 92, "canal": 0},
            "contralto": {"patch": 20, "pan": 60, "vol": 82, "canal": 1},
            "tenor":     {"patch": 20, "pan": 58, "vol": 80, "canal": 2},
            "baixo":     {"patch": 20, "pan": 64, "vol": 88, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "05_orgao_igreja_crisis": {
        "descricao": "Orgao de Igreja Crisis - Timbre Unico",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 19, "pan": 58, "vol": 82, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "06_orgao_igreja_sgm": {
        "descricao": "Orgao de Igreja SGM - Timbre Unico",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 19, "pan": 58, "vol": 82, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "07_orgao_misto_ccb": {
        "descricao": "Orgao Hibrido Sacro CCB",
        "modo": "hibrido",
        "grupos": {
            "soprano_tenor": {
                "vozes": ["soprano", "tenor"],
                "soundfont": "Timbres_of_Heaven.sf2",
                "patches_especificos": {"soprano": 19, "tenor": 19},
                "vol": 92,
                "pan": {"soprano": 68, "tenor": 52},
            },
            "contralto_baixo": {
                "vozes": ["contralto", "baixo"],
                "soundfont": "MuseScore_General.sf2",
                "patches_especificos": {"contralto": 19, "baixo": 19},
                "vol": 88,
                "pan": {"contralto": 58, "baixo": 64},
            },
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "08_orgao_pleno_arpejado": {
        "descricao": "Orgao Pleno Timbres de Igreja com Arpejo Simples",
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 19, "pan": 58, "vol": 82, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": False, "vibrato": False,
        "arpejo": True, "fill_silencio": True,
        "reverb_fs": "0.60", "gain_fs": "0.78",
        "humanizacao": "nova",
        "arpejo_tipo": "simples",
    },

    # === NOVAS VERSÕES DE ORQUESTRA (Geração 3) ===
    "01_orq_ccb_classica": {
        "descricao": "Orquestra CCB classica e equilibrada: Flauta, Clarinete, Viola, Cello",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},  # Flute
            "contralto": {"patch": 71, "pan": 56, "vol": 85, "canal": 1},  # Clarinet
            "tenor":     {"patch": 41, "pan": 48, "vol": 82, "canal": 2},  # Viola
            "baixo":     {"patch": 42, "pan": 64, "vol": 88, "canal": 3},  # Cello
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "02_orq_orgao_fundo": {
        "descricao": "Orquestra com Orgao de Fundo (SGM + Orgao Timbres de Fundo)",
        "modo": "hibrido",
        "grupos": {
            "orquestra": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 73, "contralto": 71, "tenor": 42, "baixo": 70}, # Flute, Clarinet, Cello, Bassoon
                "vol": 92,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
            "orgao_fundo": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "Timbres_of_Heaven.sf2",
                "patches_especificos": {"soprano": 19, "contralto": 19, "tenor": 19, "baixo": 19}, # Church Organ
                "vol": 65,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "03_orq_metais_suaves": {
        "descricao": "Metais Suaves Estilo Banda: Flugelhorn, Trompa, Trombone, Tuba",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 88, "canal": 0},  # Trumpet (suave)
            "contralto": {"patch": 60, "pan": 56, "vol": 86, "canal": 1},  # Horn
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},  # Trombone
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},  # Tuba
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "04_orq_cordas_completas": {
        "descricao": "Cordas Completas: Violino 1, Violino 2/Viola, Viola/Cello, Cello grave",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 40, "pan": 68, "vol": 96, "canal": 0},  # Violin
            "contralto": {"patch": 41, "pan": 56, "vol": 85, "canal": 1},  # Viola
            "tenor":     {"patch": 42, "pan": 48, "vol": 84, "canal": 2},  # Cello
            "baixo":     {"patch": 42, "pan": 64, "vol": 90, "canal": 3},  # Cello grave
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "05_orq_madeiras_delicadas": {
        "descricao": "Madeiras Delicadas: Flauta, Oboé, Corne Inglês, Fagote",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},  # Flute
            "contralto": {"patch": 68, "pan": 56, "vol": 82, "canal": 1},  # Oboe
            "tenor":     {"patch": 69, "pan": 48, "vol": 82, "canal": 2},  # English Horn
            "baixo":     {"patch": 70, "pan": 64, "vol": 88, "canal": 3},  # Bassoon
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.45", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "06_orq_hinario_cantado": {
        "descricao": "Hinário Cantado: Flauta+Violino, Clarinete+Viola, Trompa+Cello, Fagote+Cello",
        "modo": "hibrido",
        "grupos": {
            "sopros_madeiras": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 73, "contralto": 71, "tenor": 60, "baixo": 70}, # Flute, Clarinet, Horn, Bassoon
                "vol": 90,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
            "cordas": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "CrisisGeneralMidi301.sf2",
                "patches_especificos": {"soprano": 40, "contralto": 41, "tenor": 42, "baixo": 42}, # Violin, Viola, Cello, Cello
                "vol": 85,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.52", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "07_orq_piano_leve": {
        "descricao": "Piano + Orquestra Leve: Piano Base + Flauta, Clarinete, Viola, Cello",
        "modo": "hibrido",
        "grupos": {
            "piano": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "Equinox_Grand_Pianos.sf2",
                "patches_especificos": {"soprano": 0, "contralto": 0, "tenor": 0, "baixo": 0},
                "vol": 75,
                "pan": {"soprano": 68, "contralto": 60, "tenor": 54, "baixo": 64},
            },
            "orquestra": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 73, "contralto": 71, "tenor": 41, "baixo": 42}, # Flute, Clarinet, Viola, Cello
                "vol": 88,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": True, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "08_orq_orgao_metais": {
        "descricao": "Órgão + Metais Leves: Trompete, Trompa, Trombone, Tuba + Órgão",
        "modo": "hibrido",
        "grupos": {
            "metais": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 56, "contralto": 60, "tenor": 57, "baixo": 58}, # Trumpet, Horn, Trombone, Tuba
                "vol": 90,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
            "orgao": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "Timbres_of_Heaven.sf2",
                "patches_especificos": {"soprano": 19, "contralto": 19, "tenor": 19, "baixo": 19}, # Church Organ
                "vol": 65,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "09_orq_grande": {
        "descricao": "Orquestra Grande: Flauta+Violino, Clarinete+Viola, Trompa+Cello, Fagote+Tuba+Cello",
        "modo": "hibrido",
        "grupos": {
            "madeiras_metais": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 73, "contralto": 71, "tenor": 60, "baixo": 58}, # Flute, Clarinet, Horn, Tuba
                "vol": 88,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
            "cordas_baixo": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "CrisisGeneralMidi301.sf2",
                "patches_especificos": {"soprano": 40, "contralto": 41, "tenor": 42, "baixo": 70}, # Violin, Viola, Cello, Bassoon
                "vol": 85,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "10_orq_tradicional_banda": {
        "descricao": "Tradicional CCB Banda: Clarinete, Trompa, Trombone, Tuba",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 71, "pan": 68, "vol": 95, "canal": 0},  # Clarinet
            "contralto": {"patch": 60, "pan": 56, "vol": 86, "canal": 1},  # Horn
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},  # Trombone
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},  # Tuba
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "11_orq_favorita_ia": {
        "descricao": "Favorita da IA: Flauta+Violino, Clarinete+Viola, Trompa+Cello, Fagote+Cello/Tuba",
        "modo": "hibrido",
        "grupos": {
            "sopros": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "SGM-V2.01.sf2",
                "patches_especificos": {"soprano": 73, "contralto": 71, "tenor": 60, "baixo": 70}, # Flute, Clarinet, Horn, Bassoon
                "vol": 92,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
            "cordas": {
                "vozes": ["soprano", "contralto", "tenor", "baixo"],
                "soundfont": "CrisisGeneralMidi301.sf2",
                "patches_especificos": {"soprano": 40, "contralto": 41, "tenor": 42, "baixo": 42}, # Violin, Viola, Cello, Cello
                "vol": 88,
                "pan": {"soprano": 68, "contralto": 56, "tenor": 48, "baixo": 64},
            },
        },
        "pedal": False, "expressao": True, "vibrato": True,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },

    # === NOVAS VERSÕES DE METAIS (Geração 4) ===
    "01_met_quarteto_tradicional": {
        "descricao": "Quarteto Tradicional de Metais: Trompete, Trompa, Trombone, Tuba",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 85, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "02_met_metais_suaves": {
        "descricao": "Metais Suaves: Flugelhorn, Trompa, Eufonio, Tuba suave",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 90, "canal": 0},  # Muted Trumpet (Flugelhorn style)
            "contralto": {"patch": 60, "pan": 56, "vol": 84, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 80, "canal": 2},  # Trombone (soft)
            "baixo":     {"patch": 58, "pan": 64, "vol": 84, "canal": 3},  # Tuba (soft)
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.78",
        "humanizacao": "nova",
    },
    "03_met_solene_cheio": {
        "descricao": "Solene e Cheio: Trompete, Trompa, Eufonio, Tuba",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 98, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 88, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 85, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.82",
        "humanizacao": "nova",
    },
    "04_met_mais_encorpado": {
        "descricao": "Mais Encorpado: Cornet/Trompete suave, Trompa, Trombone, Trombone Baixo/Tuba",
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 94, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 86, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "05_met_estilo_banda": {
        "descricao": "Estilo Banda: Trompete, Trompete 2, Trombone, Tuba",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 96, "canal": 0},
            "contralto": {"patch": 56, "pan": 58, "vol": 86, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.45", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "06_met_hino_calmo": {
        "descricao": "Hino Calmo: Flugelhorn, Trompa, Eufonio, Tuba leve",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 88, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 78, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 76, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 80, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.75",
        "humanizacao": "nova",
    },
    "07_met_hino_forte": {
        "descricao": "Hino Forte: Trompete, Trompa, Trombone tenor, Tuba",
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 90,  "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 88,  "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 94,  "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.82",
        "humanizacao": "nova",
    },
    "08_met_som_mais_nobre": {
        "descricao": "Som Mais Nobre: Trompete com pouca forca, Trompa, Trompa grave, Tuba",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 92, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 84, "canal": 1},
            "tenor":     {"patch": 60, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 86, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "09_met_gravacao_suave": {
        "descricao": "Gravação Suave MIDI: Flugelhorn, French Horn, Euphonium, Tuba",
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 90, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 82, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 80, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 84, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.78",
        "humanizacao": "nova",
    },
    "10_met_metais_graves": {
        "descricao": "Metais Graves: Trompete suave, Trompa, Eufonio, Trombone baixo",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 59, "pan": 68, "vol": 90, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 82, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},
            "baixo":     {"patch": 57, "pan": 64, "vol": 88, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.50", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "11_met_clima_sacro": {
        "descricao": "Clima de Orquestra Sacra: Trompete, Trompa, Trombone, Tuba + Brass Section",
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 60, "pan": 56, "vol": 85, "canal": 1},
            "tenor":     {"patch": 57, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},
        },
        "pad_strings": True,
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.55", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
    "12_met_estudo_vozes": {
        "descricao": "Estudo das Vozes de Metais: Trompete, Trompa, Trombone, Tuba (panning extremo)",
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 20,  "vol": 95, "canal": 0},
            "contralto": {"patch": 60, "pan": 50,  "vol": 85, "canal": 1},
            "tenor":     {"patch": 57, "pan": 78,  "vol": 85, "canal": 2},
            "baixo":     {"patch": 58, "pan": 108, "vol": 90, "canal": 3},
        },
        "pedal": False, "expressao": True, "vibrato": False,
        "arpejo": False, "fill_silencio": False,
        "reverb_fs": "0.40", "gain_fs": "0.80",
        "humanizacao": "nova",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Orquestração (modo simples) — remapeia canais, programas, volume e pan
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_orquestracao(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    preset_cfg: dict,
    log: logging.Logger
) -> tuple[mido.MidiFile, Path]:
    """
    Remapeia as trilhas SATB para canais/programas do preset (modo simples).
    Inclui pad de strings secundário para 'orquestra_completa'.
    Retorna (midi_orquestrado, caminho_sf2).
    """
    cfg_vozes = preset_cfg.get("vozes", {})
    sf2_nome  = preset_cfg.get("soundfont", "MuseScore_General.sf2")

    try:
        caminho_sf2 = obter_sf2(sf2_nome)
    except FileNotFoundError:
        todas = list(SOUNDFONTS.values())
        caminho_sf2 = todas[0] if todas else None
        log.warning(f"SF2 '{sf2_nome}' não encontrada, usando fallback: "
                    f"{caminho_sf2.name if caminho_sf2 else 'nenhum'}")

    log.info(f"🎼 Orquestrando para: {preset_cfg.get('descricao', '')}")
    log.info(f"   SoundFont: {caminho_sf2.name if caminho_sf2 else 'N/A'}")

    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)

    # Copia trilha de meta/tempo
    trilha_tempo = mido.MidiTrack()
    for msg in midi_in.tracks[0]:
        if msg.type not in ("note_on", "note_off", "program_change", "control_change"):
            trilha_tempo.append(msg.copy())
    novo_midi.tracks.append(trilha_tempo)

    inversao = {idx: papel for papel, idx in vozes.items()}

    # Adiciona cada voz remapeada
    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0 or "arpejo" in (trilha.name or "").lower():
            continue

        papel = inversao.get(idx_trilha)
        if not papel or papel not in cfg_vozes:
            continue

        cfg = cfg_vozes[papel]
        canal = cfg["canal"]

        trilha_remap = mido.MidiTrack()
        trilha_remap.name = f"{papel.capitalize()} (v4)"
        trilha_remap.append(mido.Message("program_change", channel=canal, program=cfg["patch"], time=0))
        trilha_remap.append(mido.Message("control_change", channel=canal, control=7,  value=cfg["vol"], time=0))
        trilha_remap.append(mido.Message("control_change", channel=canal, control=10, value=cfg["pan"], time=0))

        # Copia notas e outros eventos remapeando para o canal correto (preserva CCs como expressão, pedal e vibrato)
        time_acumulado = 0
        for msg in trilha:
            time_acumulado += msg.time
            
            # Descarta program_change originais e CCs de volume (7), pan (10), bank (0)
            deve_descartar = False
            if msg.type == "program_change":
                deve_descartar = True
            elif msg.type == "control_change" and msg.control in (0, 7, 10):
                deve_descartar = True
            
            if deve_descartar:
                continue
            
            msg_copia = msg.copy()
            msg_copia.time = time_acumulado
            time_acumulado = 0
            
            if hasattr(msg_copia, "channel"):
                msg_copia.channel = canal
            trilha_remap.append(msg_copia)

        novo_midi.tracks.append(trilha_remap)

    # Copia trilha de arpejo, se existir (deve ir no final)
    for trilha in midi_in.tracks:
        if "arpejo" in (trilha.name or "").lower():
            novo_midi.tracks.append(trilha)

    # Pad de strings secundário (para orquestra_completa — Crisis.md recomendação)
    if preset_cfg.get("pad_strings", False):
        log.info("   Adicionando pad de cordas secundário (String Ensemble)...")
        for papel, cfg in cfg_vozes.items():
            idx_orig = vozes.get(papel)
            if idx_orig is None or idx_orig >= len(midi_in.tracks):
                continue
            trilha_orig = midi_in.tracks[idx_orig]
            canal_pad = cfg["canal"] + 4   # canais 4-7

            trilha_pad = mido.MidiTrack()
            trilha_pad.name = f"{papel.capitalize()} Strings Pad"
            trilha_pad.append(mido.Message("program_change", channel=canal_pad, program=48, time=0))
            trilha_pad.append(mido.Message("control_change", channel=canal_pad, control=7,
                                           value=int(cfg["vol"] * 0.58), time=0))
            trilha_pad.append(mido.Message("control_change", channel=canal_pad, control=10,
                                           value=cfg["pan"], time=0))
            time_acumulado = 0
            for msg in trilha_orig:
                time_acumulado += msg.time
                
                deve_descartar = False
                if msg.type == "program_change":
                    deve_descartar = True
                elif msg.type == "control_change" and msg.control in (0, 7, 10):
                    deve_descartar = True
                    
                if deve_descartar:
                    continue
                    
                msg_copia = msg.copy()
                msg_copia.time = time_acumulado
                time_acumulado = 0
                
                if hasattr(msg_copia, "channel"):
                    msg_copia.channel = canal_pad
                trilha_pad.append(msg_copia)
            novo_midi.tracks.append(trilha_pad)

    return novo_midi, caminho_sf2

# ─────────────────────────────────────────────────────────────────────────────
# Renderização — Modo Simples (FluidSynth único)
# ─────────────────────────────────────────────────────────────────────────────
def renderizar_simples(
    midi_orquestrado: mido.MidiFile,
    caminho_sf2: Path,
    arquivo_mp3: Path,
    preset_cfg: dict,
    log: logging.Logger
):
    """Renderiza com um único FluidSynth, converte e normaliza com FFmpeg."""
    log.info(f"🔊 Renderizando (simples) → {caminho_sf2.name}")

    gain   = preset_cfg.get("gain_fs",   "0.80")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path    = Path(tmpdir)
        arquivo_mid = tmp_path / "orquestrado.mid"
        arquivo_wav = tmp_path / "audio.wav"

        midi_orquestrado.save(str(arquivo_mid))

        cmd_fluid = [
            "fluidsynth",
            "-F", str(arquivo_wav),
            "-O", "float",
            "-T", "wav",
            "-g", gain,
            "--quiet",
            str(caminho_sf2),
            str(arquivo_mid)
        ]
        log.debug(f"FluidSynth: {' '.join(cmd_fluid)}")
        res = subprocess.run(cmd_fluid, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FluidSynth falhou:\n{res.stderr}")

        _wav_para_mp3(arquivo_wav, arquivo_mp3, log)

# ─────────────────────────────────────────────────────────────────────────────
# Renderização — Modo Híbrido (múltiplos FluidSynths + mix FFmpeg)
# ─────────────────────────────────────────────────────────────────────────────
def renderizar_hibrido(
    midi_humanizado: mido.MidiFile,
    vozes: dict[str, int],
    preset_cfg: dict,
    arquivo_mp3: Path,
    log: logging.Logger
):
    """
    Renderiza cada grupo de vozes com seu SF2 próprio via FluidSynth.
    Mixa todos os WAVs resultantes com FFmpeg + normalização EBU R128.
    Arquitetura herdada do v3, generalizada para múltiplos grupos.
    """
    log.info("🔊 Renderizando (híbrido multi-SF2)...")
    grupos = preset_cfg.get("grupos", {})
    ticks_por_beat = midi_humanizado.ticks_per_beat
    gain = preset_cfg.get("gain_fs", "0.80")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wavs_temp: list[Path] = []

        for nome_grupo, cfg_grupo in grupos.items():
            papeis_grupo    = cfg_grupo["vozes"]
            sf2             = obter_sf2(cfg_grupo["soundfont"])
            patches_esp     = cfg_grupo.get("patches_especificos", {})
            vol_grupo       = cfg_grupo.get("vol", 90)
            pans_grupo      = cfg_grupo.get("pan", {})

            log.info(f"  → Subgrupo '{nome_grupo}' [{sf2.name}]: {papeis_grupo}")

            # Cria MIDI temporário apenas com as vozes do grupo
            midi_grupo = mido.MidiFile(type=1, ticks_per_beat=ticks_por_beat)

            trilha_tempo = mido.MidiTrack()
            for msg in midi_humanizado.tracks[0]:
                if msg.type not in ("note_on", "note_off", "program_change", "control_change"):
                    trilha_tempo.append(msg.copy())
            midi_grupo.tracks.append(trilha_tempo)

            canal_local = 0
            for papel in papeis_grupo:
                idx_trilha = vozes.get(papel)
                if idx_trilha is None or idx_trilha >= len(midi_humanizado.tracks):
                    continue
                trilha_orig = midi_humanizado.tracks[idx_trilha]

                patch = patches_esp.get(papel, 0)
                pan   = pans_grupo.get(papel, 64)

                trilha_nova = mido.MidiTrack()
                trilha_nova.name = f"{papel}_{nome_grupo}"
                trilha_nova.append(mido.Message("program_change", channel=canal_local, program=patch, time=0))
                trilha_nova.append(mido.Message("control_change", channel=canal_local, control=7,  value=vol_grupo, time=0))
                trilha_nova.append(mido.Message("control_change", channel=canal_local, control=10, value=pan, time=0))

                time_acumulado = 0
                for msg in trilha_orig:
                    time_acumulado += msg.time
                    
                    deve_descartar = False
                    if msg.type == "program_change":
                        deve_descartar = True
                    elif msg.type == "control_change" and msg.control in (0, 7, 10):
                        deve_descartar = True
                        
                    if deve_descartar:
                        continue
                        
                    msg_copia = msg.copy()
                    msg_copia.time = time_acumulado
                    time_acumulado = 0
                    
                    if hasattr(msg_copia, "channel"):
                        msg_copia.channel = canal_local
                    trilha_nova.append(msg_copia)

                midi_grupo.tracks.append(trilha_nova)
                canal_local += 1

            # Salva e renderiza com FluidSynth
            mid_grupo_path = tmp_path / f"{nome_grupo}.mid"
            wav_grupo_path = tmp_path / f"{nome_grupo}.wav"
            midi_grupo.save(str(mid_grupo_path))

            cmd_fluid = [
                "fluidsynth",
                "-F", str(wav_grupo_path),
                "-O", "float", "-T", "wav",
                "-g", gain, "--quiet",
                str(sf2), str(mid_grupo_path)
            ]
            res = subprocess.run(cmd_fluid, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"FluidSynth falhou para '{nome_grupo}':\n{res.stderr}")

            wavs_temp.append(wav_grupo_path)

        # Aplica pedal no grupo piano do híbrido (se configurado)
        # Nota: já aplicado no midi_humanizado antes de chegar aqui

        _mixar_wavs(wavs_temp, arquivo_mp3, log)

# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg — Conversão WAV → MP3 e Mixagem
# ─────────────────────────────────────────────────────────────────────────────
def _wav_para_mp3(arquivo_wav: Path, arquivo_mp3: Path, log: logging.Logger):
    """Normaliza (EBU R128 -14 LUFS) e converte WAV → MP3 de alta qualidade."""
    filtros = (
        "alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false,"
        "loudnorm=I=-14:TP=-1:LRA=11"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(arquivo_wav),
        "-af", filtros,
        "-q:a", "0",           # VBR máxima qualidade
        "-map_metadata", "-1", # limpa metadados
        "-loglevel", "error",
        str(arquivo_mp3)
    ]
    log.info("  → Normalizando EBU R128 e convertendo para MP3...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg falhou:\n{res.stderr}")

def _mixar_wavs(wavs: list[Path], arquivo_mp3: Path, log: logging.Logger):
    """Mixa múltiplos WAVs com amix, normaliza e converte para MP3."""
    log.info(f"  → Mixando {len(wavs)} subgrupos e normalizando...")
    cmd = ["ffmpeg", "-y"]
    for wav in wavs:
        cmd.extend(["-i", str(wav)])

    filtro = (
        f"amix=inputs={len(wavs)}:duration=longest:dropout_transition=2,"
        f"alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false,"
        f"loudnorm=I=-14:TP=-1:LRA=11"
    )
    cmd.extend([
        "-filter_complex", filtro,
        "-q:a", "0",
        "-map_metadata", "-1",
        "-loglevel", "error",
        str(arquivo_mp3)
    ])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg (mix) falhou:\n{res.stderr}")

# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Principal de Processamento
# ─────────────────────────────────────────────────────────────────────────────
def processar_midi(
    caminho_mid: Path,
    preset_nome: str,
    saida_dir: Path,
    seed: int,
    salvar_midi: bool,
    salvar_json: bool,
    reiniciar: bool,
    debug: bool,
    log: logging.Logger,
    conn: sqlite3.Connection | None = None
) -> bool:
    """
    Pipeline completo Geração 4:
    1. Carrega MIDI
    2. Detecta SATB
    3. Detecta estrutura do hino (Intro/Corpo/Final)
    4. Aplica Arpejo Sacro v3 (se preset pedir)
    5. Humanização v4 (delays + velocities orgânicas por seção)
    6. Pedal de sustain (se preset pedir)
    7. CC11 Expressão + CC1 Vibrato (se preset pedir)
    8. Renderização (simples ou híbrida)
    9. Salva MIDI intermediário + JSON de parâmetros
    """
    preset_cfg = PRESETS_V4.get(preset_nome)
    if preset_cfg is None:
        log.error(f"Preset '{preset_nome}' desconhecido. Disponíveis: {list(PRESETS_V4.keys())}")
        return False

    arquivo_mp3 = saida_dir / f"{caminho_mid.stem}.mp3"

    # Verifica progresso no SQLite
    if conn and not reiniciar and ja_concluido(conn, caminho_mid.name, preset_nome):
        log.info(f"  ⏭ Já renderizado (DB): {caminho_mid.name} [{preset_nome}] — pulando.")
        return True
    if arquivo_mp3.exists() and not reiniciar:
        log.info(f"  ⏭ Arquivo já existe: {arquivo_mp3.name} — pulando (use --reiniciar).")
        return True

    saida_dir.mkdir(parents=True, exist_ok=True)
    if conn:
        registrar_pendente(conn, caminho_mid.name, preset_nome)

    log.info("=" * 66)
    log.info(f" Geração 4 — {caminho_mid.name}")
    log.info(f" Preset: {preset_nome}")
    log.info(f" {preset_cfg['descricao']}")
    log.info("=" * 66)

    try:
        # 1. Carrega MIDI original
        midi = mido.MidiFile(str(caminho_mid))

        # 2. Detecção SATB
        vozes = detectar_vozes_satb(midi, log)

        # Salva log de análise SATB
        with open(saida_dir / "analise_satb.txt", "w", encoding="utf-8") as f:
            f.write(f"Análise SATB — {caminho_mid.name}\n")
            f.write(f"Data: {datetime.now().isoformat()}\n")
            f.write(f"Preset: {preset_nome}\n\n")
            for voz, idx in vozes.items():
                nome_t = midi.tracks[idx].name if midi.tracks[idx].name else f"Trilha {idx}"
                f.write(f"  {voz.capitalize():10s}: Trilha #{idx} ({nome_t})\n")

        # 3. Detecta estrutura do hino
        estrutura = detectar_estrutura_hino(midi, vozes, log)

        # 4. Arpejo Sacro v3 (antes da humanização, para incluir a nova trilha)
        midi_processado = midi
        if preset_cfg.get("arpejo", False):
            midi_processado = aplicar_arpejo_sacro_v3(midi_processado, vozes, preset_cfg, log)

        # 5. Humanização v4 (delays + velocities orgânicas + micro-roll + dinâmica de frase)
        midi_processado = aplicar_humanizacao_v4(
            midi_processado, vozes, estrutura, preset_cfg, seed, log)

        # 6. Pedal de Sustain CC64 (apenas para presets de piano, modo simples)
        if preset_cfg.get("pedal", False):
            midi_processado = aplicar_pedal_sustain(midi_processado, vozes, log)

        # 7. Expressão CC11 + Vibrato CC1 (cordas, sopros, coro)
        if preset_cfg.get("expressao", False) or preset_cfg.get("vibrato", False):
            midi_processado = aplicar_expressao_vibrato(
                midi_processado, estrutura, log,
                cc11=preset_cfg.get("expressao", False),
                cc1 =preset_cfg.get("vibrato",   False)
            )

        # 8. Salva MIDI humanizado intermediário
        if salvar_midi:
            mid_out = saida_dir / f"{caminho_mid.stem}_humanizado.mid"
            midi_processado.save(str(mid_out))
            log.info(f"  💾 MIDI humanizado salvo: {mid_out.name}")

        # 9. Renderização
        if preset_cfg.get("modo") == "hibrido":
            renderizar_hibrido(midi_processado, vozes, preset_cfg, arquivo_mp3, log)
        else:
            midi_orquestrado, caminho_sf2 = aplicar_orquestracao(
                midi_processado, vozes, preset_cfg, log)
            renderizar_simples(midi_orquestrado, caminho_sf2, arquivo_mp3, preset_cfg, log)

        # 10. Salva JSON de parâmetros
        if salvar_json:
            def limpar_para_json(obj):
                if isinstance(obj, dict):
                    return {k: limpar_para_json(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [limpar_para_json(x) for x in obj]
                elif isinstance(obj, Path):
                    return str(obj)
                else:
                    return obj

            params_recebidos = {
                "caminho_mid": caminho_mid,
                "preset_nome": preset_nome,
                "saida_dir": saida_dir,
                "seed": seed,
                "salvar_midi": salvar_midi,
                "salvar_json": salvar_json,
                "reiniciar": reiniciar,
                "debug": debug,
            }

            params = {
                "geracao":          4,
                "mid_original":     caminho_mid.name,
                "preset":           preset_nome,
                "descricao":        preset_cfg["descricao"],
                "modo":             preset_cfg.get("modo", "simples"),
                "soundfont":        preset_cfg.get("soundfont", "hibrido"),
                "seed":             seed,
                "arpejo_v3":        preset_cfg.get("arpejo",        False),
                "fill_silencio":    preset_cfg.get("fill_silencio", False),
                "pedal_cc64":       preset_cfg.get("pedal",         False),
                "expressao_cc11":   preset_cfg.get("expressao",     False),
                "vibrato_cc1":      preset_cfg.get("vibrato",       False),
                "vozes_satb":       vozes,
                "estrutura_hino":   {k: [int(v[0]), int(v[1])] for k, v in estrutura.items()},
                "data_processamento": datetime.now().isoformat(),
                "parametros_recebidos": limpar_para_json(params_recebidos),
                "configuracao_interna": limpar_para_json(preset_cfg)
            }
            with open(saida_dir / "parametros.json", "w", encoding="utf-8") as f:
                json.dump(limpar_para_json(params), f, indent=4, ensure_ascii=False)
            log.info("  💾 Parâmetros salvos em parametros.json")

        log.info(f"  ✅ Sucesso! MP3 em: {arquivo_mp3}")

        if conn:
            marcar_concluido(conn, caminho_mid.name, preset_nome, str(arquivo_mp3))
        return True

    except Exception as e:
        log.error(f"  ❌ Erro: {e}", exc_info=debug)
        if conn:
            marcar_erro(conn, caminho_mid.name, preset_nome, str(e))
        return False

# ─────────────────────────────────────────────────────────────────────────────
# CLI Principal
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="renderizador4.py — Geração 4: Humanizador MIDI Sacro Avançado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Presets disponíveis: " + ", ".join(PRESETS_V4.keys())
    )
    parser.add_argument("--mid", type=str,
                        help="Arquivo MIDI (omita para processar toda a pasta mid/)")
    parser.add_argument("--preset", type=str, default="equinox_sacro",
                        choices=list(PRESETS_V4.keys()),
                        help="Preset de renderização")
    parser.add_argument("--saida-dir", type=str,
                        help="Diretório de saída (padrão: output4/{stem}/{preset}/)")
    parser.add_argument("--seed",       type=int,  default=42,
                        help="Seed para humanização determinística")
    parser.add_argument("--reiniciar",  action="store_true",
                        help="Sobrescreve arquivos já gerados")
    parser.add_argument("--salvar-midi", action="store_true", default=True,
                        help="Salva MIDI humanizado intermediário")
    parser.add_argument("--salvar-json", action="store_true", default=True,
                        help="Salva parâmetros em JSON")
    parser.add_argument("--debug",      action="store_true",
                        help="Logs detalhados de depuração")
    parser.add_argument("--listar-presets", action="store_true",
                        help="Lista os 6 presets disponíveis e sai")
    parser.add_argument("--status",     action="store_true",
                        help="Mostra progresso do banco SQLite e sai")

    args = parser.parse_args()
    log = configurar_log(verbose=args.debug)

    # ── Listar presets ─────────────────────────────────────────────────────
    if args.listar_presets:
        print("\n=== Presets Geração 4 ===\n")
        for nome, cfg in PRESETS_V4.items():
            modo = cfg.get("modo", "simples").upper()
            print(f"  {nome:22s} [{modo:7s}] — {cfg['descricao']}")
        print()
        return

    # ── Banco SQLite ───────────────────────────────────────────────────────
    conn = abrir_banco()

    # ── Status do banco ────────────────────────────────────────────────────
    if args.status:
        rows = conn.execute(
            "SELECT arquivo_mid, preset, status, data_conclusao "
            "FROM renders4 ORDER BY data_conclusao DESC LIMIT 40"
        ).fetchall()
        print("\n=== Status Banco de Progresso (v4) ===\n")
        for r in rows:
            print(f"  [{r['status']:10s}] {r['arquivo_mid'][:32]:32s} / {r['preset']}")
        totais = conn.execute(
            "SELECT status, COUNT(*) n FROM renders4 GROUP BY status"
        ).fetchall()
        print()
        for t in totais:
            print(f"  {t['status']:12s}: {t['n']}")
        print()
        return

    # ── Modo arquivo único ─────────────────────────────────────────────────
    if args.mid:
        caminho_mid = Path(args.mid)
        if not caminho_mid.exists():
            caminho_mid = MID_DIR / args.mid
            if not caminho_mid.exists():
                log.error(f"MIDI não encontrado: {args.mid}")
                sys.exit(1)

        saida_dir = (
            Path(args.saida_dir) if args.saida_dir
            else OUTPUT_DIR / caminho_mid.stem / args.preset
        )

        ok = processar_midi(
            caminho_mid=caminho_mid,
            preset_nome=args.preset,
            saida_dir=saida_dir,
            seed=args.seed,
            salvar_midi=args.salvar_midi,
            salvar_json=args.salvar_json,
            reiniciar=args.reiniciar,
            debug=args.debug,
            log=log,
            conn=conn,
        )
        sys.exit(0 if ok else 2)

    # ── Modo lote (toda a pasta mid/) ──────────────────────────────────────
    midis = sorted(p for p in MID_DIR.glob("*.mid") if not p.name.startswith("._"))
    if not midis:
        log.error(f"Nenhum .mid encontrado em {MID_DIR}")
        sys.exit(1)

    log.info(f"Modo lote: {len(midis)} arquivo(s) em {MID_DIR} | Preset: {args.preset}")
    sucessos, falhas = [], []

    for caminho_mid in midis:
        saida_dir = OUTPUT_DIR / caminho_mid.stem / args.preset
        ok = processar_midi(
            caminho_mid=caminho_mid,
            preset_nome=args.preset,
            saida_dir=saida_dir,
            seed=args.seed,
            salvar_midi=args.salvar_midi,
            salvar_json=args.salvar_json,
            reiniciar=args.reiniciar,
            debug=args.debug,
            log=log,
            conn=conn,
        )
        (sucessos if ok else falhas).append(caminho_mid.name)

    log.info("=" * 66)
    log.info(f"Lote concluído: {len(sucessos)}/{len(midis)} com sucesso")
    if falhas:
        log.warning(f"Falhas: {falhas}")
    sys.exit(0 if not falhas else 2)


if __name__ == "__main__":
    main()
