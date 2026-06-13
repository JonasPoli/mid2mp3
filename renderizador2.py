#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador2.py — Nova geração do conversor MIDI → MP3 com detecção inteligente SATB,
humanização avançada (micro-roll, delays, dinâmica por frase, pedal inteligente, expressão CC11,
vibrato CC1) e 14 presets orquestrais/instrumentais.
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
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
BASE_DIR = Path(__file__).parent.resolve()
MID_DIR = BASE_DIR / "mid"
OUTPUT_DIR = BASE_DIR / "output2"
SOUNDFONTS_DIR = BASE_DIR / "soundfonts"

# ─────────────────────────────────────────────────────────────────────────────
# Descoberta dinâmica de SoundFonts
# ─────────────────────────────────────────────────────────────────────────────
def descobrir_soundfonts() -> dict[str, Path]:
    sf2s = {}
    if SOUNDFONTS_DIR.exists():
        for sf2 in sorted(SOUNDFONTS_DIR.glob("*.sf2")):
            if sf2.name.startswith("."):
                continue
            nome_cli = sf2.stem.replace(" ", "_").replace("-", "_").lower()
            sf2s[nome_cli] = sf2
    return sf2s

SOUNDFONTS = descobrir_soundfonts()

def obter_caminho_sf2(nome_desejado: str) -> Path | None:
    """Busca aproximada para encontrar o arquivo .sf2 no dicionário de soundfonts."""
    nome_norm = nome_desejado.replace(" ", "_").replace("-", "_").lower()
    for k, v in SOUNDFONTS.items():
        if nome_norm in k or k in nome_norm:
            return v
    # Tenta busca direta por arquivo caso o nome do preset seja o nome do arquivo
    caminho_direto = SOUNDFONTS_DIR / nome_desejado
    if caminho_direto.exists():
        return caminho_direto
    caminho_sf2 = SOUNDFONTS_DIR / f"{nome_desejado}.sf2"
    if caminho_sf2.exists():
        return caminho_sf2
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Configuração do Logger
# ─────────────────────────────────────────────────────────────────────────────
def configurar_log(verbose: bool = False) -> logging.Logger:
    nivel = logging.DEBUG if verbose else logging.INFO
    # Limpa handlers anteriores para evitar duplicação
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout
    )
    return logging.getLogger("renderizador2")

