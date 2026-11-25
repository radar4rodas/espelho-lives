"""Prepara e abre o OBS Studio como "ponte" para a live de vendas do TikTok Shop.

O Gerenciador de LIVE do TikTok Shop transmite pelo navegador e só aceita câmera e microfone.
Então:
  - o painel manda o vídeo (com atraso e mudo) para udp://127.0.0.1:PORTA_OBS;
  - o OBS mostra esse vídeo na tela vertical 720x1280 e liga a "OBS Virtual Camera";
  - o som da fonte vai SÓ para o monitoramento, que aponta para o "CABLE Input" (VB-CABLE).
No navegador: Fonte de vídeo = OBS Virtual Camera, Fonte de áudio = CABLE Output.
Nenhum outro som do computador entra (áudio da área de trabalho e microfone ficam desligados).
"""
import configparser
import json
import os
import socket
import subprocess
import time
import uuid
import winreg
from pathlib import Path

PORTA_OBS = 9100
# O OBS fica "escutando" (SRT) e o painel conecta e envia; se o envio cair, o OBS volta a escutar sozinho.
ENTRADA_OBS = f"srt://127.0.0.1:{PORTA_OBS}?mode=listener&latency=120000"
DESTINO_OBS = f"srt://127.0.0.1:{PORTA_OBS}?mode=caller&latency=120000&pkt_size=1316"
NOME = "EspelhoTikTok"
FONTE = "Video da live"   # nome da fonte; precisa ser diferente do nome da cena ("Live")
FONTE_IMAGEM = "Ja voltamos"
FONTE_CONTADOR = "Contador"   # página do painel com o contador "Oferta termina em", por cima do vídeo
IMAGEM_PAUSA = Path(__file__).resolve().parent / "imagens" / "ja_voltamos.png"
PASTA_CFG = Path(os.environ.get("APPDATA", "")) / "obs-studio"
PASTA_OBS = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio" / "bin" / "64bit"
EXE_OBS = PASTA_OBS / "obs64.exe"
LARGURA, ALTURA, FPS = 720, 1280, 30
VERSAO_OBS = (33 << 24)   # evita a tela de "novidades" na primeira abertura

_DESC = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_DISP = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


def obs_instalado():
    return EXE_OBS.exists()


def obs_aberto():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return "obs64.exe" in r.stdout.lower()


