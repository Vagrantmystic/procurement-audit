@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Auditoria de compras - Drogueria Emmanuelle
echo.
python auditoria.py --year 2026 %*
echo.
pause
