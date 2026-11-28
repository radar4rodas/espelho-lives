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


def salvar_ajuste_voz_medido(chave, ms):
    """Guarda o ajuste que o vigia da sincronia achou (um por formato de vídeo): a próxima live com
    vídeo do mesmo formato já começa com ele."""
    cfg = ler_config()
    cfg[chave] = int(ms)
    cfg.pop("ajuste_voz_medido_ms", None)      # valor antigo, de antes de separar por formato
    salvar_config(cfg)


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
TT_API_ESPERA_BLOQUEIO = 600
_tt_api_bloqueios = {}
_lock_tt_api = threading.Lock()


def _tiktok_em_bloqueio(usuario):
    chave = usuario.casefold()
    agora = time.monotonic()
    with _lock_tt_api:
        ate = _tt_api_bloqueios.get(chave, 0)
        if ate <= agora:
            _tt_api_bloqueios.pop(chave, None)
            return False
        return True


def _registrar_bloqueio_tiktok(usuario):
    with _lock_tt_api:
        _tt_api_bloqueios[usuario.casefold()] = time.monotonic() + TT_API_ESPERA_BLOQUEIO


def _mensagem_bloqueio_tiktok():
    minutos = (TT_API_ESPERA_BLOQUEIO + 59) // 60
    return f"TikTok respondeu HTTP 403. Pausando consultas por {minutos} min para não insistir durante o bloqueio."


def _via_api(usuario, sessionid, mot=None):
    """Consulta direta à API de lives do TikTok (imitando o Chrome).

    Retorna ("ao_vivo", (url, cabecalhos)), ("offline", None) ou ("erro", "mensagem").
    mot: onde guardar o código da sala (padrão: o 'motivo' global, que o espelhamento usa). O gravador usa um
    próprio, para não confundir o espelhamento quando os dois rodam ao mesmo tempo.
    """
    motivo = globals()["motivo"] if mot is None else mot
    motivo["codigo"] = None      # código da sala nesta consulta (2 = ao vivo, 3 = pausada, outro = acabou)
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
            if r.status_code == 403:
                _registrar_bloqueio_tiktok(usuario)
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
    motivo["codigo"] = sala.get("status")
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
    if _tiktok_em_bloqueio(usuario):
        return "erro", None
    try:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
        if sessionid:
            s.cookies.set("sessionid", sessionid, domain=".tiktok.com")
        r = s.get("https://www.tiktok.com/api-live/user/room/",
                  params={"aid": "1988", "uniqueId": usuario, "sourceType": "54"}, timeout=8)
        if r.status_code != 200:
            if r.status_code == 403:
                _registrar_bloqueio_tiktok(usuario)
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
    if _tiktok_em_bloqueio(usuario):
        alternativas["lista"] = []
        motivo["texto"] = _mensagem_bloqueio_tiktok()
        return None

    situacao, resultado = _via_api(usuario, sessionid)
    if situacao == "ao_vivo":
        motivo["texto"] = ""
        alternativas["lista"] = resultado[1:]      # reservas, se o primeiro link falhar
        return resultado[0]
    if situacao == "offline":
        motivo["texto"] = f"O TikTok confirma: @{usuario} não está ao vivo agora."
        return None

    if _tiktok_em_bloqueio(usuario):
        alternativas["lista"] = []
        motivo["texto"] = _mensagem_bloqueio_tiktok()
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
                nome=s.get("nickname") or "", codigo=s.get("status"),
                # números da live (espectadores, curtidas...) para a página do celular
                sessao_bruta={k: s.get(k) for k in ("viewer_count", "max_member_cnt", "member_cnt",
                                                    "like_cnt", "share_cnt", "start_time")})
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

def _hms(seg):
    seg = int(seg)
    return f"{seg // 3600}:{seg // 60 % 60:02d}:{seg % 60:02d}"


