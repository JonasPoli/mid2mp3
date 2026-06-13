# Mapa de Parâmetros dos Scripts (mid2mp3)

Este documento apresenta a referência completa e detalhada de todos os parâmetros aceitáveis por cada script Python (`renderizador.py` a `renderizador4.py`) e pelos scripts de automação em lote Shell (`todas_versoes.sh` a `todas_versoes4.sh`).

---

## 1. renderizador.py (Geração 1)
O script de conversão original que inclui controle de progresso via SQLite, transposição, velocidade de andamento e remapeamento básico de canais e patches General MIDI.

### Parâmetros da CLI:

*   **`--formato`**: Define a SoundFont padrão para o processamento do áudio.
    *   *Tipo:* Texto (`str`)
    *   *Padrão:* O primeiro da lista abaixo (normalmente `CrisisGeneralMidi301` ou `MuseScore_General`).
    *   *Possibilidades de Entrada:*
        *   `CrisisGeneralMidi301`
        *   `Equinox_Grand_Pianos`
        *   `GeneralUser_GS`
        *   `Mellotron`
        *   `MusicBox`
        *   `MuseScore_General`
        *   `SGM_V2.01`
        *   `Timbres_of_Heaven`
        *   `VintageDreamsWaves`
        *   `aaviolin`
*   **`--mid`**: Especifica um arquivo MIDI para processamento individual.
    *   *Tipo:* Texto (`str`)
    *   *Padrão:* Nenhum (processa em lote se omitido)
    *   *Possibilidades de Entrada:* Nome de arquivo localizado na pasta `mid/` (ex: `001- Cristo meu Mestre.mid`) ou caminho físico do arquivo.
*   **`--continuar`**: Pula os arquivos MIDI que já foram marcados como concluídos com sucesso no banco de dados SQLite.
    *   *Tipo:* Flag/Booleano (Ativo por padrão)
*   **`--reiniciar`**: Força o reprocessamento de todos os arquivos MIDI do lote, limpando os registros de status anteriores do banco SQLite.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--arpejo`**: Ativa o gerador de arpejos na trilha de som mais grave detectada no arquivo MIDI (Baixo).
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--estilo-arpejo`**: Define a direção e comportamento das notas no arpejo do Baixo.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `sacro`
    *   *Possibilidades de Entrada:*
        *   `ascendente`: Notas tocadas de baixo para cima.
        *   `descendente`: Notas tocadas de cima para baixo.
        *   `alternado`: Alterna subida e descida.
        *   `sacro`: Arpejo especial com ritmo e pausas litúrgicas.
*   **`--instrumento`**: Sobrescreve todos os instrumentos MIDI (program changes) por um único patch GM especificado por nome amigável.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* Nenhum
    *   *Possibilidades de Entrada:* `piano`, `cravo`, `caixa_de_musica`, `glockenspiel`, `vibrafone`, `marimba`, `xilofone`, `sinos`, `celesta`, `cordas`, `pizzicato`, `harpa`, `orgao`, `orgao_igreja`, `orgao_percussao`, `orgao_rock`, `orgao_reed`, `flauta`, `trompete`, `trombone`, `tuba`, `coro`, `orquestra`, `quarteto_cordas`, `metais`, `brass`.
*   **`--patch-numero`**: Sobrescreve todos os patches de instrumento por seu respectivo número General MIDI. (É sobreposto por `--instrumento` se ambos forem passados).
    *   *Tipo:* Inteiro (`int`)
    *   *Padrão:* Nenhum
    *   *Possibilidades de Entrada:* Inteiros entre `0` e `127`.
