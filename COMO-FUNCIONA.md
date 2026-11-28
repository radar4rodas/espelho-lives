# Como tudo funciona: guia completo do Espelho de Lives

Este guia explica o projeto inteiro para quem vai usar ou mexer nele pela primeira vez: o que ele faz,
como instalar num computador novo, como usar em cada live e como as peças conversam entre si.
Os detalhes de cada botão também estão no [LEIA-ME.md](LEIA-ME.md).

---

## 1. O que o projeto faz

É um programa para **Windows** que retransmite lives entre o **TikTok** e a **Shopee Live**, nos dois sentidos,
e que pode rodar os dois ao mesmo tempo:

| Sentido | O que acontece | Como chega no destino |
|---|---|---|
| **TikTok → Shopee** | Fica de olho num perfil do TikTok. Quando a live começa, manda o vídeo para a Shopee. | Direto por RTMP (URL + chave que a Shopee dá) |
| **Shopee → TikTok** | Pega uma live da Shopee que já está no ar e leva para a live de vendas do TikTok Shop. | Pelo OBS (câmera virtual) + VB-CABLE (microfone virtual), escolhidos no Gerenciador de LIVE do TikTok |

Além de retransmitir, ele:

- **atrasa** o vídeo alguns segundos (padrão 7 s) e usa esse tempo para **mutar palavras** proibidas:
  - TikTok → Shopee: "tiktok" e "tiktok shop";
  - Shopee → TikTok: "shopee", "shop" e "pix";
  - nos dois sentidos: "bom dia", "boa tarde" e "boa noite";
- **reconecta sozinho** se a conexão cair, sem derrubar a live do outro lado;
- **encerra a live do destino** quando a de origem acaba (pela extensão do Chrome);
- **encerra só a live afetada** se aparecer um aviso de restrição da plataforma;
- **grava** as lives e permite **reprisar** a gravação, com uma etiqueta no canto (ligada por padrão, texto escolhido no painel);
- mostra um **contador "Oferta termina em"** dentro do vídeo;
- tem uma **página para o celular** (no Wi-Fi de casa) com números da live, chat, resposta e botões de encerrar;
- manda **notificações no celular** (app ntfy) com erros e perguntas de clientes.

> Use só com lives **suas** ou de quem te autorizou. Confira as regras da Shopee Live e do TikTok Shop sobre
> retransmissão e lives gravadas.

---

## 2. Como as peças se encaixam

```
                      ┌──────────── seu computador ────────────────────────────────────┐
                      │                                                                │
 TikTok Live ──────►  │  painel.py  (http://127.0.0.1:8765, abre no navegador)          │
 (yt-dlp/API)         │    ├─ espelho.py      vigia as lives, reconecta, decide encerrar│
                      │    ├─ transmissao.py  ffmpeg: atraso, mudo (Vosk), contador,    │ ──► Shopee (RTMP)
 Shopee Live ──────►  │    │                  gravação, reprise                         │
                      │    ├─ obs_tiktok.py   abre e configura o OBS sozinho            │ ──► OBS ──► Chrome ──► TikTok Shop
                      │    ├─ sincronia.py    confere se voz e imagem estão juntas      │      (câmera virtual + CABLE)
                      │    ├─ contador.py     contador "Oferta termina em"              │
                      │    ├─ extensao.py     conversa com a extensão (ws://…:8766)     │◄──► extensão do Chrome
                      │    ├─ celular.py      página do celular (porta 8770, só Wi-Fi)  │◄──► celular
                      │    └─ notificacoes.py notificações pelo ntfy.sh                 │ ──► app ntfy no celular
                      └────────────────────────────────────────────────────────────────┘
```

### Arquivos

