"""
Painel no navegador do Espelho de Live.

Dois lados, que podem rodar ao mesmo tempo:
    "ts" = TikTok -> Shopee   (espelhar a live do TikTok ou reprisar gravação na Shopee)
    "st" = Shopee -> TikTok   (levar a live da Shopee ou uma gravação para o TikTok Shop, pelo OBS)

    python painel.py                 abre http://127.0.0.1:8765 no navegador
    python painel.py --sem-navegador só sobe o servidor

Só aceita conexões deste computador (127.0.0.1).
"""

import json
import logging
import os
import sys
import tempfile
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import espelho
from extensao import PASTA_EXTENSAO, ponte
import obs_tiktok
import transmissao

PAGINA = espelho.BASE / "painel.html"
LADOS = ("ts", "st")
NOME_LADO = {"ts": "TikTok → Shopee", "st": "Shopee → TikTok"}
_obs_cache = {"quando": 0.0, "valor": None}


def estado_obs():
    """Situação do OBS para a página (guardada por 3 s para não consultar o Windows toda hora)."""
    if time.time() - _obs_cache["quando"] < 3 and _obs_cache["valor"]:
        return _obs_cache["valor"]
    instalado = obs_tiktok.obs_instalado()
    aberto = instalado and obs_tiktok.obs_aberto()
    pronto = aberto and obs_tiktok.obs_escutando()
    cabo = obs_tiktok.achar_cable_input()[0] is not None
    if not instalado:
        texto = "OBS Studio não instalado."
    elif not cabo:
        texto = "VB-CABLE não encontrado (o microfone virtual). Instale e reinicie o computador."
    elif pronto:
        texto = "OBS aberto, câmera virtual ligada e esperando o vídeo."
    elif aberto:
        texto = "OBS aberto, mas sem a cena do painel. Feche o OBS e clique em Abrir o OBS."
    else:
        texto = "OBS fechado. Ele abre sozinho quando você clicar em Iniciar."
    valor = {"instalado": instalado, "aberto": aberto, "pronto": pronto, "cabo": cabo, "texto": texto}
    _obs_cache.update(quando=time.time(), valor=valor)
    return valor


def _atraso(valor, padrao):
    return min(30.0, max(0.0, float(padrao if valor in (None, "") else valor)))


def _tempo_loop(d):
    """(horas de loop 0..10, encerrar a live quando acabar) vindos da página. 0 = até clicar em Parar."""
    try:
        horas = min(10, max(0, int(d.get("horas") or 0)))
    except (TypeError, ValueError):
        horas = 0
    return horas, bool(d.get("encerrar_fim", True))


def _log_lado(lado, nivel, texto, *args):
    espelho.log.log(nivel, texto, *args, extra={"lado": lado})


class Lado:
    """Estado de uma das transmissões (TikTok -> Shopee ou Shopee -> TikTok)."""

    def __init__(self, sentido):
        self.sentido = sentido
        self.esp = None
        self.estado = "parado"
        self.mensagem = "Preencha os dados e clique em Iniciar."
        self.stats = ""
        self.ao_vivo_desde = None
        self.mudos = 0
        self.ultimos_mudos = deque(maxlen=5)
        self.atraso_ativo = None
        self.mudo_ativo = False
        self.tipo_ativo = None
        self.alarme = False
        self.alarme_tipo = ""
        self.pausa = False
        self.preview = Path(tempfile.gettempdir()) / f"espelho_preview_{sentido}.jpg"

    def apagar_preview(self):
        try:
            self.preview.unlink()
        except OSError:
            pass

    def resumo(self):
        rodando = self.esp is not None
        return {
            "estado": self.estado,
            "mensagem": self.mensagem,
            "stats": self.stats,
            "rodando": rodando,
            "ao_vivo_seg": int(time.time() - self.ao_vivo_desde) if self.ao_vivo_desde else None,
            "mudos": self.mudos,
            "ultimos_mudos": list(self.ultimos_mudos),
            "atraso_ativo": self.atraso_ativo if rodando else None,
            "mudo_ativo": self.mudo_ativo if rodando else False,
            "tipo_ativo": self.tipo_ativo if rodando else None,
            "alarme": self.alarme,
            "alarme_tipo": self.alarme_tipo,
            "pausa": self.pausa if rodando else False,
        }


