"""
Cortes para Reels: cada momento marcado com ⭐ durante a live vira um vídeo curto vertical, pronto para postar.

Quando a live termina, cada marcação vira um corte de ~45 s (35 s antes do toque e 10 s depois, porque o
toque vem logo DEPOIS do momento bom) em duas versões, cada uma com o mudo da plataforma onde vai ser postada:
  - ..._tiktok.mp4  (TikTok):       sem "shopee", "shop", "pix", "bom dia", "boa tarde" e "boa noite"
  - ..._shopee.mp4  (Shopee Vídeo): sem "tiktok", "tiktok shop", "bom dia", "boa tarde" e "boa noite"
Formato 1080x1920 (9:16); vídeo deitado fica no meio, com o próprio vídeo desfocado de fundo.
Ficam em gravacoes/Cortes/<nome da live>/.

A gravação é a cópia crua da origem (sem o mudo da live), por isso o reconhecimento de voz roda de novo
em cada corte. Sem o modelo de voz, os cortes não são feitos: iriam com as palavras proibidas.
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

import espelho
from mudo import TAXA_VOZ, _mesclar, achar_palavras, carregar_modelo, gramatica_json

log = logging.getLogger("espelho")

ANTES = 35.0            # segundos do corte antes do toque
DEPOIS = 10.0           # e depois
MINIMO = 8.0            # trecho gravado menor que isto não vira corte
MESMO_MOMENTO = 15.0    # toques mais próximos que isto são o mesmo momento (vale o último)
FORA_DA_GRAVACAO = 10.0  # toque mais que isto depois do fim da parte: nada estava gravando
FOLGA_EXTRA = 0.1       # mudo um pouco mais largo que na live (o corte começa num ponto aproximado)
LARGURA, ALTURA = 1080, 1920
QUASE_VERTICAL = 0.15   # até 15% fora de 9:16 preenche a tela (corta um pouco); mais que isso, fundo desfocado
# (sufixo do arquivo, palavras a mutar): a versão do TikTok tira as palavras da Shopee e vice-versa
VERSOES = (("tiktok", "shopee"), ("shopee", "tiktok"))
PASTA_CORTES = espelho.PASTA_GRAVACOES / "Cortes"
PRIORIDADE_BAIXA = 0x00004000   # BELOW_NORMAL_PRIORITY_CLASS: não atrapalha a outra live, se estiver no ar


def _flags():
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW | PRIORIDADE_BAIXA


# ---------------------------------------------------------------- onde cada toque cai na gravação

def agrupar(marcas):
    """Toques seguidos no mesmo momento (dois cliques, celular e painel juntos) viram um só: vale o último."""
    grupos = []
    for t in sorted(marcas):
        if grupos and t - grupos[-1] < MESMO_MOMENTO:
            grupos[-1] = t
        else:
            grupos.append(t)
    return grupos


def localizar(marcas, partes, duracoes):
    """Trecho de cada toque dentro da parte da gravação em que ele caiu.

    marcas: horários dos toques (time.time()). partes: [(caminho, inicio)] na ordem em que foram gravadas,
    inicio = time.time() de quando a parte começou. duracoes: {caminho: segundos gravados}.
    Devolve [(caminho, inicio_do_corte, duracao_do_corte)], em segundos dentro da parte."""
    cortes = []
    for t in agrupar(marcas):
        dona = None
        for caminho, inicio in partes:
            if inicio <= t:
                dona = (caminho, inicio)
        if dona is None:
            continue                                # tocou antes de começar a gravar
        caminho, inicio = dona
        total = duracoes.get(caminho) or 0.0
        pos = t - inicio
        if pos > total + FORA_DA_GRAVACAO:
            continue                                # a live tinha acabado / caído: nada gravando
        pos = min(pos, total)
        ini, fim = max(0.0, pos - ANTES), min(total, pos + DEPOIS)
        if fim - ini >= MINIMO:
            cortes.append((caminho, round(ini, 2), round(fim - ini, 2)))
    return cortes


def _duracao(ffprobe, arquivo):
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(arquivo)], capture_output=True, text=True, timeout=60, creationflags=_flags())
        return float(r.stdout.strip())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0.0


def _esperar_fechar(caminhos, limite=30):
    """A gravação acabou de fechar: espera os arquivos pararem de crescer."""
    for _ in range(limite):
        if all(not c.exists() or time.time() - c.stat().st_mtime > 3 for c in caminhos):
            return
        time.sleep(1)


def planejar(partes, marcas):
    """Antes de emendar as partes: onde fica cada corte, na parte e no vídeo emendado.

    partes: [(pasta, base, caminho, inicio)] gravadas nesta execução. Devolve uma lista de dicts com
    pasta, base, parte, ini, dur e desloc (onde a parte começa no vídeo emendado base.ts)."""
    if not marcas or not partes:
        return []
    ffprobe = espelho._ffprobe()
    if not ffprobe:
        log.warning("🎬 ffprobe não encontrado: não consigo fazer os cortes para Reels.")
        return []
    _esperar_fechar([Path(c) for _, _, c, _ in partes])
    base_de = {Path(c): (Path(pasta), base) for pasta, base, c, _ in partes}
    duracoes, desloc = {}, {}
    for pasta, base in set(base_de.values()):
        acumulado = 0.0
        # todas as partes da base, inclusive de uma execução anterior: é assim que elas serão emendadas
        for p in espelho._partes_da_base(pasta, base):
            duracoes[p] = _duracao(ffprobe, p)
            desloc[p] = acumulado
            acumulado += duracoes[p]
    plano = []
    for caminho, ini, dur in localizar(marcas, [(Path(c), t) for _, _, c, t in partes], duracoes):
        pasta, base = base_de[caminho]
        plano.append({"pasta": pasta, "base": base, "parte": caminho, "ini": ini, "dur": dur,
                      "desloc": desloc.get(caminho, 0.0)})
    if len(plano) < len(agrupar(marcas)):
        log.info("🎬 %d marcação(ões) caíram fora da gravação e não viram corte.", len(agrupar(marcas)) - len(plano))
    return plano


def origem_do_corte(item):
    """(arquivo, início) de onde cortar: a parte, se ainda existe; senão o vídeo emendado (base.ts)."""
    if item["parte"].exists():
        return item["parte"], item["ini"]
    return item["pasta"] / f"{item['base']}.ts", item["ini"] + item["desloc"]


# ---------------------------------------------------------------- mudo e corte

def achar_mudos(ffmpeg, modelo, arquivo, ini, dur, alvo):
    """Trechos (início, fim) do corte com as palavras a mutar, com o mesmo reconhecimento da live."""
    import vosk
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{ini:.2f}",
                        "-t", f"{dur:.2f}", "-i", str(arquivo), "-map", "0:a:0", "-ac", "1",
                        "-ar", str(TAXA_VOZ), "-f", "s16le", "-"],
                       capture_output=True, timeout=300, creationflags=_flags())
    if r.returncode != 0:
        raise RuntimeError("não consegui ler o áudio: " + (r.stderr.decode("utf-8", "replace").strip()
                                                            .splitlines() or ["sem detalhe"])[-1])
    rec = vosk.KaldiRecognizer(modelo, TAXA_VOZ, gramatica_json(alvo))
    rec.SetWords(True)
    rec.SetPartialWords(True)
    janelas, bloco = [], TAXA_VOZ * 2 // 10           # 0,1 s de áudio, como na live
    for i in range(0, len(r.stdout), bloco):
        # resultado definitivo e provisório, como na live: o que a live mutaria, o corte também muta
        if rec.AcceptWaveform(r.stdout[i:i + bloco]):
            janelas += achar_palavras(json.loads(rec.Result()).get("result", []), True, alvo)
        else:
            janelas += achar_palavras(json.loads(rec.PartialResult()).get("partial_result", []), False, alvo)
    janelas += achar_palavras(json.loads(rec.FinalResult()).get("result", []), True, alvo)
    return _mesclar([(max(0.0, a - FOLGA_EXTRA), b + FOLGA_EXTRA) for a, b, _ in janelas])


def filtro_audio(mudos):
    filtro = "asetpts=PTS-STARTPTS"
    if mudos:
        expr = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in mudos)
        filtro += f",volume=0:enable='{expr}'"
    return filtro


def filtro_video(proporcao=None):
    """9:16 cheio. proporcao = largura/altura do vídeo gravado (None = não sei).
    Perto de 9:16 (ex.: 640x1280, 720x1280): preenche a tela, cortando um pouco das bordas.
    Longe disso (vídeo deitado): fica no meio, com ele mesmo desfocado de fundo."""
    if proporcao and abs(proporcao / (LARGURA / ALTURA) - 1) <= QUASE_VERTICAL:
        return (f"[0:v:0]setpts=PTS-STARTPTS,scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,"
                f"crop={LARGURA}:{ALTURA},setsar=1,fps=30,format=yuv420p[v]")
    return (f"[0:v:0]setpts=PTS-STARTPTS,split=2[frente][fundo];"
            f"[fundo]scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase,crop={LARGURA}:{ALTURA},"
            f"boxblur=20:2[fundo2];"
            f"[frente]scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=decrease[frente2];"
            f"[fundo2][frente2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v]")


def proporcao_do_video(arquivo):
    """largura/altura do primeiro vídeo do arquivo, ou None."""
    ffprobe = espelho._ffprobe()
    if not ffprobe:
        return None
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(arquivo)], capture_output=True, text=True, timeout=60,
                           creationflags=_flags())
        largura, altura = (int(x) for x in r.stdout.strip().splitlines()[0].split(",")[:2])
        return largura / altura
    except (ValueError, IndexError, OSError, subprocess.TimeoutExpired):
        return None


def cmd_corte(ffmpeg, arquivo, ini, dur, mudos, saida, proporcao=None):
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{ini:.2f}", "-t", f"{dur:.2f}", "-i", str(arquivo),
            "-filter_complex", filtro_video(proporcao), "-map", "[v]",
            "-map", "0:a:0", "-af", filtro_audio(mudos),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(saida)]


def cortar(ffmpeg, arquivo, ini, dur, mudos, saida):
    temp = saida.with_name(saida.stem + "_fazendo.mp4")
    r = subprocess.run(cmd_corte(ffmpeg, arquivo, ini, dur, mudos, temp, proporcao_do_video(arquivo)),
                       capture_output=True, text=True, timeout=900, creationflags=_flags())
    if r.returncode != 0 or not temp.exists() or temp.stat().st_size < 10_000:
        try:
            temp.unlink()
        except OSError:
            pass
        raise RuntimeError(((r.stderr or "").strip().splitlines() or [f"código {r.returncode}"])[-1])
    temp.replace(saida)


def _proximo_numero(pasta):
    """Uma mesma live pode ter mais de uma execução (caiu e voltou): continua a numeração."""
    nums = [int(m.group(1)) for p in pasta.glob("corte*_*.mp4") if (m := re.match(r"corte(\d+)_", p.name))]
    return max(nums, default=0) + 1


def gerar(ffmpeg, plano):
    """Faz os cortes do plano (depois de emendar as partes). Devolve os arquivos criados."""
    if not plano:
        return []
    modelo = carregar_modelo()
    if not modelo:
        log.warning("🎬 Sem o reconhecimento de voz não faço os cortes para Reels: eles iriam com as palavras "
                    "proibidas. Confira a pasta modelos (veja o LEIA-ME).")
        return []
    log.info("🎬 Fazendo %d corte(s) para Reels (versão TikTok e versão Shopee) em segundo plano...", len(plano))
    feitos, numeros = [], {}
    for item in plano:
        pasta = PASTA_CORTES / item["base"]
        pasta.mkdir(parents=True, exist_ok=True)
        n = numeros.get(pasta) or _proximo_numero(pasta)
        numeros[pasta] = n + 1
        arquivo, ini = origem_do_corte(item)
        for sufixo, alvo in VERSOES:
            saida = pasta / f"corte{n:02d}_{sufixo}.mp4"
            try:
                mudos = achar_mudos(ffmpeg, modelo, arquivo, ini, item["dur"], alvo)
                cortar(ffmpeg, arquivo, ini, item["dur"], mudos, saida)
            except Exception as e:      # um corte que falha não pode impedir os outros
                log.warning("🎬 Não consegui fazer o corte %02d (%s): %s", n, sufixo, e)
                continue
            feitos.append(saida)
            log.info("🎬 Corte %02d (%s) pronto: %d s, %d palavra(s) mutada(s).", n, sufixo, round(item["dur"]),
                     len(mudos))
    if feitos:
        pastas = sorted({p.parent.name for p in feitos})
        log.info("🎬 %d vídeo(s) para Reels prontos em gravacoes\\Cortes\\%s.", len(feitos), ", ".join(pastas))
        try:
            import notificacoes
            notificacoes.enviar("🎬 Cortes prontos", f"{len(feitos)} vídeo(s) para Reels da live "
                                f"{', '.join(pastas)} (versões TikTok e Shopee).", prioridade=3)
        except Exception as e:      # noqa: BLE001
            log.debug("ntfy: %s", e)
    return feitos
