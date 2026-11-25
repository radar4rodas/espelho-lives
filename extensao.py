"""
Ponte entre o painel e a extensão "Espelho de Live - Finalizar Shopee" do seu Chrome.

A extensão se conecta em ws://127.0.0.1:8766 (só aceita conexões deste computador e só de
extensões do Chrome). Por essa conexão o painel:
- sabe se a página da live da Shopee está aberta e no ar;
- pede para clicar em "Finalizar Transmissão";
- pede a URL e a chave da tela "Associar transmissão com OBS".
"""

import asyncio
import itertools
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("espelho")
logging.getLogger("websockets").setLevel(logging.WARNING)   # sem mensagens técnicas na janela

PORTA = 8766
PASTA_EXTENSAO = Path(__file__).resolve().parent / "extensao_shopee"


class PonteExtensao:
    def __init__(self):
        self._loop = None
        self._conexoes = set()
        self._abas = {}
        self._abas_hora = 0.0      # quando a extensão mandou a lista de abas pela última vez
        self._pendentes = {}
        self._ids = itertools.count(1)
        self._thread = None
        self.erro_inicio = ""
        self.ao_aviso = None   # função(site, texto): aviso de restrição lido na Shopee ou no TikTok

    # ------------------------------------------------------------ servidor

    def iniciar(self, porta=PORTA):
        if self._thread:
            return
        pronto = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._servir(porta, pronto)), daemon=True)
        self._thread.start()
        pronto.wait(5)

    async def _servir(self, porta, pronto):
        try:
            from websockets.asyncio.server import serve
        except ImportError:
            self.erro_inicio = "componente websockets não instalado"
            pronto.set()
            return
        self._loop = asyncio.get_running_loop()
        try:
            async with serve(self._atender, "127.0.0.1", porta, ping_interval=None):
                pronto.set()
                asyncio.create_task(self._manter_viva())
                await asyncio.Future()
        except OSError as e:
            self.erro_inicio = f"porta {porta} ocupada ({e})"
            log.warning("Não consegui abrir a conexão para a extensão da Shopee: %s", self.erro_inicio)
            pronto.set()

    async def _atender(self, conn):
        origem = conn.request.headers.get("Origin", "") if conn.request else ""
        if not origem.startswith("chrome-extension://"):
            await conn.close(1008, "origem não permitida")   # só a extensão pode falar com a ponte
            return
        primeira = not self._conexoes
        self._conexoes.add(conn)
        if primeira:
            log.info("🧩 Extensão da Shopee conectada.")
        try:
            async for bruto in conn:
                try:
                    d = json.loads(bruto)
                except ValueError:
                    continue
                if d.get("tipo") == "estado":
                    self._abas = d.get("abas") or {}
                    self._abas_hora = time.time()
                elif d.get("tipo") == "aviso":
                    # fora desta thread: quem trata vai pedir coisas à extensão por esta mesma conexão
                    if self.ao_aviso:
                        threading.Thread(target=self.ao_aviso, args=(str(d.get("site") or ""), str(d.get("texto") or "")),
                                         daemon=True, name="aviso-restricao").start()
                elif d.get("tipo") == "resultado":
                    p = self._pendentes.get(d.get("id"))
                    if p:
                        p["respostas"] += 1
                        # "sem_aba" de um Chrome não vale se outro Chrome ainda pode responder
                        if not d.get("sem_aba") or p["respostas"] >= p["esperadas"]:
                            p["resultado"] = d
                            p["evento"].set()
        except Exception:
            pass
        finally:
            self._conexoes.discard(conn)
            if not self._conexoes:
                self._abas = {}
                log.info("🧩 Extensão da Shopee desconectada.")

    async def _manter_viva(self):
        # mensagens a cada 20 s mantêm a extensão acordada no Chrome
        while True:
            await asyncio.sleep(20)
            for c in list(self._conexoes):
                try:
                    await c.send('{"tipo":"ping"}')
                except Exception:
                    pass

    # ------------------------------------------------------------ pedidos (qualquer thread)

    def _pedir(self, tipo, timeout, **extra):
        conexoes = list(self._conexoes)
        if not conexoes or not self._loop:
            return {"ok": False, "detalhe": "A extensão não está conectada. O Chrome está aberto "
                                            "com a extensão instalada?"}
        ident = next(self._ids)
        p = {"evento": threading.Event(), "resultado": None, "respostas": 0, "esperadas": len(conexoes)}
        self._pendentes[ident] = p
        msg = json.dumps({**extra, "tipo": tipo, "id": ident})
        for c in conexoes:
            asyncio.run_coroutine_threadsafe(c.send(msg), self._loop)
        try:
            if not p["evento"].wait(timeout):
                return {"ok": False, "detalhe": "A extensão não respondeu a tempo."}
            return p["resultado"]
        finally:
            self._pendentes.pop(ident, None)

    def encerrar(self):
        """Clica em Finalizar Transmissão. Retorna {"ok", "detalhe", "segundos"}."""
        return self._pedir("finalizar", timeout=15)

    def pegar_chave(self):
        return self._pedir("pegar_chave", timeout=6)

    # ---- Gerenciador de LIVE do TikTok Shop (shop.tiktok.com)

    def tiktok_pausar(self):
        return self._pedir("tiktok_pausar", timeout=15)

    def tiktok_retomar(self):
        return self._pedir("tiktok_retomar", timeout=15)

    def tiktok_encerrar(self):
        return self._pedir("tiktok_encerrar", timeout=15)

    def tiktok_diagnostico(self):
        return self._pedir("tiktok_diagnostico", timeout=6)

    # ---- comentários das lives (lidos da página) e resposta pelo celular

    def comentarios(self, site, max_idade=40):
        """Últimos comentários do chat da live aberta no Chrome: {"lista": [{nome, texto}], "caixa": bool} ou None."""
        if not self._conexoes or time.time() - self._abas_hora > max_idade:
            return None
        for a in self._por_preferencia(self._abas_do_site(site)):
            if isinstance(a.get("comentarios"), list):
                return {"lista": a["comentarios"][-30:], "caixa": bool(a.get("tem_caixa"))}
        return None

    def responder(self, site, texto):
        """Escreve 'texto' na caixa de comentário da live (Shopee ou TikTok) e envia."""
        return self._pedir(f"{'tiktok' if site == 'tiktok' else 'shopee'}_responder", timeout=12, texto=texto)

    def _abas_do_site(self, site):
        return [a for a in self._abas.values() if (a.get("site") or "shopee") == site]

    @staticmethod
    def _por_preferencia(abas):
        """Abas com a live no ar primeiro; depois a usada mais recentemente."""
        return sorted(abas, key=lambda a: (a.get("situacao") == "ao_vivo", a.get("quando") or 0), reverse=True)

    def numeros_tiktok(self, max_idade=45):
        """Números da live lidos da página do Gerenciador de LIVE aberta no Chrome:
        {"espectadores", "entradas" (desde que a página abriu), "no_ar_seg", "ao_vivo"} ou None.
        A extensão manda quando muda e a cada 15 s; mais velho que max_idade não vale."""
        if not self._conexoes or time.time() - self._abas_hora > max_idade:
            return None
        for a in self._por_preferencia(self._abas_do_site("tiktok")):
            n = a.get("numeros")
            if isinstance(n, dict) and n.get("espectadores") is not None:
                return {**n, "ao_vivo": a.get("situacao") == "ao_vivo"}
        return None

    @property
    def sessao_shopee(self):
        """Número da live da Shopee aberta no Chrome (da url ...?session=123), ou None.
        Prefere a aba com a live no ar (a que tem 'Finalizar Transmissão')."""
        if not self._conexoes:
            return None
        import re
        abas = self._por_preferencia(self._abas_do_site("shopee"))
        abas.sort(key=lambda a: a.get("situacao") == "ao_vivo" or "/pc/live" in (a.get("url") or ""), reverse=True)
        for a in abas:
            m = re.search(r"[?&]session(?:_id)?=(\d+)", a.get("url") or "")
            if m:
                return m.group(1)
        return None

    @property
    def status_tiktok(self):
        if self.erro_inicio or not self._conexoes:
            return {"aberto": False, "situacao": "fechado",
                    "texto": "Extensão não conectada. Instale/atualize a extensão no Chrome."}
        abas = self._abas_do_site("tiktok")
        situacoes = [a.get("situacao") for a in abas]
        if not abas:
            return {"aberto": True, "situacao": "aberto",
                    "texto": "Extensão conectada. Abra o Gerenciador de LIVE do TikTok Shop no Chrome."}
        if "ao_vivo" in situacoes:
            pausa = any(a.get("tem_pausa") for a in abas)
            return {"aberto": True, "situacao": "ao_vivo",
                    "texto": "Live no ar no TikTok." + (" Pronto para pausar e encerrar sozinho."
                                                        if pausa else " Pronto para encerrar sozinho.")}
        return {"aberto": True, "situacao": "preparada",
                "texto": "Gerenciador de LIVE aberto. Inicie a live no TikTok quando a prévia mostrar a Shopee."}

    @property
    def status(self):
        if self.erro_inicio:
            return {"aberto": False, "situacao": "erro", "texto": f"Conexão com a extensão indisponível: {self.erro_inicio}."}
        if not self._conexoes:
            return {"aberto": False, "situacao": "fechado",
                    "texto": "Extensão não conectada. Instale a extensão no Chrome (veja como abaixo)."}
        situacoes = [a.get("situacao") for a in self._abas_do_site("shopee")]
        if "ao_vivo" in situacoes:
            return {"aberto": True, "situacao": "ao_vivo",
                    "texto": "Live em andamento na Shopee. Pronto para finalizar sozinho."}
        if "preparada" in situacoes:
            return {"aberto": True, "situacao": "preparada",
                    "texto": "Live criada. Clique em 'Entrar ao vivo?' na página da Shopee."}
        if situacoes:
            return {"aberto": True, "situacao": "aberto",
                    "texto": "Extensão conectada. Abra a página da sua live na Shopee no Chrome."}
        return {"aberto": True, "situacao": "aberto",
                "texto": "Extensão conectada. Abra a página da sua live (live.shopee.com.br) no Chrome."}


ponte = PonteExtensao()
