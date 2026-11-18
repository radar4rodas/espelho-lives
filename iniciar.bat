@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Espelho TikTok -^> Shopee  (NAO FECHE durante a live)

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
    echo Python nao encontrado. Instale com:  winget install Python.Python.3.12
    pause
    exit /b 1
)

echo Atualizando o leitor do TikTok (yt-dlp)...
rem O TikTok muda com frequencia; versoes antigas do yt-dlp param de funcionar.
"%PY%" -m pip install -q --disable-pip-version-check --no-warn-script-location --user -U -r requirements.txt

"%PY%" painel.py
rem 3 = o painel ja estava aberto: so abriu o navegador, entao fecha esta janela sozinha
if errorlevel 3 if not errorlevel 4 (
    timeout /t 4 >nul
    exit /b 0
)
pause
