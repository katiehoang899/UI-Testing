@echo off
rem Chay UI Spec Checker tren Windows: double-click file nay roi mo http://localhost:8000
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000

where tesseract >nul 2>nul
if errorlevel 1 (
  if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    if "%TESSERACT_CMD%"=="" (
      echo Chua cai Tesseract OCR.
      echo Tai ban cai dat tai: https://github.com/UB-Mannheim/tesseract/wiki
      echo Khi cai, chon them ngon ngu "Vietnamese" o muc Additional language data.
      pause
      exit /b 1
    )
  )
)

where py >nul 2>nul
if errorlevel 1 (set PY=python) else (set PY=py -3)

if not exist .venv (
  echo Tao moi truong Python ^(.venv^)...
  %PY% -m venv .venv || (echo Can cai Python 3.10+ tu https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt || (pause & exit /b 1)

echo UI Spec Checker dang chay: http://localhost:%PORT%  (Ctrl+C de dung)
start "" http://localhost:%PORT%
python -m uvicorn uicheck.server:app --host 127.0.0.1 --port %PORT%
pause
