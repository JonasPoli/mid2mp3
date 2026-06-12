Para instruir um sistema MIDI com IA, você precisa ensinar a IA a **não criar um arpejo genérico**, mas sim um acompanhamento que respeite a escrita coral sacra: **soprano, contralto, tenor e baixo**.

A ideia central é esta:

> A IA deve analisar cada compasso, identificar a primeira nota do baixo, entender a harmonia formada pelas quatro vozes e criar um arpejo suave, reverente e musical, sem esconder a melodia do soprano.

---

# 1. O que a IA precisa receber

O sistema MIDI precisa ter acesso, de preferência, às 4 vozes separadas:

**Soprano**
**Contralto**
**Tenor**
**Baixo**

No MIDI, isso pode estar em 4 trilhas separadas ou em uma única trilha com as notas organizadas por registro. O ideal é separar assim:

| Voz       | Função                           |
| --------- | -------------------------------- |
| Soprano   | melodia principal                |
| Contralto | preenchimento harmônico superior |
| Tenor     | preenchimento harmônico médio    |
| Baixo     | base harmônica                   |
| Arpejo IA | nova trilha criada pelo sistema  |

A IA deve criar uma **quinta trilha MIDI**, chamada por exemplo:

**Arpejo Sacro Inteligente**

---

# 2. Regra principal do arpejo

A instrução mais importante seria:

> Para cada compasso, use a primeira nota do baixo como ponto inicial do arpejo. Depois analise as notas simultâneas das quatro vozes para descobrir a harmonia real do compasso. O arpejo deve nascer do baixo, passar pelas vozes internas e nunca competir com o soprano.

Isso evita aquele erro comum de fazer arpejo bonito, mas harmonicamente errado.

Exemplo:

Se o baixo começa em **Dó**, mas as outras vozes formam Dó maior, a IA pode usar:

**Dó → Sol → Mi → Sol → Dó**

Mas se o baixo começa em **Mi** e as vozes formam Dó maior em primeira inversão, a IA não deve pensar “acorde de Mi”. Ela deve entender:

**Dó maior com Mi no baixo**

Então o arpejo poderia ser:

**Mi → Sol → Dó → Sol → Mi**

---

# 3. Como dividir o compasso

A IA precisa saber o tipo de compasso.

## Em 4/4

Um padrão bonito seria dividir o compasso em 8 partes, como colcheias:

**1 & 2 & 3 & 4 &**

Modelo:

**baixo → quinta → terça → quinta → soprano → quinta → terça → quinta**

Exemplo em Dó maior:

**Dó → Sol → Mi → Sol → Dó → Sol → Mi → Sol**

Esse modelo fica fluido e sacro, porque tem movimento, mas não é exagerado.

---

## Em 3/4

Para hinos em andamento mais ternário, use 6 partes:

**1 & 2 & 3 &**

Modelo:

**baixo → quinta → terça → soprano → terça → quinta**

Exemplo em Fá maior:

**Fá → Dó → Lá → Fá → Lá → Dó**

Esse formato lembra uma ondulação suave, bom para acompanhamento delicado.

---

## Em 2/4

Use 4 partes:

**1 & 2 &**

Modelo:

**baixo → quinta → terça → quinta**

Exemplo em Sol maior:

**Sol → Ré → Si → Ré**

Simples, bonito e funcional.

---

# 4. Como o arpejo deve soar

Para música sacra, especialmente no estilo de hinos tradicionais, a IA deve seguir uma estética mais contida.

O arpejo deve ser:

**reverente**
**suave**
**cantável**
**sem exagero rítmico**
**sem notas cromáticas desnecessárias**
**sem parecer música pop ou trilha de cinema**
**sem atrapalhar a melodia do soprano**

A IA deve evitar:

**muitos saltos grandes**
**velocidade excessiva**
**notas fora da harmonia**
**arpejos muito brilhantes o tempo todo**
**preencher todos os espaços sem respirar**

Um arpejo sacro bonito tem que parecer uma sustentação, não uma competição.

---

# 5. Como preservar o soprano

A regra mais importante de musicalidade:

> O soprano deve continuar sendo a voz mais clara.

Então a IA deve evitar tocar o arpejo acima da melodia principal o tempo todo.

Melhor regra:

**O arpejo deve ficar abaixo do soprano ou, quando tocar a nota do soprano, deve fazer isso suavemente.**

Exemplo:

Se o soprano está em **Mi agudo**, o arpejo não deve ficar subindo muito acima dele.

O arpejo pode chegar no Mi, mas com velocidade menor e volume mais baixo.

---

# 6. Dinâmica MIDI

Para ficar bonito, não basta escolher as notas. A IA precisa mexer em:

**velocity**
**duração das notas**
**sustain pedal**
**humanização**
**registro do teclado**

Sugestão:

| Elemento MIDI               | Configuração recomendada      |
| --------------------------- | ----------------------------- |
| Velocity do baixo           | 55 a 75                       |
| Velocity das notas internas | 35 a 55                       |
| Velocity próxima ao soprano | 30 a 45                       |
| Quantização                 | leve, não totalmente robótica |
| Sustain                     | moderado                      |
| Registro                    | médio-grave e médio           |
| Ataque                      | suave                         |

O baixo pode ter um pouco mais de presença. As notas internas devem ser mais leves.

---

# 7. Regra de condução entre compassos

A IA também deve pensar no próximo compasso.

Não basta criar um arpejo isolado por compasso. Ela precisa fazer uma transição bonita.

Regra:

> A última nota do arpejo de um compasso deve preparar suavemente a primeira nota do baixo do próximo compasso.

Exemplo:

Compasso 1 termina em **Sol**
Compasso 2 começa com baixo em **Lá**

Boa transição:

**Mi → Sol → Lá**

Evite:

**Dó agudo → Fá grave**, se isso criar um salto brusco sem intenção.

---

# 8. Estrutura ideal da trilha gerada

Eu instruiria a IA a criar o arpejo em camadas:

## Camada 1 — base

A primeira nota do baixo no início de cada compasso.

## Camada 2 — preenchimento

Notas do tenor e contralto distribuídas em arpejo.

## Camada 3 — brilho controlado

Notas próximas ao soprano, mas com menor intensidade.

## Camada 4 — respiração

Pequenos espaços sem notas para não deixar o acompanhamento cansativo.

---

# 9. Prompt principal para o sistema MIDI com IA

Você poderia instruir o sistema assim:

```text
Crie uma nova trilha MIDI de acompanhamento em arpejo para uma música sacra escrita em quatro vozes: soprano, contralto, tenor e baixo.

Para cada compasso, analise a primeira nota do baixo e use essa nota como ponto de partida do arpejo. Em seguida, identifique a harmonia real do compasso a partir das notas do soprano, contralto, tenor e baixo. Não trate automaticamente a nota do baixo como a fundamental do acorde; verifique se há inversão.

O arpejo deve ter caráter sacro, reverente, suave e cantável, adequado para hinos tradicionais. O acompanhamento deve preservar a melodia do soprano, sem competir com ela. Evite tocar constantemente acima do soprano.

Use as notas reais da harmonia do compasso, priorizando baixo, tenor, contralto e soprano. Distribua essas notas em movimento arpejado, criando uma sensação de sustentação coral.

Em compassos 4/4, prefira padrões em colcheias com movimento: baixo, quinta, terça, quinta, soprano ou oitava, quinta, terça, quinta.

Em compassos 3/4, prefira: baixo, quinta, terça, soprano, terça, quinta.

Em compassos 2/4, prefira: baixo, quinta, terça, quinta.

Quando o acorde estiver invertido, comece pela nota real do baixo, mas use as notas do acorde identificado pelas quatro vozes.

Use dinâmica suave. O baixo pode ter velocity entre 55 e 75. As notas internas devem ter velocity entre 35 e 55. As notas próximas ao soprano devem ser mais leves, entre 30 e 45.

Aplique leve humanização rítmica e dinâmica, sem descaracterizar o hino. Use sustain moderado, evitando embolar a harmonia.

A última nota de cada compasso deve preparar suavemente a primeira nota do baixo do próximo compasso, evitando saltos bruscos desnecessários.

O resultado deve soar como um acompanhamento de piano/teclado sacro, belo, respeitoso, limpo e emocional, sem parecer pop, jazz, gospel moderno ou música cinematográfica.
```