*   **`--orquestra`**: Atalho para aplicar o arranjo `orquestra_completa`, duplicando e distribuindo as vozes.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--arranjo`**: Aplica uma estrutura de orquestração que redistribui as vozes SATB por canais e patches pré-configurados.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* Nenhum
    *   *Possibilidades de Entrada:*
        *   `cordas`, `metais`, `orgaos`, `orquestra_completa`, `pianos`, `classico_1`, `sintetizado`, `combinacao_3`, `combinacao_4`, `combinacao_5`, `orgao_igreja`, `orgao_reed`, `orquestra_sacra_1`, `orquestra_sacra_2`, `orquestra_suave`.
*   **`--piano-modelo`**: Seleciona o modelo de gravação/timbre do piano ao utilizar a SoundFont `Equinox_Grand_Pianos.sf2`.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `steinway_lr`
    *   *Possibilidades de Entrada:*
        *   `steinway`: Steinway D.
        *   `yamaha`: Yamaha C7.
        *   `steinway_lr`: Estéreo Steinway L/R.
        *   `yamaha_lr`: Estéreo Yamaha L/R.
*   **`--crisis-modelo`**: Seleciona o modelo de orquestração e humanização dinâmico ao usar a SoundFont `CrisisGeneralMidi301.sf2`.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `expressiva`
    *   *Possibilidades de Entrada:* `padrao`, `expressiva`, `sinfonica`.
*   **`--humanizar-cordas`**: Ativa micro-variações dinâmicas de velocity e legato exclusivamente em trilhas contendo instrumentos da família das cordas.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--desincronismo`**: Ativa um atraso progressivo e linear entre as trilhas MIDI.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--delay-faixa`**: Define a quantidade de atraso (em segundos) acumulativo por trilha quando `--desincronismo` está ativado.
    *   *Tipo:* Ponto flutuante (`float`)
    *   *Padrão:* `0.1`
    *   *Possibilidades de Entrada:* Qualquer número real positivo (geralmente entre `0.01` e `0.5`).
*   **`--velocidade`**: Multiplicador de andamento (tempo) global do arquivo MIDI.
    *   *Tipo:* Ponto flutuante (`float`)
    *   *Padrão:* `100.0` (velocidade original)
    *   *Possibilidades de Entrada:* Qualquer valor no intervalo `10.0` a `1000.0`. Por exemplo, `90.0` torna o andamento 10% mais lento e `115.0` acelera 15%.
*   **`--semitons`**: Transpõe a altura das notas musicais para cima ou para baixo (ignora o canal de percussão 9).
    *   *Tipo:* Inteiro (`int`)
    *   *Padrão:* `0` (sem transposição)
    *   *Possibilidades de Entrada:* Valores inteiros (geralmente entre `-12` e `12`).
*   **`--soundfonts`**: Lista todas as SoundFonts válidas localizadas na pasta `soundfonts/` e encerra a execução do script.
    *   *Tipo:* Flag/Booleano
*   **`--listar`**: Mostra no terminal a tabela com o status de progresso do banco SQLite e encerra a execução.
    *   *Tipo:* Flag/Booleano
*   **`--status`**: Exibe um resumo numérico simplificado (concluídos, falhas, total) do banco SQLite e sai.
    *   *Tipo:* Flag/Booleano
*   **`--dry-run`**: Analisa e executa toda a lógica MIDI interna, mas ignora a gravação final de arquivos WAV/MP3 em disco.
    *   *Tipo:* Flag/Booleano
*   **`--verbose`** / **`-v`**: Habilita a gravação e saída em nível DEBUG no terminal de logs detalhados de processamento.
    *   *Tipo:* Flag/Booleano
*   **`--opcoes`**: Imprime uma ajuda completa com todos os parâmetros aceitáveis e encerra a execução.
    *   *Tipo:* Flag/Booleano

---

## 2. renderizador2.py (Geração 2)
Script focado em humanização rítmica de órgãos de igreja e pianos, aplicando atrasos aleatórios determinísticos interpolados de forma contínua com base no início de compassos e onsets (0-150ms normais, 0-200ms em finais de frase).

### Parâmetros da CLI:

*   **`--mid`**: Caminho do arquivo MIDI de entrada.
    *   *Tipo:* Texto (`str`) - **Obrigatório**
    *   *Possibilidades de Entrada:* Nome do arquivo em `mid/` ou caminho físico absoluto.
*   **`--preset`**: Define as SoundFonts, ganho, reverb, e remapeamento de patches e canais SATB.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `01_piano_devocional`
    *   *Possibilidades de Entrada (62 presets no total):*
        *   *Pianos:* `01_piano_devocional`, `02_piano_arpejado_suave`, `03_equinox_grand`, `38_equinox_sacro`, `42_meditativo`.
        *   *Órgãos:* `05_orgao_sacro`, `06_timbres_heaven_suave`, `08_orgao_liturgico_timbres`, `09_orgao_tradicional_crisis`, `10_orgao_eletronico_drawbar`, `11_orgao_suave_musescore`, `12_orgao_ccb_celeste`, `13_orgao_ccb_misto`, `14_orgao_pleno_majestoso`, `15_orgao_pleno_arpejado`, `44_orgao_igreja_musescore`, `45_orgao_reed_musescore`, `46_orgao_igreja_timbres`, `47_orgao_reed_timbres`, `48_orgao_igreja_crisis`, `49_orgao_igreja_sgm`, `50_orgao_misto_ccb`.
        *   *Orquestras:* `16_orq_ccb_classica`, `17_orq_orgao_fundo`, `18_orq_metais_suaves`, `19_orq_cordas_completas`, `20_orq_madeiras_delicadas`, `21_orq_hinario_cantado`, `22_orq_piano_leve`, `23_orq_orgao_metais`, `24_orq_grande`, `25_orq_tradicional_banda`, `26_orq_favorita_ia`, `41_orquestra_completa`.
        *   *Metais (Brass):* `51_met_quarteto_tradicional`, `52_met_metais_suaves`, `53_met_solene_cheio`, `54_met_mais_encorpado`, `55_met_estilo_banda`, `56_met_hino_calmo`, `57_met_hino_forte`, `58_met_som_mais_nobre`, `59_met_gravacao_suave`, `60_met_metais_graves`, `61_met_clima_sacro`, `62_met_estudo_vozes`.
        *   *Compatibilidade Geração 1:* `27_eq_steinway_arpejado` ao `37_ms_sintetizado`.
        *   *Outros:* `04_musicbox_suave`, `43_musicbox_arpejado`, `07_generaluser_leve`, `39_coro_e_orgao`, `40_violino_e_piano`.
*   **`--saida-dir`**: Diretório físico onde os arquivos mp3, midi e json serão armazenados.
    *   *Tipo:* Texto (`str`)
    *   *Padrão:* `output2/{stem_do_midi}/{nome_do_preset}/`
*   **`--seed`**: Valor de semente de números pseudoaleatórios para garantir que a humanização seja determinística e reproduzível.
    *   *Tipo:* Inteiro (`int`)
    *   *Padrão:* `42`
*   **`--reiniciar`**: Sobrescreve arquivos MP3, MIDI ou JSON já existentes na pasta de saída.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--salvar-midi`**: Salva em disco o arquivo `.mid` humanizado intermediário.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--salvar-json`**: Salva os parâmetros CLI informados e a configuração estática interna do preset no arquivo `parametros.json`.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--humanizacao`**: Ativa ou desativa todo o processo de atrasos e dinâmicas de nota.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--arpejo-sacro`**: Força a ativação do arpejo sacro v2 no Baixo.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--sem-arpejo`**: Desativa qualquer configuração de arpejo padrão que o preset selecionado contenha.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--normalizar`**: Ativa a normalização de volume baseada na norma EBU R128 (-14 LUFS) usando o filtro FFmpeg.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--manter-wav`**: Mantém o arquivo WAV gerado temporariamente pelo FluidSynth na pasta de saída.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--debug`**: Habilita a gravação e exibição de mensagens detalhadas de depuração.
    *   *Tipo:* Flag/Booleano

---

## 3. renderizador3.py (Geração 3)
Versão voltada para a criação de mixagens orquestrais sacras de alta expressividade, aplicando automações de CC11 (Expressão) e CC1 (Vibrato) em trilhas de cordas e sopros.

### Parâmetros da CLI:

*   **`--mid`**: Caminho do arquivo MIDI de entrada.
    *   *Tipo:* Texto (`str`) - **Obrigatório**
    *   *Possibilidades de Entrada:* Nome de arquivo localizado na pasta `mid/` ou caminho físico.
*   **`--saida-mp3`**: Caminho físico completo onde o MP3 final renderizado será gravado.
    *   *Tipo:* Texto (`str`) - **Obrigatório**
    *   *Possibilidades de Entrada:* Caminho físico completo (ex: `/caminho/para/output3/hino.mp3`).
*   **`--tipo-arranjo`**: Define a orquestração e a mixagem híbrida de SoundFonts.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `piano_solo`
    *   *Possibilidades de Entrada:*
        *   `piano_solo`: Usa a SoundFont `Equinox_Grand_Pianos.sf2` para todas as vozes.
        *   `quarteto_cordas`: Usa `aaviolin.sf2` (Violino) para soprano, e `CrisisGeneralMidi301.sf2` para contralto, tenor e baixo (Viola/Violoncelo/Contrabaixo).
        *   `orgao_coral`: Soprano e contralto com `Mellotron.sf2` (Choir Aahs), e tenor e baixo com `Timbres_of_Heaven.sf2` (Church Organ).
        *   `orquestra_completa`: Flauta, Clarinete, Trompa de pistão e Violoncelo da SoundFont `CrisisGeneralMidi301.sf2`.
*   **`--reiniciar`**: Sobrescreve o MP3 e o JSON existentes.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--debug`**: Ativa logs detalhados e pilhas de exceção.
    *   *Tipo:* Flag/Booleano