class Painel:
    """Guarda o estado dos dois lados para a página consultar."""

    def __init__(self):
        self.lock = threading.Lock()
        self.lados = {s: Lado(s) for s in LADOS}
        self.logs = deque(maxlen=500)
        self.prox_id = 1

    # chamado pelo logging
    def add_log(self, nivel, texto, lado=""):
        with self.lock:
            self.logs.append({"id": self.prox_id, "hora": time.strftime("%H:%M:%S"),
                              "nivel": nivel, "texto": texto, "lado": lado})
            self.prox_id += 1

    # avisos do motor (outra thread), já com o lado certo
    def _aviso_para(self, lado):
        def aviso(estado, msg):
            self._aviso(lado, estado, msg)
        return aviso

    def _aviso(self, lado, estado, msg):
        apagar = False
        with self.lock:
            if estado == "stats":
                lado.stats = msg
                return
            if estado == "pausa":
                lado.pausa = msg == "shopee"
                return
            if estado == "mudo":
                lado.mudos += 1
                lado.ultimos_mudos.appendleft({"hora": time.strftime("%H:%M:%S"), "palavra": msg.split("|")[0]})
                return
            if estado == "alarme":
                lado.alarme, lado.alarme_tipo = True, msg
            else:
                lado.estado, lado.mensagem = estado, msg
                if estado in ("ao_vivo", "reprise"):
                    lado.ao_vivo_desde = time.time()
                else:
                    lado.stats, lado.ao_vivo_desde = "", None
                    apagar = True
        if estado == "alarme":
            try:
                import winsound
                for _ in range(3):
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    time.sleep(0.4)
            except Exception:
                pass
        if apagar:
            lado.apagar_preview()

    # -------------------------------------------------------- iniciar / parar

    def iniciar(self, d):
        tipo = d.get("tipo")
        sentido = "st" if (tipo == "tiktok" or (tipo == "reprise" and d.get("sentido") == "st")) else "ts"
        lado = self.lados[sentido]
        with self.lock:
            if lado.esp:
                return f"{NOME_LADO[sentido]} já está rodando. Clique em Parar antes de iniciar de novo."
        if sentido == "st":
            r = self._montar_tiktok(d, lado) if tipo == "tiktok" else self._montar_reprise_tiktok(d, lado)
        else:
            r = self._montar_shopee(d, lado)
        if isinstance(r, str):
            return r
        esp, tipo_ativo, atraso, mudo = r
        with self.lock:
            lado.esp = esp
            lado.alarme = False
            lado.pausa = False
            lado.tipo_ativo = tipo_ativo
            lado.mudos = 0
            lado.ultimos_mudos.clear()
            lado.atraso_ativo = atraso
            lado.mudo_ativo = mudo
        _obs_cache["quando"] = 0.0
        threading.Thread(target=self._rodar, args=(lado, esp), daemon=True, name=f"{sentido}:motor").start()
        return None

    def _montar_shopee(self, d, lado):
        """TikTok -> Shopee: espelhar ao vivo ou reprisar uma gravação da pasta 'TikTok para Shopee'."""
        reprise = d.get("tipo") == "reprise"
        usuario = espelho.limpar_usuario(d.get("usuario"))
        url = str(d.get("url") or "").strip()
        chave = str(d.get("chave") or "").strip()
        sessionid = espelho.limpar_sessionid(d.get("sessionid"))
        arquivo = None
        if reprise:
            arquivo = espelho.caminho_gravacao(d.get("arquivo"), "ts")
            if not arquivo:
                return "Escolha um vídeo da lista de gravações."
            erro = espelho.validar("reprise", url)   # o @ do TikTok não é usado na reprise
        else:
            erro = espelho.validar(usuario, url) or espelho.validar_sessionid(sessionid)
        if erro:
            return erro

        cfg = espelho.ler_config()
        try:
            cfg["atraso_seg"] = _atraso(d.get("atraso"), cfg["atraso_seg"])
        except (TypeError, ValueError):
            return "O atraso precisa ser um número de segundos."
        cfg["mutar_palavras"] = bool(d.get("mutar_palavras", cfg["mutar_palavras"]))
        if cfg["mutar_palavras"] and not transmissao.carregar_modelo():
            return ("O mudo automático precisa do reconhecimento de voz, que não está instalado. "
                    "Desmarque 'Mutar' ou reinstale (veja o LEIA-ME).")
        lembrar = bool(d.get("lembrar_sessionid"))
        if sessionid:
            cfg["tiktok_sessionid"] = sessionid
        elif not lembrar:
            cfg.pop("tiktok_sessionid", None)   # campo vazio e sem "lembrar" = não usar
        cfg["lembrar_sessionid"] = lembrar and bool(cfg.get("tiktok_sessionid"))
        ffmpeg = espelho.achar_ffmpeg(cfg)
        if not ffmpeg:
            return "ffmpeg não encontrado. Instale com: winget install Gyan.FFmpeg"
        if d.get("qualidade") in espelho.QUALIDADES:
            res, br = espelho.QUALIDADES[d["qualidade"]]
            cfg.update(resolucao=res, bitrate_video_kbps=br)
        if d.get("modo") in ("recodificar", "copiar"):
            cfg["modo"] = d["modo"]
        try:
            cfg["fps"] = min(60, max(15, int(d.get("fps") or cfg["fps"])))
            cfg["intervalo_verificacao_seg"] = min(300, max(10, int(d.get("intervalo") or cfg["intervalo_verificacao_seg"])))
        except (TypeError, ValueError):
            return "FPS e intervalo precisam ser números."
        cfg["shopee_url_servidor"] = url
        if reprise:
            cfg["repetir_reprise"] = bool(d.get("repetir", True))
            cfg["horas_reprise"], cfg["encerrar_fim_reprise"] = _tempo_loop(d)
            if cfg["repetir_reprise"] and cfg["horas_reprise"] and cfg["encerrar_fim_reprise"] \
                    and not ponte.status.get("aberto"):
                return ("Para encerrar a live na Shopee quando o tempo acabar, a extensão precisa estar instalada "
                        "no Chrome e conectada. Ou desligue essa opção.")
        else:
            cfg["tiktok_usuario"] = usuario
            cfg["gravar"] = bool(d.get("gravar", cfg.get("gravar")))
            cfg["encerrar_shopee"] = bool(d.get("encerrar_shopee", cfg.get("encerrar_shopee")))
            if cfg["encerrar_shopee"] and not ponte.status.get("aberto"):
                return ("Para finalizar a Shopee sozinho, a extensão precisa estar instalada no Chrome e "
                        "conectada (veja 'Como instalar a extensão'). Ou desligue essa opção.")
        try:
            espelho.salvar_config(cfg)
        except OSError:
            pass

        destino = espelho.montar_destino(url, chave)
        aviso = self._aviso_para(lado)
        if reprise:
            esp = espelho.Reprisador(cfg, ffmpeg, destino, arquivo, repetir=cfg["repetir_reprise"],
                                     limite_seg=cfg["horas_reprise"] * 3600,
                                     ao_acabar_tempo=ponte.encerrar if cfg["encerrar_fim_reprise"] else None,
                                     aviso=aviso, preview=str(lado.preview))
        else:
            esp = espelho.Espelhador(cfg, ffmpeg, destino, aviso=aviso, preview=str(lado.preview),
                                     ao_encerrar_tiktok=ponte.encerrar if cfg["encerrar_shopee"] else None)
            if cfg["encerrar_shopee"]:
                _log_lado("ts", logging.INFO, "Finalização automática ligada: quando a live acabar no TikTok, "
                                              "clico em 'Finalizar Transmissão' na Shopee.")
        return esp, ("reprise" if reprise else "espelho"), cfg["atraso_seg"], cfg["mutar_palavras"]

    def _cfg_tiktok(self, d):
        """Configuração do lado Shopee -> TikTok (atraso e mudo próprios deste lado). Retorna (cfg, erro)."""
        obs = estado_obs()
        if not obs["instalado"]:
            return None, "O OBS Studio não está instalado. Ele é necessário para levar o vídeo até o TikTok."
        if not obs["cabo"]:
            return None, "O VB-CABLE (microfone virtual) não foi encontrado. Instale e reinicie o computador."
        cfg = espelho.ler_config()
        try:
            cfg["atraso_st"] = _atraso(d.get("atraso"), cfg.get("atraso_st", cfg["atraso_seg"]))
        except (TypeError, ValueError):
            return None, "O atraso precisa ser um número de segundos."
        cfg["mutar_st"] = bool(d.get("mutar_palavras", cfg.get("mutar_st", True)))
        if cfg["mutar_st"] and not transmissao.carregar_modelo():
            return None, ("O mudo automático precisa do reconhecimento de voz, que não está instalado. "
                          "Desmarque 'Mutar' ou reinstale (veja o LEIA-ME).")
        if not espelho.achar_ffmpeg(cfg):
            return None, "ffmpeg não encontrado. Instale com: winget install Gyan.FFmpeg"
        return cfg, None

    def _montar_tiktok(self, d, lado):
        """Shopee -> OBS -> Gerenciador de LIVE do TikTok Shop."""
        link = str(d.get("shopee_link") or "").strip()
        sessao_id, erro = espelho.resolver_link_shopee(link)
        if erro:
            return erro[0].upper() + erro[1:] + "."
        cfg, erro = self._cfg_tiktok(d)
        if erro:
            return erro
        cfg["gravar_st"] = bool(d.get("gravar", cfg.get("gravar_st")))
        cfg["shopee_link_live"] = link
        cfg["auto_tiktok"] = bool(d.get("auto_tiktok", cfg.get("auto_tiktok")))
        if cfg["auto_tiktok"] and not ponte.status.get("aberto"):
            return ("Para encerrar o TikTok sozinho, a extensão precisa estar instalada no Chrome e "
                    "conectada. Ou desligue essa opção.")
        try:
            espelho.salvar_config(cfg)
        except OSError:
            pass
        motor = {**cfg, "atraso_seg": cfg["atraso_st"], "mutar_palavras": cfg["mutar_st"], "gravar": cfg["gravar_st"]}
        esp = espelho.EspelhadorShopee(motor, espelho.achar_ffmpeg(cfg), sessao_id,
                                       controle_tiktok=ponte if cfg["auto_tiktok"] else None,
                                       aviso=self._aviso_para(lado), preview=str(lado.preview))
        return esp, "tiktok", cfg["atraso_st"], cfg["mutar_st"]

    def _montar_reprise_tiktok(self, d, lado):
        """Reprisa uma gravação da pasta 'Shopee para TikTok' no TikTok Shop, pelo OBS."""
        arquivo = espelho.caminho_gravacao(d.get("arquivo"), "st")
        if not arquivo:
            return "Escolha um vídeo da lista de gravações."
        cfg, erro = self._cfg_tiktok(d)
        if erro:
            return erro
        cfg["repetir_st"] = bool(d.get("repetir", True))
        cfg["horas_st"], cfg["encerrar_fim_st"] = _tempo_loop(d)
        if cfg["repetir_st"] and cfg["horas_st"] and cfg["encerrar_fim_st"] and not ponte.status.get("aberto"):
            return ("Para encerrar a live no TikTok quando o tempo acabar, a extensão precisa estar instalada "
                    "no Chrome e conectada. Ou desligue essa opção.")
        try:
            espelho.salvar_config(cfg)
        except OSError:
            pass
        motor = {**cfg, "atraso_seg": cfg["atraso_st"], "mutar_palavras": cfg["mutar_st"]}
        esp = espelho.ReprisadorTikTok(motor, espelho.achar_ffmpeg(cfg), arquivo, repetir=cfg["repetir_st"],
                                       limite_seg=cfg["horas_st"] * 3600,
                                       ao_acabar_tempo=ponte.tiktok_encerrar if cfg["encerrar_fim_st"] else None,
                                       aviso=self._aviso_para(lado), preview=str(lado.preview))
        return esp, "reprise_tt", cfg["atraso_st"], cfg["mutar_st"]

    def _rodar(self, lado, esp):
        try:
            esp.executar()
        except Exception as e:
            espelho.log.exception("Erro inesperado: %s", e)
        finally:
            with self.lock:
                lado.esp = None
                if lado.estado != "parado":
                    lado.estado, lado.mensagem = "parado", "Transmissão encerrada."
                lado.stats, lado.ao_vivo_desde = "", None
            lado.apagar_preview()

    def parar(self, sentido=None):
        """Para um lado (ou os dois, se sentido for None)."""
        for s in ([sentido] if sentido in LADOS else LADOS):
            lado = self.lados[s]
            with self.lock:
                esp = lado.esp
            if esp:
                _log_lado(s, logging.INFO, "Parando a pedido do painel...")
                threading.Thread(target=esp.parar, daemon=True, name=f"{s}:parar").start()

    def alarme_ok(self, sentido=None):
        with self.lock:
            for s in ([sentido] if sentido in LADOS else LADOS):
                self.lados[s].alarme = False

    def foto(self, sentido):
        """Bytes da prévia do lado, se for recente (menos de 10 s)."""
        lado = self.lados.get(sentido)
        if not lado:
            return None
        try:
            if time.time() - lado.preview.stat().st_mtime < 10:
                return lado.preview.read_bytes()
        except OSError:
            pass
        return None

    def resumo(self, desde_log):
        cfg = espelho.ler_config()
        qual = next((n for n, (r, _) in espelho.QUALIDADES.items() if r == cfg.get("resolucao")),
                    next(iter(espelho.QUALIDADES)))
        with self.lock:
            lados = {s: l.resumo() for s, l in self.lados.items()}
            logs = [l for l in self.logs if l["id"] > desde_log]
        return {
            "lados": lados,
            "logs": logs,
            "shopee": ponte.status,
            "tiktok": ponte.status_tiktok,
            "obs": estado_obs(),
            "config": {
                "encerrar_shopee": bool(cfg.get("encerrar_shopee")),
                "gravar": bool(cfg.get("gravar")),
                "repetir_reprise": bool(cfg.get("repetir_reprise", True)),
                "repetir_st": bool(cfg.get("repetir_st", True)),
                "horas_reprise": int(cfg.get("horas_reprise", 0) or 0),
                "horas_st": int(cfg.get("horas_st", 0) or 0),
                "encerrar_fim_reprise": bool(cfg.get("encerrar_fim_reprise", True)),
                "encerrar_fim_st": bool(cfg.get("encerrar_fim_st", True)),
                "atraso": cfg.get("atraso_seg", 7),
                "mutar_palavras": cfg.get("mutar_palavras", True),
                "atraso_st": cfg.get("atraso_st", cfg.get("atraso_seg", 7)),
                "mutar_st": cfg.get("mutar_st", True),
                "usuario": cfg.get("tiktok_usuario", ""),
                # nunca devolve o valor do sessionid para a página, só se existe um salvo
                "sessionid_salvo": bool(cfg.get("lembrar_sessionid") and cfg.get("tiktok_sessionid")),
                "url": cfg.get("shopee_url_servidor", ""),
                "shopee_link": cfg.get("shopee_link_live", ""),
                "gravar_st": bool(cfg.get("gravar_st")),
                "auto_tiktok": bool(cfg.get("auto_tiktok")),
                "qualidade": qual,
                "modo": cfg.get("modo"),
                "fps": cfg.get("fps"),
                "intervalo": cfg.get("intervalo_verificacao_seg"),
            },
            "qualidades": list(espelho.QUALIDADES),
        }


