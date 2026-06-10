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
10. [Notas](#10-notas)

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

## 10. Notas

- Os MP3s são salvos em `output/<formato>[_sufixos]/` com o nome do MIDI + sufixos descritivos
- Arquivos ocultos do macOS (`._001...`) na pasta `mid/` são ignorados automaticamente
- A transposição (`--semitons`) preserva a percussão (canal MIDI 9)
- Notas que ultrapassem o limite MIDI (0–127) após transposição são descartadas com aviso
- O volume de saída é normalizado automaticamente (EBU R128, −14 LUFS)
