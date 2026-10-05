@echo off
rem Atalho de duplo clique: sobe o pdf2md e abre o navegador quando estiver pronto.
rem A janela precisa continuar aberta — é ela que hospeda o servidor.
title pdf2md
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Abrir
echo.
echo O servidor foi encerrado.
pause
