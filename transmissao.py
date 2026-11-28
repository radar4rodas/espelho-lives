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
import hashlib
import json
import logging
import os
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import contador
from mudo import (DESCRICAO_MUDO, GRAMATICAS, TAXA_VOZ, achar_palavras,  # noqa: F401
                  carregar_modelo, _mesclar)

log = logging.getLogger("espelho")

BASE = Path(__file__).resolve().parent


def nome_thread(sufixo):
    """Nome para uma thread nova que herda o "lado" da atual (ts:... ou st:...).
    O painel usa esse prefixo para saber de qual transmissão vem cada mensagem do registro."""
    atual = threading.current_thread().name
    lado = atual.split(":", 1)[0] if ":" in atual else ""
    return f"{lado}:{sufixo}" if lado else sufixo


def _ouvir():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    s.settimeout(30)
    return s, s.getsockname()[1]


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
          # o próprio ffmpeg corrige (soma 1 no tempo); não muda nada na live, só enchia a janela
          "Non-monotonic DTS", "may result in incorrect timestamps",
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


def _erro_de_imagem(linha):
    """Mensagem do decodificador de vídeo: um pedaço da imagem que chegou não pôde ser decodificado."""
    return linha.startswith(("[hevc @", "[h264 @"))


class VigiaImagem:
    """Percebe quando a imagem TRAVOU de vez (e não foi só um tropeço da internet).

    Acontece quando quem faz a live troca de codificador no meio dela (ex.: o vídeo passa de 24 para
    30 quadros por segundo). O servidor manda as "instruções" novas do vídeo (VPS/SPS/PPS) só uma vez,
    e o ffmpeg, ao copiar o vídeo HEVC do FLV para o MPEG-TS, continua repetindo as antigas. Daí em
    diante nenhum quadro decodifica: no TikTok a tela congela e só a voz passa, até o fim da live.
    Uma conexão nova recebe as instruções certas logo no começo, e a imagem volta.

    Tropeço normal: erros por 1 ou 2 segundos, até o próximo quadro-chave. Travado: erros que não
    param, espalhados por vários segundos."""

    def __init__(self, duracao=5.0, janela=12.0, minimo=3):
        self.duracao, self.janela, self.minimo = duracao, janela, minimo
        self.marcas = collections.deque()

    def travou(self, agora=None):
        agora = time.monotonic() if agora is None else agora
        self.marcas.append(agora)
        while self.marcas and agora - self.marcas[0] > self.janela:
            self.marcas.popleft()
        return len(self.marcas) >= self.minimo and self.marcas[-1] - self.marcas[0] >= self.duracao

    def zerar(self):
        self.marcas.clear()


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
        # derruba a conexão. dts_delta_threshold 1 = emenda qualquer pulo maior que 1 s.
        cmd += ["-dts_delta_threshold", "1", "-fflags", "+genpts",
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


# ms que a imagem é atrasada no OBS para casar com o som que vai pelo CABLE (medidos). Negativo = atrasa a
# VOZ (quando ela chega antes da boca mesmo sem atrasar a imagem).
# Depende do formato do vídeo que vem da Shopee:
AJUSTE_VOZ_HEVC = 300     # HEVC com quadros B (algumas lojas da Shopee). Medido em 19/11.
AJUSTE_VOZ_H264 = 0       # H.264 sem quadros B (outras lojas). Medido em 19/11 (+7 ms com 0).
AJUSTE_VOZ_PADRAO = AJUSTE_VOZ_HEVC      # formato ainda desconhecido / valor fixo sem número
AJUSTE_VOZ_REPRISE = 50   # reprise: vídeo refeito em H.264. Medido em 18/11 com flash + bipe.
AJUSTE_POR_FORMATO = {"hevc": AJUSTE_VOZ_HEVC, "h264": AJUSTE_VOZ_H264}
# Obs.: o "audio buffering" que aparece no registro do OBS NÃO atrasa esse som (medido em 18/11:
# compensar ele adiantava a voz 1 s).


def _ms(valor, padrao, minimo=-1000, maximo=2000):
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return padrao


def chave_ajuste_medido(formato):
    """Onde fica guardado, no config, o último valor que o vigia achou para este formato de vídeo."""
    return f"ajuste_voz_medido_{formato}" if formato in AJUSTE_POR_FORMATO else "ajuste_voz_medido_outro"


def ajuste_voz_efetivo(cfg, reprise=False, formato=None):
    """Shopee -> TikTok: quantos ms a imagem é atrasada para a voz casar com a boca (negativo = a voz é que
    é atrasada). Automático (padrão): o último valor que o vigia da sincronia achou para esse formato de
    vídeo (HEVC ou H.264); sem medição ainda, o valor medido para o formato."""
    if cfg.get("sincronia_auto", True):
        if reprise:
            return AJUSTE_VOZ_REPRISE
        padrao = AJUSTE_POR_FORMATO.get(formato, AJUSTE_VOZ_PADRAO)
        return _ms(cfg.get(chave_ajuste_medido(formato), padrao), padrao)
    return _ms(cfg.get("ajuste_voz_ms", AJUSTE_VOZ_PADRAO), AJUSTE_VOZ_PADRAO, minimo=0)


_TIPOS_VIDEO = {0x1B: "h264", 0x24: "hevc"}


def formato_do_ts(dados):
    """Formato do vídeo (h264/hevc) lendo a tabela PMT do MPEG-TS, ou None se ainda não apareceu."""
    pmts = set()
    for i in range(0, len(dados) - 187, 188):
        pkt = dados[i:i + 188]
        if pkt[0] != 0x47 or not (pkt[1] & 0x40):
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        p = 4
        if pkt[3] & 0x20:                       # campo de adaptação
            p += 1 + pkt[4]
        if p >= 188:
            continue
        p += 1 + pkt[p]                         # pointer_field
        if p + 12 > 188:
            continue
        tabela = pkt[p]
        tam = ((pkt[p + 1] & 0x0F) << 8) | pkt[p + 2]
        fim = min(188, p + 3 + tam - 4)
        if pid == 0 and tabela == 0x00:         # PAT: onde está a PMT
            q = p + 8
            while q + 4 <= fim:
                if ((pkt[q] << 8) | pkt[q + 1]) != 0:
                    pmts.add(((pkt[q + 2] & 0x1F) << 8) | pkt[q + 3])
                q += 4
        elif tabela == 0x02 and (pid in pmts or not pmts):   # PMT: os tipos dos fluxos
            q = p + 12 + (((pkt[p + 10] & 0x0F) << 8) | pkt[p + 11])
            while q + 5 <= fim:
                if pkt[q] in _TIPOS_VIDEO:
                    return _TIPOS_VIDEO[pkt[q]]
                q += 5 + (((pkt[q + 3] & 0x0F) << 8) | pkt[q + 4])
    return None


_FONTE_ETIQUETA =Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf"


ETIQUETA_PADRAO = "REPRISE"
ETIQUETA_MAX = 40


def texto_etiqueta(cfg):
    """Texto da etiqueta da reprise escolhido no painel ("" = desligada)."""
    if not cfg.get("etiqueta_ligada", True):
        return ""
    texto = " ".join(str(cfg.get("etiqueta_texto") or "").split())[:ETIQUETA_MAX]
    return texto or ETIQUETA_PADRAO


def _caminho_filtro(caminho):
    return "'" + Path(caminho).as_posix().replace(":", r"\:") + "'"


def filtro_etiqueta(texto=ETIQUETA_PADRAO):
    """Etiqueta pequena no canto de cima, à direita, na reprise (deixa claro que é gravação).
    O texto vai num arquivo: assim aspas, dois-pontos, % e barras escritos no painel não quebram o filtro do ffmpeg."""
    arquivo = Path(tempfile.gettempdir()) / (
        "espelho_etiqueta_" + hashlib.sha1(texto.encode("utf-8")).hexdigest()[:10] + ".txt")
    if not arquivo.exists():
        arquivo.write_text(texto, encoding="utf-8")
    fonte = ("fontfile=" + _caminho_filtro(_FONTE_ETIQUETA) + ":") if _FONTE_ETIQUETA.exists() else ""
    # pequena e bem no canto de cima, para não parecer nome de produto
    return (f"drawtext={fonte}textfile={_caminho_filtro(arquivo)}:expansion=none:fontsize=h*0.0125:"
            "fontcolor=white@0.85:box=1:boxcolor=black@0.45:boxborderw=5:x=w-tw-w*0.03:y=h*0.02")


def _codificador(ffmpeg, cfg):
    if nvenc_disponivel(ffmpeg):
        # placa NVIDIA: quase não usa o processador
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr", "-bf", "0"]
    return ["-c:v", "libx264", "-preset", cfg.get("preset_x264", "veryfast"), "-tune", "zerolatency",
            "-sc_threshold", "0"]


def cmd_saida(cfg, ffmpeg, porta_ts, preview, deslocamento=0.0, etiqueta="", ajuste_voz=None, tela=None):
    """PROCESSADOR de um trecho: aplica o mudo (e recodifica, se for o caso) e devolve MPEG-TS no stdout.
    'deslocamento' faz os tempos deste trecho continuarem de onde o trecho anterior parou.
    etiqueta="texto" (reprise): escreve o texto no vídeo, o que obriga a recodificar ("" = sem etiqueta).
    tela (contador.TelaFFmpeg): desenha o contador "Oferta termina em" (só para a Shopee; obriga a recodificar)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats", "-y",
           "-analyzeduration", "1500000",
           "-f", "mpegts", "-i", f"tcp://127.0.0.1:{porta_ts}",
           "-map", "0:v:0", "-map", "0:a:0"]
    final = ["-f", "mpegts", "-output_ts_offset", f"{deslocamento:.3f}", "pipe:1"]
    # volume@mute recebe, em tempo real, a lista de trechos a silenciar
    filtro_audio = "volume@mute=volume=1:eval=frame,aresample=44100"
    ab = f"{int(cfg['bitrate_audio_kbps'])}k"
    marca = ("," + filtro_etiqueta(etiqueta)) if etiqueta else ""
    # prévia no painel (1 quadro por segundo), com a etiqueta quando houver
    vf_previa = ("fps=1," + filtro_etiqueta(etiqueta) + ",scale=360:-2") if etiqueta else "fps=1,scale=360:-2"
    if cfg.get("saida") == "obs":
        # Para o OBS (live de vendas do TikTok Shop): o vídeo vai como veio (o OBS decodifica),
        # só o áudio é refeito por causa do mudo. Leve para o computador.
        # Na reprise com etiqueta o vídeo é refeito para levá-la.
        if etiqueta:
            # enc_time_base 1/90000: sem isso o codificador conta o tempo em quadros (1/30 s) e o atraso
            # da imagem abaixo (ex.: 50 ms = 1,5 quadro) arredonda para dois quadros com o mesmo tempo,
            # e o ffmpeg enche a janela de "Non-monotonic DTS".
            cmd += ["-vf", "format=yuv420p" + marca] + _codificador(ffmpeg, cfg) + [
                    "-profile:v", "main", "-pix_fmt", "yuv420p", "-enc_time_base:v", "1:90000",
                    "-b:v", "4000k", "-maxrate", "4000k", "-bufsize", "8000k", "-g", "60", "-keyint_min", "60"]
        else:
            cmd += ["-c:v", "copy"]
        # O som do OBS vai para o TikTok pelo monitoramento (CABLE Input), que chega um pouco depois da
        # câmera virtual. Atrasa a imagem na mesma medida (ou, se o valor for negativo, atrasa a voz).
        ajuste = ajuste_voz_efetivo(cfg) if ajuste_voz is None else ajuste_voz
        if ajuste > 0:
            cmd += ["-bsf:v", f"setts=ts=TS+{ajuste / 1000:.3f}/TB"]
        cmd += ["-af", "volume@mute=volume=1:eval=frame,aresample=48000", "-c:a", "aac", "-b:a", ab, "-ac", "2"]
        if ajuste < 0:
            cmd += ["-bsf:a", f"setts=ts=TS+{-ajuste / 1000:.3f}/TB"]
        cmd += final
        if preview:
            cmd += ["-map", "0:v:0", "-vf", vf_previa, "-q:v", "6",
                    "-update", "1", "-atomic_writing", "1", preview]
        return cmd
    if cfg["modo"] == "copiar" and not etiqueta and tela is None:
        cmd += ["-c:v", "copy"]
    else:
        larg, alt = cfg["resolucao"].lower().split("x")
        fps = int(cfg["fps"])
        vb = int(cfg["bitrate_video_kbps"])
        # a etiqueta vai antes das faixas pretas, para ficar dentro da imagem
        cadeia = (f"scale={larg}:{alt}:force_original_aspect_ratio=decrease{marca},"
                  f"pad={larg}:{alt}:(ow-iw)/2:(oh-ih)/2,fps={fps}")
        # o contador vai por cima de tudo (posição medida na imagem final, já com as faixas)
        cmd += ["-vf", tela.filtro(cadeia) if tela is not None else cadeia]
        if tela is not None:
            # o ffmpeg lê UM comando do stdin por volta do laço, e o laço espera o intervalo das estatísticas
            # (0,5 s por padrão): numa troca de fase do contador vão 4 comandos e formava fila. 0,1 s resolve.
            cmd[1:1] = ["-stats_period", "0.1"]
        cmd += _codificador(ffmpeg, cfg)
        cmd += ["-profile:v", "main", "-pix_fmt", "yuv420p",
                "-b:v", f"{vb}k", "-maxrate", f"{vb}k", "-bufsize", f"{vb * 2}k",
                "-g", str(fps * 2), "-keyint_min", str(fps * 2)]
    # o envio codifica o áudio de novo para o FLV: aqui vai com folga de qualidade
    cmd += ["-af", filtro_audio, "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += final
    if preview:
        cmd += ["-map", "0:v:0", "-vf", vf_previa, "-q:v", "6",
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
        final = self.rec.AcceptWaveform(dados)
        if final:
            palavras = json.loads(self.rec.Result()).get("result", [])
        else:
            palavras = json.loads(self.rec.PartialResult()).get("partial_result", [])
        for ini, fim, nome in achar_palavras(palavras, final, self.alvo):
            self._mutar(ini, fim, nome)

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
                 arquivo=None, repetir=False, gravar_em=None, renovar=None, silencioso=False, inicio=0.0,
                 salvar_ajuste_voz=None):
        self.cfg = cfg
        self.inicio = max(0.0, float(inicio or 0))   # reprise: começa deste ponto do vídeo (retomada após queda)
        self.posicao_ao_cair = None     # reprise: ponto do vídeo que a Shopee estava mostrando quando acabou
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
        self._trocas_imagem = collections.deque()   # trocas feitas porque a imagem travou
        # vigia da sincronia da voz (Shopee -> TikTok, live): mede de tempos em tempos e corrige sozinho
        self._vigia_sincronia = self.para_obs and not arquivo
        self._recentes = collections.deque()        # (momento, trecho, dados) que foram para o OBS
        self._seg_ajuste = None                     # trecho que está saindo com self._ajuste_voz
        self._formato = {}                          # trecho -> formato do vídeo (h264/hevc)
        self.formato_video = None                   # formato do trecho que está saindo
        self.salvar_ajuste_voz = salvar_ajuste_voz  # guarda no config.json o valor que o vigia achou
        self._avisou_imagem = False
        self._processadores = {}        # trecho -> processo que aplica o mudo
        self._repasses = {}             # trecho -> thread que leva a saída do processador ao envio
        self._cmd_mudo = {}             # trecho -> último comando de mudo
        self._total_mudos = [0]
        self._relogio = RelogioTS()
        self._ajuste_voz = None         # OBS: atraso da imagem (ms) com que o trecho atual está saindo
        self.envio = None
        self._conn_envio = None
        self._srv_ts = self._srv_envio = None
        self._porta_ts = self._porta_envio = None
        self._threads = []
        # contador "Oferta termina em" desenhado no vídeo (só TikTok -> Shopee; no TikTok quem desenha é o OBS)
        self._tela = None
        lado_contador = cfg.get("contador_lado")
        if lado_contador and not self.para_obs:
            try:
                larg, alt = str(cfg.get("resolucao", "720x1280")).lower().split("x")
                self._tela = contador.TelaFFmpeg(lado_contador, int(larg), int(alt))
            except Exception as e:
                log.warning("Não consegui preparar o contador na tela (%s). A live segue sem ele.", e)

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

    def reaplicar(self, motivo):
        """Mudou um ajuste do vídeo (ex.: sincronia da voz): abre uma conexão nova, que já sai com o ajuste
        novo, e troca sem parar a live (no máximo ~1 s de imagem parada)."""
        if self._parar.is_set():
            return
        self._thread(lambda: self._trocar(motivo) or log.info(
            "O ajuste vale a partir da próxima reconexão (não deu para trocar agora)."), "ajuste")

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
            self._iniciar_entrada(self.url, self.cabecalhos, 1, inicio=self.inicio)
            self._thread(self._escrever_ts)
            if self._vigia_sincronia:
                self._thread(self._vigiar_sincronia, "sincronia")
            if self._tela is not None:
                self._thread(self._vigiar_contador, "contador")
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
            self.posicao_ao_cair = self.posicao()
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

    def posicao(self):
        """Reprise: em que ponto do vídeo (s) está o que o destino está mostrando agora (já descontado o atraso)."""
        if not self.arquivo:
            return None
        with self._lock:
            quando_pos = self._inicio_seg.get(self._seg_ativo)
        if not quando_pos:
            return None
        quando, pos = quando_pos
        return max(0.0, pos + (time.monotonic() - quando) - self.atraso)

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

    # -------------------------------------------------------- vigia da sincronia da voz

    SINCRONIA_PRIMEIRA = 25      # s depois de o atraso encher: primeira medição
    SINCRONIA_INTERVALO = 180    # depois, a cada 3 minutos
    # Voz adiantada se percebe bem mais cedo do que voz atrasada (a partir de ~45 ms adiantada; atrasada
    # só passa a incomodar perto de 100 ms). Por isso o alvo é a voz levemente depois da boca.
    SINCRONIA_ALVO = 15          # ms
    SINCRONIA_ADIANTADA = -30    # ms: voz mais adiantada que isso = corrige
    SINCRONIA_ATRASADA = 60      # ms: voz mais atrasada que isso = corrige
    SINCRONIA_CONFIANCA = (0.7, 0.6)   # mínimo para confiar na medição (imagem, voz)

    def _original_recente(self, minimo=20.0):
        """O que foi para o OBS nos últimos ~50 s, se for tudo do trecho que está saindo agora
        (logo depois de uma troca de conexão os tempos mudam; aí espera encher de novo)."""
        itens = list(self._recentes)
        if not itens or itens[-1][1] != self._seg_ajuste:
            return None
        seg = itens[-1][1]
        i = len(itens)
        while i > 0 and itens[i - 1][1] == seg:
            i -= 1
        itens = itens[i:]
        if itens[-1][0] - itens[0][0] < minimo:
            return None
        return b"".join(d for _, _, d in itens)

    def _vigiar_sincronia(self):
        """Shopee -> TikTok: de tempos em tempos confere se a voz que sai do OBS casa com a boca
        (sincronia.medir) e, se estiver fora em duas medições seguidas, corrige o atraso da imagem
        sozinho, sem parar a live. Só no modo Automático."""
        import sincronia
        espera = self.atraso + self.SINCRONIA_PRIMEIRA
        anterior = None
        while not self._parar.wait(espera):
            espera = self.SINCRONIA_INTERVALO
            if not self.cfg.get("sincronia_auto", True) or self._trocando or self._sem_troca:
                anterior = None
                continue
            if self._original_recente() is None:      # trocou a conexão há pouco: espera encher
                anterior, espera = None, 30
                continue
            ajuste, seg = self._ajuste_voz, self._seg_ajuste
            try:
                r = sincronia.medir(self.ffmpeg, self._original_recente)
            except Exception as e:      # noqa: BLE001 - medir nunca pode atrapalhar a live
                log.debug("sincronia: %s", e)
                r = None
            if self._parar.is_set():
                break
            ci, cv = self.SINCRONIA_CONFIANCA
            if not r or r["conf_imagem"] < ci or r["conf_voz"] < cv or seg != self._seg_ajuste:
                log.debug("Sincronia: medição sem confiança, tento de novo (%s).", r)
                anterior, espera = None, 60
                continue
            voz = r["voz_ms"]
            if self.SINCRONIA_ADIANTADA <= voz <= self.SINCRONIA_ATRASADA:
                log.info("🎚 Sincronia da voz conferida: certa (%+d ms).", voz)
                anterior = None
                continue
            if anterior is None or abs(anterior - voz) > 80 or (anterior > 0) != (voz > 0):
                # fora: confirma com mais uma medição antes de mexer
                log.debug("Sincronia: voz %+d ms; confirmando...", voz)
                anterior, espera = voz, 20
                continue
            medido = round((anterior + voz) / 2)
            anterior = None
            novo = _ms(ajuste + max(-1000, min(1000, medido - self.SINCRONIA_ALVO)), ajuste)
            if novo == ajuste:
                continue
            formato = self.formato_video
            self.cfg[chave_ajuste_medido(formato)] = novo
            if self.salvar_ajuste_voz:
                try:
                    self.salvar_ajuste_voz(chave_ajuste_medido(formato), novo)
                except Exception as e:  # noqa: BLE001
                    log.debug("salvar ajuste da voz: %s", e)
            como = (f"imagem atrasada em {novo} ms" if novo >= 0 else f"voz atrasada em {-novo} ms")
            log.info("🎚 Sincronia da voz: estava chegando %d ms %s da imagem. Corrigindo sozinho, sem parar "
                     "a live (%s; vídeo %s)...", abs(medido), "depois" if medido > 0 else "antes", como,
                     (formato or "?").upper())
            self.reaplicar("sincronia automática da voz")
            espera = self.atraso + self.SINCRONIA_PRIMEIRA

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
            quando, pos = self._inicio_seg.get(seg, (time.monotonic(), 0))
            if tentar and self.repetir and not self._parar.is_set():
                if time.monotonic() - quando < 3 and pos < 1:   # retomado perto do fim: só recomeça
                    log.warning("O vídeo terminou logo depois de começar (arquivo com problema?). Parei o loop.")
                else:
                    self._trocar("fim do vídeo", volta=True)
        elif tentar and not self._parar.is_set():
            self._trocar(f"a conexão {self._origem()} caiu")
        self._verificar_fim()

    def _reiniciar_por_relogio(self, onde):
        self._corrigir_origem(f"O relógio do vídeo que vem {onde} bagunçou (isso deixa a live travando e "
                              "com a voz falhada).", "relógio do vídeo bagunçado")

    MAX_TROCAS_IMAGEM = 3       # trocas por imagem travada dentro de JANELA_TROCAS_IMAGEM
    JANELA_TROCAS_IMAGEM = 600.0

    def _imagem_travada(self):
        """A imagem que vem da origem parou de decodificar (só a voz passa). Conexão nova resolve."""
        agora = time.monotonic()
        with self._lock:
            if self.precisa_reiniciar or self._parar.is_set() or self._trocando:
                return
            if self.arquivo:
                # reprise: o defeito está gravado no arquivo; reabrir não adianta
                if not self._avisou_imagem:
                    self._avisou_imagem = True
                    log.warning("⚠ Esta gravação tem um trecho com a imagem com defeito (foi gravada assim). "
                                "Nesse trecho a imagem pode ficar parada enquanto a voz continua.")
                return
            trocas = self._trocas_imagem
            while trocas and agora - trocas[0] > self.JANELA_TROCAS_IMAGEM:
                trocas.popleft()
            if len(trocas) >= self.MAX_TROCAS_IMAGEM:
                # conexões novas não resolveram: não fica reconectando sem parar
                if not self._avisou_imagem:
                    self._avisou_imagem = True
                    log.warning("⚠ A imagem que vem %s continua chegando com defeito mesmo com conexões novas "
                                "(tentei %d vezes). Se a tela do TikTok estiver congelada, pare e inicie de novo.",
                                self._origem(), len(trocas))
                return
            trocas.append(agora)
        self._corrigir_origem(f"A imagem que vem {self._origem()} travou (só a voz está passando): quem está "
                              "fazendo a live trocou a configuração do vídeo.", "imagem travada")

    def _corrigir_origem(self, problema, motivo):
        with self._lock:
            if self.precisa_reiniciar or self._parar.is_set() or self._trocando:
                return
            pode_trocar = bool(self.renovar) and not self._sem_troca
            if not pode_trocar:
                self.precisa_reiniciar = True
        if pode_trocar:
            log.warning("⚠ %s Trocando a conexão sem parar a live...", problema)
            threading.Thread(target=lambda: self._trocar_ou_reiniciar(motivo), daemon=True,
                             name=nome_thread("troca")).start()
        else:
            log.warning("⚠ %s Reconectando para corrigir...", problema)
            threading.Thread(target=self.parar, daemon=True, name=nome_thread("parar")).start()

    def _trocar_ou_reiniciar(self, motivo="relógio do vídeo bagunçado"):
        if self._trocar(motivo) or self._parar.is_set():
            return
        with self._lock:
            if self._sem_troca:
                return
            self.precisa_reiniciar = True
        log.warning("Não deu para trocar sem parar; reconectando...")
        self.parar()

    def _ler_stderr_entrada(self, proc, seg):
        vigia = VigiaRelogio(limite=30)            # "Non-monotonic DTS" seguidos na entrada
        ultima = ""
        for linha in proc.stderr:
            linha = linha.decode("utf-8", "replace").strip()
            if not linha or linha == ultima:
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
                if seg not in self._formato:
                    f = formato_do_ts(dados)
                    if f:
                        self._formato[seg] = f
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
                if self._vigia_sincronia:
                    # o que acabou de ir para o OBS: o vigia da sincronia compara com o que sai dele
                    agora = time.monotonic()
                    self._recentes.append((agora, seg, dados))
                    while self._recentes and agora - self._recentes[0][0] > 50:
                        self._recentes.popleft()
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
        if self.para_obs:
            self.formato_video = self._formato.get(seg)
            self._ajuste_voz = ajuste_voz_efetivo(self.cfg, reprise=bool(self.arquivo), formato=self.formato_video)
            self._seg_ajuste = seg
        proc = subprocess.Popen(
            cmd_saida(self.cfg, self.ffmpeg, self._porta_ts, self.preview, deslocamento,
                      etiqueta=texto_etiqueta(self.cfg) if self.arquivo else "", ajuste_voz=self._ajuste_voz, tela=self._tela),
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
            conn = self._garantir_envio()
            if conn is None:
                break
            try:
                conn.sendall(dados)
            except OSError:
                break

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

    def _vigiar_contador(self):
        """Mantém o contador do vídeo em dia: reescreve o tempo e troca o fundo quando muda a fase."""
        while not self._parar.wait(0.25):
            try:
                cmds = self._tela.comandos()
            except Exception as e:
                log.debug("contador: %s", e)
                continue
            if not cmds:
                continue
            with self._lock:
                procs = [p for p in self._processadores.values() if p.poll() is None]
            for p in procs:
                self._escrever_stdin(p, "".join(cmds))

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
        imagem = VigiaImagem()             # erros do decodificador que não param = imagem travada
        inicio = time.monotonic()
        ultimo_stats = 0.0
        ultima = ""
        try:
            for linha in self._linhas(proc.stderr):
                if linha.startswith("frame=") or ("time=" in linha and "speed=" in linha):
                    if time.time() - ultimo_stats >= 2:
                        self.aviso("stats", self.resumir_stats(linha))
                        ultimo_stats = time.time()
                elif _erro_de_imagem(linha):
                    # um por linha encheria o registro (eram milhares por live): só no modo detalhado
                    log.debug("saída: %s", linha)
                    no_comeco = time.monotonic() - inicio < 8      # começar no meio do vídeo dá alguns
                    if not no_comeco and seg == self._seg_ativo and imagem.travou():
                        imagem.zerar()
                        self._imagem_travada()
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
