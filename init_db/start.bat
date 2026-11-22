@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Без аргументов открываем ежедневное меню.
rem start.bat install - первичная установка.
rem start.bat server - сразу поднять сайт без меню.

if /I "%~1"=="install" goto install
if /I "%~1"=="server" goto server

echo Рабочее меню...
python launcher.py
goto end

:install
echo Первичная установка...
python install.py
goto end

:server
echo Запуск сайта...
python server.py --no-reload
goto end

:end
pause
