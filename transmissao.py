"""
Uma sessão de retransmissão com atraso e mudo automático.

  ffmpeg ENTRADA ──(cópia, MPEG-TS)──> Python segura N s ──> ffmpeg PROCESSADOR ──> Python ──> ffmpeg ENVIO ──> Shopee/OBS
        │                                                    ▲ (mudo, recodifica)              (fica ligado a live toda)
        └──áudio 16 kHz, sem atraso──> reconhecimento de voz ──"mute de t1 a t2"─┘ (stdin do PROCESSADOR)

Se o relógio do vídeo bagunçar ou a conexão com a origem cair, só a ENTRADA (e o processador daquele
trecho) é trocada; o ENVIO continua conectado e os tempos continuam de onde pararam.

Como o áudio é ouvido N segundos antes de sair para a Shopee, dá tempo de reconhecer
"tiktok" / "tiktok shop" e silenciar exatamente aquele trecho na saída.
"""

import collections
import json
import logging
import os
import queue
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("espelho")

BASE = Path(__file__).resolve().parent
PASTA_MODELO = BASE / "modelos" / "vosk-model-small-pt-0.3"
TAXA_VOZ = 16000

# O modelo em português "ouve" TikTok como "tique toque" e Shopee ("xópi") como "chope".
# Procurar só por estas frases (o resto vira [unk]) é bem mais confiável do que transcrever tudo.
GRAMATICAS = {
    "tiktok": ["tique toque", "tique toque shop", "tique toque shopping", "[unk]"],
    "shopee": ["chope", "[unk]"],
}
DESCRICAO_MUDO = {"tiktok": "'tiktok' e 'tiktok shop'", "shopee": "'shopee'"}
PALAVRAS_SHOP = {"shop", "shopping"}
FOLGA_ANTES = 0.15   # segundos de mudo antes da palavra
FOLGA_DEPOIS = 0.20  # e depois

_modelo = {"obj": None, "tentou": False}


def nome_thread(sufixo):
    """Nome para uma thread nova que herda o "lado" da atual (ts:... ou st:...).
    O painel usa esse prefixo para saber de qual transmissão vem cada mensagem do registro."""
    atual = threading.current_thread().name
    lado = atual.split(":", 1)[0] if ":" in atual else ""
    return f"{lado}:{sufixo}" if lado else sufixo


def carregar_modelo():
    """Carrega o modelo de voz uma vez só. Retorna None se não estiver disponível."""
    if _modelo["tentou"]:
        return _modelo["obj"]
    _modelo["tentou"] = True
    try:
        import vosk
        vosk.SetLogLevel(-2)   # só erros; -1 ainda mostra avisos internos sem importância
        if not PASTA_MODELO.is_dir():
            log.warning("Modelo de voz não encontrado em %s. O mudo automático fica desligado.", PASTA_MODELO)
            return None
        _modelo["obj"] = vosk.Model(str(PASTA_MODELO))
    except ImportError:
        log.warning("Pacote vosk não instalado (python -m pip install --user vosk). Mudo automático desligado.")
    except Exception as e:
        log.warning("Não consegui carregar o modelo de voz (%s). Mudo automático desligado.", e)
    return _modelo["obj"]


def _ouvir():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    s.settimeout(30)
    return s, s.getsockname()[1]


def _mesclar(janelas):
    out = []
    for a, b in sorted(janelas):
        if out and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _erro_de_envio(linha):
    """Linhas do ffmpeg que indicam que o envio (RTMP para a Shopee ou SRT para o OBS) morreu."""
    return ("Error submitting a packet to the muxer" in linha
            or "Error muxing a packet" in linha
            or "Connection setup failure" in linha
            or (linha.startswith(("[rtmp", "[srt")) and ("rror" in linha or "refused" in linha))
            or (linha.startswith("[out#0") and "rror" in linha))


_RUIDO = ("PPS id out of range", "undecodable NALU", "non-existing PPS", "no frame!", "co located POCs",
          "Consider increasing the value", "decode_slice_header", "Could not find ref with POC",
          "error while decoding MB", "concealing", "Invalid NAL unit", "missing picture in access unit",
          "reference picture missing", "Missing reference picture", "Timestamps are unset",
          "timestamp discontinuity",
          "Last message repeated")


def _ruido(linha):
    """Avisos do decodificador quando o vídeo começa no meio (sem importância: some no próximo quadro-chave)."""
    return any(r in linha for r in _RUIDO)


def _traduzir(linha, destino="Shopee"):
    if destino == "OBS":
        if "setup failure" in linha or "Error opening output" in linha or "refused" in linha.lower():
            return "o OBS não está recebendo; confira se o OBS está aberto"
        return "a conexão com o OBS caiu (o OBS foi fechado?)"
    if "Error opening output" in linha or "Cannot open connection" in linha or "-138" in linha:
        return "não consegui conectar ao servidor da Shopee; confira a URL do servidor e se a live está criada"
    if "-10053" in linha or "-10054" in linha or "reset" in linha.lower():
        return "conexão derrubada pelo servidor da Shopee"
    if "End of file" in linha:
        return "o servidor da Shopee fechou a conexão"
    if "refused" in linha.lower():
        return "o servidor da Shopee recusou a conexão"
    if "rtmp" in linha.lower():
        return "erro no servidor RTMP da Shopee: " + linha.split("]", 1)[-1].strip()
    return linha.split("]", 1)[-1].strip()


# ---------------------------------------------------------------- comandos ffmpeg

PRIORIDADE_ALTA = 0x00008000   # ABOVE_NORMAL_PRIORITY_CLASS: o Chrome não "rouba" tempo do vídeo
_nvenc = {"ok": None}


