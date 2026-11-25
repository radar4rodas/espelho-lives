"""Contador "Oferta termina em" desenhado no vídeo das lives.

- Você aperta Iniciar (no painel do PC ou no celular) escolhendo o tempo; o contador aparece no vídeo,
  à esquerda, logo abaixo do cartão de produto que a plataforma mostra.
- No último minuto ele fica vermelho; quando chega a zero ele some da tela.
- Um contador para cada lado: "ts" (TikTok -> Shopee, aparece na live da Shopee) e
  "st" (Shopee -> TikTok, aparece na live do TikTok).

Como chega ao vídeo:
- TikTok -> Shopee: o painel já refaz o vídeo antes de mandar para a Shopee. Os filtros do ffmpeg
  desenham o fundo (imagens PNG geradas aqui) e o texto; as mudanças vão por comandos no stdin do ffmpeg.
- Shopee -> TikTok: o OBS mostra a página contador.html (fundo transparente) por cima do vídeo.
"""
import os
import struct
import tempfile
import threading
import time
import zlib
from pathlib import Path

TEXTO = "Oferta termina em"
URGENTE_SEG = 60          # último minuto: vermelho
MAX_SEG = 99 * 60 + 59

# posição e tamanho, em fração da imagem 9:16 (medidos sobre um print da live no celular):
# logo abaixo do cartão de produto que a Shopee mostra à esquerda
# X: o celular corta as laterais do vídeo (tela mais comprida que 9:16: ~7% de cada lado num iPhone, até ~10%
# nos mais compridos). Com 5,4% o contador saiu cortado na live de 23/11; 11% fica visível e alinhado com o cartão.
POS_X, POS_Y = 0.11, 0.28
LARG, ALT = 0.29, 0.076           # "um pouco maior" que o desenho aprovado, com a frase maior
BRANCO, VERMELHO = (255, 255, 255), (224, 36, 36)
LARANJA = (238, 77, 45)

PASTA = Path(tempfile.gettempdir()) / "espelho_contador"
FONTES = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

_lock = threading.Lock()
_estado = {"ts": {"fim": None, "total": 0}, "st": {"fim": None, "total": 0}}


# ---------------------------------------------------------------- estado

def iniciar(lado, segundos):
    segundos = int(segundos)
    if lado not in _estado:
        return "Lado inválido."
    if not 5 <= segundos <= MAX_SEG:
        return "Escolha um tempo entre 5 segundos e 99 minutos."
    with _lock:
        _estado[lado] = {"fim": time.time() + segundos, "total": segundos}
    return None


def parar(lado):
    if lado in _estado:
        with _lock:
            _estado[lado] = {"fim": None, "total": 0}


def estado(lado, agora=None):
    """fase: "off", "normal" ou "urgente" (último minuto). Chegou a zero: some (volta a "off")."""
    agora = time.time() if agora is None else agora
    with _lock:
        fim, total = _estado[lado]["fim"], _estado[lado]["total"]
    if fim is None:
        return {"fase": "off", "resta": 0, "tempo": "", "texto": TEXTO, "total": 0}
    resta = fim - agora
    if resta <= 0:
        with _lock:
            if _estado[lado]["fim"] == fim:
                _estado[lado] = {"fim": None, "total": 0}
        return {"fase": "off", "resta": 0, "tempo": "", "texto": TEXTO, "total": 0}
    seg = int(resta + 0.999)          # 04:59,2 aparece como 05:00 só no primeiro instante
    fase = "urgente" if seg <= URGENTE_SEG else "normal"
    return {"fase": fase, "resta": seg, "tempo": f"{seg // 60:02d}:{seg % 60:02d}", "texto": TEXTO, "total": total}


def resumo():
    return {lado: estado(lado) for lado in _estado}


ONDE = {"ts": "na live da Shopee", "st": "na live do TikTok"}