class Espelhador:
    """Espera a live do TikTok, retransmite para a Shopee e reconecta sozinho.

    aviso(estado, mensagem) é chamado com estado em:
    'aguardando', 'ao_vivo', 'stats', 'reconectando', 'parado'.
    """

    def __init__(self, cfg, ffmpeg, destino, aviso=None, sem_janela=False, preview=None,
                 ao_encerrar_tiktok=None, estado_destino=None):
        self.cfg = cfg
        # função que devolve o status da página da live no destino (a extensão), ou None
        self.estado_destino = estado_destino
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
        self._bases_gravadas = []        # (pasta, base) gravados nesta execução: emendados no fim
        self._partes = []                # (pasta, base, arquivo, quando começou): onde cai cada ⭐ marcação
        self._marcas = []                # ⭐ momentos marcados (time.time()): viram cortes para Reels no fim
        self._tiktok_encerrou_em = None

    # (eram só da reprise; o espelhamento ao vivo também usa quando a conexão cai: sem eles, a queda da Shopee
    #  derrubava o motor com "'Espelhador' object has no attribute '_pagina_fora_do_ar'" — 23/11 17:15)
    def _pagina_fora_do_ar(self):
        """Se a extensão mostra a página da live mas ela não está mais ao vivo, explica o motivo."""
        if not self.estado_destino:
            return ""
        try:
            st = self.estado_destino() or {}
        except Exception:       # noqa: BLE001
            return ""
        if st.get("aberto") and st.get("situacao") in ("aberto", "preparada"):
            return ("A página da live na Shopee não está mais ao vivo: a própria Shopee encerrou a live. "
                    "Crie uma live nova na Shopee e clique em Iniciar com a URL e a chave novas.")
        return ""

    def _msg_recusou(self):
        pagina = self._pagina_fora_do_ar()
        return MSG_SHOPEE_RECUSOU + ("\n   " + pagina if pagina else "")

    def _encerrar_shopee_ligado(self):
        return bool(self.cfg.get("encerrar_shopee") and self.ao_encerrar_tiktok)

    # Consultar a cada ~0,4 s (25/11) e depois a cada ~1,4 s (27/11) coincidia com HTTP 403 do TikTok (bloqueio):
    # aí, quando a live engasgava, o painel não conseguia reconectar. Quando a live acaba o vídeo também para,
    # e a conferência logo após a queda finaliza na hora.
    VIGIA_INTERVALO_SEG = 10
    VIGIA_ESPERA_ERRO = (10, 20, 30)   # s depois de 1, 2, 3+ erros seguidos (403 = o TikTok pediu calma)

    def _vigiar_fim(self, sessao, usuario, sessionid):
        """Durante a live, pergunta ao TikTok a cada VIGIA_INTERVALO_SEG se ela acabou (2 confirmações seguidas)."""
        seguidos, avisou_pausa, erros = 0, False, 0
        while not sessao._parar.is_set() and not self._parar.is_set():
            situacao, codigo = status_sala(usuario, sessionid)
            espera = self.VIGIA_INTERVALO_SEG
            if situacao == "encerrada":
                erros = 0
                seguidos += 1
                if seguidos == 1:
                    primeira = time.time()
                    continue                # reconfirma na hora, sem esperar
                self._tiktok_encerrou_em = primeira
                log.info("⏹ O TikTok confirmou que a live de @%s acabou (código %s).", usuario, codigo)
                sessao.parar()
                return
            elif situacao == "erro":
                seguidos = 0
                erros += 1
                espera = self.VIGIA_ESPERA_ERRO[min(erros, len(self.VIGIA_ESPERA_ERRO)) - 1]
            else:
                seguidos = erros = 0
                if situacao == "pausada" and not avisou_pausa:
                    log.info("⏸ A live foi pausada no TikTok. A Shopee continua aberta.")
                    avisou_pausa = True
                elif situacao == "ao_vivo":
                    avisou_pausa = False
            sessao._parar.wait(espera)     # acorda na hora se a transmissão parar

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
        # a transmissão caiu e o TikTok não respondeu (ex.: HTTP 403) quando conferi se a live acabou:
        # sigo conferindo enquanto espero, para finalizar a Shopee assim que o TikTok confirmar o fim
        fim_a_confirmar = False
        caiu_em = 0.0       # quando a última transmissão parou (logo depois de uma queda, tenta mais rápido)

        while not self._parar.is_set():
            stream = obter_stream(usuario, sessionid)
            if self._parar.is_set():
                break
            if not stream and fim_a_confirmar:
                situacao, codigo = status_sala(usuario, sessionid)
                if situacao == "encerrada":
                    self._tiktok_encerrou_em = time.time()
                    log.info("⏹ O TikTok confirmou que a live de @%s acabou (código %s).", usuario, codigo)
                    self._finalizar_shopee(usuario)
                    return
                if situacao != "erro":
                    fim_a_confirmar = False     # a live continua (ou pausada): só espera ela voltar
            if not stream:
                texto = motivo["texto"] or f"@{usuario} ainda não está ao vivo."
                # a live caiu há menos de 3 min: tenta de novo a cada 5 s (antes esperava 20 s com a Shopee parada)
                espera = 5 if time.time() - caiu_em < 180 else intervalo
                if texto != ultimo_motivo:     # só registra quando o motivo muda
                    erro = texto.startswith("Não consegui")
                    self._estado("aguardando", f"{texto} Verificando a cada {espera}s...",
                                 logging.WARNING if erro else logging.INFO)
                    ultimo_motivo = texto
                self._parar.wait(espera)
                continue

            if ultimo_motivo is not None or self._base_gravacao is None:
                # live nova (estava offline antes): novo nome de arquivo de gravação
                self._base_gravacao = time.strftime("%Y-%m-%d_%Hh%M_") + usuario
            ultimo_motivo = None
            fim_a_confirmar = False
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
                    elif situacao == "erro":
                        fim_a_confirmar = True
                if self._tiktok_encerrou_em:
                    self._finalizar_shopee(usuario)
                    return

            dur = int(time.time() - inicio)
            caiu_em = time.time()
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
            pagina = self._pagina_fora_do_ar()
            if pagina:
                msg += "\n   " + pagina
            self._estado("reconectando", msg, logging.WARNING)
            self._parar.wait(5 if dur > 60 else 15)

        self._estado("parado", "Espelhamento encerrado.")

    sentido = "ts"      # pasta de gravação: TikTok -> Shopee

    def _proximo_arquivo_gravacao(self):
        base = self._base_gravacao or time.strftime("%Y-%m-%d_%Hh%M_live")
        pasta = pasta_gravacao(self.sentido)
        if (str(pasta), base) in _juntando:   # ainda emendando a execução anterior desta live: usa outro nome
            base += "_b"
            self._base_gravacao = base
        caminho = pasta / f"{base}.ts"
        parte = 2
        while caminho.exists():            # a live caiu e voltou: grava a continuação em outra parte
            caminho = pasta / f"{base}_parte{parte}.ts"
            parte += 1
        if (pasta, base) not in self._bases_gravadas:
            self._bases_gravadas.append((pasta, base))
        self._partes.append((pasta, base, caminho, time.time()))   # chamado quando chega o 1º dado da parte
        return caminho

    def marcar(self):
        """⭐ Marca o momento atual para virar corte de Reels no fim da live. Devolve o erro, ou None."""
        if not self._partes:
            return "nada está sendo gravado (ligue 'Gravar' antes de iniciar)"
        self._marcas.append(time.time())
        return None

    @property
    def marcas(self):
        return len(self._marcas)

    def juntar_gravacoes(self):
        """Chamado quando a transmissão termina: emenda as partes de cada live gravada num vídeo só."""
        bases, self._bases_gravadas = self._bases_gravadas, []
        partes, self._partes = self._partes, []
        marcas, self._marcas = self._marcas, []
        import cortes
        try:
            plano = cortes.planejar(partes, marcas)     # antes de emendar: onde cada ⭐ caiu em cada parte
        except Exception as e:          # noqa: BLE001
            log.warning("🎬 Não consegui preparar os cortes para Reels (%s).", e)
            plano = []
        for pasta, base in bases:
            try:
                juntar_partes(self.ffmpeg, pasta, base)
            except Exception as e:      # nunca pode atrapalhar o painel; as partes ficam como estão
                log.warning("Não consegui juntar as partes da gravação %s (%s). As partes foram mantidas.", base, e)
        try:
            cortes.gerar(self.ffmpeg, plano)
        except Exception as e:          # noqa: BLE001
            log.warning("🎬 Não consegui fazer os cortes para Reels (%s).", e)

    def ajustar_voz(self, ms):
        """Shopee -> TikTok: muda, com a live no ar, quanto a imagem é atrasada para casar com a voz.
        ms=None: automático (valor medido)."""
        from transmissao import ajuste_voz_efetivo
        self.cfg["sincronia_auto"] = ms is None
        if ms is not None:
            self.cfg["ajuste_voz_ms"] = int(ms)
        sessao = self._sessao
        if sessao:
            if ms is None:
                log.info("🎚 Sincronia da voz automática (agora: %d ms, vídeo %s; o painel confere e corrige "
                         "sozinho). Aplicando sem parar a live...",
                         ajuste_voz_efetivo(self.cfg, formato=getattr(sessao, "formato_video", None)),
                         (getattr(sessao, "formato_video", None) or "?").upper())
            else:
                log.info("🎚 Sincronia da voz: imagem atrasada em %d ms. Aplicando sem parar a live...", int(ms))
            sessao.reaplicar("ajuste da sincronia da voz")

    def _transmitir(self, url, cabecalhos, arquivo=None, repetir=False, inicio=0.0, fila=None, inicio_idx=0):
        from transmissao import Transmissao
        gravar_em = renovar = None
        if self.cfg.get("gravar") and not arquivo:
            gravar_em = self._proximo_arquivo_gravacao      # um arquivo por trecho (parte2, parte3...)
        if not arquivo:
            usuario, sessionid = self.cfg["tiktok_usuario"], self.cfg.get("tiktok_sessionid", "")
            renovar = lambda: obter_stream(usuario, sessionid)    # link novo para trocar sem parar
        # com a imagem "Já voltamos!" no ar: a última consulta (do renovar) disse que a live ACABOU? (pausada não)
        origem_encerrou = None if arquivo else (lambda: motivo.get("codigo") not in (None, 2, 3))
        sessao = Transmissao(self.cfg, self.ffmpeg, url, cabecalhos, self.destino, self.preview,
                             aviso=self.aviso, sem_janela=self.sem_janela, resumir_stats=resumir_stats,
                             arquivo=arquivo, repetir=repetir, gravar_em=gravar_em, renovar=renovar,
                             inicio=inicio, origem_encerrou=origem_encerrou, fila=fila, inicio_idx=inicio_idx)
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
            self._posicao_queda = sessao.posicao_ao_cair
            self._idx_queda = sessao.idx_ao_cair
            self._voltas = sessao.voltas
            self._sessao = None


