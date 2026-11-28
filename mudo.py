"""
Mudo automático: quais palavras procurar no áudio e onde elas foram ditas (reconhecimento de voz Vosk).

Usado na live (transmissao.Censor, ao vivo) e nos cortes para Reels (cortes.py, depois da live).
"""

import logging
from pathlib import Path

log = logging.getLogger("espelho")

BASE = Path(__file__).resolve().parent
PASTA_MODELO = BASE / "modelos" / "vosk-model-small-pt-0.3"
TAXA_VOZ = 16000

# O modelo em português "ouve" TikTok como "tique toque" e Shopee ("xópi") como "chope".
# Procurar só por estas frases (o resto vira [unk]) é bem mais confiável do que transcrever tudo.
# Saudações também são mutadas nas duas direções (ex.: "boa noite" numa live que sai em outro horário).
SAUDACOES = {("boa", "noite"): "boa noite", ("boa", "tarde"): "boa tarde", ("bom", "dia"): "bom dia"}
_FRASES_SAUDACAO = list(SAUDACOES.values())
GRAMATICAS = {
    "tiktok": ["tique toque", "tique toque shop", "tique toque shopping"] + _FRASES_SAUDACAO + ["[unk]"],
    # Shopee -> TikTok: "Shopee" vira "chope"; "shop"/"shopping" sozinhos; e "Pix" (o modelo não conhece "pix":
    # ouve "piques"). O TikTok Shop trata "pague no Pix" / "na Shopee" como mandar o cliente para fora (aviso de 23/11).
    "shopee": ["chope", "shop", "shopping", "piques"] + _FRASES_SAUDACAO + ["[unk]"],
}
# palavra reconhecida -> nome que aparece no registro
MUDO_SHOPEE = {"chope": "shopee", "shop": "shop", "shopping": "shop", "piques": "pix"}
DESCRICAO_MUDO = {"tiktok": "'tiktok', 'tiktok shop', 'bom dia', 'boa tarde' e 'boa noite'",
                  "shopee": "'shopee', 'shop', 'pix', 'bom dia', 'boa tarde' e 'boa noite'"}
PALAVRAS_SHOP = {"shop", "shopping"}
FOLGA_ANTES = 0.15   # segundos de mudo antes da palavra
FOLGA_DEPOIS = 0.20  # e depois

_modelo = {"obj": None, "tentou": False}


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


def _mesclar(janelas):
    out = []
    for a, b in sorted(janelas):
        if out and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def achar_palavras(palavras, final, alvo):
    """Trechos (início, fim, nome) a mutar nas palavras que o Vosk reconheceu.
    final: é o resultado definitivo do Vosk (no provisório, a última palavra ainda pode mudar).
    alvo: "tiktok" (procura tiktok/tiktok shop) ou "shopee" (procura shopee/shop/pix). Saudações nos dois."""
    for i in range(len(palavras) - 1):
        a, b = palavras[i], palavras[i + 1]
        nome = SAUDACOES.get((a["word"], b["word"]))
        # as duas palavras coladas: "muito bom, e o dia..." (com pausa no meio) não conta.
        # No resultado provisório, a última palavra ainda pode mudar de tempo: espera vir mais uma.
        if nome and b["start"] - a["end"] < 0.15 and (final or i + 2 < len(palavras)):
            yield max(0.0, a["start"] - FOLGA_ANTES), b["end"] + FOLGA_DEPOIS, nome
    if alvo == "shopee":
        for j, p in enumerate(palavras):
            nome = MUDO_SHOPEE.get(p["word"])
            # "pix" ("piques") confunde com "pode pedir" no resultado provisório: só vale no resultado final
            # (com boa confiança) ou, no provisório, se já veio mais uma palavra depois
            if nome == "pix" and not ((final and p.get("conf", 1.0) >= 0.5) or (not final and j + 1 < len(palavras))):
                continue
            if nome:
                yield max(0.0, p["start"] - FOLGA_ANTES), p["end"] + FOLGA_DEPOIS, nome
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
            yield max(0.0, ini), fim, nome