def nvenc_disponivel(ffmpeg):
    """A placa NVIDIA consegue codificar o vídeo? (testa uma vez só; precisa de driver atualizado)"""
    if _nvenc["ok"] is None:
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "color=c=black:s=320x240:r=30", "-frames:v", "10",
                                "-c:v", "h264_nvenc", "-f", "null", "-"],
                               capture_output=True, text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _nvenc["ok"] = r.returncode == 0
            if _nvenc["ok"]:
                log.info("🎮 Usando a placa de vídeo NVIDIA para codificar (alivia o processador).")
            elif "driver" in r.stderr.lower():
                log.info("A placa NVIDIA não pôde ser usada: o driver está desatualizado. Atualize o driver da "
                         "NVIDIA para o vídeo pesar menos no processador. Por enquanto, uso o processador.")
        except (OSError, subprocess.TimeoutExpired):
            _nvenc["ok"] = False
    return _nvenc["ok"]


class VigiaRelogio:
    """Percebe quando o "relógio" do vídeo bagunçou: o ffmpeg passa a corrigir os tempos sem parar,
    e a live fica travando e com a voz falhada até o fim. Aí o certo é reconectar."""

    def __init__(self, limite, janela=10.0):
        self.limite, self.janela = limite, janela
        self.marcas = collections.deque()

    def bagunçou(self, agora=None):
        agora = time.monotonic() if agora is None else agora
        self.marcas.append(agora)
        while self.marcas and agora - self.marcas[0] > self.janela:
            self.marcas.popleft()
        return len(self.marcas) >= self.limite


def _cabecalhos(cabecalhos):
    if not cabecalhos:
        return []
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in cabecalhos.items())]


def cmd_entrada(ffmpeg, url, cabecalhos, porta_voz, arquivo=None, repetir=False, inicio=0.0):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin"]
    if arquivo:
        # reprise: lê o vídeo no ritmo normal (-re). O loop não usa o -stream_loop do ffmpeg (que
        # bagunça os tempos em gravações de live): a sessão emenda o vídeo de novo sem parar o envio.
        if inicio > 0:
            cmd += ["-ss", f"{inicio:.1f}"]
        # Gravações de live costumam ter "pulos" no relógio (a live engasgou enquanto gravava). Sem
        # corrigir, a reprise fica segundos sem mandar nada e depois manda tudo de uma vez, e a Shopee
        # derruba a conexão. dts_delta_threshold 4 = emenda os pulos maiores que 4 s.
        # NÃO usar 1: um buraco de ~2 s só no vídeo (sem buraco no áudio) faz o ffmpeg corrigir vídeo e
        # áudio um contra o outro sem parar, o -re perde o ritmo e a reprise passa a correr ~20% mais
        # rápido que o normal. Depois de uns 20 min adiantada, a Shopee derruba a conexão.
        cmd += ["-dts_delta_threshold", "4", "-fflags", "+genpts",
                "-re", "-stream_loop", "-1" if repetir else "0", "-i", str(arquivo)]
    else:
        cmd += _cabecalhos(cabecalhos)
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "3",
                # o servidor do TikTok às vezes responde 404 no primeiro pedido e funciona no seguinte
                "-reconnect_on_http_error", "4xx,5xx",
                "-rw_timeout", "15000000", "-i", url]
    cmd += [
            # 1) cópia fiel de vídeo+áudio, que o Python vai atrasar
            "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-copyinkf",
            "-f", "mpegts", "pipe:1"]
    if porta_voz:
        # 2) áudio para o reconhecimento de voz, alinhado à mesma linha do tempo (começa em 0)
        cmd += ["-map", "0:a:0", "-af", f"aresample={TAXA_VOZ}:async=1:first_pts=0", "-ac", "1",
                "-f", "s16le", f"tcp://127.0.0.1:{porta_voz}"]
    return cmd


def cmd_saida(cfg, ffmpeg, porta_ts, preview, deslocamento=0.0):
    """PROCESSADOR de um trecho: aplica o mudo (e recodifica, se for o caso) e devolve MPEG-TS no stdout.
    'deslocamento' faz os tempos deste trecho continuarem de onde o trecho anterior parou."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats", "-y",
           "-analyzeduration", "1500000",
           "-f", "mpegts", "-i", f"tcp://127.0.0.1:{porta_ts}",
           "-map", "0:v:0", "-map", "0:a:0"]
    final = ["-f", "mpegts", "-output_ts_offset", f"{deslocamento:.3f}", "pipe:1"]
    # volume@mute recebe, em tempo real, a lista de trechos a silenciar
    filtro_audio = "volume@mute=volume=1:eval=frame,aresample=44100"
    ab = f"{int(cfg['bitrate_audio_kbps'])}k"
    if cfg.get("saida") == "obs":
        # Para o OBS (live de vendas do TikTok Shop): o vídeo vai como veio (o OBS decodifica),
        # só o áudio é refeito por causa do mudo. Leve para o computador.
        cmd += ["-c:v", "copy",
                "-af", "volume@mute=volume=1:eval=frame,aresample=48000", "-c:a", "aac", "-b:a", ab, "-ac", "2"]
        cmd += final
        if preview:
            cmd += ["-map", "0:v:0", "-vf", "fps=1,scale=360:-2", "-q:v", "6",
                    "-update", "1", "-atomic_writing", "1", preview]
        return cmd
    if cfg["modo"] == "copiar":
        cmd += ["-c:v", "copy"]
    else:
        larg, alt = cfg["resolucao"].lower().split("x")
        fps = int(cfg["fps"])
        vb = int(cfg["bitrate_video_kbps"])
        cmd += ["-vf", f"scale={larg}:{alt}:force_original_aspect_ratio=decrease,"
                       f"pad={larg}:{alt}:(ow-iw)/2:(oh-ih)/2,fps={fps}"]
        if nvenc_disponivel(ffmpeg):
            # placa NVIDIA: quase não usa o processador
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr", "-bf", "0"]
        else:
            cmd += ["-c:v", "libx264", "-preset", cfg["preset_x264"], "-tune", "zerolatency", "-sc_threshold", "0"]
        cmd += ["-profile:v", "main", "-pix_fmt", "yuv420p",
                "-b:v", f"{vb}k", "-maxrate", f"{vb}k", "-bufsize", f"{vb * 2}k",
                "-g", str(fps * 2), "-keyint_min", str(fps * 2)]
    # o envio codifica o áudio de novo para o FLV: aqui vai com folga de qualidade
    cmd += ["-af", filtro_audio, "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += final
    if preview:
        cmd += ["-map", "0:v:0", "-vf", "fps=1,scale=360:-2", "-q:v", "6",
                "-update", "1", "-atomic_writing", "1", preview]
    return cmd


def cmd_envio(cfg, ffmpeg, porta, destino):
    """ENVIO: fica ligado a live inteira (Shopee ou OBS) e só repassa o que os processadores entregam.
    Trocar a conexão com a origem não derruba esta conexão."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostats", "-nostdin",
           "-f", "mpegts", "-i", f"tcp://127.0.0.1:{porta}",
           "-map", "0:v:0", "-map", "0:a:0"]
    if cfg.get("saida") == "obs":
        return cmd + ["-c", "copy", "-f", "mpegts", destino]
    # Shopee (RTMP/FLV): o áudio é codificado de novo aqui. Copiado do MPEG-TS, o FLV começaria com uma
    # configuração de áudio VAZIA (a correta só vem depois), e o servidor da Shopee derruba a conexão.
    ab = f"{int(cfg['bitrate_audio_kbps'])}k"
    return cmd + ["-c:v", "copy", "-c:a", "aac", "-b:a", ab, "-ar", "44100", "-ac", "2",
                  "-f", "flv", "-flvflags", "no_duration_filesize", destino]


