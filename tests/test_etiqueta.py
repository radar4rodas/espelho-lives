"""Etiqueta da reprise: ligar/desligar e texto escolhidos no painel."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import painel  # noqa: E402
import transmissao  # noqa: E402


class TestTextoEtiqueta(unittest.TestCase):
    def test_padrao_continua_reprise(self):
        self.assertEqual(transmissao.texto_etiqueta({}), "REPRISE")

    def test_desligada_nao_tem_texto(self):
        self.assertEqual(transmissao.texto_etiqueta({"etiqueta_ligada": False, "etiqueta_texto": "X"}), "")

    def test_usa_o_texto_escolhido_sem_quebras_de_linha(self):
        cfg = {"etiqueta_ligada": True, "etiqueta_texto": "  Gravado\n hoje  "}
        self.assertEqual(transmissao.texto_etiqueta(cfg), "Gravado hoje")


class TestFiltroEtiqueta(unittest.TestCase):
    def test_texto_vai_num_arquivo_e_nao_dentro_do_filtro(self):
        texto = "Live: 50% off 'hoje'"
        filtro = transmissao.filtro_etiqueta(texto)
        self.assertNotIn("50%", filtro)
        self.assertIn("expansion=none", filtro)
        caminho = filtro.split("textfile='", 1)[1].split("'", 1)[0].replace("\\:", ":")
        self.assertEqual(Path(caminho).read_text(encoding="utf-8"), texto)


class TestCmdSaida(unittest.TestCase):
    CFG = {"modo": "copiar", "bitrate_audio_kbps": 128, "resolucao": "1280x720", "fps": 30,
           "bitrate_video_kbps": 2500}

    def _cmd(self, etiqueta):
        return " ".join(transmissao.cmd_saida(dict(self.CFG), "ffmpeg", 5000, None, etiqueta=etiqueta))

    def test_sem_etiqueta_o_modo_copiar_nao_refaz_o_video(self):
        cmd = self._cmd("")
        self.assertIn("-c:v copy", cmd)
        self.assertNotIn("drawtext", cmd)

    def test_com_etiqueta_escreve_no_video(self):
        self.assertIn("drawtext", self._cmd("REPRISE"))


class TestEtiquetaDoPainel(unittest.TestCase):
    def test_desligada(self):
        self.assertEqual(painel._etiqueta({"etiqueta": False, "etiqueta_texto": ""}), ((False, "REPRISE"), None))

    def test_ligada_com_texto(self):
        self.assertEqual(painel._etiqueta({"etiqueta": True, "etiqueta_texto": "Gravado"}), ((True, "Gravado"), None))

    def test_ligada_sem_texto_da_erro(self):
        self.assertIsNotNone(painel._etiqueta({"etiqueta": True, "etiqueta_texto": "  "})[1])

    def test_texto_longo_demais_da_erro(self):
        self.assertIsNotNone(painel._etiqueta({"etiqueta": True, "etiqueta_texto": "x" * 41})[1])


if __name__ == "__main__":
    unittest.main()