class Reprisador(Espelhador):
    """Manda um vídeo gravado para a Shopee, uma vez ou em loop, com o mesmo atraso e mudo."""

    onde_destino = "na Shopee"
    alarme_fim = "tempo_shopee"

    def __init__(self, cfg, ffmpeg, destino, arquivo, repetir=True, limite_seg=0, ao_acabar_tempo=None,
                 estado_destino=None, **kw):
        """arquivo: um vídeo, ou uma lista de vídeos (fila: passam em sequência, emendados sem parar a live).
        limite_seg: em loop, por quanto tempo a reprise fica no ar (0 = até clicar em Parar).
        ao_acabar_tempo: função que encerra a live no destino quando o tempo acaba (a extensão), ou None.
        estado_destino: função que devolve o status da página da live (a extensão), ou None."""
        super().__init__(cfg, ffmpeg, destino, **kw)
        self.estado_destino = estado_destino
        self._retomar_em = 0.0          # depois de uma queda, continua deste ponto do vídeo
        self._retomar_idx = 0           # ... e deste vídeo da fila
        self._posicao_queda = None
        self._idx_queda = None
        lista = arquivo if isinstance(arquivo, (list, tuple)) else [arquivo]
        self.fila = [Path(a) for a in lista]
        self.arquivo = self.fila[0]
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

    # -------------------------------------------------------- retomada após queda

    def _guardar_ponto(self, dur):
        """Depois de uma queda no meio da reprise, a próxima tentativa continua de onde a Shopee parou
        (um pouco antes, para ninguém perder nada) em vez de voltar ao começo do vídeo."""
        if self._shopee_recusou and dur < 60:
            return                                  # recusou logo no início: mantém o mesmo ponto
        pos = self._posicao_queda
        if self._retomar_em and dur < 15 and not self._shopee_recusou:
            self._retomar_em = 0.0                  # o ponto ficou além do fim do vídeo: recomeça
        elif pos is not None and dur >= 15:
            self._retomar_em = max(0.0, pos - 5)
            if self._idx_queda is not None and 0 <= self._idx_queda < len(self.fila):
                self._retomar_idx = self._idx_queda

    # -------------------------------------------------------- fila de vídeos

    def _nome(self):
        """Nome para o registro: o vídeo, ou a fila inteira."""
        if len(self.fila) == 1:
            return self.arquivo.name
        return f"a fila de {len(self.fila)} vídeos ({' → '.join(p.name for p in self.fila)})"

    def _continuando(self):
        if not self._retomar_em:
            return ""
        qual = f" do vídeo {self._retomar_idx + 1} da fila" if len(self.fila) > 1 else " do vídeo"
        return f", continuando de {_hms(self._retomar_em)}{qual}"

    def _msg_fim(self, extra=""):
        if len(self.fila) == 1:
            return f"A reprise de {self.arquivo.name} terminou.{extra}"
        return f"A fila de reprises terminou ({len(self.fila)} vídeos).{extra}"

    def _transmitir_fila(self):
        return self._transmitir(None, {}, arquivo=self.fila[self._retomar_idx], repetir=self.repetir,
                                inicio=self._retomar_em, fila=self.fila, inicio_idx=self._retomar_idx)

    def executar(self):
        nome = self._nome()
        self._ligar_cronometro()
        recusas_seguidas = 0
        while not self._parar.is_set():
            self._estado("reprise", f"Reprisando {nome} ({self._jeito()}) na Shopee{self._continuando()}...")
            inicio = time.time()
            ultimo_erro = self._transmitir_fila()
            if self._parar.is_set():
                break
            dur = int(time.time() - inicio)
            self._guardar_ponto(dur)
            if self._shopee_recusou and dur < 60:
                recusas_seguidas += 1
            else:
                recusas_seguidas = 0
            if recusas_seguidas >= 3:
                self._estado("parado", self._msg_recusou(), logging.ERROR)
                self._parar.set()
                return
            if not self.repetir and not self._shopee_recusou:
                fim = self._msg_fim()
                if ultimo_erro and dur < 10:
                    fim = f"Não consegui reprisar {nome}: {ultimo_erro}"
                self._estado("parado", fim, logging.WARNING if dur < 10 else logging.INFO)
                return
            msg = f"A reprise parou após {dur // 60}min{dur % 60:02d}s. Tentando de novo..."
            if ultimo_erro:
                msg += f"\n   Motivo: {ultimo_erro}"
            if recusas_seguidas:
                msg += f"\n   Tentativa {recusas_seguidas} de 3. Confira se a live na Shopee está aberta e se a chave é a atual."
            pagina = self._pagina_fora_do_ar()
            if pagina:
                msg += "\n   " + pagina
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
    SEG_PAUSA = 5       # sem vídeo da Shopee por este tempo = live pausada (se a Shopee confirmar)
    # A imagem pode parar de chegar por alguns segundos por falha de conexão, com a live NO AR na Shopee.
    # Isso não é pausa: reconecta sem mexer no TikTok. Só depois deste tempo sem imagem vira "pausa".
    SEG_TRAVADA_MAX = 30
    # Pausa de verdade: só encerra a live no TikTok depois de ~este tempo sem imagem (antes eram ~7 s, e quedas
    # de conexão de 6 a 11 s encerravam o TikTok à toa — 23/11 09:43 e 11:16).
    ESPERA_ENCERRAR = 30

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
        self._tiktok_pausado = False    # a extensão pausou a live no TikTok (Shopee pausada)
        self._vez_pausa = 0             # muda a cada pausa/volta: cancela uma ação do TikTok que ficou velha
        self._lock_tiktok = threading.Lock()

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

    def _tiktok_depois(self, espera, vez, pausar):
        """Shopee pausou: ENCERRA a live no TikTok daqui a `espera` s (quando o atraso termina de sair),
        se ela continuar pausada. Shopee voltou: retoma a live no TikTok, se ela tiver ficado só pausada."""
        if self._parar.wait(max(0.0, espera)):
            return
        with self._lock_tiktok:
            if vez != self._vez_pausa or self._shopee_acabou:
                return
            if pausar and self._shopee_pausada:
                # confere de novo, na hora, antes de encerrar: a imagem voltou? a Shopee diz que a live continua?
                if self._ultimo_dado is not None and time.monotonic() - self._ultimo_dado < 3:
                    return
                situacao, _ = status_shopee(self.sessao_id)
                if vez != self._vez_pausa or not self._shopee_pausada:
                    return
                if situacao in ("ao_vivo", "erro"):
                    sem = time.monotonic() - self._ultimo_dado if self._ultimo_dado else 0
                    log.warning("⚠ A imagem da Shopee não chega há %.0fs, mas %s. NÃO encerrei a live no TikTok "
                                "(ela mostra a imagem 'Já voltamos'). Confira a live da Shopee.", sem,
                                "a Shopee diz que a live continua no ar" if situacao == "ao_vivo"
                                else "não consegui confirmar com a Shopee se ela pausou")
                    return
                log.info("⏹ A live pausou na Shopee: encerrando a live no TikTok...")
                r = self._acao_tiktok("encerrar", "tiktok_encerrar")
                if r.get("ok"):
                    self._shopee_acabou = True          # não volta mais: o TikTok foi encerrado
                    self._estado("parado", "A live pausou na Shopee e a live no TikTok foi encerrada.")
                    self._parar.set()
                    s = self._sessao
                    if s is not None:
                        s.parar()
                    return
                # não deu para encerrar: pausa (volta sozinha se a Shopee voltar) e avisa
                if not self._tiktok_pausado:
                    p = self._acao_tiktok("pausar", "tiktok_pausar")
                    self._tiktok_pausado = bool(p.get("ok"))
                log.warning("⚠ Não consegui encerrar a live no TikTok. %s ENCERRE A LIVE NO TIKTOK agora "
                            "(Gerenciador de LIVE).", "Deixei ela PAUSADA." if self._tiktok_pausado
                            else "Ela está mostrando a imagem 'Já voltamos'.")
                self.aviso("alarme", "finalizar_tiktok")
            elif not pausar and not self._shopee_pausada and self._tiktok_pausado:
                self._acao_tiktok("retomar", "tiktok_retomar")
                self._tiktok_pausado = False

    def _agendar_tiktok(self, pausar):
        if not self.tiktok:
            return
        self._vez_pausa += 1
        atraso = float(self.cfg.get("atraso_seg", 7))
        if pausar:
            # espera a pausa se confirmar (~ESPERA_ENCERRAR s sem imagem) antes de encerrar o TikTok
            espera = max(atraso - self.SEG_PAUSA + 0.5, self.ESPERA_ENCERRAR - self.SEG_PAUSA)
        else:
            # o vídeo novo volta no TikTok depois do atraso: retoma um pouco antes, para não perder nada
            espera = atraso - 1.5
        from transmissao import nome_thread
        threading.Thread(target=self._tiktok_depois, args=(espera, self._vez_pausa, pausar), daemon=True,
                         name=nome_thread("tiktok")).start()

    def _vigiar_pausa(self):
        """Sem vídeo da Shopee por 5 s = pausa. O envio para o OBS é fechado depois que o atraso termina de
        sair e o OBS mostra a imagem "Já voltamos" (fica atrás do vídeo). Com a extensão, a live no TikTok
        também é pausada nessa hora (o Gerenciador na web não tem botão de pausar: a extensão desliga a câmera
        e o som da conexão da live) e retomada quando o vídeo da Shopee volta."""
        ultimo_reinicio = 0.0
        ultima_troca = 0.0              # última reconexão por imagem travada (live no ar na Shopee)
        avisou_travada = False
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
                    elif situacao in ("ao_vivo", "erro") and parado < self.SEG_TRAVADA_MAX:
                        # a Shopee diz que a live continua (ou não respondeu): é a IMAGEM que parou de chegar,
                        # não uma pausa. Reconecta sem parar e sem mexer na live do TikTok.
                        if s is not None and time.monotonic() - ultima_troca >= 10:
                            ultima_troca = time.monotonic()
                            if not avisou_travada:
                                avisou_travada = True
                                log.warning("📶 A imagem da Shopee parou de chegar há %.0fs, mas %s. Reconectando "
                                            "sem encerrar a live no TikTok...", parado,
                                            "a live continua no ar na Shopee" if situacao == "ao_vivo"
                                            else "a Shopee não confirmou pausa")
                            s.reaplicar("a imagem da Shopee parou de chegar")
                    elif time.monotonic() - self._ultimo_dado >= self.SEG_PAUSA:
                        if self.tiktok:
                            falta = self.ESPERA_ENCERRAR - parado
                            log.info("⏸ A live parou na Shopee (sem vídeo há %.0fs). %s", parado,
                                     f"Se não voltar em ~{falta:.0f}s, a live no TikTok é encerrada." if falta >= 2 else
                                     "Antes de encerrar a live no TikTok, confiro com a Shopee se ela pausou mesmo.")
                        else:
                            log.info("⏸ A live parou na Shopee (sem vídeo há %.0fs). O TikTok mostra a imagem "
                                     "'Já voltamos' até ela voltar.", parado)
                        self.aviso("pausa", "shopee")
                        self._shopee_pausada = True
                        self._agendar_tiktok(pausar=True)
                        ultimo_reinicio = time.monotonic()
                        s = self._sessao
                        if s is not None:
                            s.terminar_entrada()    # o que está no atraso termina de sair e entra a imagem
                elif not self._shopee_pausada and parado < 1.5 and avisou_travada:
                    avisou_travada = False
                    log.info("✅ A imagem da Shopee voltou. A live no TikTok seguiu no ar.")
                elif self._shopee_pausada and parado < 1.5:
                    avisou_travada = False
                    atraso = float(self.cfg.get("atraso_seg", 7))
                    log.info("▶ A live voltou na Shopee. O vídeo volta no TikTok em ~%.0fs (atraso).", atraso)
                    self._shopee_pausada = False
                    self._agendar_tiktok(pausar=False)
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
            with self._lock_tiktok:
                self._vez_pausa += 1
                r = self._acao_tiktok("encerrar", "tiktok_encerrar")
                if r.get("ok"):
                    self._estado("parado", f"A live de {nome} acabou na Shopee e a live no TikTok foi encerrada.")
                    self._parar.set()
                    return
                # não deu para encerrar: pelo menos pausa, para não ficar no ar com a imagem parada
                if not self._tiktok_pausado:
                    p = self._acao_tiktok("pausar", "tiktok_pausar")
                    self._tiktok_pausado = bool(p.get("ok"))
        pausei = " Deixei a live no TikTok PAUSADA, mas" if self._tiktok_pausado else ""
        self._estado("parado", f"⚠ A live de {nome} acabou na Shopee e o envio para o TikTok parou.{pausei} "
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
                # a mesma live da Shopee (mesmo link) iniciada de novo continua no mesmo vídeo
                self._base_gravacao = _bases_shopee.get(str(self.sessao_id)) or (
                    time.strftime("%Y-%m-%d_%Hh%M_shopee_") + re.sub(
                        r"[^A-Za-z0-9._-]", "", info.get("usuario") or str(self.sessao_id)))
                _bases_shopee[str(self.sessao_id)] = self._base_gravacao
            if not esteve_no_ar:
                titulo = f" ({info['titulo']})" if info.get("titulo") else ""
                log.info("Live da Shopee encontrada: %s%s.", nome, titulo)
                if self._vigia_pausa is None:
                    if self.tiktok:
                        log.info("Se a live da Shopee acabar ou ficar pausada por ~%ds, a live no TikTok é "
                                 "encerrada sozinha (falha rápida de conexão não encerra).", self.ESPERA_ENCERRAR)
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

    def _transmitir(self, url, cabecalhos, arquivo=None, repetir=False, inicio=0.0):
        from transmissao import Transmissao
        gravar_em = self._proximo_arquivo_gravacao if self.cfg.get("gravar") else None
        sessao = Transmissao(self.cfg, self.ffmpeg, url, cabecalhos, self.destino, self.preview,
                             aviso=self.aviso, sem_janela=self.sem_janela, resumir_stats=resumir_stats,
                             gravar_em=gravar_em, renovar=self._link_novo, silencioso=self._shopee_pausada,
                             salvar_ajuste_voz=salvar_ajuste_voz_medido)
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
            self._posicao_queda = sessao.posicao_ao_cair
            self._voltas = sessao.voltas
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
        nome = self._nome()
        self._ligar_cronometro()
        falhas = 0
        while not self._parar.is_set():
            erro_obs = obs_tiktok.garantir(log)
            if self._parar.is_set():
                break
            if erro_obs:
                self._estado("parado", erro_obs, logging.ERROR)
                return
            self._estado("reprise", f"Reprisando {nome} ({self._jeito()}) no TikTok pelo OBS{self._continuando()}...")
            inicio = time.time()
            ultimo_erro = self._transmitir_fila()
            if self._parar.is_set():
                break
            dur = int(time.time() - inicio)
            self._guardar_ponto(dur)
            falhas = falhas + 1 if (self._shopee_recusou and dur < 60) else 0
            if falhas >= 3:
                self._estado("parado", "O OBS recusou o vídeo 3 vezes seguidas. Feche o OBS e clique em "
                                       "Iniciar de novo (o painel reabre o OBS configurado).", logging.ERROR)
                return
            if not self.repetir and not self._shopee_recusou:
                fim = self._msg_fim(" Encerre a live no TikTok.")
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


class Gravador:
    """Só GRAVA uma live, sem transmitir para lugar nenhum (acervo para reprisar depois).

    sentido "ts": grava a live do TikTok na pasta "TikTok para Shopee" (para reprisar na Shopee).
    sentido "st": grava a live da Shopee na pasta "Shopee para TikTok" (para reprisar no TikTok).

    O vídeo é copiado como chega (sem recodificar: quase não pesa no computador). Se a conexão cair,
    reconecta e grava a continuação numa parte nova; no fim as partes são emendadas num vídeo só.
    Termina sozinho quando a plataforma confirma que a live acabou (ou ao clicar em Parar)."""

    ESPERA = 15             # s entre as consultas enquanto a live não começa
    ESPERA_QUEDA = 5        # s logo depois de uma queda (até 3 min)

    def __init__(self, sentido, cfg, ffmpeg, aviso=None, usuario="", sessionid="", sessao_id=None):
        self.sentido = "st" if sentido == "st" else "ts"
        self.cfg = cfg
        self.ffmpeg = ffmpeg
        self.aviso = aviso or (lambda estado, msg: None)
        self.usuario = usuario
        self.sessionid = sessionid
        self.sessao_id = sessao_id
        self._parar = threading.Event()
        self._proc = None
        self._motivo = {"texto": "", "codigo": None}
        self._base = None
        self._bases = []
        self.arquivo_atual = None
        self.desde = None               # quando começou a gravar (a primeira parte)
        self.partes = 0

    @property
    def origem(self):
        return f"@{self.usuario}" if self.sentido == "ts" else f"a live {self.sessao_id} da Shopee"

    def parar(self):
        self._parar.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                # "q" no stdin fecha o arquivo direitinho; se não responder, mata
                p.stdin.write(b"q")
                p.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass
            threading.Timer(3, lambda: p.poll() is None and p.kill()).start()

    def _estado(self, estado, msg, nivel=logging.INFO):
        log.log(nivel, msg)
        self.aviso(estado, msg)

    # -------------------------------------------------------- onde está a live

    def _link(self):
        """(url, cabecalhos, nome_para_o_arquivo) se a live estiver no ar, senão None (motivo em self._motivo)."""
        if self.sentido == "ts":
            situacao, res = _via_api(self.usuario, self.sessionid, self._motivo)
            if situacao == "ao_vivo":
                url, cab = res[0]
                return url, cab, self.usuario
            if situacao == "offline":
                self._motivo["texto"] = f"@{self.usuario} ainda não está ao vivo no TikTok."
                return None
            achado = _via_ytdlp(self.usuario, self.sessionid)
            if achado:
                return achado[0], achado[1], self.usuario
            self._motivo["texto"] = f"Não consegui confirmar a live de @{self.usuario}: {res}."
            return None
        situacao, info = status_shopee(self.sessao_id)
        self._motivo["codigo"] = info.get("codigo")
        if situacao == "ao_vivo":
            nome = re.sub(r"[^A-Za-z0-9._-]", "", info.get("usuario") or str(self.sessao_id))
            return info["urls"][0], {"Referer": "https://live.shopee.com.br/"}, "shopee_" + nome
        if situacao == "erro":
            self._motivo["texto"] = f"Não consegui consultar a live da Shopee: {info.get('erro')}."
        elif info.get("codigo") == 2:
            self._motivo["texto"] = "Essa live da Shopee já acabou. Cada live tem um link próprio: cole o link da live nova."
        else:
            self._motivo["texto"] = "A live da Shopee ainda não está no ar."
        return None

    def _acabou(self):
        """A plataforma confirma que a live terminou de vez? (pausa não conta)"""
        if self.sentido == "ts":
            situacao, _ = status_sala(self.usuario, self.sessionid)
            return situacao == "encerrada"
        situacao, info = status_shopee(self.sessao_id)
        return situacao == "fora_do_ar" and info.get("codigo") == 2

    # -------------------------------------------------------- arquivo

    def _proximo_arquivo(self):
        pasta = pasta_gravacao(self.sentido)
        pasta.mkdir(parents=True, exist_ok=True)
        if (str(pasta), self._base) in _juntando:
            self._base += "_b"
        caminho = pasta / f"{self._base}.ts"
        parte = 2
        while caminho.exists():
            caminho = pasta / f"{self._base}_parte{parte}.ts"
            parte += 1
        if (pasta, self._base) not in self._bases:
            self._bases.append((pasta, self._base))
        return caminho

    def _gravar(self, url, cab, caminho):
        """Grava até a conexão cair ou clicar em Parar. Devolve quantos bytes foram gravados."""
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostats"]
        if cab:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in cab.items())]
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "3",
                "-reconnect_on_http_error", "4xx,5xx", "-rw_timeout", "15000000", "-i", url,
                "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "mpegts", "-y", str(caminho)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, creationflags=flags)
        if self._parar.is_set():
            proc.kill()
        ultima = ""
        for linha in proc.stderr:
            linha = linha.decode("utf-8", "replace").strip()
            if linha and linha != ultima:
                log.debug("gravador: %s", linha)
                ultima = linha
        proc.wait()
        self._proc = None
        try:
            tamanho = caminho.stat().st_size
        except OSError:
            tamanho = 0
        if tamanho == 0:
            try:
                caminho.unlink()
            except OSError:
                pass
        return tamanho

    # -------------------------------------------------------- laço

    def executar(self):
        self._estado("aguardando", f"Só gravar: esperando {self.origem} entrar ao vivo...")
        ultimo_motivo, caiu_em, gravou = None, 0.0, 0
        try:
            while not self._parar.is_set():
                link = self._link()
                if self._parar.is_set():
                    break
                if not link:
                    if gravou and self._acabou():
                        break
                    texto = self._motivo.get("texto") or f"{self.origem} não está ao vivo."
                    espera = self.ESPERA_QUEDA if time.time() - caiu_em < 180 else self.ESPERA
                    if texto != ultimo_motivo:
                        self._estado("aguardando" if not gravou else "reconectando",
                                     f"Só gravar: {texto} Verificando a cada {espera}s...")
                        ultimo_motivo = texto
                    self._parar.wait(espera)
                    continue
                ultimo_motivo = None
                url, cab, nome = link
                if self._base is None:
                    self._base = time.strftime("%Y-%m-%d_%Hh%M_") + nome
                caminho = self._proximo_arquivo()
                self.arquivo_atual = caminho
                self.partes += 1
                if self.desde is None:
                    self.desde = time.time()
                self._estado("gravando", f"⏺ Gravando {self.origem} (sem transmitir) em: "
                                         f"gravacoes\\{caminho.parent.name}\\{caminho.name}")
                inicio = time.time()
                tamanho = self._gravar(url, cab, caminho)
                gravou += tamanho
                if self._parar.is_set():
                    break
                dur = int(time.time() - inicio)
                caiu_em = time.time()
                if self._acabou():
                    break
                self._estado("reconectando", f"Só gravar: a conexão com {self.origem} caiu depois de "
                                             f"{dur // 60}min{dur % 60:02d}s. Reconectando (a continuação vai "
                                             "numa parte nova, emendada no fim)...", logging.WARNING)
                self._parar.wait(2)
        finally:
            self.arquivo_atual = None
            if gravou:
                tam = f"{gravou / 1e9:.2f} GB" if gravou >= 1e9 else f"{gravou / 1e6:.0f} MB"
                self._estado("parado", f"⏹ Gravação de {self.origem} encerrada ({tam}). "
                                       f"O vídeo fica na pasta \"{pasta_gravacao(self.sentido).name}\".")
            else:
                self._estado("parado", f"Gravação de {self.origem} encerrada (nada foi gravado).")

    def juntar_gravacoes(self):
        bases, self._bases = self._bases, []
        for pasta, base in bases:
            try:
                juntar_partes(self.ffmpeg, pasta, base)
            except Exception as e:      # noqa: BLE001
                log.warning("Não consegui juntar as partes da gravação %s (%s). As partes foram mantidas.", base, e)


