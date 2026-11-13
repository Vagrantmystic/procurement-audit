@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Iniciando interfaz de compras en http://127.0.0.1:5050
echo Presione Ctrl+C para detener.
echo.
python ui\app.py
pause
