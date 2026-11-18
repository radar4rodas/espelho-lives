"""
Espelhamento de live: TikTok -> Shopee Live

Motor usado pela janela (app.py). Também funciona sozinho no terminal:
    python espelho.py
e pergunta o @ do TikTok e os dados da Shopee.
"""

import http.cookiejar
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Falta o pacote yt-dlp. Rode: python -m pip install --user -U yt-dlp")
    sys.exit(1)

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
LOG = BASE / "espelho.log"
PASTA_GRAVACOES = BASE / "gravacoes"
# cada sentido grava na sua pasta: "ts" = TikTok -> Shopee, "st" = Shopee -> TikTok
PASTAS_GRAVACAO = {
    "ts": PASTA_GRAVACOES / "TikTok para Shopee",
    "st": PASTA_GRAVACOES / "Shopee para TikTok",
}


def pasta_gravacao(sentido):
    return PASTAS_GRAVACAO.get(sentido, PASTAS_GRAVACAO["ts"])


def migrar_gravacoes():
    """Cria as duas pastas e move os vídeos antigos (soltos em 'gravacoes') para 'TikTok para Shopee'."""
    for p in PASTAS_GRAVACAO.values():
        p.mkdir(parents=True, exist_ok=True)
    movidos = 0
    for p in PASTA_GRAVACOES.iterdir():
        if p.is_file() and p.suffix.lower() in EXTENSOES_VIDEO and time.time() - p.stat().st_mtime > 30:
            destino = PASTAS_GRAVACAO["ts"] / p.name
            if not destino.exists():
                try:
                    p.rename(destino)
                    movidos += 1
                except OSError:
                    pass
    return movidos

AVANCADO = {
    "modo": "recodificar",           # "recodificar" (mais compatível) ou "copiar" (leve, sem perda)
    "resolucao": "720x1280",
    "fps": 30,
    "bitrate_video_kbps": 2500,
    "bitrate_audio_kbps": 128,
    "preset_x264": "veryfast",
    "intervalo_verificacao_seg": 20,
    "atraso_seg": 7,                 # atraso da Shopee em relação ao TikTok
    "mutar_palavras": True,          # mutar "tiktok" e "tiktok shop" automaticamente
    "gravar": False,                 # salvar cada live na pasta "gravacoes"
    "encerrar_shopee": False,        # finalizar a transmissão na Shopee quando o TikTok acabar
    "ffmpeg": "",                    # caminho do ffmpeg.exe; vazio = procurar sozinho
}

QUALIDADES = {
    "720p (recomendado)": ("720x1280", 2500),
    "1080p (internet boa)": ("1080x1920", 4500),
}

log = logging.getLogger("espelho")


# ---------------------------------------------------------------- utilidades