| Arquivo / pasta | Para que serve |
|---|---|
| `iniciar.bat` | **Por onde tudo começa.** Atualiza as bibliotecas (principalmente o yt-dlp) e abre o painel. |
| `painel.py` + `painel.html` | O painel no navegador: campos, botões Iniciar/Parar, monitor, registro, contador. |
| `espelho.py` | O "cérebro": acha o link do vídeo do TikTok/Shopee, vigia se a live está no ar, pausa, fim, quedas e reconexões. Também lê e salva o `config.json`. |
| `transmissao.py` | Monta e controla os processos do **ffmpeg**: atraso, mudo por reconhecimento de voz (Vosk), troca de conexão sem pausa, gravação, emenda das partes, reprise em loop, etiqueta da reprise e contador no vídeo. |
| `obs_tiktok.py` | Para Shopee → TikTok: cria o perfil e a cena **"EspelhoTikTok"** no OBS, abre o OBS e liga a câmera virtual. O som vai para o "CABLE Input". |
| `sincronia.py` | Grava alguns segundos do que sai do OBS e compara com o original para corrigir sozinho o atraso da voz no TikTok. |
| `cortes.py` | Cortes para Reels: no fim da live, cada ⭐ marcação vira um vídeo vertical de ~45 s, em versão TikTok e versão Shopee Vídeo, cada uma com o seu mudo. |
| `mudo.py` | Quais palavras o mudo procura e onde foram ditas (reconhecimento de voz Vosk). Usado na live e nos cortes. |
| `tests/` | Testes: `python -m unittest discover -s tests`. |
| `contador.py` + `contador.html` | O contador "Oferta termina em": desenhado pelo ffmpeg na Shopee e por uma camada transparente do OBS no TikTok. |
| `extensao.py` | Servidor local (porta 8766) que conversa com a extensão do Chrome. |
| `extensao_shopee/` | **Extensão do Chrome** (versão 1.13): pega URL e chave da Shopee, clica em Finalizar/Encerrar, lê avisos de restrição, lê e envia comentários, lê os números do Gerenciador de LIVE e deixa o som limpo no TikTok. |
| `celular.py` + `celular.html` | Página do celular, separada do painel, que só aceita aparelhos da rede de casa. |
| `notificacoes.py` | Envia notificações pelo ntfy.sh para um canal com nome aleatório e secreto. |
| `imagens/ja_voltamos.png` | Imagem "Já voltamos!": o OBS mostra se a Shopee pausar, e a Shopee recebe se o vídeo do TikTok sumir. |
| `imagens/siga_o_perfil.png` | Banner "SIGA O PERFIL" desenhado no vídeo que vai para a Shopee. |
| `imagens/card_siga.png` | Card "SIGA O PERFIL" que liga e desliga pelo painel/celular (Shopee: no vídeo; TikTok: camada do OBS). |
| `requirements.txt` | Bibliotecas Python: yt-dlp, curl_cffi, vosk e websockets. |
| `config.json` | **Criado sozinho** na primeira vez. Guarda as suas configurações. **Nunca vai para o GitHub.** |
| `modelos/` | Modelo de voz em português do Vosk. **Baixar à parte** (veja a instalação). |
| `gravacoes/` | Lives gravadas, uma pasta para cada sentido. Não vai para o GitHub. |
| `perfil_navegador_shopee/` | Perfil de navegador com login. **Pessoal**, nunca vai para o GitHub. |
| `espelho.log` | Registro de tudo que aconteceu. Primeiro lugar para olhar quando algo dá errado. |

### Portas usadas (todas só no próprio computador, menos a do celular)

| Porta | Quem usa |
|---|---|
| 8765 | Painel (`http://127.0.0.1:8765`) e a página do contador para o OBS |
| 8766 | Extensão do Chrome ↔ painel |
| 8770 | Página do celular (aceita só endereços da rede de casa: 192.168.x.x, 10.x.x.x, 172.16-31.x.x) |
| 9100 | Controle do OBS (obs-websocket) |

---

## 3. Instalação num computador novo (uma vez só)

Abra o **PowerShell** e instale os programas com o `winget`:

```powershell
winget install Python.Python.3.12
```

```powershell
winget install Gyan.FFmpeg
```

Só se for usar **Shopee → TikTok**:

```powershell
winget install OBSProject.OBSStudio
```

Depois:

1. **Baixe o projeto** do GitHub (botão verde **Code → Download ZIP**, ou `git clone`) e coloque numa pasta,
   por exemplo `D:\Lives`.
2. **Modelo de voz (obrigatório para o mudo):** baixe **vosk-model-small-pt-0.3** em
   <https://alphacephei.com/vosk/models>, descompacte e deixe assim:
   `D:\Lives\modelos\vosk-model-small-pt-0.3\` (dentro dela ficam pastas como `am`, `conf`, `graph`).
3. **VB-CABLE** (só para Shopee → TikTok): instale o driver gratuito do site oficial da VB-Audio
   (vb-audio.com/Cable) e reinicie o computador.
4. **Extensão do Chrome:**
   1. Abra `chrome://extensions`.
   2. Ligue o **Modo do desenvolvedor**.
   3. Clique em **Carregar sem compactação** e escolha a pasta `extensao_shopee`.
5. Dê dois cliques em **`iniciar.bat`**. Na primeira vez ele instala as bibliotecas do `requirements.txt`
   e abre o painel. O `config.json` é criado na hora.

> O `iniciar.bat` procura o Python em `%LOCALAPPDATA%\Programs\Python\Python312`, que é onde o winget instala.
> Se o Python estiver em outro lugar, ajuste a linha `set "PY=..."` do `iniciar.bat`.

---

## 4. Usando em cada live

**Regra de ouro:** a **janela preta** que abre com o `iniciar.bat` é o programa. **Não feche durante a live.**
A aba do painel no navegador pode ser fechada e aberta à vontade.

