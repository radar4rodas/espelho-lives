# Espelho de Live: TikTok → Shopee

Fica de olho num perfil do TikTok. Quando a live começa, retransmite o vídeo
automaticamente para a Shopee Live. Se a conexão cair, reconecta sozinho.

```
TikTok Live ──> atraso de 7 s ──> mudo de "tiktok" / "tiktok shop" ──> Shopee Live
                    └── reconhecimento de voz ouve a live na hora ──┘
```

## Como usar (toda live)

1. Na **Shopee**, crie a live escolhendo transmitir **por software / OBS / RTMP**.
   A tela vai mostrar a **URL do servidor** e a **Chave da transmissão**.
2. Dê dois cliques em **`iniciar.bat`**. Abre uma janela preta (o servidor) e o **painel
   no navegador**, em **http://127.0.0.1:8765**.
   **Não feche a janela preta** durante a live: fechar ela encerra o espelhamento.
3. No painel, preencha os campos:
   - **@ do perfil** do TikTok (pode colar o link do perfil também)
   - **sessionid do TikTok** (opcional, só para lives com **restrição de idade**; veja abaixo)
   - **URL do servidor** da Shopee
   - **Chave da transmissão** da Shopee
4. Clique em **▶ Iniciar espelhamento**.
5. Comece a live no **TikTok**. Em até 20 s o status muda para **🔴 AO VIVO**. O painel
   mostra uma prévia da imagem que está indo para a Shopee, o tempo no ar e a velocidade de envio.
6. Ao terminar, clique em **■ Parar**.

Pode fechar e abrir a aba do navegador à vontade: o espelhamento continua rodando na
janela preta. O painel só aceita acesso deste computador.

O @ e a URL ficam salvos para a próxima vez. A chave **não** é salva, porque a Shopee
gera uma nova a cada live.

> A transmissão por software não fica liberada para todas as contas da Shopee. Se a opção
> não aparecer no Seller Centre, isso se resolve com a Shopee.

## Atraso e mudo automático

Na seção **3 · Atraso e mudo automático** do painel:

- **Atraso** (padrão **7 segundos**): a Shopee recebe a live 7 s depois do TikTok, além
  dos poucos segundos de atraso normal de qualquer retransmissão.
- **Mutar "tiktok" e "tiktok shop"** (ligado por padrão): o sistema ouve a live do TikTok
  na hora e, aproveitando o atraso, silencia essas palavras antes delas saírem na Shopee.
  A palavra costuma ser reconhecida 1 a 2 s depois de dita, então sobram uns 5 s de folga.
  Com atraso menor que 4 s pode não dar tempo de mutar.

Durante a live, o monitor mostra quantas palavras foram mutadas e quando. O registro também
avisa cada mudo (🔇).

Como funciona: o reconhecimento de voz (Vosk, roda offline no seu computador, nada é enviado
para a internet) procura só pelo som de "tiktok" ("tique toque") e "tiktok shop". O áudio só
fica mudo nesses trechos, com uma pequena folga antes e depois.

**Mais preciso (desde 28/11):** o reconhecimento também conhece palavras comuns com som parecido ("tira",
"toque", "local", "estoque"...). Antes ele só tinha "tiktok" e "nada" para escolher, e falas como "tira o bocal"
viravam "tiktok", mutando a fala no meio. Num teste com 40 min de uma live gravada, isso mutava à toa umas 11 vezes;
agora nenhuma, e os "TikTok" de verdade continuaram sendo mutados. No sentido Shopee → TikTok os mudos à toa caíram
pela metade, sem perder nenhum "Shopee" a mais. Só vale depois de reabrir o painel.

**Saudações:** "bom dia", "boa tarde" e "boa noite" também são mutadas, nas duas direções
(TikTok → Shopee e Shopee → TikTok). Só conta quando as duas palavras vêm juntas: "muito bom,
e o dia..." não é mutado.

> **Limites:** funciona melhor com fala clara. Música alta por cima, várias pessoas falando
> ao mesmo tempo ou pronúncias muito diferentes podem fazer o sistema perder alguma palavra.
> Teste numa live antes de depender dele.

## Finalizar a Shopee sozinho quando o TikTok acabar

Isso usa uma pequena extensão no **seu Chrome normal** (pasta `extensao_shopee`). Ela só age nas
páginas live.shopee.com.br e só conversa com o painel, no seu computador.

**Instalar (uma vez só):**
1. No Chrome, abra **chrome://extensions**.
2. Ligue o **Modo do desenvolvedor** (canto superior direito).
3. Clique em **Carregar sem compactação** e escolha a pasta **extensao_shopee** (dentro de Lives).
4. Se a página da Shopee Live já estava aberta, aperte **F5** nela.

