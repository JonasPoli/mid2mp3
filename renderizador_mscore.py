#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renderizador_mscore.py — Conversor MIDI → MP3 de Alta Fidelidade via MuseScore 4 (Muse Sounds)
"""

import os
import sys
import argparse
import logging
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# Configuração de Diretórios
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MSCORE = "/Applications/MuseScore 4.app/Contents/MacOS/mscore"

INSTRUMENTS_TEMPLATES = {
    "metais_premium": {
        "descricao": "Quarteto de Metais (Trumpet, French Horn, Trombone, Tuba)",
        "vozes": {
            "soprano": {
                "id": "trumpet",
                "longName": "Trumpet",
                "shortName": "Tpt.",
                "trackName": "Trumpet",
                "instrumentId": "brass.trumpet",
                "clef": "G"
            },
            "contralto": {
                "id": "french-horn",
                "longName": "French Horn",
                "shortName": "F. Hn.",
                "trackName": "French Horn",
                "instrumentId": "brass.french-horn",
                "clef": "G"
            },
            "tenor": {
                "id": "trombone",
                "longName": "Trombone",
                "shortName": "Tbn.",
                "trackName": "Trombone",
                "instrumentId": "brass.trombone",
                "clef": "F"
            },
            "baixo": {
                "id": "tuba",
                "longName": "Tuba",
                "shortName": "Tba.",
                "trackName": "Tuba",
                "instrumentId": "brass.tuba",
                "clef": "F"
            }
        }
    },
    "madeiras_premium": {
        "descricao": "Quarteto de Madeiras (Flute, Oboe, Clarinet, Bassoon)",
        "vozes": {
            "soprano": {
                "id": "flute",
                "longName": "Flute",
                "shortName": "Fl.",
                "trackName": "Flute",
                "instrumentId": "wind.flutes.flute",
                "clef": "G"
            },
            "contralto": {
                "id": "oboe",
                "longName": "Oboe",
                "shortName": "Ob.",
                "trackName": "Oboe",
                "instrumentId": "wind.reed.oboe",
                "clef": "G"
            },
            "tenor": {
                "id": "clarinet",
                "longName": "Clarinet in B♭",
                "shortName": "Cl.",
                "trackName": "Clarinet",
                "instrumentId": "wind.reed.clarinet.b-flat",
                "clef": "G"
            },
            "baixo": {
                "id": "bassoon",
                "longName": "Bassoon",
                "shortName": "Bsn.",
                "trackName": "Bassoon",
                "instrumentId": "wind.reed.bassoon",
                "clef": "F"
            }
        }
    },
    "cordas_premium": {
        "descricao": "Quarteto de Cordas Solo (Violin I, Violin II, Viola, Cello)",
        "vozes": {
            "soprano": {
                "id": "violin-i",
                "longName": "Violin I",
                "shortName": "Vln. I",
                "trackName": "Violin I",
                "instrumentId": "strings.violin",
                "clef": "G"
            },
            "contralto": {
                "id": "violin-ii",
                "longName": "Violin II",
                "shortName": "Vln. II",
                "trackName": "Violin II",
                "instrumentId": "strings.violin",
                "clef": "G"
            },
            "tenor": {
                "id": "viola",
                "longName": "Viola",
                "shortName": "Vla.",
                "trackName": "Viola",
                "instrumentId": "strings.viola",
                "clef": "C"  # Viola usa clef C (Alto clef) por padrão em MuseScore, ou podemos manter F/G
            },
            "baixo": {
                "id": "violoncello",
                "longName": "Violoncello",
                "shortName": "Vc.",
                "trackName": "Violoncello",
                "instrumentId": "strings.cello",
                "clef": "F"
            }
        }
    },
    "orgao_premium": {
        "descricao": "Grande Órgão de Igreja Muse Sounds",
        "vozes": {
            "soprano": {
                "id": "church-organ",
                "longName": "Church Organ",
                "shortName": "Org.",
                "trackName": "Church Organ",
                "instrumentId": "keyboard.organ.church",
                "clef": "G"
            },
            "contralto": {
                "id": "church-organ",
                "longName": "Church Organ",
                "shortName": "Org.",
                "trackName": "Church Organ",
                "instrumentId": "keyboard.organ.church",
                "clef": "G"
            },
            "tenor": {
                "id": "church-organ",
                "longName": "Church Organ",
                "shortName": "Org.",
                "trackName": "Church Organ",
                "instrumentId": "keyboard.organ.church",
                "clef": "F"
            },
            "baixo": {
                "id": "church-organ",
                "longName": "Church Organ",
                "shortName": "Org.",
                "trackName": "Church Organ",
                "instrumentId": "keyboard.organ.church",
                "clef": "F"
            }
        }
    },
    "piano_premium": {
        "descricao": "Piano de Cauda Steinway Muse Sounds",
        "vozes": {
            "soprano": {
                "id": "piano",
                "longName": "Piano",
                "shortName": "Pno.",
                "trackName": "Piano",
                "instrumentId": "keyboard.piano",
                "clef": "G"
            },
            "contralto": {
                "id": "piano",
                "longName": "Piano",
                "shortName": "Pno.",
                "trackName": "Piano",
                "instrumentId": "keyboard.piano",
                "clef": "G"
            },
            "tenor": {
                "id": "piano",
                "longName": "Piano",
                "shortName": "Pno.",
                "trackName": "Piano",
                "instrumentId": "keyboard.piano",
                "clef": "F"
            },
            "baixo": {
                "id": "piano",
                "longName": "Piano",
                "shortName": "Pno.",
                "trackName": "Piano",
                "instrumentId": "keyboard.piano",
                "clef": "F"
            }
        }
    }
}

def configurar_log(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("renderizador_mscore")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger

def modificar_mscx(file_path: Path, preset: str, log: logging.Logger) -> bool:
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        parts = root.findall(".//Part")
        log.info(f"  → Mapeando {len(parts)} partes para o arranjo '{preset}'...")
        
        template = INSTRUMENTS_TEMPLATES.get(preset)
        if not template:
            log.error(f"Preset '{preset}' não encontrado nos templates.")
            return False
            
        roles = ["soprano", "contralto", "tenor", "baixo"]
        
        for idx, part in enumerate(parts):
            if idx >= len(roles):
                break
            role = roles[idx]
            tmpl = template["vozes"][role]
            
            instrument = part.find("Instrument")
            if instrument is not None:
                # Altera o atributo id do instrumento
                instrument.set("id", tmpl["id"])
                
                # Altera ou adiciona elementos filho
                for tag in ["longName", "shortName", "trackName", "instrumentId"]:
                    elem = instrument.find(tag)
                    if elem is not None:
                        elem.text = tmpl[tag]
                    else:
                        new_elem = ET.SubElement(instrument, tag)
                        new_elem.text = tmpl[tag]
                
                clef = instrument.find("clef")
                if clef is not None:
                    clef.text = tmpl["clef"]
                else:
                    new_clef = ET.SubElement(instrument, "clef")
                    new_clef.text = tmpl["clef"]
                    
            track_name = part.find("trackName")
            if track_name is not None:
                track_name.text = tmpl["trackName"]
                
        tree.write(file_path, encoding="UTF-8", xml_declaration=True)
        log.info(f"  → XML modificado com sucesso!")
        return True
    except Exception as e:
        log.error(f"Erro ao modificar arquivo MSCX: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="renderizador_mscore.py — Renderização Premium com MuseScore 4")
    parser.add_argument("--mid", type=str, required=True, help="Arquivo MIDI de entrada")
    parser.add_argument("--preset", type=str, default="metais_premium", choices=list(INSTRUMENTS_TEMPLATES.keys()), help="Preset de instrumentos")
    parser.add_argument("--saida-dir", type=str, help="Diretório de saída")
    parser.add_argument("--mscore-path", type=str, default=DEFAULT_MSCORE, help="Caminho do executável do MuseScore 4")
    parser.add_argument("--reiniciar", action="store_true", help="Sobrescreve arquivos já gerados")
    parser.add_argument("--manter-wav", action="store_true", help="Mantém o arquivo WAV temporário")
    parser.add_argument("--debug", action="store_true", help="Ativa logs de depuração")
    
    args = parser.parse_args()
    log = configurar_log(args.debug)
    
    caminho_mid = Path(args.mid)
    if not caminho_mid.exists():
        # Busca no diretório 'mid' padrão
        caminho_mid = SCRIPT_DIR / "mid" / args.mid
        if not caminho_mid.exists():
            log.error(f"Arquivo MIDI não encontrado: {args.mid}")
            sys.exit(1)
            
    saida_dir = Path(args.saida_dir) if args.saida_dir else SCRIPT_DIR / "output_mscore" / caminho_mid.stem / args.preset
    saida_dir.mkdir(parents=True, exist_ok=True)
    
    arquivo_mp3 = saida_dir / f"{caminho_mid.stem}.mp3"
    
    if arquivo_mp3.exists() and not args.reiniciar:
        log.info(f"O arquivo {arquivo_mp3.name} já existe. Pulando (use --reiniciar para forçar).")
        sys.exit(0)
        
    log.info("==================================================================")
    log.info(f" Renderizador MuseScore | {caminho_mid.name} | Preset: {args.preset}")
    log.info(f" Arranjo: {INSTRUMENTS_TEMPLATES[args.preset]['descricao']}")
    log.info("==================================================================")
    
    mscore_cmd = args.mscore_path
    if not Path(mscore_cmd).exists():
        log.error(f"Executável do MuseScore 4 não encontrado em '{mscore_cmd}'. Verifique o caminho.")
        sys.exit(1)
        
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        temp_mscx = tmp_path / "temp_score.mscx"
        temp_wav = tmp_path / "temp_audio.wav"
        
        # 1. Converter MIDI para MSCX via MuseScore CLI
        log.info("  1. Importando MIDI para formato nativo do MuseScore...")
        cmd_import = [mscore_cmd, "-o", str(temp_mscx), str(caminho_mid)]
        res_import = subprocess.run(cmd_import, capture_output=True, text=True)
        if res_import.returncode != 0:
            log.error(f"Erro ao importar MIDI no MuseScore:\n{res_import.stderr}")
            sys.exit(1)
            
        # 2. Modificar MSCX para atribuir instrumentos premium
        log.info("  2. Reconfigurando instrumentos para arranjo premium...")
        if not modificar_mscx(temp_mscx, args.preset, log):
            sys.exit(1)
            
        # 3. Renderizar áudio (WAV) via MuseScore CLI (carrega Muse Sounds automaticamente)
        log.info("  3. Renderizando áudio de alta fidelidade (Muse Sounds)...")
        cmd_render = [mscore_cmd, "-o", str(temp_wav), str(temp_mscx)]
        res_render = subprocess.run(cmd_render, capture_output=True, text=True)
        if res_render.returncode != 0:
            log.error(f"Erro ao renderizar áudio no MuseScore:\n{res_render.stderr}")
            sys.exit(1)
            
        # 4. Normalizar áudio e converter para MP3 via FFmpeg
        log.info("  4. Normalizando volume (EBU R128) e gerando MP3...")
        filtros = (
            "alimiter=level_in=1:level_out=1:limit=0.891:attack=1:release=50:level=false,"
            "loudnorm=I=-14:TP=-1:LRA=11"
        )
        cmd_ffmpeg = [
            "ffmpeg", "-y",
            "-i", str(temp_wav),
            "-af", filtros,
            "-q:a", "0",
            "-loglevel", "error",
            str(arquivo_mp3)
        ]
        res_ffmpeg = subprocess.run(cmd_ffmpeg, capture_output=True, text=True)
        if res_ffmpeg.returncode != 0:
            log.error(f"Erro no FFmpeg:\n{res_ffmpeg.stderr}")
            sys.exit(1)
            
        if args.manter_wav:
            wav_saida = saida_dir / f"{caminho_mid.stem}.wav"
            import shutil
            shutil.copy(str(temp_wav), str(wav_saida))
            log.info(f"  💾 Áudio WAV intermediário mantido em: {wav_saida.name}")
            
    log.info(f"==================================================================")
    log.info(f"  ✓ Sucesso! Áudio de altíssimo realismo gerado em:")
    log.info(f"  → {arquivo_mp3}")
    log.info(f"==================================================================")

if __name__ == "__main__":
    main()