class RelogioTS:
    """Lê os tempos (PTS) dos pacotes MPEG-TS que vão para o envio, para o próximo trecho
    começar exatamente depois do último pacote (sem pulo de relógio no envio)."""

    VOLTA = 1 << 33

    def __init__(self):
        self.resto = b""
        self.primeiro = None     # PTS do primeiro pacote enviado (90 kHz, sem "dar a volta")
        self.maior = None
        self._ultimo = None

    def novo_trecho(self):
        self.resto = b""

    def ver(self, dados):
        buf = self.resto + dados if self.resto else dados
        i, n = 0, len(buf)
        while i + 188 <= n:
            if buf[i] != 0x47:              # fora de sincronia: procura o próximo pacote
                j = buf.find(b"\x47", i + 1)
                if j < 0:
                    i = n
                    break
                i = j
                continue
            if buf[i + 1] & 0x40:           # começo de um pacote de áudio/vídeo
                self._pes(buf, i)
            i += 188
        self.resto = buf[i:]

    def _pes(self, b, i):
        flags = b[i + 3]
        if not flags & 0x10:
            return
        p = i + 4
        if flags & 0x20:
            p += 1 + b[p]
        if p + 14 > i + 188 or b[p] or b[p + 1] or b[p + 2] != 1:
            return
        sid = b[p + 3]
        if not (0xC0 <= sid <= 0xEF or sid == 0xBD) or not b[p + 7] & 0x80:
            return
        pts = (((b[p + 9] >> 1) & 7) << 30 | b[p + 10] << 22 | (b[p + 11] >> 1) << 15
               | b[p + 12] << 7 | b[p + 13] >> 1)
        if self._ultimo is None:
            atual = pts
        else:
            d = pts - (self._ultimo % self.VOLTA)
            if d < -(self.VOLTA >> 1):
                d += self.VOLTA
            elif d > (self.VOLTA >> 1):
                d -= self.VOLTA
            atual = self._ultimo + d
        self._ultimo = atual
        if self.primeiro is None:
            self.primeiro = atual
        if self.maior is None or atual > self.maior:
            self.maior = atual

    def continuacao(self):
        """Deslocamento (s) para o próximo trecho começar logo depois do último pacote enviado."""
        if self.primeiro is None:
            return 0.0
        return max(0.0, (self.maior - self.primeiro) / 90000 + 0.1)


# ---------------------------------------------------------------- reconhecimento de voz

class Censor:
    """Ouve o áudio ao vivo e manda a SAÍDA silenciar os trechos com as palavras."""

    def __init__(self, modelo, enviar, atraso, aviso, alvo="tiktok", destino="Shopee", contador=None):
        import vosk
        self.contador = contador if contador is not None else [0]   # total de mudos (vale entre trechos)
        vosk.SetLogLevel(-2)
        self.alvo = alvo if alvo in GRAMATICAS else "tiktok"
        self.destino = destino
        self.rec = vosk.KaldiRecognizer(modelo, TAXA_VOZ, json.dumps(GRAMATICAS[self.alvo]))
        self.rec.SetWords(True)
        self.rec.SetPartialWords(True)
        self.enviar = enviar
        self.atraso = atraso
        self.aviso = aviso
        self.amostras = 0
        self.janelas = []
        self.ultimo_cmd = ""
        self.total = 0
        self._shop_avisado = set()

    @property
    def agora(self):
        return self.amostras / TAXA_VOZ

    def alimentar(self, dados):
        self.amostras += len(dados) // 2
        if self.rec.AcceptWaveform(dados):
            palavras = json.loads(self.rec.Result()).get("result", [])
        else:
            palavras = json.loads(self.rec.PartialResult()).get("partial_result", [])
        if self.alvo == "shopee":
            for p in palavras:
                if p["word"] == "chope":
                    self._mutar(max(0.0, p["start"] - FOLGA_ANTES), p["end"] + FOLGA_DEPOIS, "shopee")
            return
        for i in range(len(palavras) - 1):
            a, b = palavras[i], palavras[i + 1]
            if a["word"] == "tique" and b["word"] == "toque":
                ini, fim, nome = a["start"] - FOLGA_ANTES, b["end"] + FOLGA_DEPOIS, "tiktok"
                if i + 2 < len(palavras):
                    c = palavras[i + 2]
                    # "shop" só conta se vier colado ao "tiktok" e com boa confiança
                    if (c["word"] in PALAVRAS_SHOP and c["start"] - b["end"] < 0.3
                            and c.get("conf", 1.0) >= 0.6):
                        fim, nome = c["end"] + FOLGA_DEPOIS, "tiktok shop"
                self._mutar(max(0.0, ini), fim, nome)

    def _mutar(self, ini, fim, nome):
        novo = not any(a < fim and ini < b for a, b in self.janelas)
        saindo = self.agora - self.atraso      # o que está indo para a Shopee agora
        self.janelas = [(a, b) for a, b in _mesclar(self.janelas + [(ini, fim)]) if b > saindo - 2]
        ativos = self.janelas[-7:]             # limite de tamanho do comando do ffmpeg
        expr = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in ativos)
        cmd = f"cvolume@mute -1 volume if({expr},0,1)\n" if expr else "cvolume@mute -1 volume 1\n"
        if cmd != self.ultimo_cmd:
            self.enviar(cmd)
            self.ultimo_cmd = cmd
        if not novo and nome == "tiktok shop":
            chave = round(ini, 0)
            if chave not in self._shop_avisado:     # o "shop" chegou um instante depois do "tiktok"
                self._shop_avisado.add(chave)
                log.info("🔇 ↳ o 'shop' logo depois também foi mutado.")
        if novo:
            if nome == "tiktok shop":
                self._shop_avisado.add(round(ini, 0))
            self.contador[0] += 1
            self.total = self.contador[0]
            folga = ini - saindo
            if folga < 0.3:
                log.warning("🔇 '%s' detectado tarde demais (%.1fs). Aumente o atraso para garantir o mudo.", nome, ini)
            else:
                log.info("🔇 '%s' detectado em %.1fs. Mutado na saída para %s (folga de %.1fs).",
                         nome, ini, self.destino, folga)
            self.aviso("mudo", f"{nome}|{self.total}")


