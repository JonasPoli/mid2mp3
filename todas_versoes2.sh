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
    "piano_devocional"
    "piano_arpejado_suave"
    "equinox_grand"
    "01_orgao_liturgico_timbres"
    "02_orgao_tradicional_crisis"
    "03_orgao_eletronico_drawbar"
    "04_orgao_suave_musescore"
    "05_orgao_ccb_celeste"
    "06_orgao_ccb_misto"
    "07_orgao_pleno_majestoso"
    "08_orgao_pleno_arpejado"
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
