# mid2mp3 — Conversor MIDI → MP3

Converte uma coleção de arquivos MIDI em MP3 usando **FluidSynth** + **FFmpeg**, com suporte a múltiplos SoundFonts, banco de progresso persistente (SQLite), arpejos, desincronismo, transposição e controle de velocidade.

---

## Sumário

1. [Pré-requisitos](#1-pré-requisitos)
2. [Instalação](#2-instalação)
3. [SoundFonts disponíveis](#3-soundfonts-disponíveis)
4. [Estrutura do projeto](#4-estrutura-do-projeto)
5. [CLI — referência completa](#5-cli--referência-completa)
6. [Exemplos práticos](#6-exemplos-práticos)
7. [Banco de progresso](#7-banco-de-progresso)
8. [Como funciona o arpejo](#8-como-funciona-o-arpejo)
9. [Como funciona o desincronismo](#9-como-funciona-o-desincronismo)
10. [Painel Administrativo Web](#10-painel-administrativo-web)
11. [Notas](#11-notas)

---

## 1. Pré-requisitos

| Ferramenta | Versão mínima | Instalação (macOS) |
|---|---|---|
| **Python** | 3.12+ | `brew install python` |
| **FluidSynth** | 2.x | `brew install fluidsynth` |
| **FFmpeg** | 6.x+ | `brew install ffmpeg` |

```bash
# Verificar instalação
python3 --version && fluidsynth --version && ffmpeg -version
```

---

## 2. Instalação

```bash
cd /Volumes/Dados/work/mid2mp3

# Criar o ambiente virtual
python3 -m venv venv

# Ativar (sempre necessário ao abrir um novo terminal)
source venv/bin/activate

# Instalar dependências Python
pip install -r requirements.txt
```

> Para desativar o ambiente virtual: `deactivate`

---

## 3. SoundFonts disponíveis

Os arquivos `.sf2` ficam em `soundfonts/` e são detectados automaticamente pelo nome do arquivo (sem extensão).

```bash
# Ver todos os SoundFonts instalados
python renderizador.py --soundfonts
```

### SoundFonts incluídos / recomendados

| `--formato` | Arquivo | Tamanho | Indicado para |
|---|---|---|---|
| `MusicBox` | `MusicBox.sf2` | ~6 MB | Som de caixinha de música puro |
| `MuseScore_General` | `MuseScore_General.sf2` | ~206 MB | Melhor qualidade geral (orquestral) |
| `GeneralUser_GS` | `GeneralUser-GS.sf2` | ~30 MB | GM padrão, testes rápidos |

### Outros SoundFonts recomendados (baixar manualmente)

- **SGM v2.01** — GM completo, alta qualidade: https://musical-artifacts.com/artifacts/523
- **Timbres of Heaven** — orquestral, cordas realistas: https://musical-artifacts.com/artifacts/28
- **FluidR3 GM** — clássico e consistente: https://musical-artifacts.com/artifacts/27

Coloque qualquer `.sf2` em `soundfonts/` e ele aparecerá automaticamente no `--formato`.

### Catálogo de Instrumentos por SoundFont

#### 1. SoundFonts General MIDI Completos
Os seguintes SoundFonts contêm todos os **128 instrumentos padrão** do protocolo General MIDI 1, além de variações e kits de bateria (Roland GS / Yamaha XG). 
*   `GeneralUser_GS` (`GeneralUser-GS.sf2`)
*   `MuseScore_General` (`MuseScore_General.sf2`)
*   `SGM_V2.01` (`SGM-V2.01.sf2`)
*   `Timbres_of_Heaven` (`Timbres_of_Heaven.sf2`)

Para referência rápida, a tabela de mapeamento padrão (patch 0-127) implementada nesses bancos é:
*   **0-7 (Pianos)**: 0: Acoustic Grand, 1: Bright Acoustic, 2: Electric Grand, 3: Honky-tonk, 4: Electric Piano 1, 5: Electric Piano 2, 6: Harpsichord, 7: Clavinet.
*   **8-15 (Percussão Cromática)**: 8: Celesta, 9: Glockenspiel, 10: Music Box, 11: Vibraphone, 12: Marimba, 13: Xylophone, 14: Tubular Bells, 15: Dulcimer.
*   **16-23 (Órgãos)**: 16: Drawbar, 17: Percussive, 18: Rock, 19: Church Organ (Igreja), 20: Reed Organ, 21: Accordion, 22: Harmonica, 23: Tango Accordion.
*   **24-31 (Guitarras/Violões)**: 24: Nylon Guitar, 25: Steel Guitar, 26: Jazz Guitar, 27: Clean Guitar, 28: Muted Guitar, 29: Overdriven, 30: Distortion, 31: Harmonics.
*   **32-39 (Baixos)**: 32: Acoustic Bass, 33: Finger Bass, 34: Pick Bass, 35: Fretless, 36: Slap Bass 1, 37: Slap Bass 2, 38: Synth Bass 1, 39: Synth Bass 2.
*   **40-47 (Cordas Solistas)**: 40: Violin, 41: Viola, 42: Cello, 43: Contrabass, 44: Tremolo Strings, 45: Pizzicato, 46: Harp, 47: Timpani.
*   **48-55 (Conjuntos/Ensembles)**: 48: String Ensemble 1 (Cordas/Orquestra), 49: String Ensemble 2, 50: Synth Strings 1, 51: Synth Strings 2, 52: Choir Aahs, 53: Voice Oohs, 54: Synth Voice, 55: Orchestra Hit.
*   **56-63 (Metais/Brass)**: 56: Trumpet, 57: Trombone, 58: Tuba, 59: Muted Trumpet, 60: French Horn, 61: Brass Section (Metais), 62: Synth Brass 1, 63: Synth Brass 2.
*   **64-71 (Palhetas/Reeds)**: 64: Soprano Sax, 65: Alto Sax, 66: Tenor Sax, 67: Baritone Sax, 68: Oboe, 69: English Horn, 70: Bassoon, 71: Clarinet.
*   **72-79 (Sopros de Tubo)**: 72: Piccolo, 73: Flute, 74: Recorder, 75: Pan Flute, 76: Bottle Blow, 77: Shakuhachi, 78: Whistle, 79: Ocarina.
*   **80-87 (Sintetizadores Lead)**: 80: Square Lead, 81: Sawtooth Lead, 82: Calliope, 83: Chiff, 84: Charang, 85: Voice Lead, 86: Fifths, 87: Bass+Lead.
*   **88-95 (Sintetizadores Pad)**: 88: New Age, 89: Warm, 90: Polysynth, 91: Choir, 92: Bowed, 93: Metallic, 94: Halo, 95: Sweep.
*   **96-103 (Efeitos Synth/FX)**: 96: Rain, 97: Soundtrack, 98: Crystal, 99: Atmosphere, 100: Brightness, 101: Goblins, 102: Echoes, 103: Sci-fi.
*   **104-111 (Étnicos)**: 104: Sitar, 105: Banjo, 106: Shamisen, 107: Koto, 108: Kalimba, 109: Bagpipe, 110: Fiddle, 111: Shanai.
*   **112-119 (Percussivos)**: 112: Tinkle Bell, 113: Agogo, 114: Steel Drums, 115: Woodblock, 116: Taiko Drum, 117: Melodic Tom, 118: Synth Drum, 119: Reverse Cymbal.
*   **120-127 (Efeitos Sonoros)**: 120: Fret Noise, 121: Breath Noise, 122: Seashore, 123: Bird Tweet, 124: Telephone Ring, 125: Helicopter, 126: Applause, 127: Gunshot.

---

#### 2. Mellotron (`Mellotron.sf2`)
Instrumento clássico vintage com fita magnética:
*   `000-000`: Gc3 Brass
*   `000-001`: Gong
*   `000-002`: M300A Violins
*   `000-003`: M300B Violin
*   `000-004`: M400 Cello
*   `000-005`: M400 Cmb Choir
*   `000-006`: M400 String Section
*   `000-007`: M400 Woodwind2
*   `000-008`: MKII 3 Violins
*   `000-009`: MKII Brass
*   `000-010`: MKII Flute
*   `000-011`: TapeKlik

---

#### 3. The Megalovania Library (`The_Megalovania_Library.sf2`)
Sintetizadores e instrumentos temáticos de videogame chiptune:
*   `000-000`: Lead & Bass OD
*   `000-001`: Synth 1
*   `000-002`: Violin Detache (Violino curto destacado - ótimo para notas rápidas)
*   `000-003`: Shreddage X (Guitarra Pesada)
*   `000-004`: Shreddage Bass (Baixo Elétrico)
*   `000-005`: Organ 3
*   `000-006`: Square
*   `000-007`: Brass 1
*   `000-008`: Impact Hit
*   `128-000`: Drums (Kits de Bateria Chiptune)

---

#### 4. MusicBox (`MusicBox.sf2`)
Caixinha de música de metal pura:
*   `000-000`: twinklestar musicbo (Repete em todas as notas e canais)

---

#### 5. Vintage Dreams Waves (`VintageDreamsWaves.sf2`)
Sintetizadores analógicos vintage e baterias eletrônicas clássicas:
*   `000-000`: FM Bells 1       | `000-043`: Lead Synth 3     | `000-086`: Sustained Harp
*   `000-001`: FM Carillion     | `000-044`: Casio VL-1 Pops  | `000-087`: Awesome Strings
*   `000-002`: Square Floot     | `000-045`: Wailing Hit      | `000-088`: Rough Strings
*   `000-003`: Grungy Ramp Bass | `000-046`: Oink Grind       | `000-089`: Techno Bells
*   `000-004`: Detuned Saws     | `000-047`: Metallic Clink   | `000-090`: Dreamy Pad
*   `000-005`: El Cheapo Organ  | `000-048`: Faerie Chorale   | `000-091`: Breath Pad
*   `000-006`: Dragon Sweep     | `000-049`: FM Bass Hit      | `000-092`: Warehouse Perc
*   `000-007`: Sheet Bass       | `000-050`: Dream Hit        | `000-093`: Sawtooth Hit
*   `000-008`: Resonating Pad   | `000-051`: Gated FM Bass    | `000-094`: Oingo-Boingo
*   `000-009`: Hard Grunge Bass | `000-052`: Harsh FM Bass    | `000-095`: Yazoo Zips
*   `000-010`: FM Xmas Bells    | `000-053`: Singing Bells    | `000-096`: Yazoo Bass Hit
*   `000-011`: Meat Grinder     | `000-054`: Phasing Choir    | `000-097`: Zippy Bass
*   `000-012`: Church Organ     | `000-055`: FM Clang         | `000-098`: Thunder
*   `000-013`: Classic FM Bass  | `000-056`: China Voices     | `000-099`: Killer Bass
*   `000-014`: Thin Sawtooth    | `000-057`: Rubber Bass      | `000-100`: Stab Bass Strings
*   `000-015`: Dream Flute      | `000-058`: Polysynth Warp   | `000-101`: Monster Strings
*   `000-016`: Triangle Simple  | `000-059`: Wavepad          | `000-102`: Cheesy Pad
*   `000-017`: Smooth Flute     | `000-060`: Pop Bass 2       | `000-103`: New Life 2
*   `000-018`: Smooth Strings 1 | `000-061`: Mega-Phaser      | `000-104`: Mono Analog Bass
*   `000-019`: FM Electric Bass | `000-062`: Screaming Pad    | `000-105`: Polysynth Hit
*   `000-020`: Lately Bass      | `000-063`: Melodic Vibrato  | `000-106`: Venus Violin Hit
*   `000-021`: Pop Bass 1       | `000-064`: Twips Ring       | `000-107`: Ping-Pong
*   `000-022`: Sweep Bass       | `000-065`: Wah              | `000-108`: Polysynth 2
*   `000-023`: Square Flute     | `000-066`: Blistering Bells | `000-109`: Cheap Synth
*   `000-024`: Chorale          | `000-067`: Click Pops       | `000-110`: Heavy Square
*   `000-025`: Breezy Calliope  | `000-068`: Xylophone        | `000-111`: Distorted Lead
*   `000-026`: D50-ish Bells    | `000-069`: New Life         | `000-112`: Singing Strings
*   `000-027`: Bingo Bells      | `000-070`: Long Bass        | `000-113`: Warble
*   `000-028`: Electric Slap    | `000-071`: Banshee Pad      | `000-114`: Wind Blast
*   `000-029`: Fantasy          | `000-072`: Undulating Pad   | `000-115`: Laser Pops
*   `000-030`: Vatican Pipes    | `000-073`: Spudge Bass      | `000-116`: Warbling Bird 1
*   `000-031`: Bass Dragon Choir| `000-074`: Warble Pad       | `000-117`: Warbling Bird 2
*   `000-032`: Cosmic Vibrap    | `000-075`: Yazoo Blips      | `000-118`: Wind Down
*   `000-033`: Water Triangle   | `000-076`: Sqncr Bass       | `000-119`: Delicate Bells
*   `000-034`: Sq Pop Flute     | `000-077`: Acid Sub Bass    | `000-120`: Delicate Marimba
*   `000-035`: Phasing Strings  | `000-078`: Echo Pop Bass    | `000-121`: Woody Bass
*   `000-036`: Vatican Bell     | `000-079`: Acid Bass 2      | `000-122`: Filtered Stack
*   `000-037`: Sine Whistle     | `000-080`: Monster Stack    | `000-123`: Sqncr Bass 2
*   `000-038`: Sine Bongos      | `000-081`: Panned Stack     | `000-124`: New Age Organ
*   `000-039`: Space Warp       | `000-082`: Ethnic Bow       | `000-125`: Gated Screamer
*   `000-040`: Aluminium Plate  | `000-083`: Noisy Pops       | `000-126`: Wonderland Xylo
*   `000-041`: Lead Synth 1     | `000-084`: House Organ      | `000-127`: Space Flute
*   `000-042`: Lead Synth 2     | `000-085`: Bonky Organ      |
*   Kits de Bateria (Bancos de Percussão em `128-xxx`):
    *   `128-000`: TR-101 Drumset
    *   `128-001`: CR-78 Drumset
    *   `128-002`: TR-808 Drumset
    *   `128-003`: TR-909 Drumset
    *   `128-004`: Analog Drumset
    *   `128-005`: Kraftwerk Drumset
    *   `128-006`: Electronic Drumset
    *   `128-007`: TR-808 Drumset 2

---

## 4. Estrutura do projeto

```
mid2mp3/
├── renderizador.py        ← script principal
├── requirements.txt       ← dependências Python (mido)
├── README.md              ← este arquivo
├── .gitignore
├── progresso.db           ← banco SQLite (criado automaticamente)
├── venv/                  ← ambiente virtual Python (não versionado)
├── mid/                   ← arquivos MIDI de entrada
│   ├── 001- Cristo meu Mestre.mid
│   └── ...
├── soundfonts/            ← coloque seus .sf2 aqui (não versionados)
│   ├── MusicBox.sf2
│   ├── MuseScore_General.sf2
│   └── GeneralUser-GS.sf2
└── output/                ← MP3s gerados (criado automaticamente, não versionado)
    ├── MusicBox/
    ├── MusicBox_arpejo_ascendente_desync010s/
    └── MuseScore_General_arpejo_ascendente/
```

---

## 5. CLI — referência completa

```bash
python renderizador.py [opções]
```

### Referência rápida (ver todas as opções)

```bash
python renderizador.py '?'
# ou
python renderizador.py --opcoes
```

### Todos os parâmetros

#### Seleção de arquivo e formato

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--formato NOME` | *(obrigatório)* | Nome do SoundFont (sem extensão) |
| `--mid "ARQUIVO.mid"` | todos | Processa apenas esse arquivo |

#### Instrumento (patch GM)

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--instrumento NOME` | original | Força um instrumento GM pelo nome (`caixa_de_musica`, `piano`, `harpa`…) |
| `--patch-numero 0-127` | original | Força um patch GM direto (0=Piano, 9=Music Box…). Sobreposto por `--instrumento` |

#### Arpejo

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--arpejo` | desativado | Adiciona uma trilha de arpejo na voz mais grave |
| `--estilo-arpejo ESTILO` | `ascendente` | `ascendente` · `descendente` · `alternado` |

A trilha de arpejo é **adicionada** (não substitui a trilha original), com volume ~75% e instrumento idêntico ao escolhido.

#### Desincronismo

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--desincronismo` | desativado | Ativa atraso aleatório por nota nas trilhas originais |
| `--delay-faixa SEG` | `0.1` | Valor base do atraso (s). Cada nota recebe aleatoriamente `SEG` ou `2×SEG` |

> A trilha 0 (condutora) e a trilha de arpejo **nunca** são desincronizadas.

#### Velocidade e transposição

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--velocidade %` | `100` | Velocidade em porcentagem (80 = 20% mais lento, 150 = 50% mais rápido) |
| `--semitons N` | `0` | Transpõe todas as notas N semitons (+12 = oitava acima, -5 = 5 tons abaixo) |

#### Controle de progresso

| Parâmetro | Descrição |
|---|---|
| `--continuar` | *(padrão)* Pula arquivos já concluídos |
| `--reiniciar` | Força reprocessamento de tudo |
| `--dry-run` | Simula sem gerar nenhum arquivo |
| `--verbose` / `-v` | Saída detalhada (DEBUG) |

#### Informação

| Parâmetro | Descrição |
|---|---|
| `--soundfonts` | Lista SoundFonts instalados |
| `--listar` | Lista MIDIs com status no banco |
| `--status` | Resumo do banco (concluídos/erros) |
| `--opcoes` / `?` | Referência completa interativa |

---

## 6. Exemplos práticos

### Verificar o ambiente

```bash
source venv/bin/activate
python renderizador.py --soundfonts
python renderizador.py --status
```

### Converter um único arquivo (caixinha de música)

```bash
python renderizador.py \
  --mid "001- Cristo meu Mestre.mid" \
  --formato MusicBox \
  --reiniciar
```

### Exemplo completo com todos os opcionais

```bash
python renderizador.py \
  --mid "001- Cristo meu Mestre.mid" \
  --formato MusicBox \
  --instrumento caixa_de_musica \
  --arpejo \
  --estilo-arpejo ascendente \
  --desincronismo \
  --delay-faixa 0.1 \
  --velocidade 100 \
  --semitons 0 \
  --reiniciar
```

### Converter tudo com MuseScore (melhor qualidade)

```bash
python renderizador.py --formato MuseScore_General
```

### Versão mais lenta, uma oitava acima

```bash
python renderizador.py \
  --mid "001- Cristo meu Mestre.mid" \
  --formato MusicBox \
  --velocidade 80 \
  --semitons 12 \
  --reiniciar
```

### Reprocessar um arquivo

```bash
python renderizador.py \
  --mid "005- A Rocha celestial.mid" \
  --formato MusicBox \
  --reiniciar
```

### Simular sem gerar (dry-run)

```bash
python renderizador.py --formato MuseScore_General --dry-run
```

---

## 7. Banco de progresso

O arquivo `progresso.db` (SQLite) armazena o status de cada conversão por combinação `(arquivo, formato)`.

| Status | Significado |
|---|---|
| `pendente` | Ainda não processado |
| `concluido` | MP3 gerado com sucesso |
| `erro` | Falha (mensagem armazenada no banco) |

- **`--continuar`** (padrão): retoma de onde parou se o script foi interrompido
- **`--reiniciar`**: marca tudo como `pendente` e reprocessa do zero
- Cada `(arquivo, formato)` é rastreado separadamente — processar o mesmo MIDI em vários formatos não gera conflito

Inspecionar o banco:
```bash
sqlite3 progresso.db "SELECT arquivo, formato, status FROM conversoes LIMIT 20;"
```
Ou use o [DB Browser for SQLite](https://sqlitebrowser.org/).

---

## 8. Como funciona o arpejo

Quando `--arpejo` é ativado, o script:

1. **Detecta a voz mais grave** do MIDI pela mediana das notas (não necessariamente a última trilha)
2. **Identifica grupos de notas** simultâneas (tolerância de 2 ticks)
3. **Cria uma nova trilha** `baixo_arpejo` — a trilha original é preservada intacta
4. **Quebra cada acorde** em notas sequenciais, distribuindo o tempo disponível igualmente
5. Aplica o estilo escolhido:
   - `ascendente`: grave → agudo ↑
   - `descendente`: agudo → grave ↓
   - `alternado`: alterna entre ↑ e ↓ a cada acorde
6. A nova trilha usa:
   - **Canal MIDI livre** (evita conflito com canais existentes)
   - **Velocidade ~75%** do original
   - **Mesmo instrumento** forçado pelo `--instrumento` (ou patch original)

> O MIDI processado é salvo junto ao MP3 (`.mid`) para inspeção em editores como MuseScore ou GarageBand.

---

## 9. Como funciona o desincronismo

Quando `--desincronismo` é ativado:

- **Trilha 0** (condutora — só meta-mensagens): sempre intocada
- **Trilha de arpejo** (`baixo_arpejo`): sempre intocada — serve como âncora rítmica
- **Demais trilhas**: cada nota individualmente recebe um atraso aleatório de:
  - `--delay-faixa` segundos, **ou**
  - `2 × --delay-faixa` segundos

A escolha é feita independentemente para cada nota, criando uma sensação de "toque humano" onde as vozes se desalinham naturalmente.

---

## 10. Painel Administrativo Web

O projeto conta com um painel administrativo baseado em Flask (localizado no repositório irmão `hinário/admin`) que permite acompanhar o status de geração dos vídeos, visualizar estatísticas, agendar postagens e gerenciar os metadados gerados para o YouTube.

### Como iniciar o painel:

1. **Entre no diretório do admin**:
   ```bash
   cd /Volumes/Dados/work/hinário/admin
   ```

2. **Ative o ambiente virtual** do hinário:
   ```bash
   source ../.venv/bin/activate
   ```

3. **Garanta que o Flask esteja instalado**:
   ```bash
   pip install flask
   ```

4. **Execute o painel**:
   ```bash
   python app.py
   ```

5. **Acesse no seu navegador**:
   [http://localhost:5000](http://localhost:5000)

---

## 11. Notas

- Os MP3s são salvos em `output/<formato>[_sufixos]/` com apenas o número do hino com 3 dígitos e zero à esquerda (ex: `001.mp3`)
- Arquivos ocultos do macOS (`._001...`) na pasta `mid/` são ignorados automaticamente
- A transposição (`--semitons`) preserva a percussão (canal MIDI 9)
- Notas que ultrapassem o limite MIDI (0–127) após transposição são descartadas com aviso
- O volume de saída é normalizado automaticamente (EBU R128, −14 LUFS)

# Boas configurações
orgão que ficou bom:
03_orgao_eletronico_drawbar
{
    "mid_original": "003- Faz-nos ouvir Tua voz.mid",
    "preset": "03_orgao_eletronico_drawbar",
    "soundfont": "Timbres_of_Heaven.sf2",
    "seed": 42,
    "humanizacao": true,
    "arpejo_ativado": false,
    "vozes_satb": {
        "soprano": 1,
        "contralto": 2,
        "tenor": 3,
        "baixo": 4
    },
    "data_processamento": "2026-06-13T00:35:25.613869"
}

05_orgao_ccb_celeste
{
    "mid_original": "003- Faz-nos ouvir Tua voz.mid",
    "preset": "05_orgao_ccb_celeste",
    "soundfont": "Timbres_of_Heaven.sf2",
    "seed": 42,
    "humanizacao": true,
    "arpejo_ativado": false,
    "vozes_satb": {
        "soprano": 1,
        "contralto": 2,
        "tenor": 3,
        "baixo": 4
    },
    "data_processamento": "2026-06-13T00:35:34.918800"
}