### TikTok → Shopee

1. No Seller Centre da Shopee, crie a live escolhendo transmitir **por software / OBS / RTMP**.
2. No painel, aba **TikTok → Shopee**, preencha:
   - o **@ do perfil** do TikTok;
   - a **URL do servidor** e a **chave** da Shopee. O botão **Pegar URL e chave da Shopee** faz isso pela
     extensão, com a página da Shopee aberta no Chrome.
3. Escolha as opções (atraso, mudo, gravar, finalizar a Shopee quando o TikTok acabar).
4. **▶ Iniciar espelhamento**, depois **Entrar ao vivo?** na Shopee.
5. Comece a live no TikTok. Em até ~20 s o painel mostra **🔴 AO VIVO**.

### Shopee → TikTok

1. No painel, aba **Shopee → TikTok**, cole o **link da live da Shopee** (Compartilhar → Copiar link).
2. **▶ Iniciar Shopee → TikTok**. O painel abre o OBS sozinho; pode minimizar, mas não feche.
3. No Chrome, abra o **Gerenciador de LIVE** do TikTok Shop e escolha:
   - **Fonte de vídeo:** OBS Virtual Camera;
   - **Fonte de áudio:** CABLE Output (VB-Audio Virtual Cable).
4. Inicie a live. Deixe a aba do Gerenciador aberta: a extensão lê os números e encerra por ela.

### Reprise

Nas sub-abas **Reprisar gravação**: escolha o vídeo, se quer repetir em loop e por quanto tempo, e clique em
Iniciar. Por padrão a reprise sai com a etiqueta **"REPRISE"** no canto, para quem assiste saber que é gravação; na etapa Gravação dá para desligar ou trocar o texto.
- **Fila:** clicando em vários vídeos, na ordem, eles passam em sequência, emendados sem parar a live (cada vídeo
  entra como um "trecho" novo, igual à volta do loop). O número em cada vídeo mostra a ordem.
- Vídeos com 1 hora ou mais aparecem com o tempo em verde.

### Cortes para Reels

