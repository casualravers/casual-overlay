@echo off
rem Lanceur de casual-overlay : fenetre de reglages (--gui) seule ; le bouton Demarrer la projection ouvre la fenetre OpenGL
rem (plein ecran sur le second ecran). --projeter l'ouvre tout de suite.
rem Les arguments donnes au .bat sont transmis tels quels, par exemple :
rem     start.bat -d "Microphone (Realtek(R) Audio)"
rem     start.bat --text "CASUAL RAVERS" --background pattern
rem     start.bat --synthetic --no-fullscreen      (sans micro ni ffmpeg)
rem Sans -d, choisis l'entree audio dans la fenetre de reglages.

setlocal
cd /d "%~dp0"

rem Python embarque (version portable : dossier python\) ou environnement de developpement (.venv).
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Python introuvable. Version portable : le dossier python\ doit etre a cote de ce fichier.
    echo Environnement de developpement : cree-le avec
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements-gl.txt
    pause
    exit /b 1
)

"%PY%" audio2wave_gl.py --gui %*
set "CODE=%ERRORLEVEL%"

rem En cas d'erreur, garde la console ouverte pour lire le message.
if not "%CODE%"=="0" (
    echo.
    echo casual-overlay s'est arrete avec le code %CODE%.
    pause
)
exit /b %CODE%