def achar_cable_input():
    """Devolve (id_do_OBS, nome) da saída 'CABLE Input' do VB-CABLE, ou (None, None)."""
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as raiz:
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(raiz, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(raiz, guid) as k:
                    estado = winreg.QueryValueEx(k, "DeviceState")[0]
                with winreg.OpenKey(raiz, guid + r"\Properties") as p:
                    desc = winreg.QueryValueEx(p, _DESC)[0]
                    disp = winreg.QueryValueEx(p, _DISP)[0]
            except OSError:
                continue
            if estado == 1 and "VB-Audio Virtual Cable" in disp and desc.lower().startswith("cable input"):
                return "{0.0.0.00000000}." + guid, f"{desc} ({disp})"
    return None, None


def _ini(caminho, secoes):
    """Atualiza (sem apagar o resto) um .ini do OBS."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    if caminho.exists():
        cp.read(caminho, encoding="utf-8-sig")
    for sec, valores in secoes.items():
        if not cp.has_section(sec):
            cp.add_section(sec)
        for k, v in valores.items():
            cp.set(sec, k, str(v))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)


def _item(nome, id_fonte, num):
    return {"name": nome, "source_uuid": id_fonte, "visible": True, "locked": True, "id": num,
            "pos": {"x": 0.0, "y": 0.0}, "rot": 0.0, "scale": {"x": 1.0, "y": 1.0}, "align": 5,
            "bounds_type": 2, "bounds_align": 0, "bounds": {"x": float(LARGURA), "y": float(ALTURA)},
            "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0}


def _porta_painel():
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
        return int(cfg.get("porta_painel", 8765))
    except (OSError, ValueError, TypeError):
        return 8765


def _colecao():
    id_fonte, id_cena, id_img = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    id_cont = str(uuid.uuid4())
    fonte = {
        "id": "ffmpeg_source", "versioned_id": "ffmpeg_source", "name": FONTE, "uuid": id_fonte,
        "enabled": True, "muted": False, "volume": 1.0, "balance": 0.5, "mixers": 255,
        "monitoring_type": 1,   # só monitoramento (vai para o CABLE Input)
        "settings": {
            "is_local_file": False,
            "input": ENTRADA_OBS,
            "input_format": "mpegts",
            "buffering_mb": 2,
            "reconnect_delay_sec": 1,
            "restart_on_activate": False,
            "close_when_inactive": False,
            "clear_on_media_end": True,
            "hw_decode": True,
        },
    }
    # A imagem "Já voltamos" fica ATRÁS do vídeo: quando o envio para (pausa na Shopee), o vídeo
    # some (clear_on_media_end) e ela aparece; quando o vídeo volta, ele cobre a imagem.
    itens, fontes = [], []
    if IMAGEM_PAUSA.exists():
        fontes.append({"id": "image_source", "versioned_id": "image_source", "name": FONTE_IMAGEM,
                       "uuid": id_img, "enabled": True, "settings": {"file": str(IMAGEM_PAUSA)}})
        itens.append(_item(FONTE_IMAGEM, id_img, 2))
    itens.append(_item(FONTE, id_fonte, 1))
    # O contador fica POR CIMA do vídeo: uma página transparente do painel, do tamanho da tela.
    # Sem contador ligado ela não mostra nada; com o painel fechado também não.
    fontes.append({"id": "browser_source", "versioned_id": "browser_source", "name": FONTE_CONTADOR,
                   "uuid": id_cont, "enabled": True, "muted": True,
                   "settings": {"url": f"http://127.0.0.1:{_porta_painel()}/contador.html?lado=st",
                                "width": LARGURA, "height": ALTURA, "fps_custom": False,
                                "css": "body { background-color: rgba(0, 0, 0, 0); margin: 0px auto; overflow: hidden; }",
                                "shutdown": False, "restart_when_active": False, "reroute_audio": False}})
    itens.append(_item(FONTE_CONTADOR, id_cont, 3))
    cena = {
        "id": "scene", "versioned_id": "scene", "name": "Live", "uuid": id_cena, "enabled": True,
        "settings": {"id_counter": 3, "custom_size": False, "items": itens},
    }
    return {
        "name": NOME, "current_scene": "Live", "current_program_scene": "Live",
        "scene_order": [{"name": "Live"}], "sources": [cena, fonte] + fontes, "groups": [],
        "transitions": [], "current_transition": "Fade", "transition_duration": 300,
        "quick_transitions": [], "saved_projectors": [], "modules": {},
        # sem áudio da área de trabalho nem microfone
        "DesktopAudioDevice1": None, "AuxAudioDevice1": None,
    }


def configurar():
    """Cria/atualiza o perfil e a cena do OBS. Devolve o nome do CABLE Input (ou levanta erro)."""
    id_cabo, nome_cabo = achar_cable_input()
    if not id_cabo:
        raise RuntimeError("Não achei o 'CABLE Input' do VB-CABLE. Instale o VB-CABLE e reinicie o computador.")
    geral = {
        "General": {"FirstRun": "true", "LastVersion": VERSAO_OBS, "ProcessPriority": "AboveNormal",
                    "Pre19Defaults": "false",
                    "Pre21Defaults": "false", "Pre23Defaults": "false", "Pre24.1Defaults": "false"},
        "Basic": {"Profile": NOME, "ProfileDir": NOME, "SceneCollection": NOME, "SceneCollectionFile": NOME},
    }
    for nome in ("user.ini", "global.ini"):
        _ini(PASTA_CFG / nome, geral)
    _ini(PASTA_CFG / "basic" / "profiles" / NOME / "basic.ini", {
        "General": {"Name": NOME},
        "Video": {"BaseCX": LARGURA, "BaseCY": ALTURA, "OutputCX": LARGURA, "OutputCY": ALTURA,
                  "FPSType": 0, "FPSCommon": FPS, "ScaleType": "bicubic", "ColorFormat": "NV12"},
        "Audio": {"SampleRate": 48000, "ChannelSetup": "Stereo",
                  "MonitoringDeviceId": id_cabo, "MonitoringDeviceName": nome_cabo},
    })
    cenas = PASTA_CFG / "basic" / "scenes" / f"{NOME}.json"
    cenas.parent.mkdir(parents=True, exist_ok=True)
    cenas.write_text(json.dumps(_colecao(), ensure_ascii=False, indent=2), encoding="utf-8")
    return nome_cabo


def abrir():
    """Abre o OBS já com a câmera virtual ligada (minimizado). Não abre de novo se já estiver aberto."""
    if obs_aberto():
        return False
    subprocess.Popen([str(EXE_OBS), "--profile", NOME, "--collection", NOME, "--scene", "Live",
                      "--startvirtualcam", "--disable-updater"], cwd=str(PASTA_OBS))
    return True


_avisou_cena = []


def cena_atualizada():
    """A cena salva do OBS já tem a imagem de pausa e o contador? (o OBS regrava a cena ao fechar)"""
    try:
        texto = (PASTA_CFG / "basic" / "scenes" / f"{NOME}.json").read_text(encoding="utf-8")
        return FONTE_IMAGEM in texto and "contador.html" in texto
    except OSError:
        return False


def obs_escutando():
    """True se o OBS já está esperando o vídeo na porta (a cena carregou)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", PORTA_OBS))
        return False          # a porta estava livre: ninguém escutando
    except OSError:
        return True
    finally:
        s.close()


def garantir(log=None, espera=40):
    """Deixa o OBS aberto e pronto para receber. Retorna None se deu certo ou a mensagem do problema."""
    if not obs_instalado():
        return "O OBS Studio não está instalado (winget install OBSProject.OBSStudio)."
    if not obs_aberto():
        try:
            configurar()
        except RuntimeError as e:
            return str(e)
        if log:
            log.info("Abrindo o OBS com a câmera virtual ligada...")
        abrir()
    elif log and not _avisou_cena and not cena_atualizada():
        _avisou_cena.append(True)
        log.warning("O OBS foi aberto com a cena antiga (sem o contador na tela ou sem a imagem 'Já voltamos'). "
                    "Na próxima vez, feche o OBS antes de clicar em Iniciar, para o painel reabrir com a cena nova.")
    fim = time.time() + espera
    while time.time() < fim:
        if obs_escutando():
            return None
        if not obs_aberto():
            return "O OBS fechou sozinho ao abrir. Abra o OBS manualmente para ver o erro."
        time.sleep(0.5)
    return ("O OBS está aberto, mas não está esperando o vídeo. Feche o OBS (para o painel reabrir com a "
            "configuração certa) e clique em Iniciar de novo.")


if __name__ == "__main__":
    print("CABLE:", configurar())
    print("OBS aberto agora:", abrir())