Durante a live, aperte **⭐ Marcar** (painel ou celular) logo depois de um momento bom. No fim da live, cada
marcação vira um vídeo vertical em `gravacoes\Cortes\`, em versão TikTok e versão Shopee Vídeo. Precisa de
"Gravar" ligado. Detalhes no [LEIA-ME.md](LEIA-ME.md).

### Só gravar

Sub-aba **Só gravar** de cada lado: grava a live (TikTok pelo @, Shopee pelo link) sem transmitir, copiando o vídeo
como chega. TikTok vai para `gravacoes\TikTok para Shopee`; Shopee para `gravacoes\Shopee para TikTok`. Para sozinha
quando a live acaba e emenda as partes no fim. É independente da transmissão daquele lado.

### Contador "Oferta termina em" e card "SIGA O PERFIL"

No quadro **⏱ Contador na tela** do painel, ou no celular: escolha o tempo e clique em **Iniciar**.
- Ele aparece à esquerda, abaixo do cartão de produto.
- Fica vermelho no último minuto e some quando zera.
- Só aparece na live que passa pelo computador: na Shopee, quando é TikTok → Shopee; no TikTok, quando é
  Shopee → TikTok.

O quadro **📣 Card "SIGA O PERFIL"** (logo abaixo, e no celular) põe e tira um card logo abaixo do contador, nos
mesmos lugares. Fica até você tirar.

### Mudo automático

O reconhecimento de voz (Vosk, offline) procura só as palavras a mutar, mais algumas palavras comuns de som
parecido ("iscas") que evitam mutar a fala à toa. Para "tiktok" ele também exige uma confiança mínima.

---

## 5. Celular e notificações

- Com o painel aberto, a janela preta mostra **"No celular (mesmo Wi-Fi): http://192.168.0.X:8770"**.
  Abra no celular conectado ao mesmo Wi-Fi.
- A página tem uma aba para cada plataforma, com cor própria. Ela mostra:
  - pessoas assistindo, tempo no ar e saúde da transmissão;
  - o chat **só com perguntas de clientes e as suas respostas**, com um campo para responder;
  - o contador;
  - os botões de encerrar, que sempre perguntam "tem certeza?".
- **Primeira vez no Windows:**
  - a rede do computador precisa estar como **Privada**;
  - se o Firewall perguntar sobre o Python, marque **Redes privadas** e clique em **Permitir**.
- **Notificações (app ntfy):** na página do celular, abra **🔔 Notificações no iPhone** e siga os passos.
  O nome do canal é criado sozinho e fica no `config.json` (`ntfy_topico`). **É secreto:** quem souber o
  nome consegue ler as notificações.

---

## 6. Configurações (`config.json`)

O painel cria e atualiza o arquivo sozinho. Os campos que mais importam:

| Campo | O que faz |
|---|---|
| `tiktok_usuario` | @ do perfil do TikTok vigiado no TikTok → Shopee |
| `tiktok_conta_live` | Conta do TikTok cujos números aparecem no celular (sem ela, usa `tiktok_usuario`) |
| `shopee_url_servidor` | URL RTMP da Shopee (a chave **não** é salva: muda a cada live) |
| `shopee_link_live` | Último link de live da Shopee usado no Shopee → TikTok |
| `atraso_seg` / `atraso_st` | Atraso em segundos de cada sentido |
| `mutar_palavras` / `mutar_st` | Liga o mudo automático de cada sentido |
| `gravar` / `gravar_st` | Grava as lives na pasta `gravacoes` |
| `encerrar_shopee` | Finaliza a Shopee quando a live do TikTok acaba |
| `auto_tiktok` | Encerra o TikTok quando a Shopee pausa ou acaba |
| `encerrar_restricao` | Encerra a live onde aparecer aviso de restrição |
| `modo` | `recodificar` (padrão, mais compatível) ou `copiar` (mais leve) |
| `resolucao`, `fps`, `bitrate_video_kbps` | Qualidade do vídeo enviado |
| `sincronia_auto`, `ajuste_voz_ms` | Sincronia da voz no TikTok |
| `ntfy_ligado`, `ntfy_perguntas`, `ntfy_topico` | Notificações no celular |
| `celular_ligado`, `porta_celular`, `porta_painel` | Liga/desliga a página do celular e as portas |
| `tiktok_sessionid` + `lembrar_sessionid` | Login do TikTok, só para lives 18+. **É como uma senha.** |
| `ffmpeg` | Caminho do ffmpeg.exe (vazio = procura sozinho) |
| `tapa_pausa` | Manda "Já voltamos!" para a Shopee quando o vídeo do TikTok some (padrão: ligado) |
| `banner_siga` | Banner "SIGA O PERFIL" ao lado do + Seguir da Shopee (padrão: ligado) |
| `banner_siga_x`, `banner_siga_y`, `banner_siga_larg` | Posição e tamanho do banner, de 0 a 1 (ajuste para o nome da sua loja) |
| `card_siga_x`, `card_siga_y`, `card_siga_larg` | Posição (canto de cima à esquerda) e largura do card, de 0 a 1 (padrão 0.11, 0.375, 0.42) |
| `gravar_tiktok_usuario` / `gravar_shopee_link` | Último @ / link usado no "Só gravar" (o painel preenche sozinho) |

---

## 7. Depois de mudar alguma coisa, o que precisa reiniciar

| Mudou… | Para valer… |
|---|---|
| Arquivo `.py` | Fechar a janela preta e abrir o `iniciar.bat` de novo. **Isso derruba as lives em andamento.** |
| `painel.html` / `celular.html` / `contador.html` | Só recarregar a página (F5) |
| Arquivos da `extensao_shopee` | Em `chrome://extensions`, clicar em recarregar na extensão e dar F5 nas abas da Shopee e do Gerenciador |
| Cena do OBS | Fechar o OBS antes de iniciar Shopee → TikTok, para o painel recriar a cena |

---

## 8. Quando algo dá errado

1. Olhe o **Registro** no painel ou o arquivo `espelho.log`: quase todo problema aparece explicado lá.
2. Problemas comuns:
   - **Fica em "Aguardando" com a live aberta:** confira o @. Reabra pelo `iniciar.bat` para atualizar o yt-dlp.
   - **Shopee não mostra nada / reconecta sem parar:** URL ou chave erradas, ou a live não foi criada na Shopee.
   - **Imagem travando:** use 720p ou o modo `copiar`.
   - **Uma palavra passou sem mutar:** aumente o atraso (ex.: 10 s).
   - **"ffmpeg não encontrado":** `winget install Gyan.FFmpeg` e reabra o `iniciar.bat`.
   - **Mudo não funciona:** confira se a pasta `modelos\vosk-model-small-pt-0.3` existe.
   - **Extensão desconectada:** confira se ela está ligada em `chrome://extensions` e se o painel está aberto.
   - **Celular não abre a página:** mesmo Wi-Fi? Rede como Privada? Firewall liberado para o Python?

---

## 9. O que NUNCA compartilhar

- `config.json`: tem o sessionid do TikTok (se salvo) e o canal secreto das notificações.
- `perfil_navegador_shopee/`: logins do navegador.
- A **chave de transmissão** da Shopee.
- Print da tela do painel com a chave à mostra.

O `.gitignore` já impede que esses arquivos subam para o GitHub. **Não apague o `.gitignore`.**
