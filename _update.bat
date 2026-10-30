@echo off
setlocal enabledelayedexpansion

REM --- ANCHORING ---
cd /d "%~dp0"

echo.
echo =================================================
echo === StereoCrafter Modern Update Script ===
echo =================================================
echo.

REM --- HEALTH CHECK ---
REM Check if running from a ZIP
echo "%~dp0" | findstr /i "Temp" >nul
if !errorlevel! equ 0 (
    echo [WARNING] It looks like you are running this from a temporary folder or a ZIP.
    echo Please EXTRACT the folder to a permanent location first.
)



REM --- The update source: StereoCrafter Studio's own repository ---
REM (it replaces the earlier StereoCrafter GUI versions; a copy installed from one of those is moved over)
set "STUDIO_URL=https://github.com/NowIAmBecomeDepth/StereoCrafterStudio.git"

set "ORIGIN_URL=Not Found"
for /f "tokens=*" %%i in ('git remote get-url origin 2^>nul') do set "ORIGIN_URL=%%i"

if "!ORIGIN_URL!"=="Not Found" (
    git remote add origin !STUDIO_URL! 2>nul
) else (
    echo !ORIGIN_URL! | findstr /i "NowIAmBecomeDepth/StereoCrafterStudio" >nul
    if !errorlevel! neq 0 (
        echo [INFO] Pointing origin at StereoCrafter Studio [was !ORIGIN_URL!]
        git remote set-url origin !STUDIO_URL! 2>nul
    )
)
set "ORIGIN_URL=!STUDIO_URL!"

echo [1/4] Update source: !STUDIO_URL!
set "REMOTE=origin"

echo.
echo [2/4] Pulling latest changes from !REMOTE!...
REM Fetch first to see if branch exists
git fetch !REMOTE! main --quiet 2>nul
set "BRANCH=master"
if !errorlevel! equ 0 set "BRANCH=main"

git pull !REMOTE! !BRANCH!

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Git pull failed. This usually means you have local changes that conflict.
    echo.
    set /p force_pull="Would you like to DISCARD your local changes and force update? [Y/N]: "
    if /i "!force_pull!"=="Y" (
        echo [INFO] Forcing update [reset --hard]...
        git reset --hard !REMOTE!/!BRANCH!
        git pull !REMOTE! !BRANCH!
    ) else (
        echo.
        echo [ERROR] Update aborted. Please commit or stash your changes manually.
        pause
        exit /b 1
    )
)


REM --- Update Submodules ---
echo.
echo [3/4] Updating submodules...
git submodule update --init --recursive

REM --- UV Sync ---
echo.
echo [4/4] Syncing dependencies with uv...

REM Verify uv is installed
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo uv not found. Installing uv...
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    REM uv installs to %USERPROFILE%\.local\bin now, older versions in .cargo\bin
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] uv was installed but this window can't find it yet.
        echo [ERROR] Close this window and run the update again.
        pause
        exit /b 1
    )
)

REM Run the sync -- with Inpainting's TensorRT extra when it is installed, or the sync takes it out
if exist ".venv\Lib\site-packages\tensorrt\" (
    uv sync --extra tensorrt
) else (
    uv sync
)

if %errorlevel% equ 0 (
    echo.
    echo =================================================
    echo UPDATE SUCCESSFUL
    echo Source: !REMOTE! [!BRANCH!]
    echo Project and environment are now up to date.
    echo =================================================
    
    REM Check for the old redundant venv folder
    if exist "venv" (
        echo.
        echo [NOTE] A legacy 'venv' folder was detected. 
        echo This project now uses '.venv' [with a dot] via UV.
        echo The 'venv' folder is now REDUNDANT and can be safely deleted 
        echo to save several GBs of disk space.
    )
) else (
    echo.
    echo [WARNING] uv sync failed. Your environment might be inconsistent.
)

echo.
pause