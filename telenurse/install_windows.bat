@echo off
TITLE Ovis Telenurse Installer
echo ===================================
echo  Ovis Telenurse Installation Script
echo ===================================
echo.
pause

REM — detect which Python launcher we have —
where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python not found. Please install Python 3.10+ and add python or py to your PATH.
    pause
    exit /b 1
  ) else (
    set "PY=py"
  )
) else (
  set "PY=python"
)

echo Using %PY% to run all Python commands.
echo.

REM — install the required packages —
echo Installing required packages…
%PY% -m pip install --upgrade pip
%PY% -m pip install openai azure-cognitiveservices-speech requests
if errorlevel 1 (
  echo Failed to install packages. Check your Internet connection and permissions.
  pause
  exit /b 1
)

REM — ask for OpenAI key —
echo.
set /p "OPENAI_API_KEY=Enter your OpenAI API key: "

REM — insert the key into gpt_convo.py (if it exists) —
if exist gpt_convo.py (
  powershell -NoProfile -Command ^
    "(Get-Content gpt_convo.py) -replace 'api_key=.*', 'api_key=\"'+$env:OPENAI_API_KEY+'\"' | ^
     Set-Content -Encoding utf8 gpt_convo.py"
) else (
  REM if no file, create a minimal stub
  echo client = openai.OpenAI(api_key="%OPENAI_API_KEY%")>gpt_convo.py
)

REM — build your launcher script —
(
  echo @echo off
  echo echo Starting Ovis Telenurse…
  echo %PY% test_interface.py
  echo pause
)>start_telenurse.bat

echo.
echo ===================================
echo  Installation Complete!
echo ===================================
echo.

REM — prompt to run immediately —
set /p "START_NOW=Would you like to start Ovis Telenurse now? (Y/N): "
if /i "%START_NOW%"=="Y" (
  start "" start_telenurse.bat
)

pause
exit /b