# ─────────────────────────────────────────────────────────────────────────────
# Detecção Inteligente SATB
# ─────────────────────────────────────────────────────────────────────────────
def detectar_vozes_satb(midi: mido.MidiFile, log: logging.Logger) -> dict[str, int]:
    """
    Detecta as vozes SATB a partir das trilhas do MIDI.
    Retorna um dicionário mapeando 'soprano', 'contralto', 'tenor', 'baixo' para o índice da trilha.
    """
    log.info("🔍 Iniciando detecção de vozes SATB...")
    trilhas_com_notas = []
    
    for idx, t in enumerate(midi.tracks):
        notas = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if notas:
            # Coleta metadados
            nome_trilha = t.name.strip() if t.name else f"Trilha {idx}"
            trilhas_com_notas.append({
                "index": idx,
                "track": t,
                "name": nome_trilha,
                "notes_count": len(notas),
                "min_note": min(notas),
                "max_note": max(notas),
                "median_note": sorted(notas)[len(notas) // 2]
            })

    if not trilhas_com_notas:
        log.warning("Nenhuma trilha com notas encontrada!")
        return {"soprano": 0, "contralto": 0, "tenor": 0, "baixo": 0}

    # Exibe logs de análise estatística
    log.info("Estatísticas das trilhas encontradas:")
    for t_meta in trilhas_com_notas:
        log.info(f"  Trilha #{t_meta['index']} ('{t_meta['name']}'): "
                 f"{t_meta['notes_count']} notas, Mín={t_meta['min_note']}, Máx={t_meta['max_note']}, Mediana={t_meta['median_note']}")

    # 1. Tenta identificar pelo nome
    vozes_mapeadas = {}
    papeis = {
        "soprano": ["soprano", "sop", "s", "cantus", "melody", "melodia", "vocal"],
        "contralto": ["contralto", "alto", "a"],
        "tenor": ["tenor", "ten", "t"],
        "baixo": ["baixo", "bass", "b"]
    }
    
    trilhas_restantes = trilhas_com_notas[:]
    for papel, termos in papeis.items():
        for t_meta in list(trilhas_restantes):
            nome_l = t_meta["name"].lower()
            # Verifica se o termo casa com a palavra inteira ou padrão delimitado
            if any(re.search(rf"\b{termo}\b", nome_l) or nome_l == termo for termo in termos):
                vozes_mapeadas[papel] = t_meta["index"]
                trilhas_restantes.remove(t_meta)
                break

    # Se conseguiu mapear os 4 principais por nome, retorna direto
    if len(vozes_mapeadas) == 4:
        log.info(f"Mapeamento SATB completo via nomes das trilhas: {vozes_mapeadas}")
        return vozes_mapeadas

    # 2. Fallback: Se faltar vozes, ordena as trilhas restantes (ou todas, se poucas batidas de nome)
    # pela mediana da altura das notas (mediana maior = voz mais aguda)
    # Para garantir robustez, ordenamos tudo que sobrou
    trilhas_restantes.sort(key=lambda x: x["median_note"], reverse=True)
    
    # Se temos pelo menos 4 trilhas, vamos usar as 4 principais ordenadas
    vozes_ordenadas = ["soprano", "contralto", "tenor", "baixo"]
    
    # Preenche o que sobrou respeitando a ordem
    idx_ord = 0
    for papel in vozes_ordenadas:
        if papel not in vozes_mapeadas and idx_ord < len(trilhas_restantes):
            vozes_mapeadas[papel] = trilhas_restantes[idx_ord]["index"]
            idx_ord += 1

    # Fallbacks de emergência se houver poucas trilhas
    if "soprano" not in vozes_mapeadas and trilhas_com_notas:
        vozes_mapeadas["soprano"] = trilhas_com_notas[0]["index"]
    if "contralto" not in vozes_mapeadas:
        vozes_mapeadas["contralto"] = vozes_mapeadas.get("soprano", 0)
    if "tenor" not in vozes_mapeadas:
        vozes_mapeadas["tenor"] = vozes_mapeadas.get("contralto", 0)
    if "baixo" not in vozes_mapeadas:
        vozes_mapeadas["baixo"] = vozes_mapeadas.get("tenor", 0)

    log.info(f"Classificação final SATB: {vozes_mapeadas}")
    return vozes_mapeadas

# ─────────────────────────────────────────────────────────────────────────────
# Funções de Apoio Rítmico / Tempo
# ─────────────────────────────────────────────────────────────────────────────
def obter_tempo_midi(midi: mido.MidiFile) -> int:
    """Retorna o tempo (µs/beat) do primeiro set_tempo encontrado."""
    for track in midi.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500_000  # Fallback 120 BPM

def converter_ms_para_ticks(ms: float, ticks_por_beat: int, tempo_us: int) -> int:
    """Converte um intervalo em milissegundos para ticks MIDI."""
    # ticks_por_segundo = ticks_por_beat * 1_000_000 / tempo_us
    # ticks = ms / 1000 * ticks_por_segundo
    return int(round((ms / 1000.0) * (ticks_por_beat * 1_000_000 / tempo_us)))

def converter_ticks_para_ms(ticks: int, ticks_por_beat: int, tempo_us: int) -> float:
    """Converte ticks MIDI para milissegundos."""
    return (ticks / ticks_por_beat) * (tempo_us / 1000.0)

def converter_trilha_para_ticks_absolutos(track: mido.MidiTrack) -> list[list]:
    """Converte a trilha MIDI (com delta times) para uma lista de eventos com ticks absolutos."""
    eventos_abs = []
    tick_acumulado = 0
    for msg in track:
        tick_acumulado += msg.time
        eventos_abs.append([tick_acumulado, msg.copy()])
    return eventos_abs

def converter_ticks_absolutos_para_trilha(eventos_abs: list) -> mido.MidiTrack:
    """Converte de volta uma lista de eventos com ticks absolutos em mido.MidiTrack."""
    eventos_abs.sort(key=lambda x: x[0])
    track = mido.MidiTrack()
    tick_prev = 0
    for tick_abs, msg in eventos_abs:
        delta = max(0, tick_abs - tick_prev)
        msg.time = delta
        track.append(msg)
        tick_prev = tick_abs
    return track

# ─────────────────────────────────────────────────────────────────────────────
# Humanização Avançada
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_humanizacao(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    preset: str,
    seed: int | None = None,
    log: logging.Logger | None = None
) -> mido.MidiFile:
    """
    Aplica atrasos aleatórios por voz usando a interpolação contínua por onsets de acordes.
    Evita gaps de silêncio e sobreposições, mantendo a transição de notas contíguas (legato).
    """
    if log is None:
        log = logging.getLogger("renderizador2")

    log.info(f"🎻 Aplicando humanização (Preset: {preset}, Seed: {seed})")
    
    rng = random.Random(seed if seed is not None else 42)
    
    tempo_us = obter_tempo_midi(midi_in)
    ticks_por_beat = midi_in.ticks_per_beat
    is_piano = "piano" in preset.lower() or "equinox" in preset.lower()

    # Desvio de velocidade por papel SATB
    velocity_deltas = {
        "baixo": (-4, 4),
        "tenor": (-6, 5),
        "contralto": (-7, 4),
        "soprano": (-3, 7)
    }

    # 1. Coleta todos os ticks de início de notas (onsets) nas trilhas de vozes e eventos para detecção de frase
    onsets_set = {0}
    todos_eventos_notas = []
    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0 or "_arpejo" in (trilha.name or "").lower():
            continue
        tick_abs = 0
        for msg in trilha:
            tick_abs += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                onsets_set.add(tick_abs)
                todos_eventos_notas.append({"type": "note_on", "tick": tick_abs})
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                todos_eventos_notas.append({"type": "note_off", "tick": tick_abs})
    
    max_tick = max(sum(msg.time for msg in t) for t in midi_in.tracks)
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
        # Atraso aleatório de 0 a 150ms (0 a 0.15s) no corpo e 0 a 200ms (0 a 0.20s) em fim de frase
        limite_delay = 0.20 if eh_fim_de_frase(t, onset_ticks, phrase_ends) else 0.15

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

    # Nova estrutura do MidiFile
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=ticks_por_beat)
    idx_para_voz = {idx: voz for voz, idx in vozes.items()}

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 (tempo/metadados) ou arpejo -> intocada
        if idx_trilha == 0 or "_arpejo" in (trilha.name or "").lower():
            novo_midi.tracks.append(trilha)
            continue

        papel_voz = idx_para_voz.get(idx_trilha, "tenor")
        eventos_abs = converter_trilha_para_ticks_absolutos(trilha)
        v_min, v_max = velocity_deltas.get(papel_voz, (-5, 5))

        for ev in eventos_abs:
            tick_abs, msg = ev
            if msg.type == "note_on" and msg.velocity > 0:
                # Desvio de velocity
                vel_var = rng.randint(v_min, v_max)
                msg.velocity = max(1, min(127, msg.velocity + vel_var))
                
                # Desloca note_on usando delay interpolado
                delay_sec = obter_delay_interpolado(tick_abs, papel_voz)
                delay_ticks = converter_ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = tick_abs + delay_ticks
                
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                # Desloca note_off usando delay interpolado
                delay_sec = obter_delay_interpolado(tick_abs, papel_voz)
                delay_ticks = converter_ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = tick_abs + delay_ticks

        # 3. Dinâmica por frase / bloco
        if is_piano:
            silence_threshold = int(ticks_por_beat * 1.5)
            note_events = sorted([ev for ev in eventos_abs if ev[1].type in ("note_on", "note_off")], key=lambda x: x[0])
            
            limites_frases = []
            inicio_frase = 0
            ultima_nota_fim = 0
            
            for i, ev in enumerate(note_events):
                msg = ev[1]
                if msg.type == "note_on" and msg.velocity > 0:
                    if ev[0] - ultima_nota_fim > silence_threshold and i > 0:
                        limites_frases.append((inicio_frase, ev[0]))
                        inicio_frase = ev[0]
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    ultima_nota_fim = max(ultima_nota_fim, ev[0])
            
            limites_frases.append((inicio_frase, max(ultima_nota_fim, inicio_frase + 1000)))

            # Se detectou poucas frases longas (ex: 1), força quebra por compassos
            if len(limites_frases) <= 2:
                limites_frases = []
                duracao_bloco = ticks_por_beat * 16
                tick_cursor = 0
                while tick_cursor < ultima_nota_fim:
                    limites_frases.append((tick_cursor, tick_cursor + duracao_bloco))
                    tick_cursor += duracao_bloco

            # Aplica a curva dinâmica (crescendo suave no meio da frase, decrescendo no final)
            for inicio, fim in limites_frases:
                duracao = fim - inicio
                if duracao <= 0:
                    continue
                for ev in eventos_abs:
                    tick_abs, msg = ev
                    if inicio <= tick_abs < fim and msg.type == "note_on" and msg.velocity > 0:
                        pos_rel = (tick_abs - inicio) / duracao
                        fator = 0.92 + 0.56 * pos_rel - 0.63 * (pos_rel ** 2)
                        msg.velocity = max(1, min(127, int(round(msg.velocity * fator))))
        else:
            # Não aplica dinâmica de volume (phrasing/block) para órgãos/outros para evitar oscilação de volume
            pass

        # Converte de volta para trilha com delta times
        nova_trilha = converter_ticks_absolutos_para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Automatização do Pedal de Sustain para Piano
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_pedal_sustain_inteligente(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger
) -> mido.MidiFile:
    """
    Simula o pedal CC64 limpando a ressonância nas mudanças de notas graves (Baixo).
    """
    log.info("🎹 Gerando pedal de sustain inteligente...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    
    baixo_idx = vozes.get("baixo", 0)
    baixo_trilha = midi_in.tracks[baixo_idx]
    
    # 1. Mapeia as notas do Baixo no tempo absoluto para saber quando há mudança harmônica
    eventos_baixo = converter_trilha_para_ticks_absolutos(baixo_trilha)
    mudancas_baixo = []  # Ticks onde notas do baixo começam
    for tick_abs, msg in eventos_baixo:
        if msg.type == "note_on" and msg.velocity > 0:
            mudancas_baixo.append(tick_abs)
    mudancas_baixo.sort()

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        # Trilha 0 -> intocada
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue
            
        eventos_abs = converter_trilha_para_ticks_absolutos(trilha)
        
        # Só injeta CC64 nos canais ativos (ignora percussão no canal 9)
        canais_ativos = set()
        for _, msg in eventos_abs:
            if hasattr(msg, "channel") and msg.channel != 9:
                canais_ativos.add(msg.channel)
                
        if not canais_ativos:
            novo_midi.tracks.append(trilha)
            continue
            
        canal = list(canais_ativos)[0]
        pedal_events = []
        
        # Pressiona pedal logo após cada ataque do baixo (+35 ticks),
        # Solta o pedal um instante antes do próximo ataque (-15 ticks)
        for i, t_baixo in enumerate(mudancas_baixo):
            t_on = t_baixo + 35
            pedal_events.append((t_on, mido.Message("control_change", channel=canal, control=64, value=127, time=0)))
            
            if i + 1 < len(mudancas_baixo):
                t_prox = mudancas_baixo[i + 1]
                t_off = max(t_on + 5, t_prox - 15)
                pedal_events.append((t_off, mido.Message("control_change", channel=canal, control=64, value=0, time=0)))
                
        # Adiciona pedal de release no final
        if mudancas_baixo:
            pedal_events.append((eventos_abs[-1][0], mido.Message("control_change", channel=canal, control=64, value=0, time=0)))

        # Injeta
        for ev in pedal_events:
            eventos_abs.append([ev[0], ev[1]])
            
        nova_trilha = converter_ticks_absolutos_para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)
        
    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Vibrato (CC1) e Expressão (CC11) para Cordas e Sopros
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_expressao_e_vibrato(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger,
    vibrato_strings: bool = True,
    expressao_sopros: bool = True
) -> mido.MidiFile:
    """
    Aplica CC11 de expressão crescendo/decrescendo e vibrato progressivo (CC1) em notas longas.
    """
    log.info("🎻 Injetando curvas de Expressão (CC11) e Vibrato (CC1)...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat
    tempo_us = obter_tempo_midi(midi_in)
    
    # Notas mais longas que uma semínima recebem vibrato
    limiar_vibrato = int(ticks_por_beat * 1.1)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue
            
        eventos_abs = converter_trilha_para_ticks_absolutos(trilha)
        canais_ativos = {msg.channel for _, msg in eventos_abs if hasattr(msg, "channel") and msg.channel != 9}
        
        if not canais_ativos:
            novo_midi.tracks.append(trilha)
            continue
            
        canal = list(canais_ativos)[0]
        
        # Ignora trilhas de órgão, piano ou percussão cromática (program < 21)
        eh_excluido = False
        for _, msg in eventos_abs:
            if msg.type == "program_change" and msg.program < 21:
                eh_excluido = True
                break
        if eh_excluido:
            novo_midi.tracks.append(trilha)
            continue
            
        novos_ccs = []

        # Guarda note_on ativas para calcular duração
        notas_ativas = {}
        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = tick_abs
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    t_on = notas_ativas.pop(msg.note)
                    duracao = tick_abs - t_on
                    
                    if duracao >= limiar_vibrato:
                        # 1. CC11 Expressão (crescendo / decrescendo)
                        if expressao_sopros:
                            # 15% de crescendo, 70% sustenta, 15% de decrescendo
                            novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=11, value=80, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.15), mido.Message("control_change", channel=canal, control=11, value=115, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.85), mido.Message("control_change", channel=canal, control=11, value=95, time=0)))
                            novos_ccs.append((tick_abs - 5, mido.Message("control_change", channel=canal, control=11, value=65, time=0)))

                        # 2. CC1 Vibrato Progressivo
                        if vibrato_strings:
                            # Começa em zero, sobe gradativamente após 25% da duração
                            novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=1, value=0, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.25), mido.Message("control_change", channel=canal, control=1, value=10, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.55), mido.Message("control_change", channel=canal, control=1, value=55, time=0)))
                            novos_ccs.append((tick_abs - 10, mido.Message("control_change", channel=canal, control=1, value=0, time=0)))

        # Injeta CCs gerados
        for t_abs, msg_cc in novos_ccs:
            eventos_abs.append([t_abs, msg_cc])
            
        nova_trilha = converter_ticks_absolutos_para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)
        
    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Arpejo Sacro Inteligente v2
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_arpejo_sacro_v2(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    preset: str,
    log: logging.Logger
) -> mido.MidiFile:
    """
    Gera uma trilha de arpejo sacro baseada na harmonia real de cada compasso.
    Garante que o arpejo não sobressaia ao soprano e use velocidade moderada.
    """
    log.info("♫ Aplicando Arpejo Sacro v2...")
    
    # Extração de notas absolutas de cada papel SATB
    def obter_notas_absolutas(track_idx: int) -> list[dict]:
        track = midi_in.tracks[track_idx]
        notas = []
        notas_ativas = {}
        tick_acumulado = 0
        for msg in track:
            tick_acumulado += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = (tick_acumulado, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    start_tick, velocity = notas_ativas.pop(msg.note)
                    notas.append({
                        "note": msg.note,
                        "start": start_tick,
                        "end": tick_acumulado,
                        "velocity": velocity
                    })
        return sorted(notas, key=lambda x: x["start"])

    soprano_notas = obter_notas_absolutas(vozes["soprano"])
    contralto_notas = obter_notas_absolutas(vozes["contralto"])
    tenor_notas = obter_notas_absolutas(vozes["tenor"])
    baixo_notas = obter_notas_absolutas(vozes["baixo"])

    # Descobrir canal MIDI livre
    canais_usados = set()
    for t in midi_in.tracks:
        for msg in t:
            if hasattr(msg, "channel"):
                canais_usados.add(msg.channel)
    canal_livre = next((c for c in range(16) if c != 9 and c not in canais_usados), 15)

    # Identificar mudanças de compasso
    ts_changes = []
    for t in midi_in.tracks:
        tick = 0
        for msg in t:
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

    # Calcular tamanho total em ticks
    max_tick = 0
    for t in midi_in.tracks:
        tick = 0
        for msg in t:
            tick += msg.time
        if tick > max_tick:
            max_tick = tick

    # Define limites dos compassos
    ticks_por_beat = midi_in.ticks_per_beat
    limites_compassos = []
    tick_atual = 0
    while tick_atual < max_tick:
        num, den = ts_em_tick(tick_atual)
        len_measure = int(ticks_por_beat * 4 * num / den)
        if len_measure <= 0:
            len_measure = ticks_por_beat * 4
        limites_compassos.append((tick_atual, tick_atual + len_measure, num, den))
        tick_atual += len_measure

    # Função auxiliar de busca
    def obter_nota_voz_no_tick(vozes_notas: list[dict], tick: int, m_start: int, m_end: int) -> int | None:
        active = [n for n in vozes_notas if n["start"] <= tick < n["end"]]
        if active:
            return active[0]["note"]
        in_measure = [n for n in vozes_notas if m_start <= n["start"] < m_end]
        if in_measure:
            in_measure.sort(key=lambda x: abs(x["start"] - tick))
            return in_measure[0]["note"]
        return None

    # Gerar notas do arpejo
    novos_eventos = []
    rng = random.Random(1337) # semente fixa para o arpejo

    vozes_notas_dict = {
        "baixo": baixo_notas,
        "tenor": tenor_notas,
        "contralto": contralto_notas,
        "soprano": soprano_notas
    }

    # Coleta e agrupa todos os onsets das vozes para detectar mudanças de acorde
    todas_notas = baixo_notas + tenor_notas + contralto_notas + soprano_notas
    onsets_orig = sorted(list(set(n["start"] for n in todas_notas)))
    grouped_onsets = []
    for o in onsets_orig:
        if not grouped_onsets or o - grouped_onsets[-1] > 50:
            grouped_onsets.append(o)

    def adaptar_nota_arpejo(t_on: int, t_off: int, role: str, grouped_onsets: list[int]) -> list[dict]:
        for onset in grouped_onsets:
            if t_on < onset < t_off:
                parte1 = {"start": t_on, "end": onset, "role": role}
                parte2_list = adaptar_nota_arpejo(onset, t_off, role, grouped_onsets)
                return [parte1] + parte2_list
        return [{"start": t_on, "end": t_off, "role": role}]

    for idx_m, (m_start, m_end, num, den) in enumerate(limites_compassos):
        # Determina padrão de compasso simples vs composto
        if den == 8:
            D = 3
            pattern = ['B', 'T', 'A']
        else:
            D = 4
            pattern = ['B', 'T', 'A', 'S']
            
        duracao_nota = int((m_end - m_start) / D)
        if duracao_nota <= 0:
            duracao_nota = 1

        # Encontra a primeira nota da voz ativa mais grave neste compasso
        primeira_nota_do_compasso = None
        for voz_nome in ["baixo", "tenor", "contralto", "soprano"]:
            notas_voz_comp = [
                n for n in vozes_notas_dict[voz_nome]
                if m_start <= n["start"] < m_end
            ]
            if notas_voz_comp:
                notas_voz_comp.sort(key=lambda x: x["start"])
                primeira_nota_do_compasso = notas_voz_comp[0]["note"]
                break

        for i in range(D):
            t_on_planned = m_start + i * duracao_nota
            t_off_planned = t_on_planned + duracao_nota
            role = pattern[i]

            partes = adaptar_nota_arpejo(t_on_planned, t_off_planned, role, grouped_onsets)
            for p in partes:
                t_on = p["start"]
                t_off = p["end"]

                # Busca notas ativas
                n_B = obter_nota_voz_no_tick(baixo_notas, t_on, m_start, m_end)
                n_T = obter_nota_voz_no_tick(tenor_notas, t_on, m_start, m_end)
                n_A = obter_nota_voz_no_tick(contralto_notas, t_on, m_start, m_end)
                n_S = obter_nota_voz_no_tick(soprano_notas, t_on, m_start, m_end)

                if n_B is None: n_B = 48
                if n_T is None: n_T = n_B + 12
                if n_A is None: n_A = n_T + 5
                if n_S is None: n_S = n_A + 7

                is_primeira_nota = (role == 'B' and t_on == m_start and primeira_nota_do_compasso is not None)

                # Seleciona nota pelo papel
                if is_primeira_nota:
                    note = primeira_nota_do_compasso
                elif role == 'B':
                    note = n_B
                elif role == 'T':
                    note = n_T
                elif role == 'A':
                    note = n_A
                else:
                    note = n_S

                # Ajusta a nota para que pertença ao acorde ativo em t_on
                # para evitar acidentes estranhos e acordes dissonantes
                if not is_primeira_nota:
                    active_at_t = [
                        n for n in todas_notas
                        if n["start"] - 50 <= t_on < n["end"] + 50
                    ]
                    allowed_pcs = {n["note"] % 12 for n in active_at_t}
                    if not allowed_pcs:
                        # Fallback: todas as notas do compasso
                        in_comp = [
                            n for n in todas_notas
                            if m_start <= n["start"] < m_end
                        ]
                        allowed_pcs = {n["note"] % 12 for n in in_comp}

                    if allowed_pcs:
                        # Tabela de penalidades de dissonância para evitar clashes:
                        # 1 ou 11 (segunda menor / sétima maior): peso 20 (evitar ao máximo)
                        # 2 ou 10 (segunda maior / sétima menor): peso 8 (evitar se houver opção melhor)
                        # 6 (trítono): peso 10
                        pesos_dissonancia = {
                            0: 0, 1: 20, 2: 8, 3: 0, 4: 0, 5: 0,
                            6: 10, 7: 0, 8: 0, 9: 0, 10: 8, 11: 20
                        }

                        best_note = note
                        best_score = 999999
                        for offset in range(-6, 7):
                            candidate = note + offset
                            if 0 <= candidate <= 127 and (candidate % 12) in allowed_pcs:
                                # Custo da distância física (queremos manter na região da voz)
                                dist_score = abs(offset)
                                
                                # Custo de dissonância contra notas soando no mesmo tick
                                diss_score = 0
                                for active_n in active_at_t:
                                    interval = abs(candidate - active_n["note"]) % 12
                                    diss_score += pesos_dissonancia.get(interval, 0)
                                
                                total_score = dist_score + diss_score
                                if total_score < best_score:
                                    best_score = total_score
                                    best_note = candidate
                        note = best_note

                # Velocity do arpejo um pouco mais alta a pedido do usuário (48 a 64)
                vel = rng.randint(48, 64)

                novos_eventos.append((t_on, mido.Message("note_on", channel=canal_livre, note=note, velocity=vel, time=0)))
                novos_eventos.append((t_off, mido.Message("note_off", channel=canal_livre, note=note, velocity=0, time=0)))

    novos_eventos.sort(key=lambda x: x[0])
    
    # Determinar instrumento padrão (Harpa=46 ou Piano=0 dependendo do preset)
    patch = 46 if "musicbox" not in preset else 10
    if "piano" in preset:
        patch = 0
        
    nova_trilha = mido.MidiTrack()
    nova_trilha.name = "Arpejo Sacro Inteligente v2"
    nova_trilha.append(mido.Message("program_change", channel=canal_livre, program=patch, time=0))

    tick_prev = 0
    for tick_abs, msg in novos_eventos:
        delta = tick_abs - tick_prev
        msg.time = delta
        nova_trilha.append(msg)
        tick_prev = tick_abs
        
    nova_trilha.append(mido.MetaMessage("end_of_track", time=0))
    
    novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)
    for t in midi_in.tracks:
        novo_midi.tracks.append(t)
    novo_midi.tracks.append(nova_trilha)
    
    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Definição dos Presets (Orquestras, Pianos, Órgãos e Outros)