---

# 10. Prompt mais curto e direto

Se o sistema não aceitar instruções longas, use assim:

```text
Crie uma trilha MIDI de arpejo sacro inteligente baseada nas quatro vozes: soprano, contralto, tenor e baixo.

Em cada compasso, comece o arpejo pela primeira nota do baixo. Depois analise as quatro vozes para descobrir o acorde real, incluindo possíveis inversões. Use as notas do acorde em arpejo, passando por baixo, tenor, contralto e soprano, mas sem competir com a melodia principal.

O estilo deve ser suave, reverente e tradicional, adequado para hinos sacros. Evite arpejos rápidos demais, notas fora da harmonia, excesso de brilho e movimentos acima do soprano.

Use dinâmica leve, sustain moderado e transições suaves entre compassos.
```

---

# 11. Fórmula musical recomendada

A fórmula mais bonita para esse tipo de IA seria:

**Baixo do compasso → quinta ou nota de apoio → terça ou voz interna → soprano → voz interna → quinta**

Em termos de vozes:

**Baixo → Tenor → Contralto → Soprano → Contralto → Tenor**

Essa fórmula funciona muito bem porque transforma o coral em movimento.

Ela não destrói a harmonia original. Apenas “abre” as vozes em forma de arpejo.

---

# 12. Exemplo prático

Suponha um compasso com estas notas:

| Voz       | Nota |
| --------- | ---- |
| Soprano   | Dó   |
| Contralto | Sol  |
| Tenor     | Mi   |
| Baixo     | Dó   |

A IA entende: **Dó maior**

Arpejo possível:

**Dó → Mi → Sol → Dó → Sol → Mi**

Agora outro compasso:

| Voz       | Nota |
| --------- | ---- |
| Soprano   | Dó   |
| Contralto | Sol  |
| Tenor     | Dó   |
| Baixo     | Mi   |

A IA entende: **Dó maior com Mi no baixo**

Arpejo possível:

**Mi → Sol → Dó → Sol → Mi**

Isso é mais inteligente do que simplesmente fazer “Mi maior”, que estaria errado.

---

# 13. Critério de beleza

Eu colocaria uma regra subjetiva, mas importante:

> O arpejo deve parecer uma oração em movimento.

Na prática, isso significa:

notas bem escolhidas, pouco excesso, dinâmica suave, respeito à melodia e movimento contínuo sem agitação.

Para hinos sacros, o arpejo mais lindo geralmente não é o mais difícil. É o que **serve melhor à melodia**.

---

# Modelo final de instrução para sua IA

```text
Objetivo: gerar um acompanhamento MIDI em arpejo para hinos sacros em quatro vozes.

Entrada: trilhas de soprano, contralto, tenor e baixo.

Processo:
1. Separar a música por compassos.
2. Em cada compasso, identificar a primeira nota do baixo.
3. Usar essa nota como início do arpejo.
4. Analisar as notas simultâneas das quatro vozes para reconhecer o acorde real.
5. Detectar inversões, evitando assumir que o baixo é sempre a fundamental.
6. Criar um arpejo usando prioritariamente as notas do acorde e das quatro vozes.
7. Manter o soprano como melodia principal, sem encobri-lo.
8. Usar movimento suave: baixo, tenor, contralto, soprano, contralto, tenor.
9. Aplicar dinâmica leve, sustain moderado e pequenas variações humanas.
10. Preparar a transição para o próximo compasso usando notas próximas ao próximo baixo.

Estética:
O resultado deve soar como acompanhamento sacro tradicional para piano/teclado, com beleza, reverência, suavidade e clareza harmônica. Evitar estilo pop, jazzístico, gospel moderno, cinematográfico ou excessivamente virtuoso.
```

Esse é o caminho mais inteligente: a IA não “inventa por cima” do hino; ela **interpreta a harmonia coral e transforma as quatro vozes em um arpejo elegante**.
