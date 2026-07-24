@echo off
title Local Studio - Web (Cloudflare Tunnel)
echo.
echo  ================================================
echo    LOCAL STUDIO - PHAT HANH WEB QUA CLOUDFLARE
echo  ================================================
echo.
echo  1) Backend chay tren may ban (GPU cua ban xu ly AI)
echo  2) Cloudflare Tunnel cap URL HTTPS cong khai
echo  3) Bat buoc dang nhap admin moi vao duoc
echo.
echo  Mat khau admin nam trong: backend\ADMIN-PASSWORD.txt
echo  Dong cua so nay de tat ca hai.
echo.

REM --- khoi dong backend trong cua so rieng ---
start "Local Studio Backend" "%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\main.py"

REM --- cho backend san sang ---
timeout /t 4 /nobreak >nul

echo  Dang mo Cloudflare Tunnel... (URL https se hien ben duoi)
echo  ------------------------------------------------
"%~dp0binaries\cloudflared.exe" tunnel --url http://127.0.0.1:8765 --no-autoupdate
pause