# ─────────────────────────────────────────────────────────────────────────────
PRESETS_CFG = {
    # Pianos
    "piano_devocional": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "piano_arpejado_suave": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": True, "reverb": "0.5", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "equinox_grand": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },

    # Caixinha de música
    "musicbox_suave": {
        "modo": "simples",
        "soundfont": "MusicBox.sf2",
        "vozes": {
            "soprano":   {"patch": 10, "pan": 70, "vol": 100, "canal": 0},
            "contralto": {"patch": 10, "pan": 58, "vol": 70,  "canal": 1},
            "tenor":     {"patch": 10, "pan": 54, "vol": 70,  "canal": 2},
            "baixo":     {"patch": 10, "pan": 64, "vol": 85,  "canal": 3},
        },
        "arpejo": True, "reverb": "0.65", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },

    # Órgãos Sacros (Geração 2 / todas_versoes2.sh)
    "01_orgao_liturgico_timbres": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 20, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 20, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 20, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 20, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "02_orgao_tradicional_crisis": {
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "03_orgao_eletronico_drawbar": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 16, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 16, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 16, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 16, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "04_orgao_suave_musescore": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "05_orgao_ccb_celeste": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 16, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 16, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 16, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 16, "pan": 64, "vol": 95, "canal": 3},
        },
        "pad_strings": True, "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "06_orgao_ccb_misto": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "pad_strings": True, "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "07_orgao_pleno_majestoso": {
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "08_orgao_pleno_arpejado": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": True, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "orgao_sacro": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "timbres_heaven_suave": {
        "modo": "simples",
        "soundfont": "Timbres_of_Heaven.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": False, "vibrato": False
    },
    "generaluser_leve": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "pan": 72, "vol": 105, "canal": 0},
            "contralto": {"patch": 0, "pan": 54, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "pan": 44, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },

    # Geração 3 (Orquestra Sacra / docs/orchestra.md)
    "01_orq_ccb_classica": {
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},  # Flute
            "contralto": {"patch": 71, "pan": 56, "vol": 85, "canal": 1},  # Clarinet
            "tenor":     {"patch": 41, "pan": 48, "vol": 82, "canal": 2},  # Viola
            "baixo":     {"patch": 42, "pan": 64, "vol": 88, "canal": 3},  # Cello
        },
        "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "02_orq_orgao_fundo": {
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
        "arpejo": False, "reverb": "0.55", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "03_orq_metais_suaves": {
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 56, "pan": 68, "vol": 88, "canal": 0},  # Trumpet (suave)
            "contralto": {"patch": 60, "pan": 56, "vol": 86, "canal": 1},  # Horn
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},  # Trombone
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},  # Tuba
        },
        "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "04_orq_cordas_completas": {
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 40, "pan": 68, "vol": 96, "canal": 0},  # Violin
            "contralto": {"patch": 41, "pan": 56, "vol": 85, "canal": 1},  # Viola
            "tenor":     {"patch": 42, "pan": 48, "vol": 84, "canal": 2},  # Cello
            "baixo":     {"patch": 42, "pan": 64, "vol": 90, "canal": 3},  # Cello grave
        },
        "arpejo": False, "reverb": "0.55", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "05_orq_madeiras_delicadas": {
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},  # Flute
            "contralto": {"patch": 68, "pan": 56, "vol": 82, "canal": 1},  # Oboe
            "tenor":     {"patch": 69, "pan": 48, "vol": 82, "canal": 2},  # English Horn
            "baixo":     {"patch": 70, "pan": 64, "vol": 88, "canal": 3},  # Bassoon
        },
        "arpejo": False, "reverb": "0.45", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "06_orq_hinario_cantado": {
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
        "arpejo": False, "reverb": "0.52", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "07_orq_piano_leve": {
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
        "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": True, "expressao": True, "vibrato": True
    },
    "08_orq_orgao_metais": {
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
        "arpejo": False, "reverb": "0.55", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "09_orq_grande": {
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
        "arpejo": False, "reverb": "0.55", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "10_orq_tradicional_banda": {
        "modo": "simples",
        "soundfont": "SGM-V2.01.sf2",
        "vozes": {
            "soprano":   {"patch": 71, "pan": 68, "vol": 95, "canal": 0},  # Clarinet
            "contralto": {"patch": 60, "pan": 56, "vol": 86, "canal": 1},  # Horn
            "tenor":     {"patch": 57, "pan": 48, "vol": 84, "canal": 2},  # Trombone
            "baixo":     {"patch": 58, "pan": 64, "vol": 88, "canal": 3},  # Tuba
        },
        "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "11_orq_favorita_ia": {
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
        "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },

    # Geração 1
    "01_eq_steinway_arpejado": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "bank": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "bank": 0, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "bank": 0, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "bank": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": True, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "02_eq_yamaha_arpejado": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 1, "bank": 1, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 1, "bank": 1, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 1, "bank": 1, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 1, "bank": 1, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": True, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "03_eq_steinway": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 0, "bank": 0, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 0, "bank": 0, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 0, "bank": 0, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 0, "bank": 0, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "04_eq_yamaha": {
        "modo": "simples",
        "soundfont": "Equinox_Grand_Pianos.sf2",
        "vozes": {
            "soprano":   {"patch": 1, "bank": 1, "pan": 68, "vol": 100, "canal": 0},
            "contralto": {"patch": 1, "bank": 1, "pan": 60, "vol": 85,  "canal": 1},
            "tenor":     {"patch": 1, "bank": 1, "pan": 58, "vol": 85,  "canal": 2},
            "baixo":     {"patch": 1, "bank": 1, "pan": 64, "vol": 95,  "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": True, "expressao": False, "vibrato": False
    },
    "05_ms_orgao_igreja": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 19, "pan": 64, "vol": 95, "canal": 0},
            "contralto": {"patch": 19, "pan": 60, "vol": 90, "canal": 1},
            "tenor":     {"patch": 19, "pan": 68, "vol": 90, "canal": 2},
            "baixo":     {"patch": 19, "pan": 64, "vol": 95, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "06_ms_orgao_reed": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 20, "pan": 64, "vol": 92, "canal": 0},
            "contralto": {"patch": 20, "pan": 60, "vol": 85, "canal": 1},
            "tenor":     {"patch": 20, "pan": 68, "vol": 85, "canal": 2},
            "baixo":     {"patch": 20, "pan": 64, "vol": 92, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "07_ms_orq_completa": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 71, "pan": 56, "vol": 85, "canal": 1},
            "tenor":     {"patch": 48, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 42, "pan": 64, "vol": 88, "canal": 3},
        },
        "arpejo": False, "reverb": "0.5", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "08_ms_orq_sacra_1": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 95, "canal": 0},
            "contralto": {"patch": 71, "pan": 56, "vol": 85, "canal": 1},
            "tenor":     {"patch": 60, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 42, "pan": 64, "vol": 88, "canal": 3},
        },
        "arpejo": False, "reverb": "0.5", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "09_ms_orq_suave": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 68, "pan": 68, "vol": 92, "canal": 0},
            "contralto": {"patch": 71, "pan": 56, "vol": 82, "canal": 1},
            "tenor":     {"patch": 41, "pan": 48, "vol": 80, "canal": 2},
            "baixo":     {"patch": 42, "pan": 64, "vol": 86, "canal": 3},
        },
        "arpejo": False, "reverb": "0.5", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "10_ms_sintetizado_arpejado": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 80, "pan": 68, "vol": 88, "canal": 0},
            "contralto": {"patch": 62, "pan": 56, "vol": 82, "canal": 1},
            "tenor":     {"patch": 50, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 38, "pan": 64, "vol": 86, "canal": 3},
        },
        "arpejo": True, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": False
    },
    "11_ms_sintetizado": {
        "modo": "simples",
        "soundfont": "MuseScore_General.sf2",
        "vozes": {
            "soprano":   {"patch": 80, "pan": 68, "vol": 88, "canal": 0},
            "contralto": {"patch": 62, "pan": 56, "vol": 82, "canal": 1},
            "tenor":     {"patch": 50, "pan": 48, "vol": 82, "canal": 2},
            "baixo":     {"patch": 38, "pan": 64, "vol": 86, "canal": 3},
        },
        "arpejo": False, "reverb": "0.4", "gain": "0.8",
        "pedal": False, "expressao": True, "vibrato": False
    },

    # Geração 4
    "04_orquestra_completa": {
        "modo": "simples",
        "soundfont": "CrisisGeneralMidi301.sf2",
        "vozes": {
            "soprano":   {"patch": 73, "pan": 68, "vol": 100, "canal": 0},  # Flute
            "contralto": {"patch": 68, "pan": 56, "vol": 88,  "canal": 1},  # Oboe
            "tenor":     {"patch": 41, "pan": 48, "vol": 85,  "canal": 2},  # Viola
            "baixo":     {"patch": 42, "pan": 64, "vol": 95,  "canal": 3},  # Cello
        },
        "pad_strings": True, "arpejo": False, "reverb": "0.50", "gain": "0.80",
        "pedal": False, "expressao": True, "vibrato": True
    },
    "06_musicbox_arpejado": {
        "modo": "simples",
        "soundfont": "MusicBox.sf2",
        "vozes": {
            "soprano":   {"patch": 10, "pan": 70, "vol": 92, "canal": 0},
            "contralto": {"patch": 10, "pan": 58, "vol": 62, "canal": 1},
            "tenor":     {"patch": 10, "pan": 54, "vol": 60, "canal": 2},
            "baixo":     {"patch": 10, "pan": 64, "vol": 75, "canal": 3},
        },
        "arpejo": True, "reverb": "0.70", "gain": "0.65",
        "pedal": False, "expressao": False, "vibrato": False
    }
}

