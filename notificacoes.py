"""
Notificações no celular pelo app ntfy (grátis, sem conta): https://ntfy.sh

O computador manda a notificação para um "canal" com nome secreto (gerado aqui na primeira vez e
guardado no config.json como "ntfy_topico"). No iPhone, o app ntfy assina esse canal e mostra a
notificação mesmo com a tela bloqueada e a página do celular fechada.

Vai para o ntfy: erros e restrições das lives e as perguntas do chat (com a plataforma).
Quem souber o nome do canal consegue ler, por isso ele é longo e aleatório; não divulgue.

Desligar: "ntfy_ligado": false no config.json (ou só as perguntas: "ntfy_perguntas": false).
"""

import json
import logging
import queue
import secrets
import string
import threading
import time
import urllib.request

import espelho

SERVIDOR = "https://ntfy.sh"
_fila = queue.Queue(maxsize=100)
_lock = threading.Lock()
_estado = {"topico": None, "thread": None, "falhou_em": 0.0, "ultimo_erro": None, "enviadas": 0}


def topico():
    """Nome do canal (cria e guarda no config.json na primeira vez)."""
    with _lock:
        if _estado["topico"]:
            return _estado["topico"]
        cfg = espelho.ler_config()
        t = str(cfg.get("ntfy_topico") or "").strip()
        if not t:
            letras = string.ascii_lowercase + string.digits
            t = "lives-" + "".join(secrets.choice(letras) for _ in range(12))
            cfg["ntfy_topico"] = t
            espelho.salvar_config(cfg)
        _estado["topico"] = t
        return t


def ligado(tipo=None):
    cfg = espelho.ler_config()
    if not cfg.get("ntfy_ligado", True):
        return False
    return tipo != "perguntas" or bool(cfg.get("ntfy_perguntas", True))


def _publicar(titulo, texto, prioridade=3, tags=None):
    corpo = {"topic": topico(), "title": titulo[:120], "message": texto[:1500], "priority": int(prioridade)}
    if tags:
        corpo["tags"] = list(tags)
    req = urllib.request.Request(SERVIDOR + "/", data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "painel-lives"})
    with urllib.request.urlopen(req, timeout=10) as r:
        if r.status >= 300:
            raise RuntimeError(f"ntfy respondeu {r.status}")


def _trabalhar():
    while True:
        titulo, texto, prioridade, tags = _fila.get()
        try:
            _publicar(titulo, texto, prioridade, tags)
            _estado["enviadas"] += 1
            _estado["ultimo_erro"] = None
        except Exception as e:
            _estado["ultimo_erro"] = str(e)
            if time.time() - _estado["falhou_em"] > 600:        # avisa no registro no máximo a cada 10 min
                _estado["falhou_em"] = time.time()
                espelho.log.warning("🔔 Não consegui mandar a notificação para o celular (ntfy): %s", e)


def enviar(titulo, texto, prioridade=3, tags=None, tipo=None):
    """Manda em segundo plano (não trava quem chamou). tipo="perguntas" respeita "ntfy_perguntas"."""
    if not ligado(tipo):
        return
    with _lock:
        if _estado["thread"] is None:
            _estado["thread"] = threading.Thread(target=_trabalhar, daemon=True, name="ntfy")
            _estado["thread"].start()
    try:
        _fila.put_nowait((titulo, texto, prioridade, tags))
    except queue.Full:
        pass


def testar():
    """Manda uma notificação de teste agora e diz se deu certo."""
    if not espelho.ler_config().get("ntfy_ligado", True):
        return {"ok": False, "detalhe": "As notificações estão desligadas (ntfy_ligado no config.json)."}
    try:
        _publicar("🔔 Teste do painel das lives", "Se você está vendo isto, as notificações estão funcionando.", 3)
        return {"ok": True, "detalhe": "Notificação de teste enviada. Chegou no iPhone?"}
    except Exception as e:
        return {"ok": False, "detalhe": f"Não consegui enviar ({e}). O computador está com internet?"}


def info():
    return {"ligado": ligado(), "perguntas": ligado("perguntas"), "topico": topico(), "servidor": SERVIDOR,
            "enviadas": _estado["enviadas"], "ultimo_erro": _estado["ultimo_erro"]}