# ---------------------------------------------------------------- gravações

EXTENSOES_VIDEO = {".ts", ".mp4", ".flv", ".mov", ".mkv", ".m4v", ".webm"}
_duracoes = {}
SUFIXO_JUNTANDO = "_juntando.ts"   # arquivo temporário enquanto as partes são emendadas
_juntando = set()                  # (pasta, base) sendo emendados agora (somem da lista de reprise)
_bases_shopee = {}                 # live da Shopee (sessão) -> nome do vídeo (reiniciar continua no mesmo)
_lock_juntar = threading.Lock()


def _partes_da_base(pasta, base):
    """base.ts, base_parte2.ts, base_parte3.ts... na ordem em que foram gravados."""
    partes = []
    padrao = re.compile(re.escape(base) + r"_parte(\d+)\.ts$")
    for p in Path(pasta).iterdir():
        if p.name == f"{base}.ts":
            partes.append((1, p))
        else:
            m = padrao.fullmatch(p.name)
            if m:
                partes.append((int(m.group(1)), p))
    return [p for _, p in sorted(partes)]


def _codecs(ffprobe, arquivo):
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                        "-of", "csv=p=0", str(arquivo)], capture_output=True, text=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return sorted(set(l.strip() for l in r.stdout.splitlines() if l.strip()))


