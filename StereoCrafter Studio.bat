@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------------
rem  StereoCrafter Studio launcher (the Qt Studio; until 2026-09-28 this was
rem  "StereoCrafter Studio v2.bat" -- the CTk one is now
rem  "StereoCrafter Studio (Prototype).bat"). Its settings live in config\ and its log in studio_last_run.log
rem  (until 2026-10-02 config_v2\ and studio_v2_last_run.log; Studio moves an
rem  old config_v2\ across by itself on its first start).
rem
rem  Same shape as "StereoCrafter Studio (Prototype).bat", for the same reasons (see the
rem  notes there): the visible pass validates the install so a problem is
rem  reported to your face, then relaunches this file hidden so no empty
rem  console sits on screen for the whole session. A crash on the hidden pass
rem  opens a visible window with the captured log.
rem
rem  Verbs:
rem    <none>    visible: validate, relaunch hidden, exit
rem    __hidden  the real run
rem    showlog   print the captured log and wait
rem    debug     run visibly under python.exe with a real terminal attached
rem ---------------------------------------------------------------------

set "PYEXE=%~dp0.venv\Scripts\pythonw.exe"
set "PYEXE_CON=%~dp0.venv\Scripts\python.exe"
set "LOG=%~dp0studio_last_run.log"

if /i "%~1"=="__hidden" goto hiddenrun
if /i "%~1"=="showlog"  goto showlog
if /i "%~1"=="debug"    goto debugrun

if not exist "%PYEXE%" (
    echo.
    echo  ERROR: virtual environment not found.
    echo.
    echo    Expected: %PYEXE%
    echo.
    echo  Put this .bat in the StereoCrafter repo root, next to the .venv folder.
    echo.
    pause
    exit /b 1
)

"%PYEXE_CON%" -c "import PySide6" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo  StereoCrafter Studio needs PySide6, which is not installed in the venv yet.
    echo.
    echo  From this folder, run:
    echo.
    echo      uv sync
    echo.
    pause
    exit /b 1
)

rem the path goes through the environment, not inside a quoted PowerShell string: an
rem apostrophe in the install folder ended the string, PowerShell failed, and the
rem window closed with nothing started and no log (bug sweep 2026-10-09, A12)
set "SC_SELF=%~f0"
powershell -NoProfile -Command "Start-Process -FilePath $env:SC_SELF -ArgumentList '__hidden' -WindowStyle Hidden"
if errorlevel 1 (
    echo.
    echo  StereoCrafter Studio could not start itself in the background ^(see the PowerShell message above^).
    echo.
    pause
    exit /b 1
)
exit /b 0

:hiddenrun
rem one Studio at a time: when one runs, bring it forward and leave its log alone
"%PYEXE%" -m studio.single_instance
if errorlevel 3 (
    endlocal & exit /b 0
)
rem keep the previous run's log: a relaunch after a crash overwrote the one that explained it
if exist "%LOG%" copy /y "%LOG%" "%~dp0studio_last_run.prev.log" >nul 2>&1
"%PYEXE%" -u -m studio.app > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    start "" cmd /c ""%~f0" showlog"
)
endlocal & exit /b %RC%

:showlog
echo.
echo  StereoCrafter Studio exited abnormally.
echo  ----------------------------------------------------------------
echo  Captured output ^(also saved to %LOG%^):
echo  ----------------------------------------------------------------
if exist "%LOG%" type "%LOG%"
echo.
pause
endlocal & exit /b 0

:debugrun
echo  Running StereoCrafter Studio in debug mode (console attached).
echo.
"%PYEXE_CON%" -u -m studio.app
set "RC=%ERRORLEVEL%"
echo.
echo  Exited with code %RC%.
pause
endlocal & exit /b %RC%
