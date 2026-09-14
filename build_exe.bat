@echo off
REM Builds dist\Chat2FTP.exe  - run this from the folder holding chat2ftp.py
setlocal

echo.
echo === Chat2FTP build ===
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher "py" not found. Install Python 3 from python.org first.
    pause
    exit /b 1
)

echo Checking the script parses...
py -c "import ast; ast.parse(open('chat2ftp.py',encoding='utf-8').read())"
if errorlevel 1 (
    echo chat2ftp.py has a syntax error - fix that first.
    pause
    exit /b 1
)

echo Installing build requirements...
py -m pip install --upgrade pip
py -m pip install --upgrade pyinstaller paramiko
if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
)

echo.
echo Building...
py -m PyInstaller --noconfirm --clean chat2ftp.spec
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

if not exist "dist\Chat2FTP.exe" (
    echo Build finished but dist\Chat2FTP.exe is missing.
    pause
    exit /b 1
)

for %%A in ("dist\Chat2FTP.exe") do set EXESIZE=%%~zA
echo.
echo Built: %CD%\dist\Chat2FTP.exe  [%EXESIZE% bytes]
if %EXESIZE% LSS 1000000 (
    echo.
    echo WARNING: that file is far too small to be a real exe.
    echo Something overwrote it after the build - check this script for stray
    echo redirection characters, and check your antivirus quarantine.
)

echo.
echo If the exe does nothing when you double click it:
echo   1. look for chat2ftp-crash.log next to the exe
echo   2. run run_chat2ftp.bat - that starts the app straight from Python
echo   3. check Windows Defender / antivirus quarantine
echo.
pause