def juntar_partes(ffmpeg, pasta, base):
    """Emenda base.ts + base_parte2.ts + ... num vídeo só (base.ts), sem recodificar.
    As partes só são apagadas depois que o vídeo emendado fica pronto e conferido; se algo der errado,
    elas ficam como estavam."""
    pasta = Path(pasta)
    chave = (str(pasta), base)
    with _lock_juntar:
        _juntando.add(chave)
        try:
            # espera os arquivos pararem de crescer (a gravação acabou de fechar)
            for _ in range(30):
                partes = _partes_da_base(pasta, base)
                if not partes or all(time.time() - p.stat().st_mtime > 3 for p in partes):
                    break
                time.sleep(1)
            partes = [p for p in _partes_da_base(pasta, base) if p.stat().st_size > 0]
            if len(partes) < 2:
                return
            total = sum(p.stat().st_size for p in partes)
            livre = shutil.disk_usage(pasta).free
            if livre < total * 1.05 + 200_000_000:
                log.warning("💾 Sem espaço no disco para juntar as %d partes de %s (precisa de %.1f GB livres). "
                            "As partes foram mantidas.", len(partes), base, total * 1.05 / 1e9 + 0.2)
                return
            ffprobe = _ffprobe()
            if ffprobe:
                tipos = {p.name: _codecs(ffprobe, p) for p in partes}
                if len(set(map(tuple, tipos.values()))) > 1:
                    log.warning("💾 As partes de %s têm formatos de vídeo diferentes (%s); não dá para emendar "
                                "sem perder qualidade. As partes foram mantidas.",
                                base, "; ".join(f"{n}: {', '.join(c)}" for n, c in tipos.items()))
                    return
            log.info("💾 Juntando as %d partes da gravação %s num vídeo só...", len(partes), base)
            temp = pasta / f"{base}{SUFIXO_JUNTANDO}"
            lista = pasta / f"{base}_lista.txt"
            lista.write_text("".join("file '" + str(p).replace("'", r"'\''") + "'\n" for p in partes),
                             encoding="utf-8")
            try:
                r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                                    "-f", "concat", "-safe", "0", "-i", str(lista),
                                    "-map", "0", "-c", "copy", "-f", "mpegts", str(temp)],
                                   capture_output=True, text=True, timeout=3 * 3600,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            finally:
                try:
                    lista.unlink()
                except OSError:
                    pass
            tamanho = temp.stat().st_size if temp.exists() else 0
            if r.returncode != 0 or tamanho < total * 0.9:
                erro = (r.stderr or "").strip().splitlines()[-1:] or [f"código {r.returncode}"]
                log.warning("💾 Não consegui juntar as partes de %s (%s). As partes foram mantidas.", base, erro[0])
                try:
                    temp.unlink()
                except OSError:
                    pass
                return
            for p in partes:
                p.unlink()
            temp.rename(pasta / f"{base}.ts")
            log.info("💾 Gravação salva num vídeo só: %s (%.1f GB, %d partes emendadas).",
                     f"{base}.ts", tamanho / 1e9, len(partes))
        finally:
            _juntando.discard(chave)


def _sendo_juntado(pasta, nome):
    if nome.endswith(SUFIXO_JUNTANDO):
        return True
    for p, base in list(_juntando):
        if p == str(pasta) and (nome == f"{base}.ts" or re.fullmatch(re.escape(base) + r"_parte\d+\.ts", nome)):
            return True
    return False


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
    arquivos = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSOES_VIDEO
                and not _sendo_juntado(pasta, p.name)]
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
    if not p.is_file() or p.suffix.lower() not in EXTENSOES_VIDEO or _sendo_juntado(p.parent, nome):
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
    esp.juntar_gravacoes()


if __name__ == "__main__":
    main()
