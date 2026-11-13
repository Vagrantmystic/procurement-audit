@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Iniciando Sistema de Compras - Drogueria Emmanuelle...
echo.
python run.py %*
echo.
echo Proceso finalizado. Revise la carpeta outputs.
pause
