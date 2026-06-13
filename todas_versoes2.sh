#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# todas_versoes2.sh -- Gera MP3 nos 8 presets de Órgãos Sacros
#
# Uso:
#   ./todas_versoes2.sh "003- Faz-nos ouvir Tua voz.mid"
#   ./todas_versoes2.sh "003- Faz-nos ouvir Tua voz.mid" --reiniciar
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
if [[ ! -f "$VENV_PYTHON" ]]; then
    VENV_PYTHON="python3"
fi
RENDERIZADOR="$SCRIPT_DIR/renderizador2.py"

# -- Validações Básicas --------------------------------------------------------
if [[ $# -lt 1 ]]; then
    echo ""
    echo "Uso: $0 \"ARQUIVO.mid\" [--reiniciar]"
    echo ""
    exit 1
fi

ARQUIVO_MID="$1"
REINICIAR_FLAG=""

# Captura --reiniciar se passado
if [[ $# -ge 2 && "$2" == "--reiniciar" ]]; then
    REINICIAR_FLAG="--reiniciar"
fi

# -- Resolução do caminho do MIDI ---------------------------------------------
MIDI_PATH="$ARQUIVO_MID"
if [[ ! -f "$MIDI_PATH" ]]; then
    MIDI_PATH="$SCRIPT_DIR/mid/$ARQUIVO_MID"
    if [[ ! -f "$MIDI_PATH" ]]; then
        echo "ERRO: Arquivo MIDI '$ARQUIVO_MID' não foi encontrado localmente nem na pasta '$SCRIPT_DIR/mid/'."
        exit 1
    fi
fi

BASENAME_MID=$(basename "$MIDI_PATH")
STEM="${BASENAME_MID%.*}"
SAIDA_BASE_DIR="$SCRIPT_DIR/output2/$STEM"

# Lista completa de presets
PRESETS=(
    # Pianos e Originais Geração 2
    "01_piano_devocional"
    "02_piano_arpejado_suave"
    "03_equinox_grand"
    "04_musicbox_suave"
    "05_orgao_sacro"
    "06_timbres_heaven_suave"
    "07_generaluser_leve"

    # Órgãos Sacros Geração 2
    "08_orgao_liturgico_timbres"
    "09_orgao_tradicional_crisis"
    "10_orgao_eletronico_drawbar"
    "11_orgao_suave_musescore"
    "12_orgao_ccb_celeste"
    "13_orgao_ccb_misto"
    "14_orgao_pleno_majestoso"
    "15_orgao_pleno_arpejado"

    # Orquestras Sacras Geração 3 (no motor v2)
    "16_orq_ccb_classica"
    "17_orq_orgao_fundo"
    "18_orq_metais_suaves"
    "19_orq_cordas_completas"
    "20_orq_madeiras_delicadas"
    "21_orq_hinario_cantado"
    "22_orq_piano_leve"
    "23_orq_orgao_metais"
    "24_orq_grande"
    "25_orq_tradicional_banda"
    "26_orq_favorita_ia"

    # Premium Geração 4 (no motor v2)
    "38_equinox_sacro"
    "39_coro_e_orgao"
    "40_violino_e_piano"
    "41_orquestra_completa"
    "42_meditativo"
    "43_musicbox_arpejado"

    # Órgãos Geração 4 (no motor v2)
    "44_orgao_igreja_musescore"
    "45_orgao_reed_musescore"
    "46_orgao_igreja_timbres"
    "47_orgao_reed_timbres"
    "48_orgao_igreja_crisis"
    "49_orgao_igreja_sgm"
    "50_orgao_misto_ccb"

    # Metais Geração 4 (no motor v2)
    "51_met_quarteto_tradicional"
    "52_met_metais_suaves"
    "53_met_solene_cheio"
    "54_met_mais_encorpado"
    "55_met_estilo_banda"
    "56_met_hino_calmo"
    "57_met_hino_forte"
    "58_met_som_mais_nobre"
    "59_met_gravacao_suave"
    "60_met_metais_graves"
    "61_met_clima_sacro"
    "62_met_estudo_vozes"
)

echo "=================================================================="
echo "   mid2mp3 v2 -- Processamento nos Presets de Órgãos"
echo "=================================================================="
echo "  MIDI    : $MIDI_PATH"
echo "  Saída   : $SAIDA_BASE_DIR"
echo ""

SUCESSOS=()
FALHAS=()

# Loop por cada preset
for pres in "${PRESETS[@]}"; do
    SAIDA_PRESET_DIR="$SAIDA_BASE_DIR/$pres"
    
    echo "------------------------------------------------------------------"
    echo "  Renderizando preset: $pres..."
    echo "------------------------------------------------------------------"
    
    set +e
    "$VENV_PYTHON" "$RENDERIZADOR" \
        --mid "$MIDI_PATH" \
        --preset "$pres" \
        --saida-dir "$SAIDA_PRESET_DIR" \
        --salvar-midi \
        --salvar-json \
        $REINICIAR_FLAG
        
    STATUS=$?
    set -e
    
    if [ $STATUS -eq 0 ]; then
        echo "  -> OK: Preset '$pres' gerado com sucesso!"
        SUCESSOS+=("$pres")
    else
        echo "  -> ERRO: Falha ao gerar preset '$pres' (Código de erro: $STATUS)" >&2
        FALHAS+=("$pres")
    fi
    echo ""
done

# -- Relatório Final -----------------------------------------------------------
echo "=================================================================="
echo "   Resumo do Processamento em Lote - Órgãos"
echo "=================================================================="
echo "  Pasta de saída: $SAIDA_BASE_DIR"
echo "  Total com sucesso : ${#SUCESSOS[@]} de ${#PRESETS[@]}"
echo "  Total com erros   : ${#FALHAS[@]}"
echo ""

if [ ${#SUCESSOS[@]} -gt 0 ]; then
    echo "  Presets bem-sucedidos:"
    for pres in "${SUCESSOS[@]}"; do
        echo "    - $pres"
    done
fi

if [ ${#FALHAS[@]} -gt 0 ]; then
    echo ""
    echo "  Presets com erro/falha:"
    for pres in "${FALHAS[@]}"; do
        echo "    - $pres"
    done
fi
echo "=================================================================="
echo ""