No painel, a seção **Shopee** mostra se a extensão está conectada (bolinha amarela) e se a live
está no ar na Shopee (bolinha verde).

**Em cada live:**
1. Crie a live no seu Chrome, como sempre (Seller Centre → Live & Video → Criar Live → Próximo).
2. Na tela "Associar transmissão com OBS", clique em **Pegar URL e chave da Shopee** no painel:
   os campos se preenchem sozinhos.
3. Ligue **"Clicar em Finalizar Transmissão na Shopee quando a live do TikTok acabar"**, clique em
   **Iniciar espelhamento** e depois em **Entrar ao vivo?** na Shopee.
4. A aba da Shopee pode ficar em segundo plano, mas **não feche** a aba nem o Chrome.

Durante a live, o painel consulta o TikTok a cada segundo. Quando o TikTok confirma o fim, o
envio é cortado e a extensão clica em **Finalizar Transmissão** e na confirmação. Nos testes,
levou cerca de 3 segundos no total. **Pausas** no TikTok não finalizam a Shopee.

**Live do TikTok engasgou ou pausou → a Shopee não cai:** se o vídeo do TikTok parar de chegar por mais de
3 segundos, a Shopee passa a receber a imagem **"Já voltamos!"** (`imagens\ja_voltamos.png`, sem som). Enquanto
isso, o painel busca a live do TikTok de novo a cada 3 a 15 segundos e, quando ela volta, emenda o vídeo **sem a
Shopee cair**. O registro mostra "📺 ... Já voltamos!" e depois "✅ O vídeo do TikTok voltou depois de Ns". A imagem
sai do ar sozinha se o TikTok confirmar que a live acabou, ou depois de 10 minutos sem a live voltar.
Antes (25/11), sem vídeo, a Shopee fechava a conexão em ~1 minuto. Para desligar: `"tapa_pausa": false` no
`config.json`.

O painel pergunta ao TikTok a cada **3 segundos** se a live acabou. Antes perguntava várias vezes por segundo, e
depois de ~50 minutos o TikTok bloqueava as consultas (erro HTTP 403), o que impedia de reconectar. Se vier 403,
ele espera 10 a 30 segundos antes de perguntar de novo. Logo depois de uma queda, tenta reconectar a cada 5 s
(antes eram 20 s).

Se algo der errado (por exemplo, a Shopee mudou a página), o painel mostra um **alarme vermelho
com som** para você finalizar manualmente. O botão **Finalizar agora na Shopee** serve para
encerrar na hora ou para testar (use uma live do tipo **Teste**).

## Gravar e reprisar lives

**Pastas:** cada sentido tem a sua pasta. As lives do TikTok → Shopee ficam em
`gravacoes\TikTok para Shopee`, e as da Shopee → TikTok em `gravacoes\Shopee para TikTok`.
Cada aba de reprise mostra só os vídeos da sua pasta.

**Gravar:** em **TikTok → Shopee → Espelhar ao vivo**, ligue **"Salvar a live neste computador"**. Cada live
vira um arquivo na pasta `gravacoes\TikTok para Shopee`, com a data e o @ no nome (por exemplo,
`2026-11-17_21h30_sualoja.ts`). Se a live cair e voltar, ou se o painel trocar a conexão, a
continuação é gravada em partes (`_parte2`, `_parte3`...) e, **quando a transmissão termina, o painel
emenda tudo num vídeo só** (sem recodificar; leva alguns segundos por GB). As partes só são apagadas
depois que o vídeo emendado fica pronto. Se faltar espaço no disco ou as partes tiverem formatos
diferentes, elas ficam como estão. Na Shopee → TikTok, iniciar de novo a mesma live (mesmo link)
continua no mesmo vídeo. Ocupa cerca de 1 GB por hora. A gravação guarda o áudio original; o mudo de
"tiktok" é aplicado de novo na hora de reprisar.

**Reprisar:** em **TikTok → Shopee → Reprisar gravação**:
1. Escolha o vídeo na lista (lives salvas pelo painel ou vídeos seus que você colocar na
   pasta, como o replay baixado do TikTok: `.mp4`, `.ts`, `.flv`, `.mov`, `.mkv`).
   Os vídeos com **1 hora ou mais** ficam com o tempo em **verde**.
   **Fila:** clique em vários vídeos, na ordem em que devem passar. Cada um ganha um número (1, 2, 3...) e eles
   passam **em sequência, emendados, sem parar a live** (a conexão com a Shopee/OBS continua; cerca de 0,2 s de
   imagem parada na troca). Clique de novo num vídeo para tirá-lo da fila, ou em **Limpar**. Um vídeo com defeito
   na fila é pulado. Com "Repetir em loop", depois do último volta para o primeiro. Se a conexão cair, continua
   no mesmo vídeo da fila, de onde parou.
