#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador3.py — Humanizador MIDI avançado de hinos sacros.
Aplica micro-timing (desquantização de 3 a 15ms), inibição de simultaneidade exata em acordes,
velocities fixas por voz (S: 85-95, B: 75-85, C/T: 60-70), pedal inteligente,
expressão CC11 progressiva, vibrato CC1 em cordas e renderização híbrida multi-soundfont.
"""

import argparse
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
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
OUTPUT_DIR = BASE_DIR / "output3"
SOUNDFONTS_DIR = BASE_DIR / "soundfonts"

# ─────────────────────────────────────────────────────────────────────────────
# Descoberta de SoundFonts
# ─────────────────────────────────────────────────────────────────────────────
def obter_soundfont(nome: str) -> Path:
    """Retorna o caminho completo de uma SoundFont na pasta soundfonts/."""
    for sf2 in SOUNDFONTS_DIR.glob("*.sf2"):
        if sf2.name.startswith("."):
            continue
        if nome.lower() in sf2.name.lower():
            return sf2
    # Caso não ache, tenta busca exata
    caminho = SOUNDFONTS_DIR / nome
    if caminho.exists() and not caminho.name.startswith("."):
        return caminho
    caminho_sf2 = SOUNDFONTS_DIR / f"{nome}.sf2"
    if caminho_sf2.exists() and not caminho_sf2.name.startswith("."):
        return caminho_sf2
    # Fallback de emergência
    todas = [sf for sf in SOUNDFONTS_DIR.glob("*.sf2") if not sf.name.startswith(".")]
    if todas:
        return todas[0]
    raise FileNotFoundError(f"SoundFont '{nome}' não encontrada e não há fallbacks em {SOUNDFONTS_DIR}")

# ─────────────────────────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────────────────────────
def configurar_log(verbose: bool = False) -> logging.Logger:
    nivel = logging.DEBUG if verbose else logging.INFO
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout
    )
    return logging.getLogger("renderizador3")

# ─────────────────────────────────────────────────────────────────────────────
# Detecção SATB por Mediana e Nome
# ─────────────────────────────────────────────────────────────────────────────
def detectar_satb(midi: mido.MidiFile, log: logging.Logger) -> dict[str, int]:
    """Detecta as quatro vozes (soprano, contralto, tenor, baixo) do MIDI."""
    trilhas_ativas = []
    for idx, t in enumerate(midi.tracks):
        notas = [m.note for m in t if m.type == "note_on" and m.velocity > 0]
        if notas:
            trilhas_ativas.append({
                "idx": idx,
                "name": t.name.strip() if t.name else f"Trilha {idx}",
                "median": sorted(notas)[len(notas) // 2]
            })

    if not trilhas_ativas:
        log.warning("Nenhuma nota encontrada nas trilhas!")
        return {"soprano": 0, "contralto": 0, "tenor": 0, "baixo": 0}

    # Busca por nome primeiro
    mapeado = {}
    termos = {
        "soprano": ["soprano", "sop", "s", "melodia", "melody"],
        "contralto": ["contralto", "alto", "a"],
        "tenor": ["tenor", "ten", "t"],
        "baixo": ["baixo", "bass", "b"]
    }
    
    trilhas_restantes = trilhas_ativas[:]
    for papel, lista_termos in termos.items():
        for t in list(trilhas_restantes):
            nome_l = t["name"].lower()
            if any(re.search(rf"\b{termo}\b", nome_l) or nome_l == termo for termo in lista_termos):
                mapeado[papel] = t["idx"]
                trilhas_restantes.remove(t)
                break

    # Ordenação por mediana para o que sobrou
    trilhas_restantes.sort(key=lambda x: x["median"], reverse=True)
    papeis_restantes = [p for p in ["soprano", "contralto", "tenor", "baixo"] if p not in mapeado]
    
    for i, papel in enumerate(papeis_restantes):
        if i < len(trilhas_restantes):
            mapeado[papel] = trilhas_restantes[i]["idx"]

    # Ajustes finais de segurança
    if "soprano" not in mapeado: mapeado["soprano"] = trilhas_ativas[0]["idx"]
    if "contralto" not in mapeado: mapeado["contralto"] = mapeado["soprano"]
    if "tenor" not in mapeado: mapeado["tenor"] = mapeado["contralto"]
    if "baixo" not in mapeado: mapeado["baixo"] = mapeado["tenor"]

    log.info(f"Classificação SATB: {mapeado}")
    return mapeado

# ─────────────────────────────────────────────────────────────────────────────
# Funções de Conversão Rítmica
# ─────────────────────────────────────────────────────────────────────────────
def obter_tempo_us(midi: mido.MidiFile) -> int:
    for t in midi.tracks:
        for msg in t:
            if msg.type == "set_tempo":
                return msg.tempo
    return 500_000

def ms_para_ticks(ms: float, ticks_por_beat: int, tempo_us: int) -> int:
    return int(round((ms / 1000.0) * (ticks_por_beat * 1_000_000 / tempo_us)))

# ─────────────────────────────────────────────────────────────────────────────
# Processamento de Humanização (Micro-Timing e Velocities Fixas)
# ─────────────────────────────────────────────────────────────────────────────
def humanizar_midi(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger
) -> mido.MidiFile:
    """
    Aplica:
    1. Dequantização Contínua: Atrasos/Adiantamentos aleatórios de 3 a 15 ms via onsets interpolados.
       Evita gaps e sobreposições/notas comidas.
    2. Micro-roll: Evita notas em bloco no mesmo tick. Baixo soa uma fração antes do Soprano.
    3. Dynamics: Velocities fixas (Soprano: 85-95, Baixo: 75-85, Contralto/Tenor: 60-70).
    """
    log.info("🎻 Humanizando dinâmicas de vozes e micro-timing com delays interpolados...")
    
    # Semente aleatória para determinismo
    rng = random.Random(42)
    
    tempo_us = obter_tempo_us(midi_in)
    ticks_por_beat = midi_in.ticks_per_beat

    # Mapeamento reverso de índices de trilhas
    idx_para_voz = {idx: voz for voz, idx in vozes.items()}

    # Velocities fixas por papel
    velocities_alvo = {
        "soprano": (85, 95),
        "baixo": (75, 85),
        "contralto": (60, 70),
        "tenor": (60, 70)
    }

    # 1. Coleta todos os ticks de início de notas (onsets) nas trilhas de vozes
    onsets_set = {0}
    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0 or "_arpejo" in (trilha.name or "").lower():
            continue
        tick_abs = 0
        for msg in trilha:
            tick_abs += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                onsets_set.add(tick_abs)
    
    max_tick = max(sum(msg.time for msg in t) for t in midi_in.tracks)
    onsets_set.add(max_tick)
    onset_ticks = sorted(list(onsets_set))

    # 2. Gera delays limitados por slope (dD/ds <= 0.75) para cada onset e cada voz
    delays_por_onset = []
    # 3 a 15 ms: e.g. [0.003, 0.006, 0.010, 0.015] seconds.
    # 50% de chance de adiantar (negativo) ou atrasar (positivo)
    opcoes = [-0.015, -0.010, -0.006, -0.003, 0.003, 0.006, 0.010, 0.015]
    
    prev_delays = {
        "soprano": 0.0,
        "contralto": 0.0,
        "tenor": 0.0,
        "baixo": 0.0
    }
    
    ticks_por_seg = ticks_por_beat * 1_000_000.0 / tempo_us
    
    for idx, t in enumerate(onset_ticks):
        if idx == 0:
            cur_delays = {
                "soprano":   rng.choice(opcoes),
                "contralto": rng.choice(opcoes),
                "tenor":     rng.choice(opcoes),
                "baixo":     rng.choice(opcoes),
            }
        else:
            dt_ticks = t - onset_ticks[idx - 1]
            ds = dt_ticks / ticks_por_seg
            max_change = 0.75 * ds
            
            cur_delays = {}
            for papel_vocal in ["soprano", "contralto", "tenor", "baixo"]:
                d_prev = prev_delays[papel_vocal]
                target = rng.choice(opcoes)
                
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

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0 or "_arpejo" in (trilha.name or "").lower():
            novo_midi.tracks.append(trilha)
            continue

        papel = idx_para_voz.get(idx_trilha, "tenor")
        
        # Converte para ticks absolutos
        eventos_abs = []
        tick_acum = 0
        for msg in trilha:
            tick_acum += msg.time
            eventos_abs.append([tick_acum, msg.copy()])

        # Desloca note_on e note_off de acordo com o delay interpolado
        for ev in eventos_abs:
            tick_abs, msg = ev
            if msg.type == "note_on" and msg.velocity > 0:
                delay_sec = obter_delay_interpolado(tick_abs, papel)
                delay_ticks = ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = max(0, tick_abs + delay_ticks)

                # Injeta velocities fixas
                v_min, v_max = velocities_alvo[papel]
                msg.velocity = rng.randint(v_min, v_max)
                
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                delay_sec = obter_delay_interpolado(tick_abs, papel)
                delay_ticks = ms_para_ticks(delay_sec * 1000.0, ticks_por_beat, tempo_us)
                ev[0] = max(0, tick_abs + delay_ticks)

        # 3. Micro-roll: Garante que notas em bloco nunca soem no exato mesmo milissegundo.
        # Ordenação do roll: Baixo (mais cedo), Tenor, Contralto, Soprano (mais tarde).
        roll_offsets = {
            "baixo": 0,
            "tenor": 6,
            "contralto": 12,
            "soprano": 18
        }
        offset_do_papel = roll_offsets.get(papel, 10)
        offset_ticks = ms_para_ticks(offset_do_papel, ticks_por_beat, tempo_us)

        # Adiciona o offset do roll a todos os note_on e note_off da trilha
        for ev in eventos_abs:
            msg = ev[1]
            if msg.type in ("note_on", "note_off"):
                ev[0] = ev[0] + offset_ticks

        # Converte de volta para delta-time
        eventos_abs.sort(key=lambda x: x[0])
        trilha_humanizada = mido.MidiTrack()
        trilha_humanizada.name = trilha.name
        
        tick_prev = 0
        for tick_abs, msg in eventos_abs:
            delta = max(0, tick_abs - tick_prev)
            msg.time = delta
            trilha_humanizada.append(msg)
            tick_prev = tick_abs

        novo_midi.tracks.append(trilha_humanizada)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Injeção de Expressão (CC11) e Vibrato (CC1)
# ─────────────────────────────────────────────────────────────────────────────
def injetar_expressao_e_vibrato(
    midi_in: mido.MidiFile,
    log: logging.Logger,
    ativar_cc11: bool = True,
    ativar_cc1: bool = True
) -> mido.MidiFile:
    """
    Injeta CC11 (Expressão) e CC1 (Vibrato) apenas em notas com duração maior que uma semínima.
    """
    log.info("🎻 Injetando CC11 (Expressão) e CC1 (Vibrato) progressivo...")
    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)
    ticks_por_beat = midi_in.ticks_per_beat
    limiar_nota_longa = int(ticks_por_beat * 1.1)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue

        # Converte para ticks absolutos
        eventos_abs = []
        tick_acum = 0
        for msg in trilha:
            tick_acum += msg.time
            eventos_abs.append([tick_acum, msg.copy()])

        canais = {msg.channel for _, msg in eventos_abs if hasattr(msg, "channel") and msg.channel != 9}
        if not canais:
            novo_midi.tracks.append(trilha)
            continue
        canal = list(canais)[0]

        novos_ccs = []
        notas_ativas = {}
        for tick_abs, msg in eventos_abs:
            if msg.type == "note_on" and msg.velocity > 0:
                notas_ativas[msg.note] = tick_abs
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in notas_ativas:
                    t_on = notas_ativas.pop(msg.note)
                    duracao = tick_abs - t_on
                    
                    if duracao >= limiar_nota_longa:
                        # CC11: Leve crescendo inicial, decrescendo suave final
                        if ativar_cc11:
                            novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=11, value=75, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.2), mido.Message("control_change", channel=canal, control=11, value=115, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.8), mido.Message("control_change", channel=canal, control=11, value=100, time=0)))
                            novos_ccs.append((tick_abs - 5, mido.Message("control_change", channel=canal, control=11, value=65, time=0)))

                        # CC1: Vibrato progressivo
                        if ativar_cc1:
                            novos_ccs.append((t_on, mido.Message("control_change", channel=canal, control=1, value=0, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.3), mido.Message("control_change", channel=canal, control=1, value=15, time=0)))
                            novos_ccs.append((t_on + int(duracao * 0.6), mido.Message("control_change", channel=canal, control=1, value=60, time=0)))
                            novos_ccs.append((tick_abs - 10, mido.Message("control_change", channel=canal, control=1, value=0, time=0)))

        # Adiciona novos CCs e ordena
        for t, msg_cc in novos_ccs:
            eventos_abs.append([t, msg_cc])
            
        trilha_processada = converter_ticks_absolutos_para_trilha(eventos_abs)
        trilha_processada.name = trilha.name
        novo_midi.tracks.append(trilha_processada)

    return novo_midi

# ─────────────────────────────────────────────────────────────────────────────
# Sustain Pedal Inteligente (CC64)
# ─────────────────────────────────────────────────────────────────────────────
def aplicar_sustain_pedal(
    midi_in: mido.MidiFile,
    vozes: dict[str, int],
    log: logging.Logger
) -> mido.MidiFile:
    """Aplica CC64 de sustain, limpando a ressonância nas mudanças de notas graves (Baixo)."""
    log.info("🎹 Aplicando automação inteligente de sustain pedal (CC64)...")
    
    baixo_idx = vozes.get("baixo", 0)
    baixo_trilha = midi_in.tracks[baixo_idx]
    
    eventos_baixo = converter_trilha_para_ticks_absolutos(baixo_trilha)
    mudancas_baixo = [tick for tick, msg in eventos_baixo if msg.type == "note_on" and msg.velocity > 0]
    mudancas_baixo.sort()

    novo_midi = mido.MidiFile(type=midi_in.type, ticks_per_beat=midi_in.ticks_per_beat)

    for idx_trilha, trilha in enumerate(midi_in.tracks):
        if idx_trilha == 0:
            novo_midi.tracks.append(trilha)
            continue
            
        eventos_abs = converter_trilha_para_ticks_absolutos(trilha)
        canais = {msg.channel for _, msg in eventos_abs if hasattr(msg, "channel") and msg.channel != 9}
        if not canais:
            novo_midi.tracks.append(trilha)
            continue
        canal = list(canais)[0]

        pedal_events = []
        for i, t_baixo in enumerate(mudancas_baixo):
            # Ativa logo após ataque (+30 ticks)
            pedal_events.append((t_baixo + 30, mido.Message("control_change", channel=canal, control=64, value=127, time=0)))
            if i + 1 < len(mudancas_baixo):
                # Desliga pouco antes do próximo ataque
                t_prox = mudancas_baixo[i + 1]
                t_off = max(t_baixo + 35, t_prox - 15)
                pedal_events.append((t_off, mido.Message("control_change", channel=canal, control=64, value=0, time=0)))
                
        if mudancas_baixo:
            pedal_events.append((eventos_abs[-1][0], mido.Message("control_change", channel=canal, control=64, value=0, time=0)))

        for t, msg in pedal_events:
            eventos_abs.append([t, msg])
            
        nova_trilha = converter_ticks_absolutos_para_trilha(eventos_abs)
        nova_trilha.name = trilha.name
        novo_midi.tracks.append(nova_trilha)
        
    return novo_midi

# Helper conversion function
def converter_ticks_absolutos_para_trilha(eventos_abs: list) -> mido.MidiTrack:
    eventos_abs.sort(key=lambda x: x[0])
    track = mido.MidiTrack()
    tick_prev = 0
    for tick_abs, msg in eventos_abs:
        delta = max(0, tick_abs - tick_prev)
        msg.time = delta
        track.append(msg)
        tick_prev = tick_abs
    return track

def converter_trilha_para_ticks_absolutos(track: mido.MidiTrack) -> list:
    eventos_abs = []
    tick_acumulado = 0
    for msg in track:
        tick_acumulado += msg.time
        eventos_abs.append([tick_acumulado, msg.copy()])
    return eventos_abs

# ─────────────────────────────────────────────────────────────────────────────
# Renderização com Isolamento de Trilhas (Híbrida Multi-SoundFont)
# ─────────────────────────────────────────────────────────────────────────────
def renderizar_isolando_vozes(
    midi_humanizado: mido.MidiFile,
    vozes: dict[str, int],
    tipo_arranjo: str,
    arquivo_mp3: Path,
    log: logging.Logger
):
    """
    Renderiza trilhas separadamente usando suas respectivas SoundFonts
    e depois as mixa usando FFmpeg para garantir separação de timbres perfeita.
    """
    log.info(f"Renderizando áudio híbrido para arranjo: {tipo_arranjo}...")
    
    ticks_por_beat = midi_humanizado.ticks_per_beat
    
    # Define quais vozes usam quais SoundFonts e patches
    # Formato: {"nome_grupo": {"trilhas": [idx], "sf2": Path, "patch": program, "vol": volume}}
    grupos_render = {}

    if tipo_arranjo == "piano_solo":
        sf = obter_soundfont("Equinox_Grand_Pianos.sf2")
        grupos_render = {
            "piano": {
                "trilhas": [vozes["soprano"], vozes["contralto"], vozes["tenor"], vozes["baixo"]],
                "sf2": sf,
                "patch": 0,
                "vol": 100
            }
        }
    elif tipo_arranjo == "quarteto_cordas":
        sf_sop = obter_soundfont("aaviolin.sf2")
        sf_outros = obter_soundfont("CrisisGeneralMidi301.sf2")
        grupos_render = {
            "soprano_violin": {
                "trilhas": [vozes["soprano"]],
                "sf2": sf_sop,
                "patch": 0,
                "vol": 105
            },
            "outros_strings": {
                "trilhas": [vozes["contralto"], vozes["tenor"], vozes["baixo"]],
                "sf2": sf_outros,
                # Usaremos patches correspondentes de cordas orquestrais: Alto=Viola(41), Tenor=Cello(42), Baixo=Contrabaixo(43)
                "patches_especificos": {
                    vozes["contralto"]: 41,
                    vozes["tenor"]: 42,
                    vozes["baixo"]: 43
                },
                "vol": 85
            }
        }
    elif tipo_arranjo == "orgao_coral":
        sf_mello = obter_soundfont("Mellotron.sf2")
        sf_heaven = obter_soundfont("Timbres_of_Heaven.sf2")
        grupos_render = {
            "vocal_choir": {
                "trilhas": [vozes["soprano"], vozes["contralto"]],
                "sf2": sf_mello,
                "patch": 52,  # Choir Aahs
                "vol": 90
            },
            "church_organ": {
                "trilhas": [vozes["tenor"], vozes["baixo"]],
                "sf2": sf_heaven,
                "patch": 19,  # Church Organ
                "vol": 95
            }
        }
    else:  # orquestra_completa
        sf_crisis = obter_soundfont("CrisisGeneralMidi301.sf2")
        grupos_render = {
            "orquestra": {
                "trilhas": [vozes["soprano"], vozes["contralto"], vozes["tenor"], vozes["baixo"]],
                "sf2": sf_crisis,
                "patches_especificos": {
                    vozes["soprano"]: 73,    # Flute
                    vozes["contralto"]: 71,  # Clarinet
                    vozes["tenor"]: 60,      # Horn
                    vozes["baixo"]: 42       # Cello
                },
                "vol": 95
            }
        }

    wavs_temp = []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Renderiza cada grupo para um arquivo WAV temporário
        for nome_grupo, cfg in grupos_render.items():
            log.info(f" -> Renderizando subgrupo '{nome_grupo}' com {cfg['sf2'].name}...")
            
            # Cria um MidiFile temporário contendo apenas as trilhas do grupo
            midi_grupo = mido.MidiFile(type=1, ticks_per_beat=ticks_por_beat)
            
            # Copia a trilha de tempo
            trilha_tempo = mido.MidiTrack()
            for msg in midi_humanizado.tracks[0]:
                if msg.type not in ("note_on", "note_off", "program_change", "control_change"):
                    trilha_tempo.append(msg.copy())
            midi_grupo.tracks.append(trilha_tempo)
            
            # Copia as trilhas deste grupo
            for idx_trilha in cfg["trilhas"]:
                trilha_orig = midi_humanizado.tracks[idx_trilha]
                trilha_nova = mido.MidiTrack()
                trilha_nova.name = trilha_orig.name
                
                # Identifica canal e patch correspondente
                canal = 0  # Usamos canal 0 para simplificar
                patch = cfg.get("patch", 0)
                if "patches_especificos" in cfg:
                    patch = cfg["patches_especificos"].get(idx_trilha, patch)
                
                # Injeta inicializadores
                trilha_nova.append(mido.Message("program_change", channel=canal, program=patch, time=0))
                trilha_nova.append(mido.Message("control_change", channel=canal, control=7, value=cfg["vol"], time=0))
                
                for msg in trilha_orig:
                    if hasattr(msg, "channel") and msg.type not in ("program_change", "control_change"):
                        trilha_nova.append(msg.copy(channel=canal))
                    elif msg.type not in ("program_change", "control_change"):
                        trilha_nova.append(msg.copy())
                midi_grupo.tracks.append(trilha_nova)
            
            # Salva MIDI do subgrupo
            caminho_mid_grupo = tmp_path / f"{nome_grupo}.mid"
            midi_grupo.save(str(caminho_mid_grupo))
            
            # Renderiza com FluidSynth
            arquivo_wav_grupo = tmp_path / f"{nome_grupo}.wav"
            cmd_fluid = [
                "fluidsynth",
                "-F", str(arquivo_wav_grupo),
                "-O", "float",
                "-T", "wav",
                "-g", "0.85",
                "--quiet",
                str(cfg["sf2"]),
                str(caminho_mid_grupo)
            ]
            res = subprocess.run(cmd_fluid, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Falha no FluidSynth para o subgrupo {nome_grupo}:\n{res.stderr}")
            
            wavs_temp.append(arquivo_wav_grupo)

        # Mixa os WAVs usando FFmpeg, aplicando normalização final e limiter
        log.info("Moxando subgrupos no FFmpeg e normalizando...")
        
        cmd_ffmpeg = ["ffmpeg", "-y"]
        for wav in wavs_temp:
            cmd_ffmpeg.extend(["-i", str(wav)])
            
        # Filtro de mixagem amix + normalização loudnorm e limiter
        filtro_mix = f"amix=inputs={len(wavs_temp)}:duration=longest:dropout_transition=2," \
                     f"alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false," \
                     f"loudnorm=I=-14:TP=-1:LRA=11"
                     
        cmd_ffmpeg.extend([
            "-filter_complex", filtro_mix,
            "-q:a", "0",
            "-map_metadata", "-1",
            "-loglevel", "error",
            str(arquivo_mp3)
        ])
        
        res_ffmpeg = subprocess.run(cmd_ffmpeg, capture_output=True, text=True)
        if res_ffmpeg.returncode != 0:
            raise RuntimeError(f"Falha no FFmpeg ao mixar e normalizar:\n{res_ffmpeg.stderr}")
            
    log.info(f"✓ Geração de áudio concluída: {arquivo_mp3.name}")

# ─────────────────────────────────────────────────────────────────────────────
# Execução Principal da CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="renderizador3.py — Humanização e Arranjos Sacros")
    parser.add_argument("--mid", type=str, required=True, help="Arquivo MIDI de entrada")
    parser.add_argument("--saida-mp3", type=str, required=True, help="Caminho do arquivo MP3 de saída")
    parser.add_argument("--tipo-arranjo", type=str, default="piano_solo",
                        choices=["piano_solo", "quarteto_cordas", "orgao_coral", "orquestra_completa"],
                        help="Tipo de arranjo e mixagem de SoundFonts")
    parser.add_argument("--reiniciar", action="store_true", help="Sobrescreve arquivos já gerados")
    parser.add_argument("--debug", action="store_true", help="Ativa logs detalhados de depuração")
    
    args = parser.parse_args()
    log = configurar_log(verbose=args.debug)

    caminho_mid = Path(args.mid)
    if not caminho_mid.exists():
        caminho_mid = MID_DIR / args.mid
        if not caminho_mid.exists():
            log.error(f"Arquivo MIDI não encontrado: {args.mid}")
            sys.exit(1)

    caminho_mp3 = Path(args.saida_mp3)
    caminho_mp3.parent.mkdir(parents=True, exist_ok=True)

    if caminho_mp3.exists() and not args.reiniciar:
        log.info(f"O arquivo {caminho_mp3.name} já existe. Pulando (use --reiniciar para forçar).")
        sys.exit(0)

    log.info(f"==================================================================")
    log.info(f" Humanizador Geração 3: {caminho_mid.name}")
    log.info(f" Arranjo: {args.tipo_arranjo}")
    log.info(f"==================================================================")

    try:
        # 1. Carrega e classifica SATB
        midi = mido.MidiFile(str(caminho_mid))
        vozes = detectar_satb(midi, log)

        # 2. Aplica Humanização de micro-timing e velocities fixas
        midi_humanizado = humanizar_midi(midi, vozes, log)

        # 3. Aplica Sustain Pedal (somente no piano solo)
        if args.tipo_arranjo == "piano_solo":
            midi_humanizado = aplicar_sustain_pedal(midi_humanizado, vozes, log)

        # 4. Aplica Expressão e Vibrato (em cordas e orquestra)
        if args.tipo_arranjo in ("quarteto_cordas", "orquestra_completa"):
            midi_humanizado = injetar_expressao_e_vibrato(midi_humanizado, log)

        # Salva MIDI humanizado intermediário ao lado do MP3
        caminho_mid_out = caminho_mp3.with_suffix(".mid")
        midi_humanizado.save(str(caminho_mid_out))
        log.info(f"MIDI humanizado intermediário salvo: {caminho_mid_out.name}")

        # 5. Renderização multi-soundfont isolando canais e mixando com FFmpeg
        renderizar_isolando_vozes(midi_humanizado, vozes, args.tipo_arranjo, caminho_mp3, log)

        # 6. Salva JSON de parâmetros
        import json
        from datetime import datetime
        
        def limpar_para_json(obj):
            if isinstance(obj, dict):
                return {k: limpar_para_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [limpar_para_json(x) for x in obj]
            elif isinstance(obj, Path):
                return str(obj)
            else:
                return obj

        # Determina a configuração interna baseado no arranjo selecionado
        if args.tipo_arranjo == "piano_solo":
            cfg_arranjo = {
                "nome": "piano_solo",
                "soundfonts": ["Equinox_Grand_Pianos.sf2"],
                "vozes": {
                    "soprano": {"sf2": "Equinox_Grand_Pianos.sf2", "patch": 0, "vol": 100},
                    "contralto": {"sf2": "Equinox_Grand_Pianos.sf2", "patch": 0, "vol": 100},
                    "tenor": {"sf2": "Equinox_Grand_Pianos.sf2", "patch": 0, "vol": 100},
                    "baixo": {"sf2": "Equinox_Grand_Pianos.sf2", "patch": 0, "vol": 100}
                }
            }
        elif args.tipo_arranjo == "quarteto_cordas":
            cfg_arranjo = {
                "nome": "quarteto_cordas",
                "soundfonts": ["aaviolin.sf2", "CrisisGeneralMidi301.sf2"],
                "vozes": {
                    "soprano": {"sf2": "aaviolin.sf2", "patch": 0, "vol": 105},
                    "contralto": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 41, "vol": 85},
                    "tenor": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 42, "vol": 85},
                    "baixo": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 43, "vol": 85}
                }
            }
        elif args.tipo_arranjo == "orgao_coral":
            cfg_arranjo = {
                "nome": "orgao_coral",
                "soundfonts": ["Mellotron.sf2", "Timbres_of_Heaven.sf2"],
                "vozes": {
                    "soprano": {"sf2": "Mellotron.sf2", "patch": 52, "vol": 90},
                    "contralto": {"sf2": "Mellotron.sf2", "patch": 52, "vol": 90},
                    "tenor": {"sf2": "Timbres_of_Heaven.sf2", "patch": 19, "vol": 95},
                    "baixo": {"sf2": "Timbres_of_Heaven.sf2", "patch": 19, "vol": 95}
                }
            }
        else: # orquestra_completa
            cfg_arranjo = {
                "nome": "orquestra_completa",
                "soundfonts": ["CrisisGeneralMidi301.sf2"],
                "vozes": {
                    "soprano": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 73, "vol": 95},
                    "contralto": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 71, "vol": 95},
                    "tenor": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 60, "vol": 95},
                    "baixo": {"sf2": "CrisisGeneralMidi301.sf2", "patch": 42, "vol": 95}
                }
            }

        arquivo_json = caminho_mp3.parent / "parametros.json"
        parametros = {
            "geracao": 3,
            "mid_original": str(caminho_mid.name),
            "preset": args.tipo_arranjo,
            "seed": 42,
            "humanizacao": True,
            "vozes_satb": vozes,
            "data_processamento": datetime.now().isoformat(),
            "parametros_recebidos": limpar_para_json(vars(args)),
            "configuracao_interna": limpar_para_json(cfg_arranjo)
        }
        with open(arquivo_json, "w", encoding="utf-8") as f_json:
            json.dump(limpar_para_json(parametros), f_json, indent=4, ensure_ascii=False)
        log.info(f"Parâmetros salvos em: {arquivo_json.name}")

    except Exception as e:
        log.error(f"Erro no processamento Geração 3: {e}", exc_info=args.debug)
        sys.exit(2)

if __name__ == "__main__":
    main()