def configurar_log(tela=True):
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    log.setLevel(logging.INFO)
    arquivo = logging.FileHandler(LOG, encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%d/%m %H:%M:%S"))
    log.addHandler(arquivo)
    if tela:
        t = logging.StreamHandler(sys.stdout)
        t.setFormatter(fmt)
        log.addHandler(t)


def ler_config():
    try:
        dados = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        dados = {}
    except Exception as e:
        log.warning("config.json inválido, usando padrões (%s)", e)
        dados = {}
    return {**AVANCADO, **dados}


def salvar_config(cfg):
    # A chave da Shopee muda a cada live e não é guardada.
    # O sessionid do TikTok só é guardado se a pessoa pediu ("lembrar_sessionid").
    dados = {k: v for k, v in cfg.items() if k != "shopee_chave"}
    if not dados.get("lembrar_sessionid"):
        dados.pop("tiktok_sessionid", None)
    CONFIG.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def limpar_sessionid(texto):
    """Aceita só o valor ou 'sessionid=valor; ...' copiado do navegador."""
    t = (texto or "").strip().strip('"').strip()
    m = re.search(r"sessionid=([^;\s]+)", t)
    if m:
        t = m.group(1)
    return t.rstrip(";").strip()


def validar_sessionid(sid):
    if sid and not re.fullmatch(r"[A-Za-z0-9_%\-]{10,200}", sid):
        return "O sessionid parece inválido. Copie só o valor do cookie 'sessionid' do tiktok.com."
    return None


def limpar_usuario(texto):
    """Aceita '@fulano', 'fulano' ou o link do perfil/live."""
    t = (texto or "").strip()
    m = re.search(r"tiktok\.com/@([^/?#\s]+)", t)
    if m:
        t = m.group(1)
    return t.lstrip("@").strip()


def montar_destino(url, chave):
    url, chave = (url or "").strip(), (chave or "").strip()
    if not chave:          # a Shopee às vezes entrega a URL já com a chave
        return url
    return url.rstrip("/") + "/" + chave.lstrip("/")


def validar(usuario, url):
    if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", usuario or ""):
        return "Informe o @ do perfil do TikTok (só letras, números, ponto e _)."
    if not (url or "").strip().lower().startswith(("rtmp://", "rtmps://")):
        return "A URL do servidor da Shopee deve começar com rtmp:// (copie da tela da live na Shopee)."
    return None


def achar_ffmpeg(cfg):
    local = os.environ.get("LOCALAPPDATA", "")
    candidatos = [cfg.get("ffmpeg", "").strip(), shutil.which("ffmpeg"),
                  os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    pacotes = Path(local, "Microsoft", "WinGet", "Packages")
    if pacotes.is_dir():
        candidatos += sorted(str(p) for p in pacotes.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
    for c in candidatos:
        if c and Path(c).is_file():
            return c
    return None


# ---------------------------------------------------------------- TikTok

class _Silencioso:
    """Evita que o yt-dlp imprima 'not currently live' a cada verificação."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): log.debug(msg)


def _cookie_tiktok(nome, valor):
    return http.cookiejar.Cookie(
        version=0, name=nome, value=valor, port=None, port_specified=False,
        domain=".tiktok.com", domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True, expires=int(time.time()) + 86400 * 30,
        discard=False, comment=None, comment_url=None, rest={"HttpOnly": None})


_aviso_login = {"feito": False}


UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")
# melhor primeiro; "ao" = só áudio, nunca usar
ORDEM_QUALIDADE = ["origin", "uhd", "hd", "sd", "ld"]
ORDEM_WEBCAST = ["FULL_HD1", "HD1", "SD1", "SD2"]

# Motivo da última consulta, para o registro mostrar o que de fato aconteceu
motivo = {"texto": ""}
# Outros links da mesma live, para tentar se o primeiro for recusado
alternativas = {"lista": []}


def _via_api(usuario, sessionid):
    """Consulta direta à API de lives do TikTok (imitando o Chrome).

    Retorna ("ao_vivo", (url, cabecalhos)), ("offline", None) ou ("erro", "mensagem").
    """
    try:
        from curl_cffi import requests as creq
    except ImportError:
        return "erro", "componente curl_cffi não instalado"
    try:
        s = creq.Session(impersonate="chrome")
        if sessionid:
            s.cookies.set("sessionid", sessionid, domain=".tiktok.com")
        r = s.get("https://www.tiktok.com/api-live/user/room/",
                  params={"aid": "1988", "uniqueId": usuario, "sourceType": "54"}, timeout=20)
        if r.status_code != 200:
            return "erro", f"TikTok respondeu HTTP {r.status_code}"
        d = r.json()
    except Exception as e:
        return "erro", f"falha de conexão com o TikTok ({type(e).__name__})"

    if d.get("statusCode") not in (0, None):
        return "erro", f"TikTok respondeu: {d.get('message') or d.get('statusCode')}"
    dados = d.get("data") or {}
    if not dados.get("user"):
        return "erro", f"perfil @{usuario} não encontrado no TikTok"
    sala = dados.get("liveRoom") or {}
    if sala.get("status") != 2:
        return "offline", None

    # Sem User-Agent de Chrome: alguns servidores do TikTok devolvem 404 quando o ffmpeg
    # se apresenta como Chrome (a "assinatura" de rede não bate). Só o Referer basta.
    cab = {"Referer": "https://www.tiktok.com/"}
    links = []
    # 1) links que vêm junto com a sala (FLV, melhor qualidade primeiro; "ao" = só áudio)
    hls = []
    try:
        bruto = ((sala.get("streamData") or {}).get("pull_data") or {}).get("stream_data")
        qualidades = (json.loads(bruto).get("data") or {}) if bruto else {}
        for q in ORDEM_QUALIDADE + [k for k in qualidades if k not in ORDEM_QUALIDADE and k != "ao"]:
            principal = (qualidades.get(q) or {}).get("main") or {}
            if principal.get("flv"):
                links.append(principal["flv"])
            if principal.get("hls"):
                hls.append(principal["hls"])
    except (ValueError, AttributeError):
        pass
    # 2) informações completas da sala
    sala_id = (dados.get("user") or {}).get("roomId")
    if sala_id:
        try:
            r2 = s.get("https://webcast.tiktok.com/webcast/room/info/",
                       params={"aid": "1988", "room_id": sala_id}, timeout=20)
            flvs = (((r2.json().get("data") or {}).get("stream_url") or {}).get("flv_pull_url") or {})
            for q in ORDEM_WEBCAST + list(flvs):
                if flvs.get(q):
                    links.append(flvs[q])
        except Exception:
            pass
    links += hls   # HLS por último: funciona, mas com mais atraso
    unicos = list(dict.fromkeys(links))
    if unicos:
        return "ao_vivo", [(u, cab) for u in unicos]
    return "erro", "a live está aberta, mas o TikTok não entregou o link do vídeo (pode exigir sessionid)"


def status_sala(usuario, sessionid=""):
    """Consulta rápida (~1 s) do estado da live no TikTok.

    Retorna ("ao_vivo" | "pausada" | "encerrada" | "erro", código_do_tiktok).
    Código 2 = ao vivo; 3 = pausada (o apresentador pausou); outros = encerrada.
    """
    try:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
        if sessionid:
            s.cookies.set("sessionid", sessionid, domain=".tiktok.com")
        r = s.get("https://www.tiktok.com/api-live/user/room/",
                  params={"aid": "1988", "uniqueId": usuario, "sourceType": "54"}, timeout=8)
        if r.status_code != 200:
            return "erro", None
        d = r.json()
    except Exception:
        return "erro", None
    if d.get("statusCode") not in (0, None) or not (d.get("data") or {}).get("user"):
        return "erro", None
    codigo = ((d.get("data") or {}).get("liveRoom") or {}).get("status")
    if codigo == 2:
        return "ao_vivo", codigo
    if codigo == 3:
        return "pausada", codigo
    return "encerrada", codigo


def obter_stream(usuario, sessionid=""):
    """Retorna (url_do_video, cabecalhos) se o perfil estiver ao vivo, senão None.

    Tenta primeiro a API do TikTok direto; se falhar, usa o yt-dlp.
    O motivo da última consulta fica em motivo["texto"].
    sessionid: cookie de login do TikTok, necessário para lives com restrição de idade.
    """
    situacao, resultado = _via_api(usuario, sessionid)
    if situacao == "ao_vivo":
        motivo["texto"] = ""
        alternativas["lista"] = resultado[1:]      # reservas, se o primeiro link falhar
        return resultado[0]
    if situacao == "offline":
        motivo["texto"] = f"O TikTok confirma: @{usuario} não está ao vivo agora."
        return None

    erro_api = resultado
    alternativas["lista"] = []
    achado = _via_ytdlp(usuario, sessionid)
    if achado:
        motivo["texto"] = ""
        return achado
    motivo["texto"] = f"Não consegui confirmar a live de @{usuario}: {erro_api}."
    return None


def _via_ytdlp(usuario, sessionid=""):
    opcoes = {
        "quiet": True,
        "no_warnings": True,
        "logger": _Silencioso(),
        "skip_download": True,
        "format": "best[ext=flv]/best",   # FLV = menor atraso
    }
    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            if sessionid:
                ydl.cookiejar.set_cookie(_cookie_tiktok("sessionid", sessionid))
            info = ydl.extract_info(f"https://www.tiktok.com/@{usuario}/live", download=False)
    except yt_dlp.utils.DownloadError as e:
        texto = str(e).lower()
        if ("login" in texto or "age" in texto or "cookies" in texto) and not _aviso_login["feito"]:
            _aviso_login["feito"] = True
            if sessionid:
                log.warning("O TikTok recusou o acesso mesmo com o sessionid. Ele pode ter expirado: "
                            "pegue um novo no navegador.")
            else:
                log.warning("Esta live pode exigir login (restrição de idade). Preencha o sessionid do TikTok.")
        return None
    except Exception as e:
        log.warning("Erro ao consultar o TikTok: %s", e)
        return None

    url = info.get("url")
    if not url and info.get("requested_formats"):
        url = info["requested_formats"][0].get("url")
    if not url:
        return None
    cab = {k: v for k, v in (info.get("http_headers") or {}).items() if k.lower() != "user-agent"}
    return url, cab


# ---------------------------------------------------------------- Shopee (origem da live de vendas no TikTok)

def _sessao_curl():
    from curl_cffi import requests as creq
    return creq.Session(impersonate="chrome")


def resolver_link_shopee(texto):
    """Aceita o link curto (br.shp.ee/...), o link da live (…session=123…) ou só o número.

    Retorna (id_da_sessao, None) ou (None, "mensagem de erro").
    """
    t = (texto or "").strip()
    if re.fullmatch(r"\d{4,12}", t):
        return int(t), None
    m = re.search(r"[?&#]session=(\d{4,12})", t)
    if m:
        return int(m.group(1)), None
    if re.match(r"^(https?://)?([a-z0-9-]+\.)*shp\.ee/\S+$", t, re.I):
        url = t if t.lower().startswith("http") else "https://" + t
        try:
            r = _sessao_curl().get(url, allow_redirects=False, timeout=15)
            destino = r.headers.get("location") or r.headers.get("Location") or ""
        except Exception as e:
            return None, f"não consegui abrir o link curto da Shopee ({type(e).__name__})"
        m = re.search(r"[?&#]session=(\d{4,12})", destino)
        if m:
            return int(m.group(1)), None
        return None, "esse link da Shopee não leva para uma live (copie o link de compartilhar da live)"
    return None, "cole o link da live da Shopee (ex.: https://br.shp.ee/... ou live.shopee.com.br/...session=...)"


def status_shopee(sessao_id):
    """Consulta pública da live na Shopee (a mesma que a página de espectador usa).

    Retorna ("ao_vivo" | "fora_do_ar" | "erro", info). info tem titulo, usuario, nome, codigo e urls.
    Código 1 = no ar.
    """
    info = {"titulo": "", "usuario": "", "nome": "", "codigo": None, "urls": []}
    try:
        r = _sessao_curl().get(f"https://live.shopee.com.br/api/v1/session/{sessao_id}",
                               headers={"Referer": f"https://live.shopee.com.br/share?from=live&session={sessao_id}"},
                               timeout=10)
        if r.status_code != 200:
            return "erro", {**info, "erro": f"a Shopee respondeu HTTP {r.status_code}"}
        d = r.json()
    except Exception as e:
        return "erro", {**info, "erro": f"falha de conexão com a Shopee ({type(e).__name__})"}
    if d.get("err_code") not in (0, None):
        return "erro", {**info, "erro": f"a Shopee respondeu: {d.get('err_msg') or d.get('err_code')}"}
    dados = d.get("data") or {}
    s = dados.get("session") or {}
    info.update(titulo=(s.get("title") or "").strip(), usuario=s.get("username") or "",
                nome=s.get("nickname") or "", codigo=s.get("status"))
    urls = dados.get("play_urls") or []
    if isinstance(urls, dict):
        urls = list(urls.values())
    info["urls"] = [u for u in urls if isinstance(u, str) and u.startswith("http")]
    if info["codigo"] == 1 and info["urls"]:
        return "ao_vivo", info
    return "fora_do_ar", info


# ---------------------------------------------------------------- ffmpeg

def resumir_stats(linha):
    def pega(nome):
        m = re.search(rf"{nome}=\s*(\S+)", linha)
        return m.group(1) if m else "?"
    tempo = pega("time").split(".")[0]
    return f"Transmitindo há {tempo}  ·  {pega('bitrate')}  ·  velocidade {pega('speed')}"


# ---------------------------------------------------------------- motor

MSG_SHOPEE_RECUSOU = (
    "A Shopee recusou a transmissão 3 vezes seguidas, logo no início. Quase sempre é porque "
    "a live na Shopee já foi encerrada e a chave não vale mais (a Shopee gera uma chave nova "
    "a cada live). Crie uma nova live na Shopee, copie a URL e a chave novas e clique em Iniciar de novo.")

class Espelhador:
    """Espera a live do TikTok, retransmite para a Shopee e reconecta sozinho.

    aviso(estado, mensagem) é chamado com estado em:
    'aguardando', 'ao_vivo', 'stats', 'reconectando', 'parado'.
    """

    def __init__(self, cfg, ffmpeg, destino, aviso=None, sem_janela=False, preview=None,
                 ao_encerrar_tiktok=None):
        self.cfg = cfg
        self.ffmpeg = ffmpeg
        self.destino = destino
        self.preview = preview
        self.aviso = aviso or (lambda estado, msg: None)
        self.sem_janela = sem_janela
        # chamado quando a live do TikTok acaba (finaliza a transmissão na Shopee)
        self.ao_encerrar_tiktok = ao_encerrar_tiktok
        self._parar = threading.Event()
        self._sessao = None
        self._shopee_recusou = False
        self._link_recusado = False
        self._reiniciar = False          # a última transmissão parou para corrigir o relógio do vídeo
        self._base_gravacao = None
        self._tiktok_encerrou_em = None

    def _encerrar_shopee_ligado(self):
        return bool(self.cfg.get("encerrar_shopee") and self.ao_encerrar_tiktok)

    def _vigiar_fim(self, sessao, usuario, sessionid):
        """Durante a live, pergunta ao TikTok a cada ~1 s se ela acabou (2 confirmações seguidas)."""
        seguidos, avisou_pausa = 0, False
        while not sessao._parar.is_set() and not self._parar.is_set():
            situacao, codigo = status_sala(usuario, sessionid)
            if situacao == "encerrada":
                seguidos += 1
                if seguidos == 1:
                    primeira = time.time()
                    continue                # reconfirma na hora, sem esperar
                if seguidos >= 2:
                    self._tiktok_encerrou_em = primeira
                    log.info("⏹ O TikTok confirmou que a live de @%s acabou (código %s).", usuario, codigo)
                    sessao.parar()
                    return
            else:
                seguidos = 0
                if situacao == "pausada" and not avisou_pausa:
                    log.info("⏸ A live foi pausada no TikTok. A Shopee continua aberta.")
                    avisou_pausa = True
                elif situacao == "ao_vivo":
                    avisou_pausa = False
            time.sleep(0.4)

    def _finalizar_shopee(self, usuario):
        inicio_fim = self._tiktok_encerrou_em or time.time()
        log.info("Finalizando a transmissão na Shopee...")
        try:
            res = self.ao_encerrar_tiktok() or {}
        except Exception as e:
            res = {"ok": False, "detalhe": str(e)}
        seg = time.time() - inicio_fim
        if res.get("ok"):
            self._estado("parado", f"A live de @{usuario} acabou no TikTok e a transmissão na Shopee foi "
                                   f"finalizada {seg:.1f}s depois de o TikTok mostrar o fim.")
        else:
            self._estado("parado", "⚠ A live acabou no TikTok, mas NÃO consegui finalizar na Shopee: "
                                   f"{res.get('detalhe') or res.get('erro')} Finalize manualmente!",
                         logging.ERROR)
            self.aviso("alarme", "finalizar_shopee")
        self._parar.set()

    def parar(self):
        self._parar.set()
        s = self._sessao
        if s:
            s.parar()

    def _estado(self, estado, msg, nivel=logging.INFO):
        log.log(nivel, msg)
        self.aviso(estado, msg)

    def executar(self):
        usuario = self.cfg["tiktok_usuario"]
        sessionid = self.cfg.get("tiktok_sessionid", "")
        intervalo = max(10, int(self.cfg["intervalo_verificacao_seg"]))
        _aviso_login["feito"] = False
        extra = " (usando o login do TikTok via sessionid)" if sessionid else ""
        self._estado("aguardando", f"Monitorando @{usuario}{extra}. Pode começar a live no TikTok.")
        ultimo_motivo = None
        recusas_seguidas = 0

        while not self._parar.is_set():
            stream = obter_stream(usuario, sessionid)
            if self._parar.is_set():
                break
            if not stream:
                texto = motivo["texto"] or f"@{usuario} ainda não está ao vivo."
                if texto != ultimo_motivo:     # só registra quando o motivo muda
                    erro = texto.startswith("Não consegui")
                    self._estado("aguardando", f"{texto} Verificando a cada {intervalo}s...",
                                 logging.WARNING if erro else logging.INFO)
                    ultimo_motivo = texto
                self._parar.wait(intervalo)
                continue

            if ultimo_motivo is not None or self._base_gravacao is None:
                # live nova (estava offline antes): novo nome de arquivo de gravação
                self._base_gravacao = time.strftime("%Y-%m-%d_%Hh%M_") + usuario
            ultimo_motivo = None
            links = [stream] + list(alternativas["lista"])
            self._estado("ao_vivo", f"@{usuario} está AO VIVO. Enviando para a Shopee...")
            for n, (url, cabecalhos) in enumerate(links, 1):
                inicio = time.time()
                ultimo_erro = self._transmitir(url, cabecalhos)
                if (self._parar.is_set() or not self._link_recusado or self._shopee_recusou
                        or n == len(links)):
                    break
                # o TikTok recusou este link: tenta o próximo da mesma live na hora
                log.warning("O TikTok recusou o link de vídeo %d de %d (%s). Tentando o próximo...",
                            n, len(links), ultimo_erro)
            if self._parar.is_set():
                break

            if self._encerrar_shopee_ligado():
                if not self._tiktok_encerrou_em:
                    # a transmissão parou sem o vigia ver o fim: confere agora antes de reconectar
                    situacao, codigo = status_sala(usuario, sessionid)
                    if situacao == "encerrada":
                        self._tiktok_encerrou_em = time.time()
                        log.info("⏹ O TikTok confirmou que a live de @%s acabou (código %s).", usuario, codigo)
                if self._tiktok_encerrou_em:
                    self._finalizar_shopee(usuario)
                    return

            dur = int(time.time() - inicio)
            if self._reiniciar:
                # o relógio do vídeo bagunçou: reconecta na hora (1 a 2 s de pausa em vez de travar a live)
                self._estado("reconectando", "Reconectando para corrigir o vídeo...")
                recusas_seguidas = 0
                self._parar.wait(0.5)
                continue
            # A Shopee derrubou logo no começo: quase sempre é chave de uma live já encerrada
            if self._shopee_recusou and dur < 60:
                recusas_seguidas += 1
            else:
                recusas_seguidas = 0
            if recusas_seguidas >= 3:
                self._estado("parado", MSG_SHOPEE_RECUSOU, logging.ERROR)
                self._parar.set()
                return
            msg = f"A transmissão parou após {dur // 60}min{dur % 60:02d}s. Verificando se a live continua..."
            if ultimo_erro:
                msg += f"\n   Motivo: {ultimo_erro}"
            if recusas_seguidas:
                msg += f"\n   Tentativa {recusas_seguidas} de 3. Confira se a live na Shopee está aberta e se a chave é a atual."
            self._estado("reconectando", msg, logging.WARNING)
            self._parar.wait(5 if dur > 60 else 15)

        self._estado("parado", "Espelhamento encerrado.")

    sentido = "ts"      # pasta de gravação: TikTok -> Shopee

    def _proximo_arquivo_gravacao(self):
        base = self._base_gravacao or time.strftime("%Y-%m-%d_%Hh%M_live")
        pasta = pasta_gravacao(self.sentido)
        caminho = pasta / f"{base}.ts"
        parte = 2
        while caminho.exists():            # a live caiu e voltou: grava a continuação em outra parte
            caminho = pasta / f"{base}_parte{parte}.ts"
            parte += 1
        return caminho

    def _transmitir(self, url, cabecalhos, arquivo=None, repetir=False):
        from transmissao import Transmissao
        gravar_em = renovar = None
        if self.cfg.get("gravar") and not arquivo:
            gravar_em = self._proximo_arquivo_gravacao      # um arquivo por trecho (parte2, parte3...)
        if not arquivo:
            usuario, sessionid = self.cfg["tiktok_usuario"], self.cfg.get("tiktok_sessionid", "")
            renovar = lambda: obter_stream(usuario, sessionid)    # link novo para trocar sem parar
        sessao = Transmissao(self.cfg, self.ffmpeg, url, cabecalhos, self.destino, self.preview,
                             aviso=self.aviso, sem_janela=self.sem_janela, resumir_stats=resumir_stats,
                             arquivo=arquivo, repetir=repetir, gravar_em=gravar_em, renovar=renovar)
        self._sessao = sessao
        self._shopee_recusou = False
        self._link_recusado = False
        if self._parar.is_set():   # Parar foi clicado enquanto abria
            sessao.parar()
        if not arquivo and self._encerrar_shopee_ligado():
            from transmissao import nome_thread
            threading.Thread(target=self._vigiar_fim, daemon=True, name=nome_thread("vigia"),
                             args=(sessao, self.cfg["tiktok_usuario"], self.cfg.get("tiktok_sessionid", ""))).start()
        try:
            return sessao.executar()
        finally:
            self._shopee_recusou = sessao.shopee_recusou
            self._link_recusado = sessao.link_recusado
            self._reiniciar = sessao.precisa_reiniciar
            self._sessao = None


class Reprisador(Espelhador):
    """Manda um vídeo gravado para a Shopee, uma vez ou em loop, com o mesmo atraso e mudo."""

    onde_destino = "na Shopee"
    alarme_fim = "tempo_shopee"

    def __init__(self, cfg, ffmpeg, destino, arquivo, repetir=True, limite_seg=0, ao_acabar_tempo=None, **kw):
        """limite_seg: em loop, por quanto tempo a reprise fica no ar (0 = até clicar em Parar).
        ao_acabar_tempo: função que encerra a live no destino quando o tempo acaba (a extensão), ou None."""
        super().__init__(cfg, ffmpeg, destino, **kw)
        self.arquivo = Path(arquivo)
        self.repetir = repetir
        self.limite_seg = max(0, int(limite_seg or 0)) if repetir else 0
        self.ao_acabar_tempo = ao_acabar_tempo
        self._tempo_acabou = False
        self._fim_em = None

    # -------------------------------------------------------- tempo de loop

    def _jeito(self):
        if not self.repetir:
            return "uma vez"
        if self._fim_em:
            return "em loop até " + time.strftime("%H:%M", time.localtime(self._fim_em))
        return "em loop"

    def _ligar_cronometro(self):
        if not self.limite_seg:
            return
        self._fim_em = time.time() + self.limite_seg
        horas = self.limite_seg / 3600
        texto = f"{horas:g} hora" + ("" if horas == 1 else "s")
        fim = time.strftime("%H:%M", time.localtime(self._fim_em))
        extra = f" e a live {self.onde_destino} é encerrada" if self.ao_acabar_tempo else ""
        log.info("⏰ Loop por %s: às %s a reprise para sozinha%s.", texto, fim, extra)

        def vigiar():
            if self._parar.wait(self.limite_seg):
                return                              # parou antes (clicou em Parar)
            self._tempo_acabou = True
            log.info("⏰ Acabou o tempo de loop (%s). Parando a reprise...", texto)
            self.parar()

        from transmissao import nome_thread
        threading.Thread(target=vigiar, daemon=True, name=nome_thread("tempo")).start()

    def _encerrar_por_tempo(self):
        if self.ao_acabar_tempo:
            log.info("Encerrando a live %s...", self.onde_destino)
            try:
                res = self.ao_acabar_tempo() or {}
            except Exception as e:      # noqa: BLE001
                res = {"ok": False, "detalhe": str(e)}
            if res.get("ok"):
                self._estado("parado", f"⏰ O tempo de loop acabou: a reprise parou e a live {self.onde_destino} "
                                       "foi encerrada.")
                return
            self._estado("parado", f"⚠ O tempo de loop acabou e a reprise parou, mas NÃO consegui encerrar a live "
                                   f"{self.onde_destino}: {res.get('detalhe') or res.get('erro')} Encerre manualmente!",
                         logging.ERROR)
        else:
            self._estado("parado", f"⏰ O tempo de loop acabou e a reprise parou. Encerre a live "
                                   f"{self.onde_destino} agora.", logging.WARNING)
        self.aviso("alarme", self.alarme_fim)

    def executar(self):
        nome = self.arquivo.name
        self._ligar_cronometro()
        recusas_seguidas = 0
        while not self._parar.is_set():
            self._estado("reprise", f"Reprisando {nome} ({self._jeito()}) na Shopee...")
            inicio = time.time()
            ultimo_erro = self._transmitir(None, {}, arquivo=self.arquivo, repetir=self.repetir)
            if self._parar.is_set():
                break
            dur = int(time.time() - inicio)
            if self._shopee_recusou and dur < 60:
                recusas_seguidas += 1
            else:
                recusas_seguidas = 0
            if recusas_seguidas >= 3:
                self._estado("parado", MSG_SHOPEE_RECUSOU, logging.ERROR)
                self._parar.set()
                return
            if not self.repetir and not self._shopee_recusou:
                fim = f"A reprise de {nome} terminou."
                if ultimo_erro and dur < 10:
                    fim = f"Não consegui reprisar {nome}: {ultimo_erro}"
                self._estado("parado", fim, logging.WARNING if dur < 10 else logging.INFO)
                return
            msg = f"A reprise parou após {dur // 60}min{dur % 60:02d}s. Tentando de novo..."
            if ultimo_erro:
                msg += f"\n   Motivo: {ultimo_erro}"
            if recusas_seguidas:
                msg += f"\n   Tentativa {recusas_seguidas} de 3. Confira se a live na Shopee está aberta e se a chave é a atual."
            self._estado("reconectando", msg, logging.WARNING)
            self._parar.wait(5 if dur > 60 else 15)
        if self._tempo_acabou:
            self._encerrar_por_tempo()
            return
        self._estado("parado", "Reprise encerrada.")


class EspelhadorShopee(Espelhador):
    """Live de vendas no TikTok Shop: pega a live da Shopee e entrega ao OBS (câmera e microfone virtuais).

    O Gerenciador de LIVE do TikTok Shop transmite pelo navegador usando a "OBS Virtual Camera"
    e o "CABLE Output". Aqui só cuidamos de levar o vídeo, com atraso e "shopee" mutado, até o OBS.
    """

    sentido = "st"      # pasta de gravação: Shopee -> TikTok
    SEG_PAUSA = 5       # sem vídeo da Shopee por este tempo = live pausada

    def __init__(self, cfg, ffmpeg, sessao_id, controle_tiktok=None, **kw):
        """controle_tiktok: objeto com tiktok_pausar(), tiktok_retomar() e tiktok_encerrar() (a extensão),
        ou None para não mexer no TikTok sozinho."""
        import obs_tiktok
        cfg = {**cfg, "saida": "obs"}
        super().__init__(cfg, ffmpeg, obs_tiktok.DESTINO_OBS, **kw)
        self.sessao_id = sessao_id
        self.tiktok = controle_tiktok
        self._shopee_acabou = False
        self._ultimo_dado = None        # último momento em que chegou vídeo da Shopee
        self._shopee_pausada = False
        self._vigia_pausa = None

    def _link_novo(self):
        """Link novo da mesma live da Shopee (para trocar a conexão sem parar), ou None."""
        situacao, info = status_shopee(self.sessao_id)
        if situacao == "ao_vivo" and info.get("urls"):
            return info["urls"][0], {"Referer": "https://live.shopee.com.br/"}
        return None

    # -------------------------------------------------------- pausa e fim da live

    def _acao_tiktok(self, nome, metodo):
        try:
            r = getattr(self.tiktok, metodo)() or {}
        except Exception as e:
            r = {"ok": False, "detalhe": str(e)}
        if r.get("ok"):
            log.info("🎬 TikTok: %s", r.get("detalhe") or f"{nome} feito.")
        else:
            log.error("⚠ Não consegui %s a live no TikTok: %s", nome, r.get("detalhe") or r.get("erro"))
        return r

    def _vigiar_pausa(self):
        """Sem vídeo da Shopee por 5 s = pausa. O Gerenciador de LIVE do TikTok (na web) não tem botão de
        pausar, então o envio para o OBS é fechado depois que o atraso termina de sair e o OBS mostra a
        imagem "Já voltamos" (fica atrás do vídeo). Quando a Shopee volta, o vídeo volta por cima dela."""
        ultimo_reinicio = 0.0
        while not self._parar.is_set():
            s = self._sessao
            if s is not None and s.ultimo_dado:
                if self._ultimo_dado is None or s.ultimo_dado > self._ultimo_dado:
                    self._ultimo_dado = s.ultimo_dado
            if self._ultimo_dado is not None and not self._shopee_acabou:
                parado = time.monotonic() - self._ultimo_dado
                if not self._shopee_pausada and parado >= self.SEG_PAUSA:
                    situacao, info = status_shopee(self.sessao_id)
                    if situacao == "fora_do_ar" and info.get("codigo") == 2:
                        pass        # acabou de vez: o fim da live cuida disso
                    elif time.monotonic() - self._ultimo_dado >= self.SEG_PAUSA:
                        log.info("⏸ A live parou na Shopee (sem vídeo há %.0fs). O TikTok mostra a imagem "
                                 "'Já voltamos' até ela voltar.", parado)
                        self.aviso("pausa", "shopee")
                        self._shopee_pausada = True
                        ultimo_reinicio = time.monotonic()
                        s = self._sessao
                        if s is not None:
                            s.terminar_entrada()    # o que está no atraso termina de sair e entra a imagem
                elif self._shopee_pausada and parado < 1.5:
                    atraso = float(self.cfg.get("atraso_seg", 7))
                    log.info("▶ A live voltou na Shopee. O vídeo volta no TikTok em ~%.0fs (atraso).", atraso)
                    self._shopee_pausada = False
                    self.aviso("pausa", "fim")
                    self.aviso("ao_vivo", "A live voltou na Shopee. Enviando para o OBS → TikTok...")
                elif (self._shopee_pausada and s is not None and parado >= 12
                      and time.monotonic() - ultimo_reinicio >= 12):
                    # pausa longa: reabre a conexão com a Shopee (link novo) para voltar rápido quando ela voltar
                    ultimo_reinicio = time.monotonic()
                    s.parar()
            self._parar.wait(0.5)

    def _vigiar_fim_shopee(self, sessao):
        """Pergunta à Shopee a cada ~1,5 s se a live continua (2 confirmações seguidas de fim)."""
        seguidos = 0
        while not sessao._parar.is_set() and not self._parar.is_set():
            situacao, info = status_shopee(self.sessao_id)
            if situacao == "fora_do_ar" and info.get("codigo") == 2:     # 2 = encerrada de vez
                seguidos += 1
                if seguidos == 1:
                    continue
                self._shopee_acabou = True
                log.info("⏹ A Shopee confirmou que a live acabou (código %s).", info.get("codigo"))
                sessao.parar()
                return
            seguidos = 0
            self._parar.wait(1.5)

    def _fim_da_live(self, nome):
        if self.tiktok:
            log.info("Encerrando a live no TikTok...")
            r = self._acao_tiktok("encerrar", "tiktok_encerrar")
            if r.get("ok"):
                self._estado("parado", f"A live de {nome} acabou na Shopee e a live no TikTok foi encerrada.")
                self._parar.set()
                return
        self._estado("parado", f"⚠ A live de {nome} acabou na Shopee e o envio para o TikTok parou. "
                               "ENCERRE A LIVE NO TIKTOK agora (Gerenciador de LIVE), para não ficar tela preta.",
                     logging.WARNING)
        self.aviso("alarme", "finalizar_tiktok")
        self._parar.set()

    def executar(self):
        import obs_tiktok
        intervalo = 10
        ultimo_motivo = None
        recusas_seguidas = 0
        esteve_no_ar = False
        self._estado("aguardando", f"Monitorando a live {self.sessao_id} da Shopee...")
        while not self._parar.is_set():
            situacao, info = status_shopee(self.sessao_id)
            if self._parar.is_set():
                break
            nome = info.get("nome") or info.get("usuario") or f"sessão {self.sessao_id}"
            if situacao != "ao_vivo":
                if situacao == "fora_do_ar" and esteve_no_ar and info.get("codigo") == 2:
                    self._fim_da_live(nome)
                    return
                if esteve_no_ar and situacao == "fora_do_ar":
                    # já esteve no ar e não acabou (código diferente de 2): trata como pausa
                    self.aviso("reconectando", f"A live de {nome} está pausada na Shopee. Esperando voltar...")
                    self._parar.wait(2)
                    continue
                if situacao == "erro":
                    texto = f"Não consegui consultar a live da Shopee: {info.get('erro')}."
                elif info.get("codigo") == 2:
                    texto = (f"Essa live de {nome} já acabou na Shopee. Cada live tem um link próprio: "
                             "pare, cole o link da live nova e inicie de novo.")
                else:
                    texto = (f"A live de {nome} não está no ar na Shopee (código {info.get('codigo')}). "
                             "Se ela já acabou, cole o link da live nova.")
                if texto != ultimo_motivo:
                    self._estado("aguardando", f"{texto} Verificando a cada {intervalo}s...",
                                 logging.WARNING if situacao == "erro" else logging.INFO)
                    ultimo_motivo = texto
                self._parar.wait(intervalo)
                continue

            erro_obs = obs_tiktok.garantir(log)
            if self._parar.is_set():
                break
            if erro_obs:
                self._estado("parado", erro_obs, logging.ERROR)
                self._parar.set()
                return

            if not esteve_no_ar or self._base_gravacao is None:
                self._base_gravacao = time.strftime("%Y-%m-%d_%Hh%M_shopee_") + re.sub(
                    r"[^A-Za-z0-9._-]", "", info.get("usuario") or str(self.sessao_id))
            if not esteve_no_ar:
                titulo = f" ({info['titulo']})" if info.get("titulo") else ""
                log.info("Live da Shopee encontrada: %s%s.", nome, titulo)
                if self._vigia_pausa is None:
                    if self.tiktok:
                        log.info("Se a Shopee pausar, o TikTok mostra a imagem 'Já voltamos' (%ds depois); "
                                 "se a Shopee acabar, a live no TikTok é encerrada sozinha.", self.SEG_PAUSA)
                    from transmissao import nome_thread
                    self._vigia_pausa = threading.Thread(target=self._vigiar_pausa, daemon=True,
                                                         name=nome_thread("pausa"))
                    self._vigia_pausa.start()
            esteve_no_ar = True
            ultimo_motivo = None
            if self._shopee_pausada:      # tentando de novo durante a pausa: só atualiza o painel
                self.aviso("reconectando", f"A live de {nome} está pausada na Shopee. Esperando voltar...")
            else:
                self._estado("ao_vivo", f"{nome} está AO VIVO na Shopee. Enviando para o OBS → TikTok...")
            cab = {"Referer": "https://live.shopee.com.br/"}
            inicio = time.time()
            ultimo_erro = ""
            for n, url in enumerate(info["urls"], 1):
                inicio = time.time()
                ultimo_erro = self._transmitir(url, cab)
                if self._parar.is_set() or not self._link_recusado or self._shopee_recusou or n == len(info["urls"]):
                    break
                log.warning("A Shopee recusou o link de vídeo %d de %d. Tentando o próximo...", n, len(info["urls"]))
            if self._parar.is_set():
                break
            if self._shopee_acabou:
                self._fim_da_live(nome)
                return

            dur = int(time.time() - inicio)
            if self._reiniciar:
                # o relógio do vídeo bagunçou: reconecta na hora com um link novo da Shopee
                self._estado("reconectando", "Reconectando para corrigir o vídeo...")
                recusas_seguidas = 0
                self._parar.wait(0.5)
                continue
            # o envio para o OBS caiu logo no começo várias vezes: o OBS provavelmente foi fechado
            recusas_seguidas = recusas_seguidas + 1 if (self._shopee_recusou and dur < 60) else 0
            if recusas_seguidas >= 3:
                self._estado("parado", "O OBS recusou o vídeo 3 vezes seguidas. Feche o OBS e clique em "
                                       "Iniciar de novo (o painel reabre o OBS configurado).", logging.ERROR)
                self._parar.set()
                return
            if self._shopee_pausada or (self._ultimo_dado and time.monotonic() - self._ultimo_dado >= self.SEG_PAUSA):
                # a Shopee está em pausa: tenta de novo em silêncio até o vídeo voltar
                self.aviso("reconectando", f"A live de {nome} está pausada na Shopee. Esperando voltar...")
                recusas_seguidas = 0
                self._parar.wait(2)
                continue
            msg = f"O envio parou após {dur // 60}min{dur % 60:02d}s. Verificando se a live da Shopee continua..."
            if ultimo_erro:
                msg += f"\n   Motivo: {ultimo_erro}"
            self._estado("reconectando", msg, logging.WARNING)
            self._parar.wait(3)
        self._estado("parado", "Envio para o TikTok encerrado.")

    def _transmitir(self, url, cabecalhos, arquivo=None, repetir=False):
        from transmissao import Transmissao
        gravar_em = self._proximo_arquivo_gravacao if self.cfg.get("gravar") else None
        sessao = Transmissao(self.cfg, self.ffmpeg, url, cabecalhos, self.destino, self.preview,
                             aviso=self.aviso, sem_janela=self.sem_janela, resumir_stats=resumir_stats,
                             gravar_em=gravar_em, renovar=self._link_novo, silencioso=self._shopee_pausada)
        self._sessao = sessao
        self._shopee_recusou = False
        self._link_recusado = False
        if self._parar.is_set():
            sessao.parar()
        from transmissao import nome_thread
        threading.Thread(target=self._vigiar_fim_shopee, args=(sessao,), daemon=True,
                         name=nome_thread("vigia")).start()
        try:
            return sessao.executar()
        finally:
            self._shopee_recusou = sessao.shopee_recusou
            self._link_recusado = sessao.link_recusado
            self._reiniciar = sessao.precisa_reiniciar
            self._sessao = None


class ReprisadorTikTok(Reprisador):
    """Manda um vídeo gravado para o TikTok Shop pelo OBS, uma vez ou em loop, com atraso e 'shopee' mutado."""

    sentido = "st"
    onde_destino = "no TikTok"
    alarme_fim = "tempo_tiktok"

    def __init__(self, cfg, ffmpeg, arquivo, repetir=True, **kw):
        import obs_tiktok
        super().__init__({**cfg, "saida": "obs"}, ffmpeg, obs_tiktok.DESTINO_OBS, arquivo, repetir, **kw)

    def executar(self):
        import obs_tiktok
        nome = self.arquivo.name
        self._ligar_cronometro()
        falhas = 0
        while not self._parar.is_set():
            erro_obs = obs_tiktok.garantir(log)
            if self._parar.is_set():
                break
            if erro_obs:
                self._estado("parado", erro_obs, logging.ERROR)
                return
            self._estado("reprise", f"Reprisando {nome} ({self._jeito()}) no TikTok pelo OBS...")
            inicio = time.time()
            ultimo_erro = self._transmitir(None, {}, arquivo=self.arquivo, repetir=self.repetir)
            if self._parar.is_set():
                break
            dur = int(time.time() - inicio)
            falhas = falhas + 1 if (self._shopee_recusou and dur < 60) else 0
            if falhas >= 3:
                self._estado("parado", "O OBS recusou o vídeo 3 vezes seguidas. Feche o OBS e clique em "
                                       "Iniciar de novo (o painel reabre o OBS configurado).", logging.ERROR)
                return
            if not self.repetir and not self._shopee_recusou:
                fim = f"A reprise de {nome} terminou. Encerre a live no TikTok."
                if ultimo_erro and dur < 10:
                    fim = f"Não consegui reprisar {nome}: {ultimo_erro}"
                self._estado("parado", fim, logging.WARNING if dur < 10 else logging.INFO)
                return
            msg = f"A reprise parou após {dur // 60}min{dur % 60:02d}s. Tentando de novo..."
            if ultimo_erro:
                msg += f"\n   Motivo: {ultimo_erro}"
            self._estado("reconectando", msg, logging.WARNING)
            self._parar.wait(3)
        if self._tempo_acabou:
            self._encerrar_por_tempo()
            return
        self._estado("parado", "Reprise encerrada.")


# ---------------------------------------------------------------- gravações

EXTENSOES_VIDEO = {".ts", ".mp4", ".flv", ".mov", ".mkv", ".m4v", ".webm"}
_duracoes = {}


def _ffprobe(cfg=None):
    ff = achar_ffmpeg(cfg or AVANCADO)
    if ff:
        p = Path(ff).with_name("ffprobe.exe")
        if p.is_file():
            return str(p)
    return shutil.which("ffprobe")


def listar_gravacoes(sentido="ts"):
    """Vídeos da pasta de gravações do sentido escolhido, do mais novo para o mais antigo."""
    pasta = pasta_gravacao(sentido)
    pasta.mkdir(parents=True, exist_ok=True)
    ffprobe = _ffprobe()
    itens = []
    arquivos = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSOES_VIDEO]
    for p in sorted(arquivos, key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        gravando = time.time() - st.st_mtime < 10
        chave = (p.name, st.st_mtime, st.st_size)
        dur = _duracoes.get(chave)
        if dur is None and ffprobe and not gravando:
            try:
                r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=20,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                dur = float(r.stdout.strip())
            except (ValueError, subprocess.TimeoutExpired, OSError):
                dur = 0.0
            _duracoes[chave] = dur
        itens.append({"nome": p.name, "mb": round(st.st_size / 1_000_000, 1),
                      "duracao": int(dur) if dur else None, "gravando": gravando,
                      "data": time.strftime("%d/%m/%Y %H:%M", time.localtime(st.st_mtime))})
    return itens


def caminho_gravacao(nome, sentido="ts"):
    """Valida o nome vindo do painel e devolve o caminho dentro da pasta de gravações do sentido."""
    nome = str(nome or "")
    if not nome or Path(nome).name != nome or nome.startswith("."):
        return None
    p = pasta_gravacao(sentido) / nome
    if not p.is_file() or p.suffix.lower() not in EXTENSOES_VIDEO:
        return None
    return p


# ---------------------------------------------------------------- terminal

def _perguntar(rotulo, padrao=""):
    sufixo = f" [{padrao}]" if padrao else ""
    resp = input(f"{rotulo}{sufixo}: ").strip()
    return resp or padrao


def main():
    configurar_log()
    cfg = ler_config()
    ffmpeg = achar_ffmpeg(cfg)
    if not ffmpeg:
        log.error("ffmpeg não encontrado. Instale com:  winget install Gyan.FFmpeg")
        sys.exit(1)

    print("\n=== Espelho de Live: TikTok -> Shopee ===\n")
    while True:
        usuario = limpar_usuario(_perguntar("@ do TikTok", cfg.get("tiktok_usuario", "")))
        sid = limpar_sessionid(_perguntar("sessionid do TikTok (Enter para pular; só para restrição de idade)"))
        url = _perguntar("URL do servidor da Shopee", cfg.get("shopee_url_servidor", ""))
        chave = _perguntar("Chave da transmissão da Shopee (Enter se a URL já tiver a chave)")
        erro = validar(usuario, url) or validar_sessionid(sid)
        if not erro:
            break
        print(f"\n>> {erro}\n")

    cfg.update(tiktok_usuario=usuario, shopee_url_servidor=url.strip(),
               tiktok_sessionid=sid or cfg.get("tiktok_sessionid", ""))
    salvar_config(cfg)

    def aviso(estado, msg):
        if estado == "stats":
            print("\r" + msg + "   ", end="", flush=True)

    esp = Espelhador(cfg, ffmpeg, montar_destino(url, chave), aviso)
    print("Ctrl+C para parar.\n")
    try:
        esp.executar()
    except KeyboardInterrupt:
        print()
        esp.parar()
        log.info("Encerrado.")


if __name__ == "__main__":
    main()
