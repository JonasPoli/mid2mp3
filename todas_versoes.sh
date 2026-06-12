#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# todas_versoes.sh -- Gera MP3 nas versões selecionadas e validadas para um MIDI
#
# Uso:
#   ./todas_versoes.sh "001- Meu Hino.mid"
#   ./todas_versoes.sh "001- Meu Hino.mid" --reiniciar
#
# Todos os argumentos extras sao repassados ao renderizador.py.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
RENDERIZADOR="$SCRIPT_DIR/renderizador.py"
SOUNDFONTS_DIR="$SCRIPT_DIR/soundfonts"

# -- Validacoes basicas -------------------------------------------------------

if [[ $# -lt 1 ]]; then
    echo ""
    echo "Uso: $0 \"ARQUIVO.mid\" [opcoes extras do renderizador]"
    echo ""
    echo "Exemplos:"
    echo "  $0 \"001- Cristo meu Mestre.mid\""
    echo "  $0 \"001- Cristo meu Mestre.mid\" --arpejo --estilo-arpejo sacro"
    echo "  $0 \"001- Cristo meu Mestre.mid\" --reiniciar"
    echo ""
    exit 1
fi

ARQUIVO_MID="$1"
shift   # restante dos args vai ser repassado ao renderizador

# -- Soundfonts selecionadas --------------------------------------------------
SOUNDFONTS_ATIVAS=(
    "Equinox_Grand_Pianos"
    "MuseScore_General"
)

# -- Banner ------------------------------------------------------------------

echo ""
echo "=================================================================="
echo "   mid2mp3 -- Versões Sacras Selecionadas"
echo "=================================================================="
echo "  MIDI    : $ARQUIVO_MID"
echo "  Extras  : ${*:-(nenhum)}"
echo ""

# -- Loop por cada soundfont ativa -------------------------------------------

ERROS=0
TOTAL_GERADOS=0

for SF_NOME in "${SOUNDFONTS_ATIVAS[@]}"; do
    SF2_PATH="$SOUNDFONTS_DIR/${SF_NOME}.sf2"
    if [[ ! -f "$SF2_PATH" ]]; then
        echo "AVISO: Soundfont $SF_NOME nao encontrada em $SOUNDFONTS_DIR. Pulando..."
        continue
    fi

    echo "------------------------------------------------------------------"
    echo "  Processando Soundfont: $SF_NOME"
    echo "------------------------------------------------------------------"
    echo ""

    if [[ "$SF_NOME" == "Equinox_Grand_Pianos" ]]; then
        # Equinox Grand Pianos: Piano Solo
        # Duas texturas: Arpejada e Coral Simples.
        # Dois modelos: Steinway L/R e Yamaha L/R.
        for modelo in "steinway_lr" "yamaha_lr"; do
            desc_modelo="Steinway D L/R"
            if [[ "$modelo" == "yamaha_lr" ]]; then
                desc_modelo="Yamaha C7 L/R"
            fi

            # 1. Piano Arpejado
            echo "  [Equinox - Piano Arpejado ($desc_modelo)]..."
            if "$VENV_PYTHON" "$RENDERIZADOR" \
                --mid "$ARQUIVO_MID" \
                --formato "$SF_NOME" \
                --arranjo "pianos" \
                --piano-modelo "$modelo" \
                --arpejo \
                --estilo-arpejo sacro \
                --desincronismo \
                --reiniciar \
                "$@"; then
                echo "  -> OK: Piano Arpejado ($desc_modelo)"
                TOTAL_GERADOS=$((TOTAL_GERADOS + 1))
            else
                echo "  -> ERRO: Piano Arpejado ($desc_modelo)" >&2
                ERROS=$((ERROS + 1))
            fi

            # 2. Coral Simples
            echo "  [Equinox - Coral Simples ($desc_modelo)]..."
            if "$VENV_PYTHON" "$RENDERIZADOR" \
                --mid "$ARQUIVO_MID" \
                --formato "$SF_NOME" \
                --arranjo "pianos" \
                --piano-modelo "$modelo" \
                --desincronismo \
                --reiniciar \
                "$@"; then
                echo "  -> OK: Coral Simples ($desc_modelo)"
                TOTAL_GERADOS=$((TOTAL_GERADOS + 1))
            else
                echo "  -> ERRO: Coral Simples ($desc_modelo)" >&2
                ERROS=$((ERROS + 1))
            fi
        done

    elif [[ "$SF_NOME" == "MuseScore_General" ]]; then
        # MuseScore General: Arranjos Orquestrais, Órgãos e Sintetizados.
        
        # 1. Órgãos (Mesmo som nas 4 partes, desincronizado, SEM arpejo)
        ORGAN_ARRANJOS=(
            "orgao_igreja:Órgão de Igreja"
            "orgao_reed:Harmônio Reed"
        )
        for item in "${ORGAN_ARRANJOS[@]}"; do
            arr_nome="${item%%:*}"
            arr_desc="${item#*:}"
            echo "  [Órgão - $arr_desc (Sem Arpejo)]..."
            if "$VENV_PYTHON" "$RENDERIZADOR" \
                --mid "$ARQUIVO_MID" \
                --formato "$SF_NOME" \
                --arranjo "$arr_nome" \
                --desincronismo \
                --reiniciar \
                "$@"; then
                echo "  -> OK: $arr_desc (Sem Arpejo)"
                TOTAL_GERADOS=$((TOTAL_GERADOS + 1))
            else
                echo "  -> ERRO: $arr_desc (Sem Arpejo)" >&2
                ERROS=$((ERROS + 1))
            fi
        done

        # 2. Outras combinações de instrumentos para teste (Sempre desincronizados)
        TEST_ARRANJOS=(
            "orquestra_sacra_1:Orquestra Sacra 1 (Flauta, Clarinete, Trompa, Cello)"
            "orquestra_sacra_2:Orquestra Sacra 2 (Violino, Oboé, Viola, Fagote)"
            "orquestra_suave:Orquestra Suave (Oboé, Clarinete, Viola, Cello)"
            "sintetizado:Combinação Sintetizada (Square Lead, Synth Brass/Strings/Bass)"
            "orquestra_completa:Orquestra Completa"
            "classico_1:Clássico 1 (Flauta, Oboé, Harpa, Cello)"
            "cordas:Orquestra de Cordas"
        )
        for item in "${TEST_ARRANJOS[@]}"; do
            arr_nome="${item%%:*}"
            arr_desc="${item#*:}"
            
            # Teste com Arpejo Sacro
            echo "  [Teste Arpejado - $arr_desc]..."
            if "$VENV_PYTHON" "$RENDERIZADOR" \
                --mid "$ARQUIVO_MID" \
                --formato "$SF_NOME" \
                --arranjo "$arr_nome" \
                --arpejo \
                --estilo-arpejo sacro \
                --desincronismo \
                --reiniciar \
                "$@"; then
                echo "  -> OK: Teste Arpejado ($arr_nome)"
                TOTAL_GERADOS=$((TOTAL_GERADOS + 1))
            else
                echo "  -> ERRO: Teste Arpejado ($arr_nome)" >&2
                ERROS=$((ERROS + 1))
            fi

            # Teste sem Arpejo (Coral Simples)
            echo "  [Teste Coral Simples - $arr_desc]..."
            if "$VENV_PYTHON" "$RENDERIZADOR" \
                --mid "$ARQUIVO_MID" \
                --formato "$SF_NOME" \
                --desincronismo \
                --arranjo "$arr_nome" \
                --reiniciar \
                "$@"; then
                echo "  -> OK: Teste Coral Simples ($arr_nome)"
                TOTAL_GERADOS=$((TOTAL_GERADOS + 1))
            else
                echo "  -> ERRO: Teste Coral Simples ($arr_nome)" >&2
                ERROS=$((ERROS + 1))
            fi
        done
    fi
    echo ""
done

# -- Resumo final ------------------------------------------------------------

echo "=================================================================="
echo "  Concluídos com sucesso : $TOTAL_GERADOS versão(ões)"
if [[ $ERROS -gt 0 ]]; then
    echo "  Erros                  : $ERROS"
fi
echo ""
echo "  Os MP3s estão em: $SCRIPT_DIR/output/"
echo ""

# Lista arquivos gerados para este MIDI
BASENAME_MID=$(basename "$ARQUIVO_MID")
STEM="${BASENAME_MID%.*}"
NUM=$(echo "$STEM" | grep --color=never -oE '^[0-9]+' | tr -d '\r\n[:space:]' || true)
if [[ -n "$NUM" ]]; then
    NUM_INT=$NUM
    while [ "${NUM_INT#0}" != "$NUM_INT" ]; do
        NUM_INT="${NUM_INT#0}"
    done
    if [ -z "$NUM_INT" ]; then
        NUM_INT=0
    fi
    MP3_NOME=$(printf "%03d.mp3" "$NUM_INT")
else
    MP3_NOME="${STEM}.mp3"
fi

echo "  Arquivos gerados ($MP3_NOME em cada pasta de soundfont/arranjo):"
find "$SCRIPT_DIR/output" -name "$MP3_NOME" 2>/dev/null | sort | while IFS= read -r f; do
    echo "    $f"
done
echo ""
