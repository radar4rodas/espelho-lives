"""Mede, com a live no ar e sem mexer nela, se a voz que vai para o TikTok está casando com a boca.

Grava alguns segundos do que sai do OBS (câmera virtual + som pelo CABLE Output) e compara com o vídeo
original da Shopee que acabou de passar pelo painel:
- som: acha em que ponto do original está o som gravado (comparando o volume, a cada 5 ms);
- imagem: acha em que ponto do original está a imagem gravada (comparando as mudanças de imagem).
A diferença entre os dois pontos é quanto a voz está atrasada (positivo) ou adiantada (negativo)
em relação à imagem, já descontado o descompasso que a própria live da Shopee tenha.

Medido na live de 19/11: a voz estava 260 ms atrasada; duas medições seguidas deram 260 e 259 ms,
com 97-98% de confiança.
"""
import math
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path

DISPOSITIVOS = "video=OBS Virtual Camera:audio=CABLE Output (VB-Audio Virtual Cable)"
PRIORIDADE_BAIXA = 0x00004000          # BELOW_NORMAL_PRIORITY_CLASS: a medição não rouba tempo da live
LADO = 32                               # a imagem é reduzida a 32x32 cinza (só importa o que muda)
TAXA = 16000


def _rodar(cmd, timeout=90):
    flags = PRIORIDADE_BAIXA | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=timeout)


