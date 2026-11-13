@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Ordenes de compra desde Profit - Drogueria Emmanuelle
echo.
python main.py --source profit %*
echo.
pause
