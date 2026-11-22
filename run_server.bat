@echo off
chcp 65001 >nul
title 荒野行动Plus 本地服务端
cd /d "%~dp0"

set "PY=C:\Users\SHI\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

echo ===============================================
echo   荒野行动Plus  本地服务端
echo   HTTP :80  /  TCP :25000,:25100
echo   日志: %~dp0logs\
echo ===============================================
echo.
"%PY%" "%~dp0localsrv.py" --http 80 --ports 25000,25100
pause