# ---------------------------------------------------------------- sessão
#
# Desde a troca sem pausa, a sessão tem três partes:
#
#   ENTRADA (trecho 1, 2, 3...) ──> atraso (Python) ──> PROCESSADOR do trecho (mudo/recodifica) ──> Python ──> ENVIO
#
# O ENVIO (conexão com a Shopee ou com o OBS) fica ligado a live inteira. Quando o relógio do vídeo
# bagunça ou a conexão com a origem cai, só a ENTRADA é trocada por uma conexão nova; o vídeo que já
# está no atraso continua saindo e o trecho novo é emendado com os tempos continuando (sem pulo).
# Quem assiste vê no máximo uma paradinha de ~1 s, em vez de a live sumir e voltar.

class Transmissao:
    MAX_TROCAS = 4          # trocas sem parar dentro de JANELA_TROCAS; passou disso, reconecta tudo
    JANELA_TROCAS = 120.0

    def __init__(self, cfg, ffmpeg, url, cabecalhos, destino, preview=None,
                 aviso=None, sem_janela=False, resumir_stats=None,
                 arquivo=None, repetir=False, gravar_em=None, renovar=None, silencioso=False):
        self.cfg = cfg
        self.silencioso = silencioso    # tentativas durante a pausa: não enche o registro
        self.ffmpeg = ffmpeg
        self.arquivo = arquivo          # reprise: vídeo local no lugar da live
        self.repetir = repetir          # reprise em loop
        # salva uma cópia do vídeo recebido: caminho, ou função que devolve um caminho novo por trecho
        self.gravar_em = gravar_em
        # devolve (url, cabecalhos) novos da mesma live, ou None se ela não está mais no ar
        self.renovar = renovar
        if arquivo and renovar is None:
            # reprise: "link novo" = o mesmo arquivo (no fim do vídeo, em loop; ou ao corrigir o relógio)
            self.renovar = lambda: (None, {})
        self.voltas = 1                 # reprise em loop: quantas vezes o vídeo já começou
        self._inicio_seg = {}           # trecho -> (momento em que começou, posição no arquivo)
        self.url = url
        self.cabecalhos = cabecalhos
        self.destino = destino
        self.preview = preview
        self.aviso = aviso or (lambda e, m: None)
        self.flags = 0
        if os.name == "nt":
            self.flags = PRIORIDADE_ALTA | (subprocess.CREATE_NO_WINDOW if sem_janela else 0)
        self.precisa_reiniciar = False   # não deu para trocar sem parar: o motor reconecta tudo
        self.resumir_stats = resumir_stats or (lambda l: l)
        self.atraso = max(0.0, float(cfg.get("atraso_seg", 7)))
        # de onde vem e para onde vai (só muda os textos e as palavras mutadas)
        self.para_obs = cfg.get("saida") == "obs"
        self.nome_destino = "OBS" if self.para_obs else "Shopee"
        self.nome_origem = "Shopee" if self.para_obs else "TikTok"
        self.alvo_mudo = "shopee" if self.para_obs else "tiktok"
        self._parar = threading.Event()
        self._buffer = collections.deque()          # (chegada, trecho, dados)
        self._tem_dados = threading.Condition()
        self._fim_entrada = False
        self._fim = threading.Event()               # tudo o que chegou já foi enviado
        self.erro_entrada = self.erro_saida = ""
        self.shopee_recusou = False
        self.link_recusado = False      # a origem recusou o link do vídeo (404/403)
        self._saida_sem_entrada = False  # o processador falhou porque não chegou vídeo
        self.ultimo_dado = None          # momento (monotonic) em que chegou vídeo pela última vez
        self.modelo = None
        # trechos
        self._lock = threading.RLock()
        self._lock_stdin = threading.Lock()
        self._entradas = {}             # trecho -> processo de entrada
        self._lendo = set()             # trechos cuja entrada ainda está entregando vídeo
        self._seg_ativo = 1             # trecho cujo vídeo está entrando no atraso
        self._seg_novo = None           # trecho conectando para substituir o ativo
        self._trocando = False
        self._sem_troca = False         # pausa: deixa terminar o que está no atraso, sem reconectar
        self._trocas = collections.deque()
        self._processadores = {}        # trecho -> processo que aplica o mudo
        self._repasses = {}             # trecho -> thread que leva a saída do processador ao envio
        self._cmd_mudo = {}             # trecho -> último comando de mudo
        self._total_mudos = [0]
        self._relogio = RelogioTS()
        self._ritmo0 = None             # gravação: relógio de parede quando o 1º vídeo foi para o envio
        self._segurou = False
        self.envio = None
        self._conn_envio = None
        self._srv_ts = self._srv_envio = None
        self._porta_ts = self._porta_envio = None
        self._threads = []

    # -------------------------------------------------------- controle

    def parar(self):
        self._parar.set()
        with self._tem_dados:
            self._tem_dados.notify_all()
        with self._lock:
            procs = list(self._entradas.values()) + list(self._processadores.values()) + [self.envio]
        for p in procs:
            if p and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass

    def terminar_entrada(self):
        """Para de receber da origem, mas deixa sair o que já está no atraso (usado na pausa da Shopee).
        Depois disso a sessão acaba sozinha e o envio fecha."""
        with self._lock:
            self._sem_troca = True
            procs = list(self._entradas.values())
        for p in procs:
            if p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass

    def _thread(self, alvo, nome="transmissao"):
        t = threading.Thread(target=alvo, daemon=True, name=nome_thread(nome))
        t.start()
        self._threads.append(t)
        return t

    def executar(self):
        """Roda até a live acabar, o destino recusar ou parar() ser chamado. Retorna o último erro."""
        self.modelo = carregar_modelo() if self.cfg.get("mutar_palavras", True) else None
        self._srv_ts, self._porta_ts = _ouvir()          # os processadores conectam aqui
        self._srv_envio, self._porta_envio = _ouvir()    # o envio conecta aqui
        try:
            nivel = logging.DEBUG if self.silencioso else logging.INFO
            if self.modelo:
                log.log(nivel, "Mudo automático ligado: %s. Atraso de %.0fs.", DESCRICAO_MUDO[self.alvo_mudo], self.atraso)
            elif self.atraso:
                log.log(nivel, "Atraso de %.0fs (mudo automático desligado).", self.atraso)
            self._iniciar_entrada(self.url, self.cabecalhos, 1)
            self._thread(self._escrever_ts)
            while not self._parar.is_set():
                envio = self.envio
                if envio is not None and envio.poll() is not None:
                    break                              # a conexão com o destino caiu
                if self._fim.is_set():
                    if envio is not None:
                        try:
                            envio.wait(timeout=10)     # termina de mandar o que falta
                        except subprocess.TimeoutExpired:
                            pass
                    break
                time.sleep(0.3)
        finally:
            self.parar()
            for s in (self._srv_ts, self._srv_envio):
                if s:
                    s.close()
            for t in self._threads:
                t.join(timeout=1)
        if self.shopee_recusou:
            return self.erro_saida
        if self.link_recusado:
            m = re.search(r"(?:Server returned|HTTP error) (\d{3}[^\r\n]*)", self.erro_entrada)
            motivo = m.group(1) if m else self.erro_entrada
            return f"a Shopee recusou o link do vídeo ({motivo})" if self.para_obs \
                else f"o TikTok recusou o link do vídeo ({motivo})"
        if self.erro_entrada and self._saida_sem_entrada:
            return f"o vídeo da Shopee não chegou: {self.erro_entrada}" if self.para_obs \
                else f"o vídeo do TikTok não chegou: {self.erro_entrada}"
        return self.erro_saida or self.erro_entrada

    # -------------------------------------------------------- entrada (um processo por trecho)

    def _iniciar_entrada(self, url, cabecalhos, seg, inicio=0.0):
        srv_voz, porta_voz = _ouvir() if self.modelo else (None, None)
        # reprise: cada volta do loop é um trecho novo (o arquivo é lido uma vez por trecho)
        proc = subprocess.Popen(
            cmd_entrada(self.ffmpeg, url, cabecalhos, porta_voz, arquivo=self.arquivo, repetir=False, inicio=inicio),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=self.flags)
        with self._lock:
            self._entradas[seg] = proc
            self._lendo.add(seg)
            self._inicio_seg[seg] = (time.monotonic(), inicio)
        self._thread(lambda: self._ler_stderr_entrada(proc, seg))
        self._thread(lambda: self._ler_ts(proc, seg))
        if srv_voz:
            self._thread(lambda: self._voz(srv_voz, seg), "voz")
        if self._parar.is_set():
            proc.kill()

    def _origem(self):
        if self.arquivo:
            return "da gravação"
        return f"da {self.nome_origem}" if self.para_obs else "do TikTok"

    def _trocar(self, motivo, volta=False):
        """Abre uma conexão nova com a origem; quando o vídeo dela chegar, ela substitui a atual.
        volta=True: reprise em loop, o vídeo acabou e recomeça do início (não conta como troca).
        Devolve False se não deu (sem link novo, muitas trocas seguidas, pausa ou parando)."""
        with self._lock:
            if self._trocando or self._parar.is_set() or self._sem_troca or not self.renovar:
                return False
            agora = time.monotonic()
            while self._trocas and agora - self._trocas[0] > self.JANELA_TROCAS:
                self._trocas.popleft()
            if not volta and len(self._trocas) >= self.MAX_TROCAS:
                log.warning("A conexão já foi trocada %d vezes em %.0f minutos; vou reconectar do zero.",
                            len(self._trocas), self.JANELA_TROCAS / 60)
                return False
            # reprise: ao corrigir o relógio, continua do ponto em que estava (não volta ao começo)
            inicio = 0.0
            if self.arquivo and not volta and self._seg_ativo in self._inicio_seg:
                quando, pos = self._inicio_seg[self._seg_ativo]
                inicio = pos + (agora - quando) + 1.0
            self._trocando = True
        try:
            link = self.renovar()
        except Exception as e:      # noqa: BLE001 - qualquer falha ao pedir o link = não troca
            log.debug("renovar link: %s", e)
            link = None
        with self._lock:
            if not link or self._parar.is_set() or self._sem_troca:
                self._trocando = False
                if not link and not self._parar.is_set() and not self._sem_troca:
                    log.info("Não consegui um link novo da live (%s).", motivo)
                return False
            if not volta:
                self._trocas.append(agora)
            seg = max(self._entradas) + 1
            self._seg_novo = seg
        if volta:
            self.voltas += 1
            log.info("🔁 O vídeo chegou ao fim: recomeçando do início sem parar a transmissão (volta %d).",
                     self.voltas)
        elif self.arquivo:
            log.info("🔁 Reabrindo o vídeo em %s sem parar a transmissão (%s)...",
                     time.strftime("%H:%M:%S", time.gmtime(inicio)), motivo)
        else:
            log.info("🔁 Abrindo uma conexão nova %s sem parar a live (%s)...", self._origem(), motivo)
        url, cab = link
        self._iniciar_entrada(url, cab, seg, inicio=inicio)
        return True

    def _ativar(self, seg):
        """A conexão nova entregou vídeo: passa a ser a atual e a antiga é fechada. (com self._lock)"""
        antigos = [s for s in self._entradas if s < seg]
        self._seg_ativo = seg
        self._seg_novo = None
        self._trocando = False
        for s in antigos:
            p = self._entradas.get(s)
            if p and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        if not self.arquivo:
            log.info("✅ Conexão trocada. A live seguiu sem parar.")

    def _verificar_fim(self):
        """Se a entrada atual acabou e não há troca em andamento, avisa que não vem mais vídeo."""
        with self._lock:
            if self._trocando or self._seg_ativo in self._lendo:
                return
        with self._tem_dados:
            self._fim_entrada = True
            self._tem_dados.notify_all()

    def _entrada_acabou(self, seg, recebeu):
        with self._lock:
            self._lendo.discard(seg)
            if seg == self._seg_novo:               # a conexão nova falhou antes de chegar vídeo
                self._seg_novo = None
                self._trocando = False
                if self._seg_ativo in self._lendo:
                    log.warning("A conexão nova não entregou vídeo; sigo com a atual.")
                    return
                tentar = True                       # a atual também já caiu: tenta mais uma
            elif seg != self._seg_ativo:
                return                              # trecho antigo, já substituído
            else:
                tentar = recebeu
        if self.arquivo:
            # reprise: o vídeo acabou. Em loop, recomeça emendado; senão, a sessão termina.
            quando, _ = self._inicio_seg.get(seg, (time.monotonic(), 0))
            if tentar and self.repetir and not self._parar.is_set():
                if time.monotonic() - quando < 3:
                    log.warning("O vídeo terminou logo depois de começar (arquivo com problema?). Parei o loop.")
                else:
                    self._trocar("fim do vídeo", volta=True)
        elif tentar and not self._parar.is_set():
            self._trocar(f"a conexão {self._origem()} caiu")
        self._verificar_fim()

    def _reiniciar_por_relogio(self, onde):
        with self._lock:
            if self.precisa_reiniciar or self._parar.is_set() or self._trocando:
                return
            pode_trocar = bool(self.renovar) and not self._sem_troca
            if not pode_trocar:
                self.precisa_reiniciar = True
        if pode_trocar and self.arquivo:
            log.warning("⚠ O relógio da gravação bagunçou neste trecho. Reabrindo o vídeo no mesmo ponto "
                        "sem parar a live...")
            threading.Thread(target=self._trocar_ou_reiniciar, daemon=True, name=nome_thread("troca")).start()
        elif pode_trocar:
            log.warning("⚠ O relógio do vídeo que vem %s bagunçou (isso deixa a live travando e com a voz "
                        "falhada). Trocando a conexão sem parar a live...", onde)
            threading.Thread(target=self._trocar_ou_reiniciar, daemon=True, name=nome_thread("troca")).start()
        else:
            log.warning("⚠ O relógio do vídeo que vem %s bagunçou (isso deixa a live travando e com a voz "
                        "falhada). Reconectando para corrigir...", onde)
            threading.Thread(target=self.parar, daemon=True, name=nome_thread("parar")).start()

    def _trocar_ou_reiniciar(self):
        if self._trocar("relógio do vídeo bagunçado") or self._parar.is_set():
            return
        with self._lock:
            if self._sem_troca:
                return
            self.precisa_reiniciar = True
        log.warning("Não deu para trocar sem parar; reconectando...")
        self.parar()

    def _ler_stderr_entrada(self, proc, seg):
        vigia = VigiaRelogio(limite=30)            # "Non-monotonic DTS" seguidos na entrada
        vigia_desc = VigiaRelogio(limite=8)        # gravação: correções de relógio em série = ritmo perdido
        ultima = ""
        for linha in proc.stderr:
            linha = linha.decode("utf-8", "replace").strip()
            if not linha or linha == ultima:
                continue
            if self.arquivo and "timestamp discontinuity" in linha:
                log.debug("entrada %d: %s", seg, linha)
                if vigia_desc.bagunçou() and seg == self._seg_ativo:
                    self._reiniciar_por_relogio(self._origem())
                continue
            if "Non-monotonic DTS" in linha or linha.startswith("Last message repeated"):
                # o vídeo "voltou no tempo": acontece de vez em quando; muitas seguidas = relógio bagunçado
                log.debug("entrada: %s", linha)
                if "Non-monotonic" in linha and vigia.bagunçou() and seg == self._seg_ativo:
                    self._reiniciar_por_relogio(self._origem())
                continue
            if (seg != 1 and seg != self._seg_ativo) or _ruido(linha):
                log.debug("entrada %d: %s", seg, linha)     # conexão nova/antiga, ou aviso sem importância
                continue
            if seg == 1 and ("Server returned 4" in linha or "HTTP error 4" in linha):
                self.link_recusado = True
            if self.link_recusado and self.erro_entrada:
                log.debug("entrada: %s", linha)    # o primeiro motivo já foi registrado
                continue
            log.log(logging.DEBUG if self.silencioso else logging.INFO, "entrada: %s", linha)
            self.erro_entrada = ultima = linha

    def _caminho_gravacao(self, seg):
        if not self.gravar_em:
            return None
        if callable(self.gravar_em):
            return self.gravar_em()
        if seg == 1:
            return Path(self.gravar_em)
        p = Path(self.gravar_em)
        return p.with_name(f"{p.stem}_trecho{seg}{p.suffix}")

    def _ler_ts(self, proc, seg):
        fd = proc.stdout
        gravacao = caminho = None
        recebeu = False
        try:
            while True:
                dados = fd.read1(65536)
                if not dados:
                    break
                with self._lock:
                    if seg != self._seg_ativo:
                        if seg != self._seg_novo:
                            continue                  # conexão antiga, já substituída
                        self._ativar(seg)
                if not recebeu:
                    recebeu = True
                    caminho = self._caminho_gravacao(seg)
                    if caminho:
                        try:
                            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
                            gravacao = open(caminho, "wb")
                            try:
                                onde = Path(caminho).relative_to(BASE)
                            except ValueError:
                                onde = Path(caminho)
                            log.info("💾 Gravando a live em: %s", onde)
                        except OSError as e:
                            log.warning("Não consegui criar o arquivo de gravação (%s). A live segue sem gravar.", e)
                            gravacao = None
                self.ultimo_dado = time.monotonic()     # usado para perceber pausa na origem
                if gravacao:
                    try:
                        gravacao.write(dados)       # cópia exata do que chegou da origem
                    except OSError as e:
                        log.warning("Gravação interrompida (%s). Disco cheio?", e)
                        gravacao.close()
                        gravacao = None
                with self._tem_dados:
                    self._buffer.append((time.monotonic(), seg, dados))
                    self._tem_dados.notify()
        except (OSError, ValueError):
            pass
        finally:
            if gravacao:
                vazio = gravacao.tell() == 0
                gravacao.close()
                if vazio:
                    try:
                        os.remove(caminho)
                    except OSError:
                        pass
            self._entrada_acabou(seg, recebeu)

    # -------------------------------------------------------- atraso -> processador do trecho

    def _escrever_ts(self):
        inicio = time.monotonic()
        enchendo = self.atraso > 0
        ultimo_aviso = 0.0
        seg_proc, conn = None, None
        try:
            while not self._parar.is_set():
                with self._tem_dados:
                    while not self._buffer and not self._fim_entrada and not self._parar.is_set():
                        self._tem_dados.wait(0.5)
                    if self._parar.is_set():
                        break
                    if not self._buffer:            # entrada acabou e o buffer esvaziou
                        break
                    chegada, seg, dados = self._buffer[0]
                    espera = chegada + self.atraso - time.monotonic()
                    if espera > 0:
                        if enchendo and time.monotonic() - ultimo_aviso >= 1:
                            cheio = min(self.atraso, time.monotonic() - inicio)
                            self.aviso("stats", f"Preparando o atraso: {cheio:.0f} de {self.atraso:.0f} segundos...")
                            ultimo_aviso = time.monotonic()
                        self._tem_dados.wait(min(espera, 0.05))
                        continue
                    self._buffer.popleft()
                enchendo = False
                if seg != seg_proc:
                    # começo de um trecho novo: o processador anterior termina e entra o do trecho novo
                    if conn is not None:
                        self._fechar_processador(conn, seg_proc)
                    conn = self._abrir_processador(seg)
                    seg_proc = seg
                    if conn is None:
                        break
                try:
                    conn.sendall(dados)
                except OSError:
                    break
        finally:
            if conn is not None:
                self._fechar_processador(conn, seg_proc)
            self._fechar_envio()
            self._fim.set()

    def _abrir_processador(self, seg):
        if self._parar.is_set():
            return None
        deslocamento = self._relogio.continuacao()
        self._relogio.novo_trecho()
        proc = subprocess.Popen(
            cmd_saida(self.cfg, self.ffmpeg, self._porta_ts, self.preview, deslocamento),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=self.flags)
        with self._lock:
            self._processadores[seg] = proc
        if self._parar.is_set():
            proc.kill()
            return None
        try:
            conn, _ = self._srv_ts.accept()
        except OSError:
            proc.kill()
            return None
        self._thread(lambda: self._ler_stderr_saida(proc, seg))
        self._repasses[seg] = self._thread(lambda: self._repassar(proc))
        self._thread(lambda: self._reenviar_mudo(proc, seg), "mudo")
        return conn

    def _fechar_processador(self, conn, seg):
        try:
            conn.shutdown(socket.SHUT_WR)       # fim do trecho: o processador termina e sai sozinho
        except OSError:
            pass
        conn.close()
        t = self._repasses.get(seg)
        if t:
            t.join(timeout=8)
        p = self._processadores.get(seg)
        if p and p.poll() is None:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def _repassar(self, proc):
        """Leva a saída do processador até o envio (abre o envio quando chega o primeiro vídeo)."""
        fd = proc.stdout
        while True:
            try:
                dados = fd.read1(65536)
            except (OSError, ValueError):
                break
            if not dados:
                break
            self._relogio.ver(dados)
            if self.arquivo:
                self._segurar_ritmo()
            conn = self._garantir_envio()
            if conn is None:
                break
            try:
                conn.sendall(dados)
            except OSError:
                break

    def _segurar_ritmo(self):
        """Reprise: nunca manda para a Shopee/OBS mais rápido que o tempo real. Se o vídeo adiantar
        (relógio da gravação com defeito), segura os dados até o relógio de parede alcançar."""
        r = self._relogio
        if r.maior is None or r.primeiro is None:
            return
        if self._ritmo0 is None:
            self._ritmo0 = time.monotonic()
            return
        while not self._parar.is_set():
            adiantado = (r.maior - r.primeiro) / 90000 - (time.monotonic() - self._ritmo0)
            if adiantado <= 3:
                return
            if not self._segurou:
                self._segurou = True
                log.warning("⚠ A gravação estava indo mais rápido que o normal (%.0fs adiantada). Segurando "
                            "para manter o ritmo certo na transmissão.", adiantado)
            time.sleep(min(adiantado - 2, 1.0))

    def _garantir_envio(self):
        if self._conn_envio is not None:
            return self._conn_envio
        if self._parar.is_set():
            return None
        envio = subprocess.Popen(cmd_envio(self.cfg, self.ffmpeg, self._porta_envio, self.destino),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 creationflags=self.flags)
        with self._lock:
            self.envio = envio
        if self._parar.is_set():
            envio.kill()
            return None
        self._thread(lambda: self._ler_stderr_envio(envio))
        try:
            self._conn_envio, _ = self._srv_envio.accept()
        except OSError:
            envio.kill()
            return None
        return self._conn_envio

    def _fechar_envio(self):
        conn = self._conn_envio
        if conn is None:
            return
        try:
            conn.shutdown(socket.SHUT_WR)       # acabou: o envio termina de mandar e fecha
        except OSError:
            pass
        conn.close()

    # -------------------------------------------------------- mudo

    def _escrever_stdin(self, proc, texto):
        with self._lock_stdin:
            try:
                proc.stdin.write(texto.encode("utf-8"))
                proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass

    def _enviar_mudo(self, seg, cmd):
        with self._lock:
            self._cmd_mudo[seg] = cmd
            proc = self._processadores.get(seg)
        if proc is not None and proc.poll() is None:
            self._escrever_stdin(proc, cmd)

    def _reenviar_mudo(self, proc, seg):
        """O processador só aceita comandos depois de montar os filtros: repete o último por uns segundos."""
        for _ in range(6):
            if self._parar.wait(1.0) or proc.poll() is not None:
                return
            cmd = self._cmd_mudo.get(seg)
            if cmd:
                self._escrever_stdin(proc, cmd)

    # -------------------------------------------------------- leitura dos registros do ffmpeg

    def _linhas(self, fluxo):
        resto = b""
        while True:
            bloco = fluxo.read1(4096) if hasattr(fluxo, "read1") else fluxo.read(4096)
            if not bloco:
                break
            resto += bloco
            partes = resto.replace(b"\r", b"\n").split(b"\n")
            resto = partes.pop()
            for p in partes:
                linha = p.decode("utf-8", "replace").strip()
                if linha:
                    yield linha

    def _ler_stderr_saida(self, proc, seg):
        """Registro do PROCESSADOR de um trecho (estatísticas, relógio bagunçado)."""
        vigia = VigiaRelogio(limite=12)    # "timestamp discontinuity" seguidos
        inicio = time.monotonic()
        ultimo_stats = 0.0
        ultima = ""
        try:
            for linha in self._linhas(proc.stderr):
                if linha.startswith("frame=") or ("time=" in linha and "speed=" in linha):
                    if time.time() - ultimo_stats >= 2:
                        self.aviso("stats", self.resumir_stats(linha))
                        ultimo_stats = time.time()
                elif "timestamp discontinuity" in linha or "buffers queued" in linha:
                    # o ffmpeg está corrigindo os tempos sem parar: a live trava. Muitas seguidas = trocar
                    log.debug("saída: %s", linha)
                    no_comeco = time.monotonic() - inicio < 20     # no começo é normal um pouco disso
                    if (not no_comeco and seg == self._seg_ativo
                            and ("buffers queued" in linha or vigia.bagunçou())):
                        self._reiniciar_por_relogio(self._origem())
                elif linha.startswith("[in#0") or "tcp://127.0.0.1" in linha or "Error opening input" in linha:
                    # problema na entrada do processador = o vídeo da origem não chegou
                    if seg == 1:
                        self._saida_sem_entrada = True
                    log.debug("saída: %s", linha)
                elif (_erro_de_envio(linha) or self._parar.is_set() or _ruido(linha)
                      or linha.startswith(("Enter command", "Command reply", "[pipe", "[out#0"))):
                    log.debug("saída: %s", linha)      # o envio é que conta para a conexão com o destino
                elif linha != ultima and linha != self.erro_saida:
                    log.info("saída: %s", linha)
                    ultima = linha
        except (OSError, ValueError):
            pass

    def _ler_stderr_envio(self, proc):
        """Registro do ENVIO (conexão com a Shopee ou com o OBS)."""
        try:
            for linha in self._linhas(proc.stderr):
                if _erro_de_envio(linha):
                    # A conexão com o destino caiu: encerramos para o motor tentar de novo.
                    if not self.shopee_recusou and not self._parar.is_set():
                        self.shopee_recusou = True
                        motivo = _traduzir(linha, self.nome_destino)
                        if self.para_obs:
                            log.warning("O envio para o OBS parou (%s).", motivo)
                        else:
                            log.warning("A Shopee encerrou a conexão (%s).", motivo)
                        self.erro_saida = motivo
                        threading.Thread(target=self.parar, daemon=True, name=nome_thread("parar")).start()
                elif (self.shopee_recusou or self._parar.is_set() or "discontinuity" in linha
                      or "Continuity check" in linha or "Packet corrupt" in linha
                      or "corrupt input packet" in linha or "Error during demuxing" in linha
                      or "Non-monotonic" in linha or linha.startswith("Last message repeated")):
                    log.debug("envio: %s", linha)  # emenda de trechos / motivo já registrado
                elif linha != self.erro_saida:
                    log.info("envio: %s", linha)
                    self.erro_saida = linha
        except (OSError, ValueError):
            pass

    # -------------------------------------------------------- reconhecimento de voz (um por trecho)

    def _voz(self, srv, seg):
        try:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
        finally:
            srv.close()
        fila = queue.Queue()
        censor = Censor(self.modelo, lambda cmd: self._enviar_mudo(seg, cmd), self.atraso, self.aviso,
                        alvo=self.alvo_mudo, destino=self.nome_destino, contador=self._total_mudos)

        def receber():
            bloco = TAXA_VOZ * 2 // 10                # 0,1 s de áudio
            resto = b""
            while True:
                try:
                    dados = conn.recv(65536)
                except OSError:
                    break
                if not dados:
                    break
                resto += dados
                while len(resto) >= bloco:
                    fila.put(resto[:bloco])
                    resto = resto[bloco:]
            fila.put(None)

        threading.Thread(target=receber, daemon=True, name=nome_thread("voz")).start()
        atrasado_avisado = False
        while True:
            dados = fila.get()
            if dados is None:
                break
            censor.alimentar(dados)     # (a conexão antiga também: o que ela já entregou ainda vai sair)
            if fila.qsize() > 30 and not atrasado_avisado:    # mais de 3 s atrasado
                log.warning("O reconhecimento de voz está ficando para trás (computador sobrecarregado). "
                            "Use 720p ou o modo 'copiar'.")
                atrasado_avisado = True
        conn.close()