def _quadros(ffmpeg, arq):
    """(tempos, quadros 32x32 cinza) do vídeo, com os tempos originais."""
    r = _rodar([ffmpeg, "-hide_banner", "-copyts", "-i", str(arq), "-map", "0:v:0",
                "-vf", f"scale={LADO}:{LADO},format=gray,showinfo", "-fps_mode", "passthrough",
                "-f", "rawvideo", "pipe:1"])
    tempos = [float(x) for x in re.findall(rb"pts_time:\s*([-\d.]+)", r.stderr)]
    t = LADO * LADO
    quadros = [r.stdout[i * t:(i + 1) * t] for i in range(len(r.stdout) // t)]
    n = min(len(tempos), len(quadros))
    return tempos[:n], quadros[:n]


def _som(ffmpeg, arq):
    """(tempo do começo, amostras 16 kHz mono) do som, com os tempos originais."""
    r = _rodar([ffmpeg, "-hide_banner", "-copyts", "-i", str(arq), "-map", "0:a:0",
                "-af", f"aresample={TAXA}:async=1,ashowinfo", "-ac", "1", "-f", "s16le", "pipe:1"])
    m = re.search(rb"pts_time:\s*([-\d.]+)", r.stderr)
    if not m:
        return None, ()
    n = len(r.stdout) // 2
    return float(m.group(1)), struct.unpack(f"<{n}h", r.stdout[:n * 2])


def _serie_imagem(tempos, quadros, passo):
    """Quanto a imagem mudou de um quadro para o outro, num relógio regular."""
    pts = []
    for i in range(1, len(quadros)):
        a, b = quadros[i - 1], quadros[i]
        pts.append((tempos[i], sum(abs(x - y) for x, y in zip(a, b)) / len(a)))
    pts = [p for p in pts if p[1] > 0.0] or pts     # a câmera virtual repete quadros
    if len(pts) < 2:
        return None, []
    t0, t1 = pts[0][0], pts[-1][0]
    out, j = [], 0
    for k in range(int((t1 - t0) / passo)):
        t = t0 + k * passo
        while j + 1 < len(pts) and pts[j + 1][0] <= t:
            j += 1
        out.append(pts[j][1])
    return t0, out


def _serie_som(t0, amostras, passo):
    bloco = int(TAXA * passo)
    out = []
    for i in range(0, len(amostras) - bloco, bloco):
        s = amostras[i:i + bloco]
        out.append(math.log10(1 + sum(x * x for x in s) / bloco))
    return t0, out


def _norm(v):
    m = sum(v) / len(v)
    d = math.sqrt(sum((x - m) ** 2 for x in v)) or 1.0
    return [(x - m) / d for x in v]


def _casar(ta, a, tb, b, passo, centro=None, raio=None):
    """Deslocamento L (s) em que a série a (gravada do OBS) bate com b (original): b(t+L) = a(t).
    Devolve (L, confiança de 0 a 1)."""
    if not a or len(b) <= len(a):
        return None, 0.0
    a = _norm(a)
    if centro is None:
        ks = range(0, len(b) - len(a))
    else:
        k0 = int(round((ta + centro - tb) / passo))
        r = int(raio / passo)
        ks = range(max(0, k0 - r), min(len(b) - len(a), k0 + r) + 1)
    melhor = (-1.0, None)
    for k in ks:
        c = sum(x * y for x, y in zip(a, _norm(b[k:k + len(a)])))
        if c > melhor[0]:
            melhor = (c, k)
    c, k = melhor
    if k is None:
        return None, 0.0
    return tb + k * passo - ta, c


def _deslocamento(serie_a, serie_b):
    """Primeiro grosso (50 ms), depois fino (5 ms) perto do grosso."""
    ta, a = serie_a(0.05)
    tb, b = serie_b(0.05)
    if ta is None or tb is None:
        return None, 0.0
    L, _ = _casar(ta, a, tb, b, 0.05)
    if L is None:
        return None, 0.0
    ta, a = serie_a(0.005)
    tb, b = serie_b(0.005)
    return _casar(ta, a, tb, b, 0.005, centro=L, raio=0.08)


def analisar(ffmpeg, capturado, original):
    """Compara o que saiu do OBS (capturado) com o original. Devolve dict ou None."""
    tv_c, qv_c = _quadros(ffmpeg, capturado)
    tv_o, qv_o = _quadros(ffmpeg, original)
    ta_c, am_c = _som(ffmpeg, capturado)
    ta_o, am_o = _som(ffmpeg, original)
    if len(qv_c) < 30 or len(qv_o) < 30 or ta_c is None or ta_o is None:
        return None
    Li, ci = _deslocamento(lambda p: _serie_imagem(tv_c, qv_c, p), lambda p: _serie_imagem(tv_o, qv_o, p))
    Lv, cv = _deslocamento(lambda p: _serie_som(ta_c, am_c, p), lambda p: _serie_som(ta_o, am_o, p))
    if Li is None or Lv is None:
        return None
    return {"voz_ms": round((Li - Lv) * 1000), "conf_imagem": round(ci, 2), "conf_voz": round(cv, 2)}


def medir(ffmpeg, obter_original, segundos=12):
    """Grava `segundos` da câmera virtual + CABLE Output e compara com o original: obter_original() devolve
    os bytes MPEG-TS do vídeo da Shopee que passou pelo painel nos últimos ~40 s (ou None). É chamado
    DEPOIS de gravar, para o trecho gravado estar dentro dele. Devolve dict ou None."""
    with tempfile.TemporaryDirectory(prefix="espelho_sincronia_") as pasta:
        cap = Path(pasta) / "obs.mkv"
        orig = Path(pasta) / "original.ts"
        _rodar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-rtbufsize", "100M",
                "-use_wallclock_as_timestamps", "1", "-audio_buffer_size", "20",
                "-f", "dshow", "-i", DISPOSITIVOS, "-t", str(segundos),
                "-vf", f"scale={LADO}:{LADO},format=gray", "-c:v", "rawvideo",
                "-ar", str(TAXA), "-ac", "1", "-c:a", "pcm_s16le", str(cap)], timeout=segundos + 30)
        if not cap.exists() or cap.stat().st_size < 10000:
            return None
        original = obter_original() if callable(obter_original) else obter_original
        if not original:
            return None
        orig.write_bytes(original)
        return analisar(ffmpeg, cap, orig)


if __name__ == "__main__":
    # teste: python sincronia.py capturado.mkv original.ts
    import sys
    print(analisar("ffmpeg", sys.argv[1], sys.argv[2]))
