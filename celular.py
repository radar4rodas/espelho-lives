"""
Página do painel para o CELULAR, só dentro da rede de casa (Wi-Fi).

Roda num endereço separado do painel principal (porta 8770) e só deixa:
  - ver a situação das lives, quantas pessoas estão assistindo e entrando (TikTok e Shopee), a saúde da
    transmissão e os avisos (o computador continua agindo sozinho; o celular só fica sabendo);
  - encerrar a live no TikTok, encerrar a live na Shopee, encerrar as duas.
Não dá para iniciar transmissões, mudar configurações nem ver dados de login por aqui.

Só aceita aparelhos da rede local (endereços 192.168.x.x, 10.x.x.x, 172.16-31.x.x). Nada é aberto
para a internet: sem abrir porta no roteador, o celular só alcança esta página estando no Wi-Fi de casa.
"""

import ipaddress
import json
import logging
import re
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import contador
import espelho
import notificacoes
from extensao import ponte

PAGINA = espelho.BASE / "celular.html"
PORTA_PADRAO = 8770
LADOS = ("ts", "st")
NOME_LADO = {"ts": "TikTok → Shopee", "st": "Shopee → TikTok"}

_ultima_acao = {"tiktok": None, "shopee": None}    # o que o celular fez por último (aparece na página)


