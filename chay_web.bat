@echo off
chcp 65001 >nul
title YT NICHE HUNTER v2 PRO - Web phan tich YouTube
cd /d "%~dp0"
echo ============================================================
echo  YT NICHE HUNTER v2 PRO - Web phan tich YouTube
echo ============================================================
echo [1/2] Dang tim Python...
set PY=
where python >nul 2>nul && set PY=python
if not defined PY (
  echo Khong tim thay Python! Cai Python 3.10+ tu python.org roi chay lai.
  pause
  exit /b 1
)
echo [2/2] Dang khoi dong web server...
start "" "http://127.0.0.1:5000"
%PY% -u yt_niche_hunter.py
pause
