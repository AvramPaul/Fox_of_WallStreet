@echo off
rem ============================================================
rem  Paranoid Wolf - instalare + rulare intr-un singur pas.
rem  Prima rulare: creeaza .venv, instaleaza pachetele, cere cheia.
rem  Urmatoarele:  porneste direct agentul.
rem  Argumentele merg mai departe:  run.bat --max-iterations 6
rem ============================================================
setlocal
cd /d "%~dp0"

rem --- Pornit cu dublu-click? (ca sa nu se inchida fereastra la final)
set "DBLCLICK="
for %%x in (%cmdcmdline%) do if /i "%%~x"=="/c" set "DBLCLICK=1"

rem --- 1. Mediul Python (.venv) ---------------------------------
if exist ".venv\Scripts\python.exe" goto check_deps

echo [1/3] Caut Python 3.11+ ...
set "PY="
py -3.13 -c "" >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=python"
if not defined PY goto no_python

echo [1/3] Creez mediul virtual .venv cu: %PY%
%PY% -m venv .venv || goto fail

:check_deps
rem Reinstaleaza doar daca requirements.txt s-a schimbat de la ultima instalare.
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 && goto check_key

echo [2/3] Instalez pachetele (dureaza 1-2 minute prima data) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto fail
copy /y requirements.txt .venv\requirements.installed >nul

rem --- 2. Cheia Anthropic (.env) --------------------------------
:check_key
if exist ".env" findstr /r /c:"^ANTHROPIC_API_KEY=." .env >nul && goto run

echo [3/3] Lipseste cheia Anthropic. O gasesti la https://console.anthropic.com/settings/keys
".venv\Scripts\python.exe" -c "import getpass,pathlib,sys; e=pathlib.Path('.env'); src=e if e.exists() else pathlib.Path('.env.example'); k=getpass.getpass('      Lipeste cheia (nu se afiseaza pe ecran) si apasa Enter: ').strip(); k or sys.exit('      Cheie goala - nu am salvat nimic.'); L=src.read_text(encoding='utf-8').splitlines(); L=['ANTHROPIC_API_KEY='+k if l.startswith('ANTHROPIC_API_KEY=') else l for l in L]; e.write_text(chr(10).join(L)+chr(10), encoding='utf-8'); print('      Cheia a fost salvata in .env (fisierul nu ajunge pe git).')" || goto fail

rem --- 3. Rulare ------------------------------------------------
:run
echo.
".venv\Scripts\python.exe" agent.py %*
set "RC=%ERRORLEVEL%"
goto end

:no_python
echo.
echo [EROARE] Nu am gasit Python 3.11 sau mai nou.
echo          Instaleaza-l de pe https://www.python.org/downloads/ (bifeaza "Add python.exe to PATH")
echo          sau ruleaza:  winget install Python.Python.3.13
set "RC=1"
goto end

:fail
echo.
echo [EROARE] Instalarea s-a oprit. Vezi mesajul de mai sus.
set "RC=1"

:end
if defined DBLCLICK pause
exit /b %RC%