def acao(dados, origem="pelo computador"):
    """Pedido do painel ou do celular: {lado, acao: "iniciar"|"parar", segundos}."""
    import logging
    import espelho                      # (aqui dentro: o espelho não precisa deste módulo para carregar)
    dados = dados if isinstance(dados, dict) else {}
    lado = dados.get("lado")
    if lado not in _estado:
        return {"ok": False, "erro": "Escolha a live (Shopee ou TikTok)."}
    if dados.get("acao") == "parar":
        parar(lado)
        espelho.log.log(logging.INFO, "⏱ Contador tirado da tela %s (%s).", ONDE[lado], origem, extra={"lado": lado})
        return {"ok": True, "contador": resumo()}
    try:
        segundos = int(float(dados.get("segundos") or 0))
    except (TypeError, ValueError):
        return {"ok": False, "erro": "O tempo precisa ser um número."}
    erro = iniciar(lado, segundos)
    if erro:
        return {"ok": False, "erro": erro}
    espelho.log.log(logging.INFO, "⏱ Contador \"%s\" de %02d:%02d %s (%s).", TEXTO, segundos // 60, segundos % 60,
                    ONDE[lado], origem, extra={"lado": lado})
    return {"ok": True, "contador": resumo()}


# ---------------------------------------------------------------- imagens (PNG sem bibliotecas)

def _png(caminho, largura, altura, pixels):
    """pixels: bytearray RGBA, linha por linha."""
    linhas = b"".join(b"\x00" + bytes(pixels[y * largura * 4:(y + 1) * largura * 4]) for y in range(altura))

    def bloco(tipo, dados):
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    corpo = (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", largura, altura, 8, 6, 0, 0, 0))
             + bloco(b"IDAT", zlib.compress(linhas, 9)) + bloco(b"IEND", b""))
    tmp = caminho.with_suffix(".tmp")
    tmp.write_bytes(corpo)
    os.replace(tmp, caminho)


