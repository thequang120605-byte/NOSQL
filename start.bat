@echo off
chcp 65001 >nul
title FDB Commerce - Operations Platform
cls

echo =====================================================================
echo    FDB COMMERCE OPERATIONS PLATFORM - FOUNDATIONDB DEMO
echo    Do An Mon Hoc: Du Lieu NoSQL - De Tai 19
echo =====================================================================
echo.

echo [1/3] Kiem tra moi truong Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOI] Khong tim thay Python tren may tinh! Vui long cai dat Python 3.10 tro len.
    pause
    exit /b
)

echo [2/3] Cai dat thu vien phu thuoc tu requirements.txt...
pip install -r requirements.txt --quiet --disable-pip-version-check

echo [3/3] Khoi chay ung dung Web tren cong 5001...
echo Dang mo trinh duyet tai http://localhost:5001 ...
start "" http://localhost:5001

echo.
echo =====================================================================
echo  Ung dung dang chay tai: http://localhost:5001
echo  Nhan Ctrl + C de dung server.
echo =====================================================================
echo.

set FDB_MODE=local
python run.py
pause