2. Ligue **"Repetir em loop"** para ficar repetindo, ou deixe desligado para passar uma vez só.
   No loop, quando o vídeo acaba ele **recomeça emendado, sem parar a transmissão** (a conexão
   com a Shopee/OBS continua; cerca de 0,2 s de imagem parada na emenda).
3. Em **"Por quanto tempo fica em loop"**, escolha de **1 a 10 horas** (ou "Até eu clicar em
   Parar"). Quando o tempo acaba, a reprise para sozinha. Com **"Quando o tempo acabar, encerrar
   a live"** ligado, o painel também encerra a live na Shopee/TikTok pela extensão; desligado,
   ele toca um alarme para você encerrar.
4. Confira a URL e a chave da Shopee, e clique em **▶ Iniciar reprise**.

O mesmo vale em **Shopee → TikTok → Reprisar gravação** (loop emendado e tempo de loop).

**Só gravar (sem transmitir):** cada lado tem a aba **Só gravar**, para montar um acervo das suas lives:
- **TikTok → Shopee → Só gravar:** escreva o @ do TikTok e clique em **⏺ Começar a gravar**. A live do TikTok é
  gravada na pasta `gravacoes\TikTok para Shopee`, pronta para reprisar na Shopee.
- **Shopee → TikTok → Só gravar:** cole o link da live da Shopee. Ela é gravada em `gravacoes\Shopee para TikTok`,
  pronta para reprisar no TikTok.
- Pode começar antes da live: o painel espera ela entrar no ar. Para sozinho quando a plataforma confirma que a live
  acabou (ou em **■ Parar gravação**). Se a conexão cair, grava a continuação em partes e emenda tudo no fim.
- O vídeo é copiado como chega (não recodifica), então quase não pesa no computador. Funciona até com o lado
  transmitindo outra coisa (uma reprise, por exemplo). O cartão do lado mostra **⏺ REC** enquanto grava.

O atraso, o mudo automático e a qualidade valem também para a reprise. Use **Abrir pasta**
para ver os arquivos e **Atualizar lista** depois de colocar um vídeo novo lá.

**Etiqueta da reprise:** por padrão a reprise (para a Shopee e para o TikTok) sai com uma etiqueta
pequena **"REPRISE"** no canto de cima, à direita, para quem assiste saber que é uma live gravada. Ela
aparece também na prévia do painel. Na etapa **Gravação** você escolhe, para cada lado, se mostra a
etiqueta (**"Mostrar etiqueta no vídeo"**) e o que vai escrito nela (até 40 letras). Com a etiqueta
ligada o vídeo da reprise é sempre refeito, mesmo no modo "copiar"; desligada, o modo "copiar" manda o
vídeo como está e pesa bem menos no computador. A live espelhada ao vivo nunca tem etiqueta.

**Se a conexão cair no meio da reprise:** o painel reconecta e **continua do ponto onde estava**
(uns 5 s antes), em vez de voltar ao começo do vídeo. Se a página da Shopee não estiver mais ao
vivo, o registro avisa que a própria Shopee encerrou a live; aí é preciso criar uma live nova.

> Reprise é sempre um vídeo gravado: use só com lives suas. Confira as regras
> da Shopee Live e do TikTok Shop sobre lives gravadas.

## Rodar os dois sentidos ao mesmo tempo

O painel tem duas abas principais, **TikTok → Shopee** e **Shopee → TikTok**, e as duas podem
transmitir ao mesmo tempo, cada uma com seu **Iniciar** e **Parar**:
- no topo do painel e em cada aba aparece o estado de cada lado (Parado, Aguardando, AO VIVO
  com o tempo, Pausada na Shopee...);
- o **Monitor** mostra o lado da aba que está aberta (troque de aba para ver o outro);
- no **Registro**, cada linha tem uma etiqueta colorida dizendo de qual lado ela veio;
- o atraso, o mudo e o loop são guardados separados para cada lado;
- parar um lado não mexe no outro.

> Não use a mesma live nos dois sentidos (por exemplo, a live do TikTok indo para a Shopee e
> essa mesma live da Shopee voltando para o TikTok): vira um eco sem fim. Use lojas/contas
> diferentes em cada sentido. As duas juntas enviam cerca de 5 a 7 Mbps: tenha pelo menos
> 10 Mbps de upload.

## Shopee → TikTok (live de vendas no TikTok Shop)

Faz o caminho contrário: pega uma live que já está no ar na Shopee e leva para a live de
vendas do TikTok Shop. O Gerenciador de LIVE do TikTok Shop transmite pelo navegador e só
aceita câmera e microfone, então o painel usa o **OBS Studio** como câmera virtual e o
**VB-CABLE** como microfone virtual. Os dois já estão instalados.

1. No painel, aba **Shopee → TikTok**: cole o **link da live da Shopee** (na live, toque em
   Compartilhar → Copiar link; por exemplo `https://br.shp.ee/...`). Cada live tem um link
   próprio: na próxima live, cole o link novo.
2. Clique em **▶ Iniciar Shopee → TikTok**. O painel abre o OBS sozinho (pode minimizar,
   mas não feche) e espera a live da Shopee entrar no ar.
3. No Chrome, abra o **Gerenciador de LIVE** do TikTok Shop → **Iniciar LIVE** (ou a setinha →
   **Prática** para testar). Escolha **Fonte de vídeo: OBS Virtual Camera** e
   **Fonte de áudio: CABLE Output (VB-Audio Virtual Cable)**, e inicie.

O atraso vale igual, e o mudo automático cala **"shopee"**, **"shop"** e **"pix"** antes de chegarem
no TikTok. O TikTok Shop deu aviso por "pague no Pix" e "na Shopee": ele entende isso como mandar o cliente
para fora da plataforma. O reconhecimento não conhece a palavra "pix" e a ouve como "piques"; por isso o
"pix" só é mutado quando o reconhecimento tem certeza. Testado com voz: 8 de 8 frases com essas palavras
mutadas e nenhuma frase comum mutada por engano. Só o som da live vai para o TikTok; os outros sons do computador não entram.
O vídeo vai como veio da Shopee (sem recodificar), então pesa pouco no computador.

**Sincronia da voz (automática):** o som chega ao TikTok pelo cabo virtual, um pouco depois da câmera
virtual. No modo **Automático** (padrão, em **Atraso e mudo → "Sincronia da voz no TikTok"**), o painel
compensa e **confere sozinho durante a live**: uns 30 segundos depois de começar e depois a cada 3 minutos,
ele grava 12 segundos do que sai do OBS (câmera virtual + CABLE Output) e compara com o vídeo original da
Shopee. Se a voz estiver adiantada (mais de 0,03 s) ou atrasada (mais de 0,06 s) em duas medições seguidas,
ele corrige sem parar a live: atrasa a imagem ou, se a voz estiver adiantada mesmo assim, atrasa a voz.
O valor de partida depende do **formato do vídeo** que vem da Shopee, que o painel reconhece sozinho:
**HEVC** (usado por algumas lojas) começa com a imagem 0,30 s atrasada; **H.264** (outras lojas)
começa com 0; a reprise usa 0,05 s. O valor que o vigia acha fica guardado para cada formato, e a próxima
live do mesmo formato já começa com ele. No registro aparecem "Sincronia da voz conferida: certa" ou
"Sincronia da voz: estava chegando ... Corrigindo sozinho". Escolher um valor fixo desliga a conferência.
Obs.: o "audio buffering" que aparece no registro do OBS não atrasa o som que vai para o TikTok (medido);
não compense por ele.

**Som limpo no TikTok:** por padrão o Chrome trata o "microfone" como numa chamada de vídeo (redução de
ruído, volume automático e cancelamento de eco), o que deixa a voz da live baixa e cortada. A extensão
(versão 1.5 ou mais nova) desliga esses três tratamentos na página do Gerenciador de LIVE. Depois de
atualizar a extensão, recarregue a página do Gerenciador de LIVE (F5) antes de iniciar a live.

**Imagem travada com a voz passando:** às vezes quem faz a live na Shopee troca a configuração do vídeo no
meio dela (por exemplo, de 24 para 30 quadros por segundo). Sem tratamento, a imagem no TikTok congelava até
o fim e só a voz passava. Agora o painel percebe em poucos segundos e abre uma conexão nova com a Shopee sem
parar a live; a imagem volta em cerca de 15 segundos. No registro aparece "A imagem que vem da Shopee travou".

**Pausa ou fim na Shopee → live no TikTok encerrada:** com a opção **"Encerrar a live no TikTok quando a
Shopee pausar ou acabar"** ligada (e a extensão 1.6 ou mais nova), o painel **encerra a live no TikTok**
(clica no ⏻ ao lado do contador do Gerenciador de LIVE e confirma) quando:
- a Shopee **confirma o fim** da live; ou
- a Shopee **confirma a pausa** e o vídeo fica uns **30 segundos** sem voltar.

**Queda de conexão não encerra mais:** se a imagem da Shopee parar de chegar mas a Shopee disser que a live
continua no ar, é falha de conexão, não pausa. O painel reconecta sem mexer no TikTok, e o registro mostra
"📶 A imagem da Shopee parou de chegar... Reconectando sem encerrar a live no TikTok". Antes de clicar em
encerrar, ele confere de novo: se a imagem voltou, ou a Shopee ainda diz "no ar", ele não encerra. Antes de
23/11, quedas de 6 a 11 segundos encerravam o TikTok à toa.
Se não der para encerrar, a extensão **pausa** a live no TikTok (desliga a câmera e o som da conexão, sem
fechar nada), toca o **alarme** e, se a Shopee voltar, religa sozinha. Com a opção desligada, na pausa o OBS
mostra a imagem **"Já voltamos!"** (`imagens\ja_voltamos.png`, fica atrás do vídeo) até a Shopee voltar
(se trocar o arquivo da imagem, feche o OBS para ele recarregar). **Depois de atualizar a extensão,
recarregue a página do Gerenciador de LIVE (F5) antes de iniciar a live.**
Os botões **"Testar pausa"** e **"Retomar"** (ao lado de "Testar botões") servem para conferir a pausa
de reserva numa live de **Prática**; o resultado aparece no registro.

**Conferir o encerramento:** o botão **"Testar botões"** mostra no registro se a extensão enxerga o
contador e o ⏻ do Gerenciador; use durante uma live de **Prática** para conferir.

**Aviso de restrição → só aquela live encerrada:** com a opção **"Encerrar a live onde aparecer um
aviso de restrição"** ligada (logo abaixo dos dois sentidos, no topo do painel; vem ligada), a extensão
(1.7 ou mais nova) lê na hora as janelinhas, faixas e notificações da **Shopee Live** e do **Gerenciador de
LIVE do TikTok Shop**. Se uma delas disser que a live ou a conta **foi** restringida, suspensa, bloqueada,
interrompida/encerrada por violação, que "detectamos uma violação" ou que "você recebeu uma advertência", o
painel encerra **só a live da plataforma onde o aviso apareceu**: aviso na Shopee → para de enviar para a
Shopee e finaliza a live da Shopee; aviso no TikTok → para de enviar para o TikTok e encerra a live do
TikTok. **A outra live continua no ar.** O texto do aviso aparece no alarme e no registro. Se não conseguir
encerrar, o alarme avisa: encerre na mão. Para não encerrar à toa: o **chat dos espectadores não é lido** (ninguém
derruba a live escrevendo a frase), e lembretes do tipo "lives que violam as regras **podem** ser
encerradas" não contam. O "Testar botões" mostra se o vigia de avisos está ativo na aba do TikTok.

Sem essa opção ligada, quando a live da Shopee acaba o painel para de enviar e toca um
**alarme**: encerre a live no Gerenciador de LIVE do TikTok na hora.

**Troca sem pausa:** se o relógio do vídeo da origem bagunçar (live travando, voz falhando) ou a
conexão com a origem cair, o painel abre uma conexão nova e emenda o vídeo **sem derrubar** a
conexão com a Shopee/OBS. Quem assiste vê no máximo ~1 s de imagem parada. Se isso acontecer mais de
4 vezes em 2 minutos, ele reconecta do zero (como antes).

**Gravar e reprisar:** ligue **"Salvar a live neste computador"** para guardar a live na pasta
`gravacoes\Shopee para TikTok`. Na sub-aba **Reprisar gravação** (dentro de Shopee → TikTok),
escolha o vídeo, ligue **Repetir em loop** se quiser, e clique em **▶ Iniciar reprise no TikTok**.
O vídeo vai pelo OBS, igual à live.

> O TikTok é rígido com live que não é feita diretamente nele e com menção a outras
> plataformas (placas, embalagens, "carrinho da Shopee"). Use só com lives suas ou da sua
> família, e por sua conta e risco.

## Contador "Oferta termina em" no vídeo

Um contador que aparece **dentro do vídeo da live**, à esquerda, logo abaixo do cartão de produto que a
plataforma mostra. Fica **vermelho no último minuto** e **some da tela quando zera**.

- **Ligar:** no painel do PC, no quadro **"⏱ Contador na tela"** (embaixo do Monitor), escolha 1, 3, 5 ou
  10 minutos (ou digite minutos e segundos) e clique em **▶ Iniciar contador**. **■ Parar** tira da tela na hora.
  Vale para o lado que está aberto no painel (TikTok → Shopee ou Shopee → TikTok).
- **Pelo celular:** cada cartão (TikTok e Shopee) tem o mesmo quadro "⏱ Contador", com os mesmos botões.
- **Onde aparece:** só na live que **passa pelo computador** (a espelhada):
  - TikTok → Shopee: aparece na **Shopee**. O painel já refaz o vídeo para a Shopee e desenha o contador nele.
  - Shopee → TikTok: aparece no **TikTok**. O OBS mostra a camada "Contador" (a página
    `http://127.0.0.1:8765/contador.html`, transparente) por cima do vídeo.
  - A live original, que você transmite direto, não tem o contador.
- Pode ligar antes da live começar: ele aparece assim que aquele lado estiver no ar.
- Os dois lados têm contadores separados.

**Para ativar depois de atualizar:** feche e abra o painel. Para o TikTok, **feche também o OBS** antes de
clicar em Iniciar: o painel reabre o OBS com a cena nova (com a camada "Contador"). Se o OBS abrir com a cena
antiga, o registro avisa.

## Cortes para Reels (⭐ Marcar momento)

Durante a live, aperte **⭐ Marcar este momento** (painel do PC, quadro "🎬 Cortes para Reels") ou
**⭐ Marcar momento** (celular, logo acima de "Encerrar as duas lives") **logo depois** de um momento bom:
uma demonstração que deu certo, uma reação, a hora em que o chat acelerou.

Quando a live termina, cada marcação vira um **vídeo vertical 1080x1920 de ~45 s** (35 s antes do toque e
10 s depois), em **duas versões**:

| Arquivo | Para postar em | Mutado no corte |
|---|---|---|
| `corte01_tiktok.mp4` | TikTok | "shopee", "shop", "pix", "bom dia", "boa tarde", "boa noite" |
| `corte01_shopee.mp4` | Shopee Vídeo | "tiktok", "tiktok shop", "bom dia", "boa tarde", "boa noite" |

- Ficam em **`gravacoes\Cortes\<nome da live>\`**. Uma notificação (ntfy) avisa quando ficam prontos.
- Precisa de **"Gravar" ligado** e do modelo de voz instalado: sem ele os cortes não são feitos, porque
  iriam com as palavras proibidas.
- Marca nos dois sentidos de uma vez, se os dois estiverem gravando.
- Dois toques em menos de 15 s contam como o mesmo momento.
- Um toque com a live parada, ou antes de começar a gravar, não vira corte.
- Os cortes são feitos com prioridade baixa no computador (cerca de 15 s por versão): não atrapalham a
  outra live, se ela continuar no ar.
- O mudo dos cortes usa o mesmo reconhecimento de voz da live. Assista antes de postar: uma palavra dita
  de um jeito diferente pode passar.

## Card "SIGA O PERFIL" (liga e desliga, igual ao contador)

Um card branco com **"SIGA O PERFIL — e não perca nenhuma oferta!"** que você coloca no vídeo por um tempo e tira
quando quiser. Fica à esquerda, logo abaixo do contador.

- **Ligar:** no painel do PC, quadro **📣 Card "SIGA O PERFIL"** (embaixo do contador): **📣 Mostrar card** e
  **■ Tirar**. No celular, cada cartão (TikTok e Shopee) tem os mesmos botões, embaixo do contador.
- Aparece na live que passa pelo computador: TikTok → Shopee (na Shopee, desenhado no vídeo) e Shopee → TikTok
  (no TikTok, pela camada do OBS). Liga e desliga **sem parar a live**.
- A imagem é `imagens\card_siga.png` (fundo transparente): para trocar o desenho, troque o arquivo.
- Posição: `"card_siga_x"`, `"card_siga_y"` (canto de cima à esquerda, de 0 a 1; padrão 0.11 e 0.375) e
  `"card_siga_larg"` (largura, padrão 0.42) no `config.json`.
- Para aparecer no TikTok depois de atualizar, feche o OBS antes de iniciar (ou atualize a camada "Contador").

## Banner "SIGA O PERFIL" na Shopee

Um banner fixo **"SIGA O PERFIL"**, com uma seta apontando para o botão **+ Seguir** da Shopee, aparece no alto do
vídeo, logo à direita do botão. Aparece na **live espelhada (TikTok → Shopee)** e na **reprise
para a Shopee** (inclusive por cima do "Já voltamos!") enquanto estiver ligado.

- **Ligar e desligar com a live no ar:** quadro **📌 Banner "SIGA O PERFIL"** no painel (embaixo do contador e do
  card, no lado TikTok → Shopee) ou no cartão da Shopee no celular. Aperte **📌 Mostrar banner** para pôr; o mesmo
  botão vira **■ Tirar banner**. Muda na hora, sem parar a live, e a escolha fica guardada para a próxima live.
- A imagem é `imagens\siga_o_perfil.png` (fundo transparente). Para trocar o desenho, troque o arquivo.
- Posição: o botão + Seguir anda para a direita quando o nome da loja é mais comprido. O padrão foi medido para o
  nome da loja do dono, já descontando o corte das laterais que o celular faz. Para outra loja, ajuste no
  `config.json`: `"banner_siga_x"` (onde começa a seta, de 0 a 1, padrão 0.53), `"banner_siga_y"` (altura do
  centro, padrão 0.11, na linha do nome do perfil) e `"banner_siga_larg"` (largura, padrão 0.35).
- Para desenhar o banner, o vídeo é sempre refeito, mesmo no modo "copiar".

## Controlar pelo celular (só no Wi-Fi de casa)

Com o painel aberto, a janela preta mostra a linha **"No celular (mesmo Wi-Fi): http://192.168.0.X:8770"**.
Abra esse endereço no navegador do celular (de preferência, adicione à tela inicial). A página mostra:
- **TikTok (a conta de `"tiktok_conta_live"` no `config.json`; sem ela, o @ do painel):** se a live está no ar, **pessoas assistindo agora**, entradas e tempo no ar.
  Os números vêm da página do **Gerenciador de LIVE aberta no Chrome** (extensão 1.8): deixe-a aberta.
  Sem ela, o painel consulta o perfil no TikTok no máximo 1 vez por minuto (consultar mais que isso faz o
  TikTok bloquear);
- **Shopee:** a live que está **aberta no Chrome** (a página da sua live, a mesma do botão Encerrar), com
  **pessoas assistindo agora**, pico, visualizações e curtidas. Sem nenhuma aberta, usa a live do link
  salvo no painel;
- o que o painel está enviando, a prévia, os alarmes e as últimas mensagens;
- os botões **Encerrar live no TikTok**, **Encerrar live na Shopee** e **Encerrar as duas lives**
  (todos perguntam "tem certeza?").

No topo fica a lista de **Avisos**: envio que caiu ou voltou, live da Shopee pausada, OBS parou de receber,
extensão do Chrome desconectada, aviso de restrição, live que começou ou acabou e **queda de público** (metade
em 1 minuto). O computador continua agindo sozinho; o celular só avisa. Toque em **Ligar som** uma vez para os
avisos apitarem. Eles só tocam com a página aberta e a tela ligada (no iPhone não vibra).
Em cada card, **Transmissão** mostra a saúde do envio: 🟢 boa, 🟡 atenção ou 🔴 problema, com a velocidade
(1,00x = tempo real), o bitrate e o motivo.

**Comentários e resposta (extensão 1.9):** cada card mostra os últimos comentários daquela live, lidos do
chat da página aberta no Chrome (Gerenciador de LIVE no TikTok, página da sua live na Shopee). Escreva no
campo **Responder** do card e toque em **Enviar**: a extensão escreve na caixa de comentário da página
("Digite algo..." no TikTok, até 100 letras; "Diga algo..." na Shopee, até 150) e envia. Tocar num comentário
já começa a resposta com **@nome**. Os avisos mostram só os 2 últimos; **Ver avisos anteriores** abre o resto.
O chat vai juntando as mensagens (a página da Shopee só mostra as últimas 5) e começa limpo a cada live
nova. Nomes: cliente em **verde**, você em **vermelho**. O chat e as notificações mostram **só mensagens de
clientes e as suas respostas**. Nenhum aviso automático da live aparece:
- "entrou na live por Nº..." e "fulano e outros 3 usuários entraram por: Nº...";
- "começou a seguir" e "está/estão comprando produtos";
- "está vendo o produto" e "Bem-vindo à Shopee Live".

**Notificações no iPhone (app ntfy, grátis):** erros, restrições e perguntas do chat chegam como
notificação, mesmo com a tela bloqueada e a página fechada, dizendo se é do TikTok ou da Shopee. Na página
do celular, abra **🔔 Notificações no iPhone** e siga os passos: instalar o app **ntfy**, tocar em **+**,
digitar o nome do canal (`ntfy_topico` no `config.json`, secreto) e tocar em **Testar notificação**.
As notificações passam pelo servidor público ntfy.sh; quem souber o nome do canal consegue ler, então não
divulgue. Perguntas: no máximo 1 notificação a cada 15 s por plataforma (juntando as que chegarem).
Desligar tudo: `"ntfy_ligado": false`; só as perguntas: `"ntfy_perguntas": false`.

Os números atualizam a cada 3 s. Nos dois cards aparecem as **pessoas assistindo agora** (com ▲/▼ de quanto mudou), quantas estão **entrando por minuto** e quantas **entraram nos últimos 5 minutos**. Para escolher a conta do TikTok mostrada: `"tiktok_conta_live": "sua_conta"` no
`config.json`.

- Só funciona com o celular no **mesmo Wi-Fi/roteador** do computador. Nada fica aberto na internet.
- Pela página não dá para iniciar transmissões, mudar configurações nem ver logins/chaves.
- Encerrar no TikTok usa o **Gerenciador de LIVE aberto no Chrome** do computador; encerrar na Shopee usa a
  página da live da Shopee no Chrome. Pelo TikTok LIVE Studio, use a Área de Trabalho Remota do Chrome.
- Na primeira vez, o Windows precisa deixar o celular entrar: a rede do computador tem que estar como
  **Privada** (Configurações → Rede e Internet → Ethernet/Wi-Fi → Perfil de rede: **Privado**) e, se
  aparecer o aviso do Firewall para o **Python**, marque **Redes privadas** e clique em **Permitir**.
- Se o endereço mudar (o roteador pode trocar o número), veja de novo na janela preta.
- Para desligar a página do celular: `"celular_ligado": false` no `config.json`.

## Lives com restrição de idade (sessionid)

Quando a live é marcada como 18+, o TikTok só mostra para quem está logado numa conta
maior de idade. O sessionid é o cookie que prova esse login.

1. No Chrome ou Edge, abra **tiktok.com** logado numa conta **maior de 18 anos**.
2. Aperte **F12** e vá na aba **Application** (ou **Aplicativo**).
3. No menu da esquerda: **Cookies → https://www.tiktok.com**.
4. Procure **sessionid** e copie o que está na coluna **Value** (Valor).
5. Cole no campo **sessionid do TikTok** do painel.

Marque **"Lembrar neste computador"** para não precisar colar toda vez. Ele fica salvo no
`config.json` desta pasta. Desmarque e inicie de novo para apagar.

> ⚠ O sessionid dá acesso à sua conta, igual a uma senha. Não envie para ninguém e não
> compartilhe o `config.json`. Se você sair da conta no navegador, ele para de funcionar
> e o painel avisa no registro. Aí é só pegar um novo.

## O que já está instalado

- Python 3.12
- FFmpeg
- yt-dlp (o `iniciar.bat` atualiza toda vez)
- Vosk + modelo de voz em português (pasta `modelos`, não apague)
- OBS Studio (câmera virtual para a live do TikTok Shop; o painel cria o perfil "EspelhoTikTok")
- VB-CABLE (microfone virtual "CABLE Output")

## Problemas comuns

- **Fica em "Aguardando" mesmo com a live aberta**: confira o @. Se estiver certo, o TikTok
  pode ter mudado o site. Feche e abra pelo `iniciar.bat` para atualizar o yt-dlp.
- **TikTok respondeu HTTP 403**: o painel pausa novas consultas por 10 minutos para não insistir
  durante o bloqueio. Não reinicie o painel nem atualize a página repetidamente; aguarde e teste
  tiktok.com no navegador. Se o próprio site continuar em 403, é uma restrição do TikTok que o
  painel não consegue remover.
- **"AO VIVO" mas a Shopee não mostra nada / fica reconectando**: a URL ou a chave estão
  erradas, ou a live na Shopee foi encerrada. Se o registro disser que a página da live não está
  mais no ar, crie outra live na Shopee e use a URL e a chave novas.
- **Imagem travando**: escolha a qualidade 720p.
- **Uma palavra passou sem mutar**: aumente o atraso (por exemplo para 10 s) e veja no
  registro se apareceu "detectado tarde demais".
- **Aviso "reconhecimento de voz está ficando para trás"**: o computador está sobrecarregado.
  Use 720p ou o modo "copiar".

Tudo fica registrado em `espelho.log`.

## Ajustes avançados

O `config.json` aceita: `modo` (`recodificar` ou `copiar`), `fps`, `bitrate_video_kbps`,
`preset_x264`, `intervalo_verificacao_seg`, `atraso_seg`, `mutar_palavras` e `ffmpeg`
(caminho do executável).

## Aviso

Use apenas com lives **suas** ou de quem te autorizou. Retransmitir conteúdo de terceiros
viola direitos autorais e os termos do TikTok e da Shopee.
