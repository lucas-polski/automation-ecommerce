@echo off
chcp 65001 > nul
title Etiquetas Pop House - Juveve
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERRO] Ambiente virtual nao encontrado!
    echo.
    echo Execute uma vez no PowerShell:
    echo   python -m venv venv
    echo   .\venv\Scripts\activate.ps1
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python gerar_etiquetas.py --aba Loja_Juveve %*

if errorlevel 1 (
    echo.
    echo [ERRO] O script terminou com erro. Veja a mensagem acima.
    pause
    exit /b 1
)

echo.
echo Pressione qualquer tecla para fechar...
pause > nul