def _dist_retangulo(px, py, x0, y0, x1, y1, raio):
    """Distância (negativa por dentro) até um retângulo de cantos arredondados."""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - raio, (y1 - y0) / 2 - raio
    dx, dy = abs(px - cx) - hx, abs(py - cy) - hy
    fora = (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5
    return fora + min(max(dx, dy), 0) - raio


def _misturar(pixels, i, cor, alfa):
    """Pinta 'cor' com opacidade 'alfa' (0..1) por cima do pixel i (RGBA, não pré-multiplicado)."""
    if alfa <= 0:
        return
    r, g, b, a = pixels[i:i + 4]
    a0 = a / 255
    a1 = alfa + a0 * (1 - alfa)
    if a1 <= 0:
        return
    for k in range(3):
        pixels[i + k] = int(round((cor[k] * alfa + pixels[i + k] * a0 * (1 - alfa)) / a1))
    pixels[i + 3] = int(round(a1 * 255))


def medidas(largura, altura):
    """Tamanhos (em pixels do vídeo) do contador para uma imagem largura x altura."""
    pw, ph = round(LARG * largura), round(ALT * altura)
    m = max(4, round(ph * 0.09))                 # margem para a sombra
    return {
        "x": round(POS_X * largura), "y": round(POS_Y * altura), "pw": pw, "ph": ph, "m": m,
        "raio": round(ph * 0.2),
        # a frase "Oferta termina em" em letra forte e maior (pedido: estava pequena e apagada)
        "fs_texto": max(11, round(ph * 0.235)), "fs_tempo": max(14, round(ph * 0.41)),
        "y_texto": round(ph * 0.12), "y_tempo": round(ph * 0.45),
    }


def _icone_relogio(pixels, larg_img, cx, cy, raio, espessura, cor):
    """Relógio: aro + ponteiros (12h e 3h), com borda suave."""
    x0, x1 = int(cx - raio - 2), int(cx + raio + 3)
    y0, y1 = int(cy - raio - 2), int(cy + raio + 3)
    meia = espessura / 2
    for y in range(y0, y1):
        for x in range(x0, x1):
            px, py = x + 0.5, y + 0.5
            d_aro = abs(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - (raio - meia)) - meia
            # ponteiro de cima (do centro até 55% do raio) e da direita (até 45%)
            d_cima = max(abs(px - cx) - meia, max(cy - raio * 0.55 - py, py - cy) - meia * 0.2)
            d_dir = max(abs(py - cy) - meia, max(cx - px, px - (cx + raio * 0.45)) - meia * 0.2)
            d = min(d_aro, d_cima, d_dir)
            _misturar(pixels, (y * larg_img + x) * 4, cor, min(1.0, max(0.0, 0.5 - d)))


def gerar_fundos(largura, altura):
    """Cria as 2 imagens de fundo (branco e vermelho, com o relógio) para o tamanho do vídeo.
    Devolve {nome: caminho}."""
    PASTA.mkdir(parents=True, exist_ok=True)
    md = medidas(largura, altura)
    pw, ph, m, raio = md["pw"], md["ph"], md["m"], md["raio"]
    li, ai = pw + 2 * m, ph + 2 * m
    caminhos = {}
    for nome, cor, relogio in (("branco", BRANCO, LARANJA), ("vermelho", VERMELHO, BRANCO)):
        destino = PASTA / f"fundo3_{nome}_{largura}x{altura}.png"
        caminhos[nome] = destino
        if destino.exists():
            continue
        pixels = bytearray(li * ai * 4)
        for y in range(ai):
            for x in range(li):
                px, py = x + 0.5, y + 0.5
                i = (y * li + x) * 4
                # sombra suave, um pouco para baixo
                ds = _dist_retangulo(px, py - m * 0.25, m, m, m + pw, m + ph, raio)
                if ds < m:
                    _misturar(pixels, i, (0, 0, 0), 0.20 * (1 - max(0.0, ds) / m) ** 2)
                d = _dist_retangulo(px, py, m, m, m + pw, m + ph, raio)
                _misturar(pixels, i, cor, min(1.0, max(0.0, 0.5 - d)))
        if relogio:
            # o relógio fica à esquerda do tempo; o conjunto (relógio + tempo) é centralizado
            fs = md["fs_tempo"]
            diam = fs * 0.62
            larg_tempo = fs * 2.42                       # "00:00" em Segoe UI Semibold
            ini = m + (pw - (diam + fs * 0.22 + larg_tempo)) / 2
            cy = m + md["y_tempo"] + fs * 0.56
            _icone_relogio(pixels, li, ini + diam / 2, cy, diam / 2, max(1.6, fs * 0.075), relogio)
        _png(destino, li, ai, pixels)
    return caminhos


# ---------------------------------------------------------------- filtros do ffmpeg (TikTok -> Shopee)

def _caminho_filtro(p):
    return "'" + Path(p).as_posix().replace(":", r"\:") + "'"


class TelaFFmpeg:
    """O contador dentro do vídeo refeito pelo ffmpeg.

    Tudo muda por COMANDOS no stdin do ffmpeg (o mesmo caminho do mudo): o fundo entra e sai da tela
    (overlay x) e o texto troca com "reinit" do drawtext. Nada é lido de arquivo durante a live: ler um
    arquivo no instante em que ele está sendo trocado faz o ffmpeg PARAR o vídeo (testado).
    Para esconder um texto, a cor fica transparente (@0), assim o texto nunca fica vazio."""

    FORA = -4000
    REPETIR_SEG = 4.0      # de tempos em tempos repete o estado do que está na tela (se um comando se perdeu)
    CORES = {"texto_b": "0x3D3D3A", "tempo_b": "0x2C2C2A"}

    def __init__(self, lado, largura, altura):
        self.lado = lado
        self.largura, self.altura = int(largura), int(altura)
        self.md = medidas(self.largura, self.altura)
        self.fundos = gerar_fundos(self.largura, self.altura)
        self._mandado = {}              # elemento -> valor que o ffmpeg já tem
        self._quando_repetiu = 0.0
        self._fase_mandada = None

    def _desejado(self):
        """Como cada elemento deve estar agora. Devolve (fase, {elemento: valor})."""
        e = estado(self.lado)
        fase = e["fase"]
        x = self.md["x"] - self.md["m"]
        oculto = "white@0"
        if fase == "normal":
            cor_texto, cor_tempo = self.CORES["texto_b"], self.CORES["tempo_b"]
        elif fase == "urgente":
            cor_texto = cor_tempo = "white"
        else:
            cor_texto = cor_tempo = oculto
        tempo = e["tempo"] if fase in ("normal", "urgente") else self._mandado.get("tempo", ("00:00", ""))[0]
        return fase, {
            "branco": x if fase == "normal" else self.FORA,
            "vermelho": x if fase == "urgente" else self.FORA,
            "texto": (e["texto"], cor_texto),
            "tempo": (tempo, cor_tempo),
        }

    @staticmethod
    def _visivel(valor):
        if isinstance(valor, tuple):
            return not valor[1].endswith("@0")
        return valor != TelaFFmpeg.FORA

    def _comando(self, elem, valor):
        if elem in ("branco", "vermelho"):
            return f"coverlay@cont_{elem} -1 x {valor}\n"
        texto, cor = valor
        # um nível de escape só (o comando não passa pelo leitor de filtros): "\:" e "\ "
        texto = texto.replace("\\", "").replace("'", "").replace(":", r"\:").replace(" ", r"\ ")
        return f"cdrawtext@cont_{elem} -1 reinit text={texto}:fontcolor={cor}\n"

    def comandos(self, forcar=False):
        """Só os comandos necessários: o que mudou (o tempo, 1 por segundo; a fase, na troca) e, de tempos
        em tempos, o que está visível. Comando demais faz o ffmpeg acumular fila e atrasar a tela."""
        fase, quero = self._desejado()
        self._fase_mandada = fase
        agora = time.monotonic()
        repetir = forcar or agora - self._quando_repetiu >= self.REPETIR_SEG
        if repetir:
            self._quando_repetiu = agora
        cmds = []
        for elem, valor in quero.items():
            if self._mandado.get(elem) != valor or (repetir and self._visivel(valor)):
                cmds.append(self._comando(elem, valor))
                self._mandado[elem] = valor
        return cmds

    def filtro(self, antes):
        """Filtro completo para '-vf': 'antes' é a cadeia que já existia (escala, faixas, fps).
        O processador novo já nasce mostrando o estado atual (sem esperar comando)."""
        md = self.md
        y_fundo = md["y"] - md["m"]
        px, py, pw = md["x"], md["y"], md["pw"]
        f_negrito = _caminho_filtro(FONTES / "seguisb.ttf")
        self._fase_mandada, quero = self._desejado()
        self._mandado = dict(quero)
        self._quando_repetiu = time.monotonic()
        partes = [f"[in]{antes}[cont_base]"]
        for n in ("branco", "vermelho"):
            partes.append(f"movie={_caminho_filtro(self.fundos[n])}[cont_img_{n}]")
        cadeia = "[cont_base]"
        for i, n in enumerate(("branco", "vermelho")):
            saida = f"[cont_o{i}]"
            partes.append(f"{cadeia}[cont_img_{n}]overlay@cont_{n}=x={quero[n]}:y={y_fundo}:eof_action=repeat{saida}")
            cadeia = saida

        def texto(elem, fonte, tam, y, x=None):
            t, cor = quero[elem]
            t = t.replace("'", "").replace(":", r"\:")      # dentro de '...' no filtro: "\:" vira ":"
            x = x or f"{px}+({pw}-tw)/2"
            return (f"drawtext@cont_{elem}=text='{t}':fontfile={fonte}:fontsize={tam}:fontcolor={cor}:"
                    f"x={x}:y={y}")

        fs = md["fs_tempo"]
        # o tempo fica à direita do relógio (o conjunto relógio + tempo é centralizado)
        x_tempo = f"{px}+({pw}-({fs * 0.84:.1f}+tw))/2+{fs * 0.84:.1f}"
        desenhos = [
            texto("texto", f_negrito, md["fs_texto"], py + md["y_texto"]),
            texto("tempo", f_negrito, fs, py + md["y_tempo"], x_tempo),
        ]
        partes.append(cadeia + ",".join(desenhos) + "[out]")
        return ";".join(partes)
