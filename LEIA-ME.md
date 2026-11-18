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

Se algo der errado (por exemplo, a Shopee mudou a página), o painel mostra um **alarme vermelho
com som** para você finalizar manualmente. O botão **Finalizar agora na Shopee** serve para
encerrar na hora ou para testar (use uma live do tipo **Teste**).

## Gravar e reprisar lives

**Pastas:** cada sentido tem a sua pasta. As lives do TikTok → Shopee ficam em
`gravacoes\TikTok para Shopee`, e as da Shopee → TikTok em `gravacoes\Shopee para TikTok`.
Cada aba de reprise mostra só os vídeos da sua pasta.

**Gravar:** em **TikTok → Shopee → Espelhar ao vivo**, ligue **"Salvar a live neste computador"**. Cada live
vira um arquivo na pasta `gravacoes\TikTok para Shopee`, com a data e o @ no nome (por exemplo,
`2026-11-17_21h30_sualoja.ts`). Se a live cair e voltar, a continuação vai para um arquivo
`_parte2`. Ocupa cerca de 1 GB por hora. A gravação guarda o áudio original; o mudo de
"tiktok" é aplicado de novo na hora de reprisar.

**Reprisar:** em **TikTok → Shopee → Reprisar gravação**:
1. Escolha o vídeo na lista (lives salvas pelo painel ou vídeos seus que você colocar na
   pasta, como o replay baixado do TikTok: `.mp4`, `.ts`, `.flv`, `.mov`, `.mkv`).
2. Ligue **"Repetir em loop"** para ficar repetindo, ou deixe desligado para passar uma vez só.
   No loop, quando o vídeo acaba ele **recomeça emendado, sem parar a transmissão** (a conexão
   com a Shopee/OBS continua; no máximo ~1 s de imagem parada na emenda, o som segue).
3. Em **"Por quanto tempo fica em loop"**, escolha de **1 a 10 horas** (ou "Até eu clicar em
   Parar"). Quando o tempo acaba, a reprise para sozinha. Com **"Quando o tempo acabar, encerrar
   a live"** ligado, o painel também encerra a live na Shopee/TikTok pela extensão; desligado,
   ele toca um alarme para você encerrar.
4. Confira a URL e a chave da Shopee, e clique em **▶ Iniciar reprise**.

O mesmo vale em **Shopee → TikTok → Reprisar gravação** (loop emendado e tempo de loop).

O atraso, o mudo automático e a qualidade valem também para a reprise. Use **Abrir pasta**
para ver os arquivos e **Atualizar lista** depois de colocar um vídeo novo lá.

> Reprisar vídeo gravado como se fosse ao vivo pode violar as regras da Shopee Live e gerar
> penalização. Use por sua conta e risco, e só com lives suas.

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

O atraso vale igual, e o mudo automático cala a palavra **"shopee"** antes de chegar no
TikTok. Só o som da live vai para o TikTok; os outros sons do computador não entram.
O vídeo vai como veio da Shopee (sem recodificar), então pesa pouco no computador.

**Pausa na Shopee:** o Gerenciador de LIVE do TikTok (na web) **não tem botão de pausar**. Então,
se o vídeo da Shopee parar de chegar por 5 s, o painel deixa sair o que ainda está no atraso e o
OBS mostra a imagem **"Já voltamos!"** (`imagens\ja_voltamos.png`, fica atrás do vídeo). Quando a
Shopee volta, o vídeo volta por cima da imagem sozinho. Isso vale sempre, com ou sem a opção abaixo.
Se você trocar o arquivo da imagem, feche o OBS para ele recarregar.

**Fim automático:** ligue **"Encerrar a live no TikTok quando a Shopee acabar"**. Com a extensão no
Chrome e a aba do Gerenciador de LIVE aberta (pode ficar em segundo plano), quando a live da Shopee
**acaba** o painel clica no botão **⏻** ao lado do contador do Gerenciador e confirma. Funciona
sozinho; se não conseguir, toca um **alarme** e escreve o motivo no registro. O botão
**"Testar: ler os botões do TikTok"** mostra no registro se a extensão enxerga o contador e o ⏻;
use durante uma live de **Prática** para conferir.

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
- Vosk + modelo de voz em português (pasta `modelos`, não apague). Se a pasta não existir, baixe o
  `vosk-model-small-pt-0.3.zip` em https://alphacephei.com/vosk/models e extraia em
  `modelos\vosk-model-small-pt-0.3`. Sem ele, o mudo automático fica desligado.
- OBS Studio (câmera virtual para a live do TikTok Shop; o painel cria o perfil "EspelhoTikTok")
- VB-CABLE (microfone virtual "CABLE Output")

## Problemas comuns

- **Fica em "Aguardando" mesmo com a live aberta**: confira o @. Se estiver certo, o TikTok
  pode ter mudado o site. Feche e abra pelo `iniciar.bat` para atualizar o yt-dlp.
- **"AO VIVO" mas a Shopee não mostra nada / fica reconectando**: a URL ou a chave estão
  erradas, ou a live na Shopee não foi criada. O motivo aparece no quadro de registro.
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
