"""Onde cada ⭐ marcação cai na gravação e como o corte é montado.

    python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cortes  # noqa: E402

A, B = Path("live.ts"), Path("live_parte2.ts")


class TestLocalizar(unittest.TestCase):
    def test_toque_no_meio_da_parte_pega_35s_antes_e_10s_depois(self):
        self.assertEqual(cortes.localizar([1100.0], [(A, 1000.0)], {A: 600.0}), [(A, 65.0, 45.0)])

    def test_toque_no_comeco_da_gravacao_nao_vai_antes_do_zero(self):
        self.assertEqual(cortes.localizar([1020.0], [(A, 1000.0)], {A: 600.0}), [(A, 0.0, 30.0)])

    def test_toque_logo_antes_do_fim_nao_passa_do_fim(self):
        self.assertEqual(cortes.localizar([1598.0], [(A, 1000.0)], {A: 600.0}), [(A, 563.0, 37.0)])

    def test_toque_cai_na_parte_que_estava_gravando(self):
        partes = [(A, 1000.0), (B, 1700.0)]
        self.assertEqual(cortes.localizar([1800.0], partes, {A: 600.0, B: 300.0}), [(B, 65.0, 45.0)])

    def test_toque_durante_a_queda_curta_usa_o_fim_da_parte_anterior(self):
        partes = [(A, 1000.0), (B, 1700.0)]
        self.assertEqual(cortes.localizar([1605.0], partes, {A: 600.0, B: 300.0}), [(A, 565.0, 35.0)])

    def test_toque_antes_de_gravar_ou_depois_que_acabou_e_ignorado(self):
        self.assertEqual(cortes.localizar([900.0, 1700.0], [(A, 1000.0)], {A: 600.0}), [])

    def test_trecho_curto_demais_nao_vira_corte(self):
        self.assertEqual(cortes.localizar([1003.0], [(A, 1000.0)], {A: 5.0}), [])

    def test_toques_seguidos_viram_um_so_e_vale_o_ultimo(self):
        self.assertEqual(cortes.agrupar([100.0, 105.0, 112.0, 200.0]), [112.0, 200.0])
        self.assertEqual(cortes.localizar([1100.0, 1104.0], [(A, 1000.0)], {A: 600.0}), [(A, 69.0, 45.0)])


class TestMontagem(unittest.TestCase):
    def test_filtro_de_audio_muta_os_trechos(self):
        self.assertEqual(cortes.filtro_audio([]), "asetpts=PTS-STARTPTS")
        self.assertEqual(cortes.filtro_audio([(1.0, 1.5), (3.25, 4.0)]),
                         "asetpts=PTS-STARTPTS,volume=0:enable='between(t,1.00,1.50)+between(t,3.25,4.00)'")

    def test_video_quase_vertical_preenche_a_tela_e_deitado_ganha_fundo_desfocado(self):
        for proporcao in (720 / 1280, 640 / 1280):
            with self.subTest(proporcao=proporcao):
                self.assertNotIn("boxblur", cortes.filtro_video(proporcao))
        for proporcao in (1280 / 720, 1.0, None):
            with self.subTest(proporcao=proporcao):
                self.assertIn("boxblur", cortes.filtro_video(proporcao))

    def test_corta_da_parte_ou_do_video_emendado(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            parte = pasta / "live_parte2.ts"
            item = {"pasta": pasta, "base": "live", "parte": parte, "ini": 10.0, "dur": 45.0, "desloc": 600.0}
            self.assertEqual(cortes.origem_do_corte(item), (pasta / "live.ts", 610.0))   # parte já emendada
            parte.write_bytes(b"x")
            self.assertEqual(cortes.origem_do_corte(item), (parte, 10.0))                 # emenda falhou

    def test_numeracao_continua_numa_live_que_ja_tem_cortes(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            self.assertEqual(cortes._proximo_numero(pasta), 1)
            (pasta / "corte03_tiktok.mp4").write_bytes(b"")
            self.assertEqual(cortes._proximo_numero(pasta), 4)


if __name__ == "__main__":
    unittest.main()