---

## 4. renderizador4.py (Geração 4)
O renderizador mais avançado. Consolida o motor de humanização v4 (com as regras de desincronização suave por onsets e oitavas de órgão sem oscilações de volume indesejadas), arpejos sacros v3 (com inversões e preenchimento de silêncios automáticos), e detecção inteligente das partes do hino (Introdução / Corpo / Final). Inclui controle de lote integrado com banco SQLite.

### Parâmetros da CLI:

*   **`--mid`**: Caminho do arquivo MIDI de entrada.
    *   *Tipo:* Texto (`str`)
    *   *Padrão:* Nenhum (processa toda a pasta `mid/` em lote caso omitido).
    *   *Possibilidades de Entrada:* Nome de arquivo em `mid/` ou caminho físico absoluto.
*   **`--preset`**: Define a orquestração premium e configurações especiais.
    *   *Tipo:* Escolha (`str`)
    *   *Padrão:* `equinox_sacro`
    *   *Possibilidades de Entrada:*
        *   *Premium Geração 4:*
            *   `equinox_sacro`: Piano Equinox com pedal, arpejo sacro v3 e preenchimento de silêncios.
            *   `coro_e_orgao`: Coral Mellotron nas vozes agudas (S+A) e Órgão Timbres of Heaven nas graves (T+B) com expressão dinâmica CC11.
            *   `violino_e_piano`: Violino aaviolin solo no Soprano com vibrato, e Piano Equinox na base harmônica (A+T+B).
            *   `orquestra_completa`: Flauta, Oboé, Viola e Violoncelo Crisis GM dobrados por pad de cordas secundário.
            *   `meditativo`: Piano MuseScore com arpejo de Harpa e reverb elevado (andamento dinâmico reduzido).
            *   `musicbox_arpejado`: Caixa de Música com arpejo sacro v3 completo e reverb.
        *   *Órgãos Sacros:*
            *   `01_orgao_igreja_musescore`, `02_orgao_reed_musescore`, `03_orgao_igreja_timbres`, `04_orgao_reed_timbres`, `05_orgao_igreja_crisis`, `06_orgao_igreja_sgm`, `07_orgao_misto_ccb`, `08_orgao_pleno_arpejado`.
        *   *Orquestras Sacras (docs/orchestra.md):*
            *   `01_orq_ccb_classica`, `02_orq_orgao_fundo`, `03_orq_metais_suaves`, `04_orq_cordas_completas`, `05_orq_madeiras_delicadas`, `06_orq_hinario_cantado`, `07_orq_piano_leve`, `08_orq_orgao_metais`, `09_orq_grande`, `10_orq_tradicional_banda`, `11_orq_favorita_ia`.
        *   *Metais (Brass):*
            *   `01_met_quarteto_tradicional`, `02_met_metais_suaves`, `03_met_solene_cheio`, `04_met_mais_encorpado`, `05_met_estilo_banda`, `06_met_hino_calmo`, `07_met_hino_forte`, `08_met_som_mais_nobre`, `09_met_gravacao_suave`, `10_met_metais_graves`, `11_met_clima_sacro`, `12_met_estudo_vozes`.
