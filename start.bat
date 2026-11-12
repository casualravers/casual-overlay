@echo off
rem Lanceur de casual-overlay : fenetre de reglages (--gui) seule ; le bouton Demarrer la projection ouvre la fenetre OpenGL
rem (en fenetre ; plein ecran par la case ou la touche F). --projeter l'ouvre tout de suite.
rem Les arguments donnes au .bat sont transmis tels quels, par exemple :
rem     start.bat -d "Microphone (Realtek(R) Audio)"
rem     start.bat --text "CASUAL RAVERS" --background pattern
rem     start.bat --synthetic --no-fullscreen      (sans micro ni ffmpeg)
rem Sans -d, choisis l'entree audio dans la fenetre de reglages.
rem
rem Premier lancement : si Python (.venv) ou ffmpeg manquent, le lanceur propose de les installer (O/N) :
rem     .venv   = py -3.12 -m venv + pip install -r requirements-gl.txt (winget installe Python 3.12 si besoin)
rem     ffmpeg  = winget (Gyan.FFmpeg), puis ffmpeg.exe et ffprobe.exe copies dans le dossier ffmpeg\ a cote du programme

setlocal EnableExtensions
cd /d "%~dp0"

rem Python embarque (version portable : dossier python\) ou environnement de developpement (.venv).
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" call :make_venv
if not exist "%PY%" exit /b 1

rem ffmpeg : inutile pour --synthetic, --diagnostic et les listes d'entrees.
set "NEEDFF=1"
for %%A in (%*) do call :skip_ffmpeg "%%~A"
if "%NEEDFF%"=="1" call :check_ffmpeg

"%PY%" audio2wave_gl.py --gui %*
set "CODE=%ERRORLEVEL%"

rem En cas d'erreur, garde la console ouverte pour lire le message.
if not "%CODE%"=="0" (
    echo.
    echo casual-overlay s'est arrete avec le code %CODE%.
    pause
)
exit /b %CODE%


:skip_ffmpeg
if /i "%~1"=="--synthetic" set "NEEDFF=0"
if /i "%~1"=="--diagnostic" set "NEEDFF=0"
if /i "%~1"=="--list-devices" set "NEEDFF=0"
if /i "%~1"=="--list-audio-devices" set "NEEDFF=0"
goto :eof


rem ---------------------------------------------------------------------------------------------
rem Environnement de developpement absent : propose de le creer (et Python 3.12 si besoin).
rem ---------------------------------------------------------------------------------------------
:make_venv
echo Python introuvable : ni le dossier python\ ^(version portable^) ni l'environnement .venv.
choice /c ON /n /m "Creer l'environnement .venv et installer les dependances (quelques minutes) ? [O/N] "
if errorlevel 2 goto venv_manual

py -3.12 -c "" >nul 2>&1
if not errorlevel 1 goto venv_create
echo Python 3.12 n'est pas installe.
where winget >nul 2>&1
if errorlevel 1 goto venv_nopython
choice /c ON /n /m "Installer Python 3.12 avec winget ? [O/N] "
if errorlevel 2 goto venv_nopython
winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
py -3.12 -c "" >nul 2>&1
if not errorlevel 1 goto venv_create
echo.
echo Python 3.12 est installe, mais cette console ne le voit pas encore : relance start.bat.
pause
exit /b 1

:venv_create
echo Creation de .venv ...
py -3.12 -m venv "%~dp0.venv"
if errorlevel 1 goto venv_failed
echo Installation des dependances ^(requirements-gl.txt^) ...
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements-gl.txt
if errorlevel 1 goto venv_pip_failed
echo Environnement pret.
goto :eof

:venv_pip_failed
rem Un .venv a moitie installe ferait croire au prochain lancement que tout est pret : on le retire.
rmdir /s /q "%~dp0.venv"
:venv_failed
echo.
echo L'installation a echoue ^(voir les messages ci-dessus^).
pause
exit /b 1

:venv_nopython
echo Installe Python 3.12 depuis https://www.python.org/downloads/ ^(avec tcl/tk^), puis relance start.bat.
pause
exit /b 1

:venv_manual
echo Pour le faire a la main :
echo     py -3.12 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements-gl.txt
echo Version portable : le dossier python\ doit etre a cote de ce fichier.
pause
exit /b 1


rem ---------------------------------------------------------------------------------------------
rem ffmpeg absent : propose de l'installer par winget et de le ranger dans ffmpeg\ (trouve ensuite sans PATH).
rem ---------------------------------------------------------------------------------------------
:check_ffmpeg
if exist "%~dp0ffmpeg\ffmpeg.exe" if exist "%~dp0ffmpeg\ffprobe.exe" goto :eof
where ffmpeg >nul 2>&1
if errorlevel 1 goto ffmpeg_missing
where ffprobe >nul 2>&1
if errorlevel 1 goto ffmpeg_missing
goto :eof

:ffmpeg_missing
echo ffmpeg ^(ou ffprobe^) est introuvable : le visuel d'audio2wave ne peut pas tourner sans lui.
where winget >nul 2>&1
if errorlevel 1 goto ffmpeg_manual
choice /c ON /n /m "Installer ffmpeg avec winget (environ 100 Mo) ? [O/N] "
if errorlevel 2 goto ffmpeg_manual
winget install --id Gyan.FFmpeg -e --source winget --accept-source-agreements --accept-package-agreements
set "FFBIN="
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do for /d %%E in ("%%D\ffmpeg-*") do if exist "%%E\bin\ffmpeg.exe" set "FFBIN=%%E\bin"
if not defined FFBIN goto ffmpeg_manual
if not exist "%~dp0ffmpeg" mkdir "%~dp0ffmpeg"
copy /y "%FFBIN%\ffmpeg.exe" "%~dp0ffmpeg\" >nul
copy /y "%FFBIN%\ffprobe.exe" "%~dp0ffmpeg\" >nul
echo ffmpeg installe dans le dossier ffmpeg\.
goto :eof

:ffmpeg_manual
echo Installe ffmpeg ^(winget install --id Gyan.FFmpeg^) ou pose ffmpeg.exe et ffprobe.exe dans le dossier ffmpeg\ a cote du programme.
echo Sans lui : start.bat --synthetic --no-fullscreen
goto :eof
