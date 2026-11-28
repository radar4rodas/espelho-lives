"""Quais palavras o mudo encontra (o mesmo jeito vale na live e nos cortes para Reels).

    python -m unittest discover -s tests
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transmissao  # noqa: E402


def p(word, start, end, conf=1.0):
    return {"word": word, "start": start, "end": end, "conf": conf}


class _RecFalso:
    """Faz o papel do reconhecedor do Vosk: devolve as palavras combinadas."""

    def __init__(self, palavras, final):
        self.palavras, self.final = palavras, final

    def AcceptWaveform(self, dados):
        return self.final

    def Result(self):
        return json.dumps({"result": self.palavras})

    def PartialResult(self):
        return json.dumps({"partial_result": self.palavras})


def mudos_da_live(palavras, final, alvo):
    """Trechos que o Censor da live manda mutar para estas palavras."""
    c = transmissao.Censor.__new__(transmissao.Censor)
    c.alvo, c.amostras, c.rec = alvo, 0, _RecFalso(palavras, final)
    achados = []
    c._mutar = lambda ini, fim, nome: achados.append((round(ini, 2), round(fim, 2), nome))
    c.alimentar(b"\x00\x00" * 160)
    return achados


CASOS = [
    # (alvo, final, palavras, esperado)
    ("tiktok", True, [p("tique", 1.0, 1.3), p("toque", 1.3, 1.6)], [(0.85, 1.8, "tiktok")]),
    ("tiktok", True, [p("tique", 1.0, 1.3), p("toque", 1.3, 1.6), p("shop", 1.7, 2.0)],
     [(0.85, 2.2, "tiktok shop")]),
    ("tiktok", True, [p("tique", 1.0, 1.3), p("toque", 1.3, 1.6), p("shop", 1.7, 2.0, conf=0.4)],
     [(0.85, 1.8, "tiktok")]),
    ("tiktok", True, [p("boa", 2.0, 2.2), p("noite", 2.25, 2.6)], [(1.85, 2.8, "boa noite")]),
    ("tiktok", True, [p("bom", 2.0, 2.2), p("dia", 2.6, 2.9)], []),               # pausa no meio: não é saudação
    ("tiktok", False, [p("boa", 2.0, 2.2), p("tarde", 2.25, 2.6)], []),            # provisório: espera mais uma
    ("shopee", True, [p("chope", 3.0, 3.4)], [(2.85, 3.6, "shopee")]),
    ("shopee", True, [p("piques", 3.0, 3.4, conf=0.3)], []),                       # pix com pouca confiança
    ("shopee", True, [p("piques", 3.0, 3.4, conf=0.8)], [(2.85, 3.6, "pix")]),
    ("shopee", False, [p("piques", 3.0, 3.4)], []),
    ("shopee", False, [p("piques", 3.0, 3.4), p("agora", 3.5, 3.9)], [(2.85, 3.6, "pix")]),
    ("shopee", True, [p("bom", 0.1, 0.3), p("dia", 0.35, 0.6), p("shopping", 1.0, 1.5)],
     [(0.0, 0.8, "bom dia"), (0.85, 1.7, "shop")]),
]


class TestMudoDaLive(unittest.TestCase):
    def test_palavras_encontradas_pela_live(self):
        for alvo, final, palavras, esperado in CASOS:
            with self.subTest(alvo=alvo, palavras=[w["word"] for w in palavras], final=final):
                self.assertEqual(mudos_da_live(palavras, final, alvo), esperado)


if __name__ == "__main__":
    unittest.main()
