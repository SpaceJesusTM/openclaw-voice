@echo off
rem Voice Room launcher: checks Node, installs dependencies, checks the API key,
rem starts the server and opens http://localhost:3000 in your browser.
rem Set NO_BROWSER=1 to skip opening the browser. Set PORT to use another port.
rem Set PRESET to open with a preset selected (demo-thoughts.bat does).
rem Set PAGE to open another page, e.g. selector.html (selector.bat does).
setlocal
cd /d "%~dp0"
if not defined PORT set PORT=3000
set URL=http://localhost:%PORT%/
if defined PRESET set URL=http://localhost:%PORT%/?preset=%PRESET%
if defined PAGE set URL=http://localhost:%PORT%/%PAGE%

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed. Install the LTS version from https://nodejs.org and run start.bat again.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Installing dependencies, this happens once...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)

if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Put your Boson API key after BOSON_API_KEY= then save and run start.bat again.
  start "" notepad .env
  pause
  exit /b 1
)

findstr /r /c:"^BOSON_API_KEY=..*" .env >nul
if errorlevel 1 (
  findstr /r /c:"^BOSON_API_KEY_FILE=..*" .env >nul
  if errorlevel 1 (
    echo No API key in .env. Put your Boson API key after BOSON_API_KEY= then save and run start.bat again.
    start "" notepad .env
    pause
    exit /b 1
  )
)

netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  rem Already serving the Voice Room? Then just open it.
  curl.exe -s -f -o nul http://127.0.0.1:%PORT%/presets.js >nul 2>nul
  if not errorlevel 1 (
    echo Voice Room is already running on port %PORT%; opening it.
    if not defined NO_BROWSER start "" %URL%
    exit /b 0
  )
  echo Port %PORT% is already in use. Close whatever is using it, or run: set PORT=3001 ^& "%~f0"
  pause
  exit /b 1
)

echo Starting Voice Room on http://localhost:%PORT%  (Chrome recommended; headphones help)
echo Close this window, or press Ctrl+C, to stop it.
if not defined NO_BROWSER start "" cmd /c "timeout /t 3 /nobreak >nul & start "" %URL%"
call npm run web
pause