def ip_da_rede():
    """Endereço deste computador no Wi-Fi/cabo de casa (ex.: 192.168.0.10), ou None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))     # não envia nada: só pergunta ao Windows qual placa de rede usaria
        ip = s.getsockname()[0]
        return ip if ipaddress.ip_address(ip).is_private and not ip.startswith("127.") else None
    except OSError:
        return None
    finally:
        s.close()


def _rede_local(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private and not a.is_link_local or a.is_loopback


def _log(texto, *args, nivel=logging.INFO, lado=""):
    espelho.log.log(nivel, "📱 " + texto, *args, extra={"lado": lado})


def _resultado(r):
    r = r or {}
    return {"ok": bool(r.get("ok")), "detalhe": r.get("detalhe") or r.get("erro") or
            ("feito" if r.get("ok") else "sem resposta da extensão")}


def executar_acao(painel, acao):
    """Ações permitidas pelo celular. Devolve {"ok", "detalhe"}."""
    hora = time.strftime("%H:%M")
    if acao == "tiktok_encerrar":
        return _encerrar_tiktok(painel, hora)
    if acao == "shopee_encerrar":
        return _encerrar_shopee(painel, hora)
    if acao == "encerrar_tudo":
        res = {}
        threads = [threading.Thread(target=lambda: res.__setitem__("TikTok", _encerrar_tiktok(painel, hora)),
                                    daemon=True),
                   threading.Thread(target=lambda: res.__setitem__("Shopee", _encerrar_shopee(painel, hora)),
                                    daemon=True)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(25)
        falhas = [n for n in ("TikTok", "Shopee") if not (res.get(n) or {}).get("ok")]
        if not falhas:
            return {"ok": True, "detalhe": "As duas lives foram encerradas."}
        return {"ok": False, "detalhe": "Não consegui encerrar: " + " e ".join(
            f"{n} ({(res.get(n) or {}).get('detalhe', 'sem resposta')})" for n in falhas)}
    if acao == "ntfy_teste":
        return notificacoes.testar()
    if acao == "alarme_ok":
        painel.alarme_ok(None)
        return {"ok": True, "detalhe": "Alarme apagado."}
    return {"ok": False, "detalhe": "ação desconhecida"}


# ---- comentários das lives e resposta pelo celular (a extensão lê e escreve no chat da página aberta no Chrome)
MAX_RESPOSTA = {"tiktok": 100, "shopee": 150}     # limite de letras de cada plataforma


def comentarios():
    r = {}
    for site, onde in (("tiktok", "o Gerenciador de LIVE"), ("shopee", "a página da sua live da Shopee")):
        c = ponte.comentarios(site)
        r[site] = ({"ok": True, **c, "max": MAX_RESPOSTA[site]} if c is not None
                   else {"ok": False, "lista": [], "caixa": False, "max": MAX_RESPOSTA[site],
                         "detalhe": f"Abra {onde} no Chrome para ver os comentários."})
    return r


def responder_comentario(site, texto):
    if site not in MAX_RESPOSTA:
        return {"ok": False, "detalhe": "plataforma desconhecida"}
    texto = " ".join(texto.split())[:MAX_RESPOSTA[site]]
    if not texto:
        return {"ok": False, "detalhe": "Escreva a resposta primeiro."}
    nome = "do TikTok" if site == "tiktok" else "da Shopee"
    _enviadas.append(texto)          # para não me avisar da minha própria resposta
    r = _resultado(ponte.responder(site, texto))
    _log("Celular: resposta no chat %s: «%s» — %s", nome, texto, r["detalhe"],
         nivel=logging.INFO if r["ok"] else logging.WARNING, lado="st" if site == "tiktok" else "ts")
    return r


def _encerrar_tiktok(painel, hora):
    r = _resultado(ponte.tiktok_encerrar())
    _log("Celular: encerrar a live no TikTok: %s", r["detalhe"],
         nivel=logging.INFO if r["ok"] else logging.WARNING, lado="st")
    if r["ok"]:
        _ultima_acao["tiktok"] = f"Encerrada pelo celular às {hora}"
        painel.parar("st")          # a live acabou: para de mandar vídeo para o OBS
    return r


def _encerrar_shopee(painel, hora):
    r = _resultado(ponte.encerrar())
    _log("Celular: encerrar a live na Shopee: %s", r["detalhe"],
         nivel=logging.INFO if r["ok"] else logging.WARNING, lado="ts")
    if r["ok"]:
        _ultima_acao["shopee"] = f"Encerrada pelo celular às {hora}"
        painel.parar("ts")          # a Shopee pede para parar de enviar
    return r


# ---- números da live da Shopee (consulta pública, a mesma da página de espectador)
_live_cache = {"quando": 0.0, "link": None, "sessao": None, "valor": None}
CACHE_SEG = 2           # consulta a plataforma no máximo a cada 2 s (a página pergunta a cada 3 s)

# contadores de entrada (TikTok: entradas; Shopee: visualizações) guardados para calcular quantas pessoas
# estão ENTRANDO: no último minuto e nos últimos 5 minutos
_historico = {"tiktok": deque(), "shopee": deque()}


def _ritmo(nome, total):
    """(entraram no último minuto, entraram nos últimos 5 min) a partir do contador total da plataforma."""
    h = _historico[nome]
    if total is None:
        return None, None
    agora = time.time()
    if h and total < h[-1][1]:
        h.clear()                       # contador voltou para trás: é outra live
    h.append((agora, total))
    while h and agora - h[0][0] > 330:
        h.popleft()

    def desde(seg, estimar):
        ref = h[0]
        for amostra in h:               # a amostra mais nova que tenha pelo menos 'seg' segundos
            if agora - amostra[0] >= seg:
                ref = amostra
            else:
                break
        janela = agora - ref[0]
        if janela < 10:
            return None                 # ainda sem dados suficientes
        if janela >= seg * 0.9 or not estimar:
            return total - ref[1]         # 5 min no começo da live: o que entrou desde que começou a contar
        return round((total - ref[1]) * seg / janela)
    return desde(60, True), desde(300, False)
_lock_live = threading.Lock()


def live_shopee():
    """Espectadores, pico, visualizações e curtidas da live da Shopee.
    Qual live: a que está aberta no Chrome (página da sua live, a mesma que o botão Encerrar usa);
    sem nenhuma aberta, a do link salvo no painel (aba Shopee → TikTok)."""
    with _lock_live:
        aberta = ponte.sessao_shopee
        link = f"sessao:{aberta}" if aberta else espelho.ler_config().get("shopee_link_live", "")
        if not link:
            return {"ok": False, "detalhe": "Abra a página da sua live da Shopee no Chrome para ver os números."}
        if _live_cache["link"] == link and time.time() - _live_cache["quando"] < CACHE_SEG and _live_cache["valor"]:
            return _live_cache["valor"]
        if _live_cache["link"] != link or not _live_cache["sessao"]:
            if aberta:
                sessao = aberta
            else:
                sessao, erro = espelho.resolver_link_shopee(link)
                if erro:
                    return {"ok": False, "detalhe": erro}
            if sessao != _live_cache["sessao"]:
                _historico["shopee"].clear()        # outra live: o ritmo de entrada recomeça
            _live_cache.update(link=link, sessao=sessao)
        situacao, info = espelho.status_shopee(_live_cache["sessao"])
        if situacao == "erro":
            return {"ok": False, "detalhe": info.get("erro") or "a Shopee não respondeu"}
        s = info.get("sessao_bruta") or {}
        # origem=True: não é a página da SUA live aberta no Chrome, e sim a live do link salvo (a que vai
        # para o TikTok). O celular não mostra isso como "sua live na Shopee no ar".
        valor = {"ok": True, "origem": not aberta,
                 "no_ar": situacao == "ao_vivo", "titulo": info.get("titulo"), "nome": info.get("nome"),
                 "espectadores": s.get("viewer_count"), "pico": s.get("max_member_cnt"),
                 "visualizacoes": s.get("member_cnt"), "curtidas": s.get("like_cnt"),
                 "compartilhamentos": s.get("share_cnt"),
                 "entrando_min": None, "entrando_5min": None,
                 "no_ar_seg": int(time.time() - s["start_time"] / 1000)
                 if situacao == "ao_vivo" and s.get("start_time") else None}
        if valor["no_ar"]:
            valor["entrando_min"], valor["entrando_5min"] = _ritmo("shopee", s.get("member_cnt"))
        _live_cache.update(quando=time.time(), valor=valor)
        return valor


# ---- pessoas assistindo a live no TikTok
# 1º: lidas da página do Gerenciador de LIVE aberta no Chrome (pela extensão; não pergunta nada ao TikTok)
# 2º: sem a página, consulta pública do perfil, no máximo 1 vez por minuto. Perguntar a cada poucos
#     segundos fez o TikTok bloquear a consulta (erro 403); bloqueado, espera 10 min antes de tentar de novo.
TT_API_INTERVALO = 60
TT_API_ESPERA_BLOQUEIO = 600
_tt_cache = {"quando": 0.0, "valor": None, "bloqueado_ate": 0.0, "fonte": None}
_lock_tt = threading.Lock()


def live_tiktok():
    """Quantas pessoas estão assistindo agora a live da conta do TikTok onde as lives do TikTok Shop são
    feitas ("tiktok_conta_live" no config.json; sem ela, usa o @ do perfil preenchido no painel)."""
    with _lock_tt:
        cfg = espelho.ler_config()
        conta = espelho.limpar_usuario(cfg.get("tiktok_conta_live") or cfg.get("tiktok_usuario") or "")
        pagina = ponte.numeros_tiktok()
        if pagina:
            no_ar = bool(pagina.get("ao_vivo"))
            valor = {"ok": True, "conta": conta, "no_ar": no_ar, "pausada": False, "titulo": "",
                     "assistindo": pagina.get("espectadores"), "entradas": pagina.get("entradas"),
                     "no_ar_seg": pagina.get("no_ar_seg") if no_ar else None,
                     "entrando_min": None, "entrando_5min": None, "fonte": "gerenciador"}
            if no_ar:
                _trocar_fonte("gerenciador")
                valor["entrando_min"], valor["entrando_5min"] = _ritmo("tiktok", pagina.get("entradas"))
            return valor
        if not conta:
            return {"ok": False, "detalhe": 'escreva a conta do TikTok em "tiktok_conta_live" no config.json, '
                                            "ou deixe o Gerenciador de LIVE aberto no Chrome"}
        return _tiktok_pela_api(conta)


def _tiktok_pela_api(conta):
    agora = time.time()
    if agora < _tt_cache["bloqueado_ate"] or (agora - _tt_cache["quando"] < TT_API_INTERVALO and _tt_cache["valor"]):
        return _tt_cache["valor"]
    _tt_cache["quando"] = agora

    def falha(detalhe, bloqueio=False):
        if bloqueio:
            _tt_cache["bloqueado_ate"] = agora + TT_API_ESPERA_BLOQUEIO
        _tt_cache["valor"] = {"ok": False, "detalhe": detalhe}
        return _tt_cache["valor"]
    try:
        from curl_cffi import requests as creq
        r = creq.Session(impersonate="chrome").get(
            "https://www.tiktok.com/api-live/user/room/",
            params={"aid": "1988", "uniqueId": conta, "sourceType": "54"}, timeout=5)
    except Exception as e:
        return falha(f"o TikTok não respondeu ({type(e).__name__})")
    try:
        d = r.json() if r.status_code == 200 else None
    except ValueError:
        d = None
    if d is None:
        return falha("o TikTok não está deixando consultar agora. Deixe o Gerenciador de LIVE aberto "
                     "no Chrome que os números vêm de lá.", bloqueio=True)
    dados = d.get("data") or {}
    if not dados.get("user"):
        return falha(f"não achei o perfil @{conta} no TikTok")
    sala = dados.get("liveRoom") or {}
    stats = sala.get("liveRoomStats") or {}
    codigo = sala.get("status")
    no_ar = codigo in (2, 3)          # 2 = ao vivo, 3 = pausada
    inicio = sala.get("startTime") or 0
    valor = {"ok": True, "conta": conta, "no_ar": no_ar, "pausada": codigo == 3,
             "titulo": (sala.get("title") or "").strip(),
             "assistindo": stats.get("userCount"), "entradas": stats.get("enterCount"),
             "no_ar_seg": int(time.time() - inicio) if no_ar and inicio else None,
             "entrando_min": None, "entrando_5min": None, "fonte": "perfil"}
    if no_ar:
        _trocar_fonte("perfil")
        valor["entrando_min"], valor["entrando_5min"] = _ritmo("tiktok", stats.get("enterCount"))
    _tt_cache["valor"] = valor
    return valor


def _trocar_fonte(fonte):
    """O contador de entradas da página e o do perfil são diferentes: ao trocar, o ritmo recomeça."""
    if _tt_cache.get("fonte") != fonte:
        _historico["tiktok"].clear()
        _tt_cache["fonte"] = fonte


def resumo(painel, desde_log, desde_aviso=0):
    with painel.lock:
        lados = {}
        for s in LADOS:
            l = painel.lados[s]
            lados[s] = {"nome": NOME_LADO[s], "estado": l.estado, "mensagem": l.mensagem,
                        "rodando": l.esp is not None,
                        "ao_vivo_seg": int(time.time() - l.ao_vivo_desde) if l.ao_vivo_desde else None,
                        "alarme": l.alarme, "alarme_tipo": l.alarme_tipo, "alarme_detalhe": l.alarme_detalhe,
                        "pausa": l.pausa if l.esp is not None else False,
                        "tipo_ativo": l.tipo_ativo if l.esp is not None else None}
        logs = [l for l in painel.logs if l["id"] > desde_log][-60:]
    return {"lados": lados, "logs": logs,
            "tiktok": {**ponte.status_tiktok, "ultima": _ultima_acao["tiktok"]},
            "shopee": {**ponte.status, "ultima": _ultima_acao["shopee"]},
            "saude": saude(painel), "avisos": avisos_desde(desde_aviso),
            "contador": contador.resumo(),
            "hora": time.strftime("%H:%M:%S")}


# ---- saúde da transmissão e avisos para o celular
_extras = {"estado_obs": None}          # função do painel que diz se o OBS está pronto
_avisos = deque(maxlen=80)
_prox_aviso = [1]
_lock_avisos = threading.Lock()
TEXTO_ALARME = {
    "restricao": "⛔ Apareceu um aviso de restrição e o computador encerrou só a live daquela plataforma.",
    "restricao_falhou": "⛔ Aviso de restrição! O computador NÃO conseguiu encerrar a live. Encerre agora.",
    "restricao_desligada": "⛔ Apareceu um aviso de restrição (a proteção está desligada, nada foi encerrado).",
    "finalizar_tiktok": "⚠ A live parou na Shopee e o envio para o TikTok parou. Encerre a live no TikTok.",
    "finalizar_shopee": "⚠ A live acabou no TikTok, mas o computador não conseguiu finalizar na Shopee.",
    "tempo_tiktok": "⏰ O tempo de loop acabou e a reprise parou de ir para o TikTok.",
    "tempo_shopee": "⏰ O tempo de loop acabou e a reprise parou de ir para a Shopee.",
}
DESTINO = {"ts": "para a Shopee", "st": "para o TikTok"}


def avisar(nivel, texto):
    """Guarda um aviso para o celular. nivel: "ok" (voltou ao normal), "atencao" ou "grave"."""
    with _lock_avisos:
        _avisos.append({"id": _prox_aviso[0], "hora": time.strftime("%H:%M:%S"), "nivel": nivel, "texto": texto})
        _prox_aviso[0] += 1
    # erros e restrições também viram notificação no iPhone (app ntfy), mesmo com a página fechada
    if nivel == "grave":
        notificacoes.enviar("🚨 Problema nas lives", texto, prioridade=5)
    elif nivel == "atencao":
        notificacoes.enviar("⚠ Lives: atenção", texto, prioridade=3)


def _numeros_stats(stats):
    """Velocidade (1.00 = tempo real) e bitrate (kbps) do texto de estatísticas do painel."""
    vel = re.search(r"velocidade\s*([\d.]+)x", stats or "")
    br = re.search(r"([\d.]+)\s*kbits/s", stats or "")
    return (float(vel.group(1)) if vel else None), (round(float(br.group(1))) if br else None)


def _obs():
    try:
        return _extras["estado_obs"]() if _extras["estado_obs"] else None
    except Exception:
        return None


def saude(painel):
    """Para cada envio: "parado", "ok", "atencao" ou "grave", com a velocidade, o bitrate e o que está errado."""
    obs, extensao = _obs(), bool(ponte.status.get("aberto"))
    with painel.lock:
        lados = {s: (painel.lados[s].esp is not None, painel.lados[s].estado, painel.lados[s].stats,
                     painel.lados[s].alarme, painel.lados[s].pausa) for s in LADOS}
    res = {}
    for s, (rodando, estado, stats, alarme, pausa) in lados.items():
        vel, br = _numeros_stats(stats)
        problemas = []
        if rodando:
            if alarme:
                problemas.append(("grave", "alarme ligado no painel"))
            if estado == "reconectando":
                problemas.append(("atencao", "reconectando"))
            elif estado == "aguardando":
                problemas.append(("atencao", "esperando a live começar"))
            if pausa:
                problemas.append(("atencao", "live da Shopee pausada"))
            if vel is not None and vel < 0.95 and estado in ("ao_vivo", "reprise"):
                problemas.append(("atencao", f"vídeo atrasando ({vel:.2f}x)".replace(".", ",")))
            if s == "st" and obs is not None and not obs.get("pronto"):
                problemas.append(("grave", "o OBS não está recebendo o vídeo"))
            if not extensao:
                problemas.append(("atencao", "extensão do Chrome desconectada (encerrar sozinho não funciona)"))
        nivel = ("parado" if not rodando else "grave" if any(n == "grave" for n, _ in problemas)
                 else "atencao" if problemas else "ok")
        res[s] = {"nivel": nivel, "velocidade": vel, "bitrate": br, "problemas": [t for _, t in problemas]}
    return res


def _vigiar(painel):
    """Roda sempre (com ou sem o celular aberto): percebe o que mudou e guarda avisos para o celular.
    Quem age continua sendo o computador; isto só avisa."""
    antes, publico, ultimo_alerta = None, {"tiktok": deque(), "shopee": deque()}, {}
    no_ar = {"tiktok": None, "shopee": None}
    ultima_consulta = 0.0
    while True:
        time.sleep(2)
        try:
            agora = {}
            with painel.lock:
                for s in LADOS:
                    l = painel.lados[s]
                    agora[s] = {"rodando": l.esp is not None, "estado": l.estado, "alarme": l.alarme,
                                "alarme_tipo": l.alarme_tipo, "alarme_detalhe": l.alarme_detalhe, "pausa": l.pausa}
            obs = _obs()
            agora["obs"] = bool(obs and obs.get("pronto"))
            agora["extensao"] = bool(ponte.status.get("aberto"))
            if antes is not None:
                _comparar(antes, agora)
            antes = agora
            # números das plataformas: só enquanto algum envio está rodando (para não consultar à toa)
            if any(agora[s]["rodando"] for s in LADOS) and time.time() - ultima_consulta >= 5:
                ultima_consulta = time.time()
                for nome, consulta, campo in (("tiktok", live_tiktok, "assistindo"), ("shopee", live_shopee, "espectadores")):
                    r = consulta()
                    if not r.get("ok"):
                        continue
                    _guardar_meu_nome(r.get("nome") if nome == "shopee" else r.get("conta"))
                    rotulo = "TikTok" if nome == "tiktok" else "Shopee"
                    onde = "no TikTok" if nome == "tiktok" else "na Shopee"
                    if no_ar[nome] is not None and r["no_ar"] != no_ar[nome]:
                        avisar("ok" if r["no_ar"] else "atencao",
                               f"🔴 A live {onde} começou." if r["no_ar"] else f"⏹ A live {onde} acabou.")
                    no_ar[nome] = r["no_ar"]
                    _queda_de_publico(nome, rotulo, r.get(campo) if r["no_ar"] else None, publico[nome], ultimo_alerta)
        except Exception as e:
            espelho.log.debug("vigia do celular: %s", e)


def _comparar(antes, agora):
    for s in LADOS:
        a, b = antes[s], agora[s]
        destino = DESTINO[s]
        if b["rodando"] and not a["rodando"]:
            avisar("ok", f"▶ O envio {destino} começou.")
        elif a["rodando"] and not b["rodando"]:
            avisar("atencao", f"⏹ O envio {destino} parou.")
        if b["rodando"]:
            if b["estado"] == "reconectando" and a["estado"] != "reconectando":
                avisar("atencao", f"⚠ O envio {destino} caiu e está reconectando...")
            elif a["estado"] == "reconectando" and b["estado"] in ("ao_vivo", "reprise"):
                avisar("ok", f"✅ O envio {destino} voltou ao normal.")
            if b["pausa"] and not a["pausa"]:
                avisar("atencao", "⏸ A live da Shopee pausou (o computador cuida do TikTok).")
            elif a["pausa"] and not b["pausa"]:
                avisar("ok", "▶ A live da Shopee voltou.")
        if b["alarme"] and not a["alarme"]:
            texto = TEXTO_ALARME.get(b["alarme_tipo"], f"⚠ Alarme no painel: {b['alarme_tipo']}")
            detalhe = (b["alarme_detalhe"] or "").split("||")[0]
            avisar("grave", texto + (f" ({detalhe})" if detalhe else ""))
    if agora["st"]["rodando"] and antes["obs"] and not agora["obs"]:
        avisar("grave", "🎥 O OBS parou de receber o vídeo (fechado ou travado?).")
    elif agora["st"]["rodando"] and not antes["obs"] and agora["obs"]:
        avisar("ok", "🎥 O OBS voltou a receber o vídeo.")
    rodando = any(agora[s]["rodando"] for s in LADOS)
    if rodando and antes["extensao"] and not agora["extensao"]:
        avisar("grave", "🧩 A extensão do Chrome desconectou: o computador não consegue encerrar as lives sozinho.")
    elif rodando and not antes["extensao"] and agora["extensao"]:
        avisar("ok", "🧩 A extensão do Chrome reconectou.")


def _queda_de_publico(nome, rotulo, n, amostras, ultimo_alerta):
    """Avisa se as pessoas assistindo caírem pela metade em 1 minuto (imagem travada? som mudo?)."""
    agora = time.time()
    if n is None:
        amostras.clear()
        return
    amostras.append((agora, n))
    while amostras and agora - amostras[0][0] > 60:
        amostras.popleft()
    maior = max(v for _, v in amostras)
    if maior >= 20 and n <= maior * 0.5 and agora - ultimo_alerta.get(nome, 0) > 180:
        ultimo_alerta[nome] = agora
        avisar("grave", f"📉 O público {'do TikTok' if nome == 'tiktok' else 'da Shopee'} caiu de {maior} para {n} em 1 minuto. Confira se a imagem e o "
                        "som estão normais.")


# ---- notificação de pergunta nova no chat (app ntfy no iPhone), dizendo a plataforma
# Mesmos critérios da página do celular: avisos automáticos do chat e as suas respostas não contam.
# (singular e plural: "fulano está comprando", "fulano e 3 estão comprando produtos!")
# avisos automáticos (nunca viram notificação), inclusive "fulano e outros 3 usuários entraram por: Nº.1 ..."
RE_EVENTO = re.compile(r"\bentr(ou|aram)\s+(na\s+live\s+)?por\b|\boutr[oa]s?\s+(\d+\s+)?usu[aá]rios?\b|\b\d+\s+outr[oa]s?\s+usu[aá]rios?\b|come[çc](ou|ar|aram) a seguir|seguir o apresentador|est(á|a|ão|ao) comprando|est(á|a|ão|ao) vendo o produto|acab(ou|aram) de entrar|bem-vind[oa] (à|a|ao) shopee live", re.I)
RE_SISTEMA = re.compile(r"est(á|a|ão|ao) comprando|compr(ou|aram)|acab(ou|aram) de entrar|\bentr(ou|aram)\b|"
                        r"segui(u|ram)|come[çc](ou|ar|aram) a seguir|compartilh(ou|aram)|curti(u|ram)|"
                        r"envi(ou|aram) (um )?presente|is buying|bought|just joined|\bjoined\b|followed|shared|liked",
                        re.I)
RE_SISTEMA_INICIO = re.compile(r"^(est(á|a|ão|ao) comprando|compr(ou|aram)|acab(ou|aram) de entrar|entr(ou|aram)|"
                               r"segui(u|ram)|come[çc](ou|ar|aram) a seguir|compartilh(ou|aram)|curti(u|ram)|"
                               r"envi(ou|aram) (um )?presente|is buying|just joined|joined|followed|shared|liked)\b",
                               re.I)
RE_SELO = re.compile(r"^(apresentador[a]?|anfitri[ãa]o?)", re.I)
# começo dos avisos em grupo: "fulano" + "e 3 entraram!" / "e mais 2 começaram a seguir" / "and 4 others joined"
RE_GRUPO = re.compile(r"^(e|and)\s+(mais\s+)?\d+\s+(outr[oa]s\s+|pessoas\s+|others?\s+)?", re.I)
# avisos automáticos que a Shopee às vezes cola numa linha só, em qualquer ponto do texto:
# "Bem-vindo à Shopee Live! Aproveite :) vitoria... e 6 entraram! claudio... e 9 entraram!"
RE_SISTEMA_MEIO = re.compile(r"bem-vind[oa] (à|a|ao) shopee live|\w\.\.\.\s+(e\s+(mais\s+)?\d+\s+)?(entr(ou|aram)|est(á|a|ão|ao) comprando|come[çc](ou|aram)|acab(ou|aram)|compr(ou|aram)|segui(u|ram)|compartilh(ou|aram)|curti(u|ram))", re.I)
INTERVALO_PERGUNTAS = 15          # no máximo 1 notificação de pergunta a cada 15 s por plataforma
_meus_nomes = set()
_enviadas = deque(maxlen=50)


def _guardar_meu_nome(nome):
    if nome:
        _meus_nomes.add(str(nome).strip().lower())
        _meus_nomes.add("@" + str(nome).strip().lower().lstrip("@"))


def _eh_pergunta(m):
    """True se é comentário de um cliente (não é aviso automático nem resposta sua)."""
    nome, texto = (m.get("nome") or "").strip(), (m.get("texto") or "").strip()
    if not texto or m.get("eu") or m.get("evento"):
        return False
    tudo = f"{nome} {texto}".strip()
    if RE_EVENTO.search(tudo) or RE_SISTEMA_MEIO.search(tudo) or (not nome and RE_SELO.match(texto)):
        return False
    baixo = texto.lower()
    if nome.lower() in _meus_nomes or (not nome and any(n and baixo.startswith(n) for n in _meus_nomes)):
        return False
    if any(len(t) >= 4 and (texto == t or texto.endswith(t)) for t in _enviadas):
        return False
    # avisos em grupo vêm com o nome separado: "fulano" + "e 3 entraram!" -> olha o que vem depois do "e 3"
    return not (RE_SISTEMA_INICIO.search(RE_GRUPO.sub("", texto)) if nome else RE_SISTEMA.search(texto))


def _vigiar_perguntas():
    vistas = {"tiktok": deque(maxlen=400), "shopee": deque(maxlen=400)}
    primeira = {"tiktok": True, "shopee": True}
    pendentes = {"tiktok": [], "shopee": []}
    ultima = {"tiktok": 0.0, "shopee": 0.0}
    while True:
        time.sleep(2)
        try:
            if not notificacoes.ligado("perguntas"):
                continue
            for site in ("tiktok", "shopee"):
                c = ponte.comentarios(site)
                if c is None:
                    continue
                lista = [m for m in (c.get("lista") or []) if _eh_pergunta(m)]
                chaves = [(m.get("nome") or "", m.get("texto") or "") for m in lista]
                novas = [m for m, k in zip(lista, chaves) if k not in vistas[site]]
                vistas[site].extend(k for k in chaves if k not in vistas[site])
                # a 1ª leitura (painel acabou de abrir) e uma lista inteira "nova" de uma vez (extensão
                # recarregada, página atualizada) são comentários antigos: só guarda, não avisa
                if primeira[site] or (len(novas) >= 4 and len(novas) == len(lista)):
                    primeira[site] = False
                    continue
                pendentes[site].extend(novas)
            for site in ("tiktok", "shopee"):
                if pendentes[site] and time.time() - ultima[site] >= INTERVALO_PERGUNTAS:
                    lista, pendentes[site] = pendentes[site], []
                    ultima[site] = time.time()
                    onde = "no TikTok" if site == "tiktok" else "na Shopee"
                    linhas = [(f"{m['nome']}: " if m.get("nome") else "") + m["texto"] for m in lista[-6:]]
                    titulo = f"❓ Pergunta {onde}" if len(lista) == 1 else f"❓ {len(lista)} perguntas {onde}"
                    notificacoes.enviar(titulo, "\n".join(linhas), prioridade=4, tipo="perguntas")
        except Exception as e:
            espelho.log.debug("vigia de perguntas: %s", e)


def avisos_desde(desde):
    with _lock_avisos:
        return [a for a in _avisos if a["id"] > desde]


class Servidor(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def criar_handler(painel, porta):

    class Handler(BaseHTTPRequestHandler):
        server_version = "EspelhoCelular"

        def log_message(self, *args):
            pass

        def _enviar(self, codigo, corpo, tipo):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, obj, codigo=200):
            self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

        def _permitido(self):
            # 1) só aparelhos da rede de casa
            if not _rede_local(self.client_address[0]):
                return False
            # 2) o endereço digitado tem que ser um IP da rede local (bloqueia sites tentando falar com
            #    a página por um nome de domínio, o "DNS rebinding")
            host, _, p = self.headers.get("Host", "").rpartition(":")
            if p != str(porta) or not (host == "localhost" or _rede_local(host)):
                return False
            # 3) pedidos vindos de outro site aberto no celular não valem
            origem = self.headers.get("Origin")
            return origem is None or origem == f"http://{self.headers.get('Host')}"

        def do_GET(self):
            if not self._permitido():
                return self._json({"erro": "proibido"}, 403)
            caminho, _, consulta = self.path.partition("?")
            params = dict(x.split("=", 1) for x in consulta.split("&") if "=" in x)
            if caminho in ("/", "/index.html", "/celular"):
                self._enviar(200, PAGINA.read_bytes(), "text/html; charset=utf-8")
            elif caminho == "/api/estado":
                try:
                    desde, desde_aviso = int(params.get("desde", 0)), int(params.get("aviso", 0))
                except ValueError:
                    desde, desde_aviso = 0, 0
                self._json(resumo(painel, desde, desde_aviso))
            elif caminho == "/api/shopee_live":
                self._json(live_shopee())
            elif caminho == "/api/tiktok_live":
                self._json(live_tiktok())
            elif caminho == "/api/comentarios":
                self._json(comentarios())
            elif caminho == "/api/notificacoes":
                self._json(notificacoes.info())
            elif caminho == "/api/preview.jpg":
                foto = painel.foto(params.get("lado", "st"))
                if foto:
                    self._enviar(200, foto, "image/jpeg")
                else:
                    self._json({"erro": "sem prévia"}, 404)
            else:
                self._json({"erro": "não encontrado"}, 404)

        def do_POST(self):
            if not self._permitido():
                return self._json({"erro": "proibido"}, 403)
            # JSON obrigatório: um site qualquer não consegue mandar isso sem permissão do navegador
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json({"erro": "use JSON"}, 415)
            try:
                tamanho = min(int(self.headers.get("Content-Length", 0)), 4_000)
                dados = json.loads(self.rfile.read(tamanho) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json({"erro": "JSON inválido"}, 400)
            if not isinstance(dados, dict):
                return self._json({"erro": "JSON inválido"}, 400)
            if self.path == "/api/acao":
                self._json(executar_acao(painel, str(dados.get("acao") or "")))
            elif self.path == "/api/contador":
                r = contador.acao(dados, "pelo celular")
                if not r.get("ok"):
                    r["detalhe"] = r.get("erro")
                self._json(r)
            elif self.path == "/api/responder":
                self._json(responder_comentario(str(dados.get("site") or ""), str(dados.get("texto") or "")))
            else:
                self._json({"erro": "não encontrado"}, 404)

    return Handler


def iniciar(painel, cfg, estado_obs=None):
    """Sobe a página do celular em segundo plano. Devolve o endereço para abrir no celular, ou None."""
    if not cfg.get("celular_ligado", True):
        return None
    porta = int(cfg.get("porta_celular", PORTA_PADRAO))
    try:
        servidor = Servidor(("0.0.0.0", porta), criar_handler(painel, porta))
    except OSError as e:
        espelho.log.warning("📱 Não consegui abrir a página do celular na porta %d (%s).", porta, e)
        return None
    _extras["estado_obs"] = estado_obs
    threading.Thread(target=servidor.serve_forever, daemon=True, name="celular").start()
    threading.Thread(target=_vigiar, args=(painel,), daemon=True, name="celular-vigia").start()
    threading.Thread(target=_vigiar_perguntas, daemon=True, name="celular-perguntas").start()
    try:
        notificacoes.topico()          # cria o canal do ntfy na primeira vez
    except Exception as e:
        espelho.log.debug("ntfy: %s", e)
    ip = ip_da_rede()
    return f"http://{ip}:{porta}" if ip else f"http://<ip-deste-computador>:{porta}"
