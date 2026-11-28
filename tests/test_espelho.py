"""Comportamento do espelhamento quando o TikTok responde HTTP 403."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espelho  # noqa: E402


class TestBloqueioTikTok(unittest.TestCase):
    def setUp(self):
        espelho._tt_api_bloqueios.clear()
        espelho.motivo["texto"] = ""
        espelho.alternativas["lista"] = []

    def test_403_pausa_consultas_e_nao_tenta_fallback_imediatamente(self):
        def responder_403(usuario, sessionid):
            espelho._registrar_bloqueio_tiktok(usuario)
            return "erro", "TikTok respondeu HTTP 403"

        with patch.object(espelho, "_via_api", side_effect=responder_403) as via_api, \
                patch.object(espelho, "_via_ytdlp") as via_ytdlp:
            self.assertIsNone(espelho.obter_stream("criador"))
            self.assertIsNone(espelho.obter_stream("criador"))

        self.assertEqual(via_api.call_count, 1)
        via_ytdlp.assert_not_called()
        self.assertIn("HTTP 403", espelho.motivo["texto"])
        self.assertIn("10 min", espelho.motivo["texto"])

    def test_bloqueio_expira_e_permite_nova_consulta(self):
        espelho._registrar_bloqueio_tiktok("criador")
        agora = espelho.time.monotonic()

        with patch.object(espelho.time, "monotonic", return_value=agora + espelho.TT_API_ESPERA_BLOQUEIO + 1):
            self.assertFalse(espelho._tiktok_em_bloqueio("criador"))


class TestFinalizarShopeeAposErro(unittest.TestCase):
    """27/11 21:30: a live caiu, a conferência do fim deu 403 e a Shopee nunca foi finalizada."""

    def _motor(self):
        cfg = {"tiktok_usuario": "criador", "intervalo_verificacao_seg": 10, "encerrar_shopee": True}
        esp = espelho.Espelhador(cfg, "ffmpeg", "rtmp://destino", ao_encerrar_tiktok=lambda: {"ok": True})
        esp._parar.wait = lambda *_: False          # sem esperas reais
        return esp

    def _rodar(self, esp, streams, situacoes):
        with patch.object(espelho, "obter_stream", side_effect=streams), \
                patch.object(espelho, "status_sala", side_effect=situacoes), \
                patch.object(esp, "_transmitir", return_value="I/O error"), \
                patch.object(esp, "_finalizar_shopee") as finalizar:
            esp.executar()
        return finalizar

    def test_finaliza_quando_o_tiktok_confirma_o_fim_depois_do_erro(self):
        esp = self._motor()
        stream = ("https://video", {})
        finalizar = self._rodar(esp, [stream, None, None],
                                [("erro", None), ("erro", None), ("encerrada", 4)])
        finalizar.assert_called_once_with("criador")

    def test_nao_finaliza_se_o_tiktok_diz_que_a_live_continua(self):
        esp = self._motor()
        stream = ("https://video", {})

        def obter(*_):
            if obter.vezes == 3:
                esp._parar.set()
            obter.vezes += 1
            return stream if obter.vezes == 1 else None
        obter.vezes = 0

        finalizar = self._rodar(esp, obter, [("erro", None), ("pausada", 3)])
        finalizar.assert_not_called()


class TestVigiaDoFim(unittest.TestCase):
    """O vigia não pode consultar o TikTok a cada ~1 s (coincidia com os 403 de 27/11)."""

    def test_espera_o_intervalo_entre_consultas_e_reconfirma_o_fim_na_hora(self):
        esp = espelho.Espelhador({"tiktok_usuario": "criador"}, "ffmpeg", "rtmp://destino")
        esperas = []

        class Sessao:
            _parar = espelho.threading.Event()
            parar = _parar.set

        Sessao._parar.wait = lambda seg: esperas.append(seg)
        respostas = [("ao_vivo", 2), ("ao_vivo", 2), ("encerrada", 4), ("encerrada", 4)]
        with patch.object(espelho, "status_sala", side_effect=respostas):
            esp._vigiar_fim(Sessao, "criador", "")

        self.assertEqual(esperas, [esp.VIGIA_INTERVALO_SEG] * 2)   # 2ª confirmação do fim sem esperar
        self.assertGreaterEqual(esp.VIGIA_INTERVALO_SEG, 10)
        self.assertTrue(Sessao._parar.is_set())
        self.assertIsNotNone(esp._tiktok_encerrou_em)


if __name__ == "__main__":
    unittest.main()