*   **`--saida-dir`**: Diretório onde os arquivos de áudio (MP3), MIDI intermediário e JSON de parâmetros serão gravados.
    *   *Tipo:* Texto (`str`)
    *   *Padrão:* `output4/{stem_do_midi}/{nome_do_preset}/`
*   **`--seed`**: Semente para o gerador de números pseudoaleatórios, assegurando reprodutibilidade temporal.
    *   *Tipo:* Inteiro (`int`)
    *   *Padrão:* `42`
*   **`--reiniciar`**: Limpa o status de renderização no banco SQLite para o arquivo MIDI e preset correspondentes, forçando uma nova conversão.
    *   *Tipo:* Flag/Booleano (Inativo por padrão)
*   **`--salvar-midi`**: Salva o arquivo `.mid` humanizado intermediário com as correções de timing.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--salvar-json`**: Cria o arquivo `parametros.json` contendo tanto as variáveis recebidas quanto as configurações internas de orquestração.
    *   *Tipo:* Booleano/Flag
    *   *Padrão:* `True` (padrão)
*   **`--debug`**: Habilita logs completos com informações de execução em nível de detalhe avançado.
    *   *Tipo:* Flag/Booleano
*   **`--listar-presets`**: Lista todos os presets disponíveis e encerra o script.
    *   *Tipo:* Flag/Booleano
*   **`--status`**: Exibe o progresso geral e estatísticas de renderização armazenadas no banco `progresso4.db` e encerra.
    *   *Tipo:* Flag/Booleano

---

## 5. Scripts de Automação em Lote Shell (`todas_versoes*.sh`)

Os scripts shell executam conversões em lote, varrendo sequencialmente múltiplos presets e gravando os arquivos organizados por subpastas.

### Parâmetros Posicionais:
*   **`$1`** (Primeiro Argumento - **Obrigatório**): O nome ou caminho do arquivo MIDI a ser processado (ex: `003- Faz-nos ouvir Tua voz.mid`).
*   **`$2`** (Segundo Argumento - **Opcional**): O parâmetro `--reiniciar` para forçar o reprocessamento de todos os presets.

### Mapa de Funcionamento por Script:

### 5.1. `todas_versoes.sh`
*   *Motor Python:* `renderizador.py` (Geração 1)
*   *Diretório de Destino:* `output/`
*   *Presets e Comportamento:*
    *   Executa conversões nas SoundFonts `Equinox_Grand_Pianos` e `MuseScore_General`.
    *   Gera versões de Piano Arpejado (com Arpejo Sacro no baixo), Piano Coral Simples, Órgão de Igreja e Harmônio Reed sem arpejo, e arranjos de teste (Orquestra Sacra 1, 2, Orquestra Suave, Sintetizado, Orquestra Completa, Clássico 1, Cordas) tanto no modo arpejado quanto no modo coral simples.

### 5.2. `todas_versoes2.sh`
*   *Motor Python:* `renderizador2.py` (Geração 2)
*   *Diretório de Destino:* `output2/{stem_do_midi}/`
*   *Presets Gerados (62 presets no total):*
    *   **Pianos/Originais Geração 2 (01 a 07)**: `01_piano_devocional`, `02_piano_arpejado_suave`, `03_equinox_grand`, `04_musicbox_suave`, `05_orgao_sacro`, `06_timbres_heaven_suave`, `07_generaluser_leve`
    *   **Órgãos Sacros Geração 2 (08 a 15)**: `08_orgao_liturgico_timbres` ao `15_orgao_pleno_arpejado`
    *   **Orquestras Sacras Geração 3 (16 a 26)**: `16_orq_ccb_classica` ao `26_orq_favorita_ia`
    *   **Compatibilidade Geração 1 (27 a 37)**: `27_eq_steinway_arpejado` ao `37_ms_sintetizado`
    *   **Premium Geração 4 (38 a 43)**: `38_equinox_sacro`, `39_coro_e_orgao`, `40_violino_e_piano`, `41_orquestra_completa`, `42_meditativo`, `43_musicbox_arpejado`
    *   **Órgãos Geração 4 (44 a 50)**: `44_orgao_igreja_musescore` ao `50_orgao_misto_ccb`
    *   **Metais Geração 4 (51 a 62)**: `51_met_quarteto_tradicional` ao `62_met_estudo_vozes`

### 5.3. `todas_versoes3.sh`
*   *Motor Python:* `renderizador4.py` (Geração 4)
*   *Diretório de Destino:* `output3/{stem_do_midi}/`
*   *Presets Gerados:*
    *   As 11 orquestras sacras da Geração 3: `01_orq_ccb_classica` até `11_orq_favorita_ia`.

### 5.4. `todas_versoes4.sh`
*   *Motor Python:* `renderizador4.py` (Geração 4)
*   *Diretório de Destino:* `output4/{stem_do_midi}/`
*   *Presets Gerados:*
    *   As 11 orquestras sacras com o motor avançado de humanização v4: `01_orq_ccb_classica` até `11_orq_favorita_ia`.

### 5.5. `todas_versoes_mscore.sh`
*   *Motor Python:* `renderizador_mscore.py` (Premium MuseScore 4)
*   *Diretório de Destino:* `output_mscore/{stem_do_midi}/`
*   *Presets Gerados:*
    *   **metais_premium**: Quarteto de metais (Trumpet, French Horn, Trombone, Tuba).
    *   **madeiras_premium**: Quarteto de madeiras (Flute, Oboe, Clarinet, Bassoon).
    *   **cordas_premium**: Quarteto de cordas solo (Violin I, Violin II, Viola, Cello).
    *   **orgao_premium**: Grande Órgão de Igreja Muse Sounds.
    *   **piano_premium**: Piano de Cauda Steinway Muse Sounds.

