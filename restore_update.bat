@echo off
chcp 65001 >nul
title 还原 update.ini
cd /d "%~dp0.."

if exist "%~dp0..\update.ini.orig" (
    copy /y "%~dp0..\update.ini.orig" "%~dp0..\update.ini" >nul
    echo [OK] update.ini 已从 update.ini.orig 还原
) else (
    powershell -NoProfile -Command ^
      "$p='%~dp0..\update.ini'; (Get-Content $p -Encoding Default) -replace '^Version=.*','Version=3.0.43' | Set-Content $p -Encoding Default"
    echo [OK] 版本号已改回 3.0.43
)
echo.
(Get-Content "%~dp0..\update.ini" -Encoding Default) | findstr /b "Version="
pause
