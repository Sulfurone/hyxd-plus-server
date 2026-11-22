@echo off
chcp 65001 >nul
title 荒野行动Plus 本地启动
cd /d "%~dp0.."

echo ============================================================
echo   荒野行动Plus  本地启动（跳过更新检查）
echo ============================================================
echo.
echo 说明：
echo   官方已于 2021-11-22 停服，登录/大厅后端不存在。
echo   本脚本只做能做的事：让客户端正常启动到登录界面。
echo   真正进入对局需要私有协议服务端，本项目未提供。
echo.

REM ---- 1) 让启动器跳过在线更新（Version 高于服务器上的 3.0.103） ----
set "INI=%~dp0..\update.ini"
set "BAK=%~dp0..\update.ini.orig"

if not exist "%BAK%" (
    copy /y "%INI%" "%BAK%" >nul
    echo [1/3] 已备份 update.ini  -^>  update.ini.orig
) else (
    echo [1/3] update.ini 备份已存在，跳过
)

powershell -NoProfile -Command ^
  "$p='%INI%'; (Get-Content $p -Encoding Default) -replace '^Version=.*','Version=3.0.999' | Set-Content $p -Encoding Default"
echo       版本号 -^> 3.0.999（跳过更新）
echo.

REM ---- 2) 清理可能残留的游戏进程 ----
echo [2/3] 清理残留进程...
for %%P in (hyp.exe hyp_beta.exe hypprotect.exe bootloader.exe PGLoader.exe) do (
    taskkill /f /im %%P >nul 2>&1
)
timeout /t 2 >nul
echo.

REM ---- 3) 启动 ----
echo [3/3] 启动 hyp_start.exe ...
echo.
echo   启动后请：
echo     1. 忽略右侧「无法安全地连接到此页面」
echo     2. 点右下角「进入游戏」
echo     3. 游戏内 EULA 弹窗点「同意」
echo     4. 之后会停在登录界面 —— 到这一步就没有服务器了
echo.
start "" "%~dp0..\hyp_start.exe"

echo 完成。恢复原始版本号：双击 restore_update.bat
echo.
pause