class Servidor(ThreadingHTTPServer):
    # No Windows, "reusar endereço" deixa um SEGUNDO painel abrir na mesma porta. Desligado, o segundo
    # clique no atalho percebe que o painel já está aberto e só abre o navegador.
    allow_reuse_address = False
    daemon_threads = True


def abrir_navegador(endereco):
    """Abre o painel numa aba nova do Chrome que já está aberto (o Chrome traz a janela para a frente).
    Se não achar o Chrome, usa o navegador padrão."""
    try:
        import subprocess
        import winreg
        caminho = None
        for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(raiz, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    caminho = winreg.QueryValue(k, None)
                    break
            except OSError:
                continue
        if caminho and Path(caminho).is_file():
            subprocess.Popen([caminho, endereco])
            return
    except Exception:
        pass
    webbrowser.open(endereco)


class _ParaPainel(logging.Handler):
    def __init__(self, painel):
        super().__init__()
        self.painel = painel

    def emit(self, record):
        # de qual lado veio: marcado à mão (extra) ou pelo nome da thread ("ts:..." / "st:...")
        lado = getattr(record, "lado", "") or record.threadName.split(":", 1)[0]
        self.painel.add_log(record.levelname, record.getMessage(), lado if lado in LADOS else "")


def _sentido(dados):
    s = dados.get("sentido") if isinstance(dados, dict) else None
    return s if s in LADOS else None


def criar_handler(painel, porta):
    hosts_ok = {f"127.0.0.1:{porta}", f"localhost:{porta}"}
    origens_ok = {f"http://{h}" for h in hosts_ok}

    class Handler(BaseHTTPRequestHandler):
        server_version = "EspelhoPainel"

        def log_message(self, *args):
            pass

        def _enviar(self, codigo, corpo, tipo):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, obj, codigo=200):
            self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

        def _permitido(self):
            # Bloqueia outros sites tentando falar com o painel (DNS rebinding / CSRF).
            if self.headers.get("Host", "") not in hosts_ok:
                return False
            origem = self.headers.get("Origin")
            return origem is None or origem in origens_ok

        def do_GET(self):
            if not self._permitido():
                return self._json({"erro": "proibido"}, 403)
            caminho, _, consulta = self.path.partition("?")
            params = dict(p.split("=", 1) for p in consulta.split("&") if "=" in p)
            if caminho in ("/", "/index.html"):
                self._enviar(200, PAGINA.read_bytes(), "text/html; charset=utf-8")
            elif caminho == "/api/estado":
                try:
                    desde = int(params.get("desde", 0))
                except ValueError:
                    desde = 0
                self._json(painel.resumo(desde))
            elif caminho == "/api/gravacoes":
                sentido = "st" if params.get("pasta") == "st" else "ts"
                self._json({"pasta": str(espelho.pasta_gravacao(sentido)),
                            "nome_pasta": espelho.pasta_gravacao(sentido).name,
                            "itens": espelho.listar_gravacoes(sentido)})
            elif caminho == "/api/preview.jpg":
                foto = painel.foto(params.get("lado", "ts"))
                if foto:
                    self._enviar(200, foto, "image/jpeg")
                else:
                    self._json({"erro": "sem prévia"}, 404)
            else:
                self._json({"erro": "não encontrado"}, 404)

        def do_POST(self):
            if not self._permitido():
                return self._json({"erro": "proibido"}, 403)
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json({"erro": "use JSON"}, 415)
            try:
                tamanho = min(int(self.headers.get("Content-Length", 0)), 64_000)
                dados = json.loads(self.rfile.read(tamanho) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json({"erro": "JSON inválido"}, 400)

            if self.path == "/api/iniciar":
                erro = painel.iniciar(dados if isinstance(dados, dict) else {})
                self._json({"ok": erro is None, "erro": erro}, 200 if erro is None else 400)
            elif self.path == "/api/parar":
                painel.parar(_sentido(dados))
                self._json({"ok": True})
            elif self.path == "/api/shopee/pasta_extensao":
                try:
                    os.startfile(str(PASTA_EXTENSAO))     # abre a pasta da extensão no Explorador
                    self._json({"ok": True, "pasta": str(PASTA_EXTENSAO)})
                except (OSError, AttributeError) as e:
                    self._json({"ok": False, "erro": str(e)}, 500)
            elif self.path == "/api/shopee/pegar_chave":
                r = ponte.pegar_chave()
                if not r.get("ok"):
                    r["erro"] = r.get("detalhe") or r.get("erro")
                self._json(r)
            elif self.path == "/api/shopee/finalizar":
                _log_lado("ts", logging.INFO, "Finalizar transmissão na Shopee: pedido manual pelo painel.")
                r = ponte.encerrar()
                if r.get("ok"):
                    painel.parar("ts")     # a Shopee pede para parar de enviar ("feche o OBS")
                _log_lado("ts", logging.INFO if r.get("ok") else logging.WARNING,
                          "Shopee: %s", r.get("detalhe") or r.get("erro"))
                self._json(r)
            elif self.path == "/api/obs/abrir":
                erro = obs_tiktok.garantir(espelho.log)
                _obs_cache["quando"] = 0.0
                self._json({"ok": erro is None, "erro": erro})
            elif self.path == "/api/alarme/ok":
                painel.alarme_ok(_sentido(dados))
                self._json({"ok": True})
            elif self.path == "/api/tiktok/diagnostico":
                r = ponte.tiktok_diagnostico()
                if r.get("botoes") is not None:
                    _log_lado("st", logging.INFO, "TikTok: %s · contador: %s · botão ⏻ de encerrar: %s. Botões visíveis: %s",
                              "AO VIVO" if r.get("situacao") == "ao_vivo" else r.get("situacao", "?"),
                              r.get("contador") or "não visto", "achei" if r.get("encerrar") else "não achei",
                              " | ".join(r["botoes"]) or "nenhum")
                else:
                    _log_lado("st", logging.WARNING, "TikTok: %s", r.get("detalhe") or r.get("erro"))
                self._json(r)
            elif self.path == "/api/abrir_pasta":
                pasta = espelho.pasta_gravacao("st" if isinstance(dados, dict) and dados.get("pasta") == "st" else "ts")
                pasta.mkdir(parents=True, exist_ok=True)
                try:
                    os.startfile(str(pasta))   # abre no Explorador do Windows
                    self._json({"ok": True})
                except (OSError, AttributeError) as e:
                    self._json({"ok": False, "erro": str(e)}, 500)
            else:
                self._json({"erro": "não encontrado"}, 404)

    return Handler


def main():
    espelho.configurar_log(tela=True)
    painel = Painel()
    espelho.log.addHandler(_ParaPainel(painel))

    cfg = espelho.ler_config()
    porta = int(cfg.get("porta_painel", 8765))
    endereco = f"http://127.0.0.1:{porta}"
    abrir = "--sem-navegador" not in sys.argv

    try:
        servidor = Servidor(("127.0.0.1", porta), criar_handler(painel, porta))
    except OSError:
        print(f"O painel já está aberto. Abrindo {endereco} no Chrome...")
        print("Se não aparecer, digite esse endereço na barra do navegador.")
        if abrir:
            abrir_navegador(endereco)
        sys.exit(3)     # o iniciar.bat fecha esta janela sozinho

    try:
        movidos = espelho.migrar_gravacoes()
        if movidos:
            espelho.log.info("📁 %d gravação(ões) antiga(s) movida(s) para gravacoes\\TikTok para Shopee.", movidos)
    except OSError as e:
        espelho.log.warning("Não consegui organizar as pastas de gravação: %s", e)
    ponte.iniciar()   # conexão com a extensão do Chrome (Shopee e TikTok)
    print("=" * 60)
    print(f"  Painel do Espelho rodando em  {endereco}")
    print("  NÃO feche esta janela enquanto estiver transmitindo.")
    print("  Fechar esta janela encerra o painel e as transmissões.")
    print("=" * 60)
    if abrir:
        threading.Timer(1.0, abrir_navegador, args=(endereco,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for lado in painel.lados.values():
            if lado.esp:
                lado.esp.parar()
        servidor.server_close()


if __name__ == "__main__":
    main()
