@echo off
title Local Studio
echo.
echo  ============================================
echo    LOCAL STUDIO - AI Video Editor (100%% local)
echo  ============================================
echo.
echo  Dang khoi dong backend tai http://127.0.0.1:8765 ...
echo  Dong cua so nay de tat ung dung.
echo.
start "" http://127.0.0.1:8765
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\main.py"
pause