def aplicar_preset_orquestracao(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    preset: str,
    log: logging.Logger
) -> tuple[mido.MidiFile, Path]:
    """
    Aplica o preset de orquestração desejado remapeando programas, volumes e pan.
    """
    log.info(f"Orquestrando MIDI para preset: {preset}")
    
    cfg_preset = PRESETS_CFG.get(preset)
    if not cfg_preset:
        log.warning(f"Preset {preset} não encontrado em PRESETS_CFG! Usando padrão piano_devocional.")
        cfg_preset = PRESETS_CFG["piano_devocional"]
        
    sf_mapeado = cfg_preset.get("soundfont", "MuseScore_General.sf2")
    caminho_sf2 = obter_caminho_sf2(sf_mapeado)
    
    if not caminho_sf2 or not caminho_sf2.exists():
        log.warning(f"Soundfont {sf_mapeado} não encontrada! Usando fallback MuseScore_General.sf2")
        caminho_sf2 = obter_caminho_sf2("MuseScore_General.sf2")
        if not caminho_sf2:
            todas = list(SOUNDFONTS_DIR.glob("*.sf2"))
            if todas:
                caminho_sf2 = todas[0]
            else:
                raise FileNotFoundError(f"Nenhum arquivo .sf2 encontrado em {SOUNDFONTS_DIR}")

    log.info(f"Usando SoundFont: {caminho_sf2.name}")

    if cfg_preset.get("modo", "simples") == "simples":
        config_vozes = cfg_preset["vozes"]
        
        # Recria o MidiFile remapeando as trilhas do SATB
        novo_midi = mido.MidiFile(type=1, ticks_per_beat=midi_in.ticks_per_beat)
        
        # Adiciona trilha de tempo/compasso
        trilha_tempo = mido.MidiTrack()
        for msg in midi_in.tracks[0]:
            if msg.type not in ("note_on", "note_off", "program_change", "control_change"):
                trilha_tempo.append(msg.copy())
        novo_midi.tracks.append(trilha_tempo)

        inversao_vozes = {idx: papel for papel, idx in vozes.items()}
        
        for idx_trilha, trilha in enumerate(midi_in.tracks):
            if idx_trilha == 0 or "_arpejo" in (trilha.name or "").lower():
                continue
                
            papel = inversao_vozes.get(idx_trilha)
            if not papel:
                continue
                
            cfg = config_vozes[papel]
            canal = cfg["canal"]
            
            trilha_remap = mido.MidiTrack()
            trilha_remap.name = f"{papel.capitalize()} ({preset})"
            
            # Injeta Bank Select MSB (CC0) se estiver configurado
            if "bank" in cfg:
                trilha_remap.append(mido.Message("control_change", channel=canal, control=0, value=cfg["bank"], time=0))
            
            # Injeta Program Change e Volume/Pan no início da trilha
            trilha_remap.append(mido.Message("program_change", channel=canal, program=cfg["patch"], time=0))
            trilha_remap.append(mido.Message("control_change", channel=canal, control=7, value=cfg["vol"], time=0))
            trilha_remap.append(mido.Message("control_change", channel=canal, control=10, value=cfg["pan"], time=0))
            
            # Copia notas e outros eventos remapeando para o canal correto
            for msg in trilha:
                if hasattr(msg, "channel") and msg.type not in ("program_change", "control_change"):
                    trilha_remap.append(msg.copy(channel=canal))
                elif msg.type not in ("program_change", "control_change"):
                    trilha_remap.append(msg.copy())
                    
            novo_midi.tracks.append(trilha_remap)

        # Se tiver pad_strings, adiciona strings (Pad 48) dobrando a harmonia
        if cfg_preset.get("pad_strings"):
            log.info("Injetando pad de strings secundário para encorpar...")
            for papel in ("soprano", "contralto", "tenor", "baixo"):
                if preset == "05_orgao_ccb_celeste" and papel != "soprano":
                    continue
                
                cfg = config_vozes[papel]
                idx_orig = vozes[papel]
                trilha_orig = midi_in.tracks[idx_orig]
                
                canal_pad = cfg["canal"] + 4  # Canais 4, 5, 6, 7
                trilha_pad = mido.MidiTrack()
                trilha_pad.name = f"{papel.capitalize()} Strings (Pad)"
                
                fator_vol = 0.50 if preset == "05_orgao_ccb_celeste" else 0.40
                if preset in ("orquestra_sacra", "crisis_orquestral", "04_orquestra_completa"):
                    fator_vol = 0.65

                trilha_pad.append(mido.Message("program_change", channel=canal_pad, program=48, time=0))
                trilha_pad.append(mido.Message("control_change", channel=canal_pad, control=7, value=int(cfg["vol"] * fator_vol), time=0))
                trilha_pad.append(mido.Message("control_change", channel=canal_pad, control=10, value=cfg["pan"], time=0))
                
                for msg in trilha_orig:
                    if hasattr(msg, "channel") and msg.type not in ("program_change", "control_change"):
                        trilha_pad.append(msg.copy(channel=canal_pad))
                    elif msg.type not in ("program_change", "control_change"):
                        trilha_pad.append(msg.copy())
                novo_midi.tracks.append(trilha_pad)

        return novo_midi, caminho_sf2
    else:
        # Híbrido: retorna intacto
        return midi_in, caminho_sf2


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
    """
    log.info("🔊 Renderizando (híbrido multi-SF2)...")
    grupos = preset_cfg.get("grupos", {})
    ticks_por_beat = midi_humanizado.ticks_per_beat
    gain = preset_cfg.get("gain", "0.80")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wavs_temp: list[Path] = []

        for nome_grupo, cfg_grupo in grupos.items():
            papeis_grupo    = cfg_grupo["vozes"]
            sf2_nome        = cfg_grupo["soundfont"]
            sf2             = obter_caminho_sf2(sf2_nome)
            if not sf2 or not sf2.exists():
                sf2 = obter_caminho_sf2("MuseScore_General.sf2")
                
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

                for msg in trilha_orig:
                    if hasattr(msg, "channel") and msg.type not in ("program_change", "control_change"):
                        trilha_nova.append(msg.copy(channel=canal_local))
                    elif msg.type not in ("program_change", "control_change"):
                        trilha_nova.append(msg.copy())

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

        _mixar_wavs(wavs_temp, arquivo_mp3, log)


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
# Execução da CLI / Processamento do MIDI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="renderizador2.py — Conversor MIDI → MP3 Avançado")
    parser.add_argument("--mid", type=str, required=True, help="Arquivo MIDI de entrada")
    parser.add_argument("--preset", type=str, default="piano_devocional", help="Preset de renderização")
    parser.add_argument("--saida-dir", type=str, help="Diretório de saída")
    parser.add_argument("--seed", type=int, default=42, help="Seed para humanização determinística")
    parser.add_argument("--reiniciar", action="store_true", help="Sobrescreve arquivos já gerados")
    parser.add_argument("--salvar-midi", action="store_true", default=True, help="Salva MIDI humanizado intermediário")
    parser.add_argument("--salvar-json", action="store_true", default=True, help="Salva parâmetros JSON usados")
    parser.add_argument("--humanizacao", action="store_true", default=True, help="Ativa a humanização das notas")
    parser.add_argument("--arpejo-sacro", action="store_true", help="Força a ativação do arpejo sacro")
    parser.add_argument("--sem-arpejo", action="store_true", help="Inibe a ativação do arpejo sacro")
    parser.add_argument("--normalizar", action="store_true", default=True, help="Normaliza o áudio final")
    parser.add_argument("--manter-wav", action="store_true", help="Não apaga arquivos WAV temporários")
    parser.add_argument("--debug", action="store_true", help="Ativa logs de depuração")

    args = parser.parse_args()

    log = configurar_log(verbose=args.debug)

    # 1. Resolver caminhos
    caminho_mid = Path(args.mid)
    if not caminho_mid.exists():
        # Tenta buscar na pasta 'mid'
        caminho_mid = MID_DIR / args.mid
        if not caminho_mid.exists():
            log.error(f"Arquivo MIDI não encontrado: {args.mid}")
            sys.exit(1)

    saida_dir = Path(args.saida_dir) if args.saida_dir else OUTPUT_DIR / caminho_mid.stem / args.preset
    saida_dir.mkdir(parents=True, exist_ok=True)

    arquivo_mp3 = saida_dir / f"{caminho_mid.stem}.mp3"
    arquivo_json = saida_dir / "parametros.json"
    arquivo_log = saida_dir / "analise_satb.txt"

    if arquivo_mp3.exists() and not args.reiniciar:
        log.info(f"O arquivo {arquivo_mp3.name} já existe. Pulando (use --reiniciar para forçar).")
        sys.exit(0)

    log.info(f"==================================================================")
    log.info(f" Processando: {caminho_mid.name} | Preset: {args.preset}")
    log.info(f"==================================================================")

    try:
        # Carrega MIDI original
        midi_original = mido.MidiFile(str(caminho_mid))
        
        # Resolve config do preset
        cfg_preset = PRESETS_CFG.get(args.preset)
        if not cfg_preset:
            log.warning(f"Preset {args.preset} não encontrado! Usando padrão piano_devocional.")
            cfg_preset = PRESETS_CFG["piano_devocional"]

        # 2. Detecção SATB
        vozes = detectar_vozes_satb(midi_original, log)
        
        # Escreve arquivo de log da análise SATB
        with open(arquivo_log, "w", encoding="utf-8") as f_log:
            f_log.write(f"Análise SATB - {caminho_mid.name}\n")
            f_log.write(f"Data: {datetime.now().isoformat()}\n")
            f_log.write(f"Vozes detectadas:\n")
            for voz, idx in vozes.items():
                nome_trilha = midi_original.tracks[idx].name if midi_original.tracks[idx].name else f"Trilha {idx}"
                f_log.write(f"  {voz.capitalize()}: Trilha #{idx} ({nome_trilha})\n")

        # 3. Orquestração e Mapeamento de Canais
        midi_processado, caminho_sf2 = aplicar_preset_orquestracao(midi_original, vozes, args.preset, log)

        # 4. Humanização (Micro-Timing, Micro-Roll e Dinâmica de Frase)
        if args.humanizacao:
            midi_processado = aplicar_humanizacao(midi_processado, vozes, args.preset, seed=args.seed, log=log)

        # 5. Pedal de Sustain Inteligente (Pianos)
        if cfg_preset.get("pedal", False) or "piano" in args.preset or "equinox" in args.preset:
            midi_processado = aplicar_pedal_sustain_inteligente(midi_processado, vozes, log)

        # 6. CC11 Expressão e CC1 Vibrato (Cordas/Sopros)
        if (cfg_preset.get("expressao", False) or 
            cfg_preset.get("vibrato", False) or 
            args.preset in ("cordas_suaves", "quarteto_cordas", "orquestra_sacra", "crisis_orquestral", "aaviolin_cantabile") or
            "orq" in args.preset):
            
            vibrato_s = cfg_preset.get("vibrato", True)
            expressao_s = cfg_preset.get("expressao", True)
            midi_processado = aplicar_expressao_e_vibrato(
                midi_processado, vozes, log,
                vibrato_strings=vibrato_s,
                expressao_sopros=expressao_s
            )

        # 7. Arpejo Sacro Inteligente v2
        ativar_arpejo = False
        if args.arpejo_sacro:
            ativar_arpejo = True
        elif args.sem_arpejo:
            ativar_arpejo = False
        else:
            # Presets que ativam arpejo por padrão
            if cfg_preset.get("arpejo", False) or args.preset in ("piano_arpejado_suave", "musicbox_suave", "08_orgao_pleno_arpejado"):
                ativar_arpejo = True

        if ativar_arpejo:
            midi_processado = aplicar_arpejo_sacro_v2(midi_processado, vozes, args.preset, log)

        # Salva MIDI humanizado intermediário
        if args.salvar_midi:
            caminho_mid_out = saida_dir / f"{caminho_mid.stem}_humanizado.mid"
            midi_processado.save(str(caminho_mid_out))
            log.info(f"MIDI humanizado salvo em: {caminho_mid_out.name}")

        # Salva JSON de parâmetros
        if args.salvar_json:
            parametros = {
                "mid_original": str(caminho_mid.name),
                "preset": args.preset,
                "soundfont": caminho_sf2.name if cfg_preset.get("modo", "simples") == "simples" else "hibrido",
                "seed": args.seed,
                "humanizacao": args.humanizacao,
                "arpejo_ativado": ativar_arpejo,
                "vozes_satb": vozes,
                "data_processamento": datetime.now().isoformat()
            }
            with open(arquivo_json, "w", encoding="utf-8") as f_json:
                json.dump(parametros, f_json, indent=4, ensure_ascii=False)
            log.info("Parâmetros do processo salvos em parametros.json")

        # 8. Renderização (Híbrida ou FluidSynth Simples)
        if cfg_preset.get("modo", "simples") == "hibrido":
            renderizar_hibrido(midi_processado, vozes, cfg_preset, arquivo_mp3, log)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                arquivo_wav = tmp_path / f"{caminho_mid.stem}.wav"
                
                # Determinando ganho/reverb padrão por preset
                gain = cfg_preset.get("gain", "0.8")
                reverb = cfg_preset.get("reverb", "0.4")

                cmd_fluid = [
                    "fluidsynth",
                    "-F", str(arquivo_wav),
                    "-O", "float",  # Headroom ilimitado
                    "-T", "wav",
                    "-g", gain,
                    "--quiet",
                    str(caminho_sf2),
                    str(saida_dir / f"{caminho_mid.stem}_humanizado.mid") if args.salvar_midi else str(caminho_mid)
                ]
                
                # Se não salvou o MIDI temporário na saída, salvamos no tmpdir para renderizar
                if not args.salvar_midi:
                    caminho_mid_temp = tmp_path / f"temp_{caminho_mid.name}"
                    midi_processado.save(str(caminho_mid_temp))
                    cmd_fluid[-1] = str(caminho_mid_temp)

                log.info("Renderizando áudio via FluidSynth...")
                log.debug(f"Comando: {' '.join(cmd_fluid)}")
                res_fluid = subprocess.run(cmd_fluid, capture_output=True, text=True)
                if res_fluid.returncode != 0:
                    raise RuntimeError(f"FluidSynth falhou:\n{res_fluid.stderr}")

                # 9. Conversão FFmpeg (WAV -> MP3)
                # Limiter pico -1dBFS + loudness normalizado em -14LUFS
                filtros = (
                    "alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false,"
                    "loudnorm=I=-14:TP=-1:LRA=11"
                )
                
                cmd_ffmpeg = [
                    "ffmpeg",
                    "-y",
                    "-i", str(arquivo_wav),
                    "-af", filtros,
                    "-q:a", "0",  # Alta qualidade VBR
                    "-map_metadata", "-1",  # Limpa metadados poluídos
                    "-loglevel", "error",
                    str(arquivo_mp3)
                ]
                
                log.info("Normalizando e convertendo para MP3 com FFmpeg...")
                log.debug(f"Comando: {' '.join(cmd_ffmpeg)}")
                res_ffmpeg = subprocess.run(cmd_ffmpeg, capture_output=True, text=True)
                if res_ffmpeg.returncode != 0:
                    raise RuntimeError(f"FFmpeg falhou:\n{res_ffmpeg.stderr}")

                # Mantém WAV se solicitado
                if args.manter_wav:
                    shutil.copy2(str(arquivo_wav), saida_dir / f"{caminho_mid.stem}.wav")
                    log.info(f"Cópia WAV mantida na pasta de saída.")

        log.info(f"✓ Sucesso! Arquivo gerado em: {arquivo_mp3.name}")

    except Exception as e:
        log.error(f"Erro no processamento do MIDI: {e}", exc_info=args.debug)
        sys.exit(2)

if __name__ == "__main__":
    main()
