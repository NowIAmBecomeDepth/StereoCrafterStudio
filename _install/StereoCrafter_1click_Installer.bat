@echo off
setlocal enabledelayedexpansion

REM --- ANCHORING ---
REM Force the script to run from its own directory [Critical for 'Run as Admin']
cd /d "%~dp0"

REM StereoCrafter Universal Smart Installer (v4.3 - Admin Path Fix)

REM --- LOG INITIALIZATION ---
set "LOGFILE=%~dp0install_log.txt"

REM --- HEALTH CHECK [Pre-flight Permissions] ---
echo [0/7] Verifying Environment...

REM Check if running from a ZIP
echo "%~dp0" | findstr /i "Temp" >nul
if !errorlevel! equ 0 (
    echo [WARNING] It looks like you are running this from a temporary folder or a ZIP.
    echo Please EXTRACT the folder to your Desktop or a permanent location first.
)



REM Check for Admin [Required for persistent pathing/winget]
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Note: Not running as Administrator. Some features [winget] might prompt for permission.
)

REM Clear the log and start the session
echo [%date% %time%] --- NEW INSTALLATION/UPDATE SESSION --- > "%LOGFILE%"


call :log "[1/7] Checking for Git..."
where git >nul 2>&1
if %errorlevel% neq 0 (
    call :log "[INFO] Git not found. Attempting install via Winget..."
    winget install --id Git.Git -e --source winget >> "%LOGFILE%" 2>&1
    if %errorlevel% neq 0 (
        call :log "[ERROR] Git install failed. Please install manually."
        pause && exit /b 1
    )
    set "PATH=%PATH%;C:\Program Files\Git\cmd"
)

call :log "[2/7] Checking for UV..."
where uv >nul 2>&1
if %errorlevel% neq 0 (
    call :log "[INFO] uv not found. Installing..."
    powershell -c "irm https://astral.sh/uv/install.ps1 | iex" >> "%LOGFILE%" 2>&1
    REM uv installs to %USERPROFILE%\.local\bin now, older versions in .cargo\bin; this window
    REM doesn't see the PATH the installer just wrote, so add both and look again
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "[ERROR] uv was installed but this window can't find it yet."
        call :log "[ERROR] Close this window and run the installer again. Log: %LOGFILE%"
        pause && exit /b 1
    )
    call :log "[INFO] uv installed."
)

call :log "[3/7] Analyzing Path..."
set "ALREADY_HOME=false"

REM 1. Check if we are already in the project root
if exist "pyproject.toml" (
    call :log "[INFO] Project root detected in current folder."
    set "ALREADY_HOME=true"
) else (
    REM 2. Check if we are inside the _install folder
    for %%I in ("%CD%") do set "CUR_DIR_NAME=%%~nxI"
    if /i "!CUR_DIR_NAME!"=="_install" (
        if exist "..\pyproject.toml" (
            call :log "[INFO] Moving out of _install folder to project root..."
            cd ..
            set "ALREADY_HOME=true"
        )
    )
)

REM 3. If still not home, check if there's a project subfolder to move into
if "!ALREADY_HOME!"=="false" (
    set "FOUND_SUB="
    for /d %%D in (StereoCrafter*) do (
        if exist "%%D\pyproject.toml" set "FOUND_SUB=%%D"
    )

    if defined FOUND_SUB (
        call :log "[INFO] Found project in subfolder: !FOUND_SUB!"
        cd "!FOUND_SUB!"
        set "ALREADY_HOME=true"
    )
)

call :log "[4/7] Repository..."

REM StereoCrafter Studio's own repository (it replaces the earlier StereoCrafter GUI versions)
set "STUDIO_URL=https://github.com/NowIAmBecomeDepth/StereoCrafterStudio.git"
set "SELECTED_URL=!STUDIO_URL!"
set "PULL_REMOTE=origin"

REM Only clone if we haven't found a project home yet
if "!ALREADY_HOME!"=="false" (
    call :log "[INFO] No existing project detected. Cloning fresh repository from !SELECTED_URL!..."
    git clone --recurse-submodules !SELECTED_URL! >> "%LOGFILE%" 2>&1
    if !errorlevel! neq 0 (
        call :log "[ERROR] Git clone failed. Check log for details: %LOGFILE%"
        pause && exit /b 1
    )
    cd StereoCrafterStudio
    set "ALREADY_HOME=true"
    set "JUST_CLONED=true"
)

REM Standardize Remotes [matching _update.bat structure]
where git >nul 2>&1
if %errorlevel% equ 0 (
    if exist ".git" (
        call :log "[INFO] Pointing origin at StereoCrafter Studio..."
        git remote set-url origin !STUDIO_URL! 2>nul || git remote add origin !STUDIO_URL! 2>nul
    )
)

REM Optional Update Pull [Only if we didn't just clone it]
if "!ALREADY_HOME!"=="true" if "!JUST_CLONED!" neq "true" (
    echo.
    set /p do_pull="Detected existing repo. Pull latest code from !PULL_REMOTE!? [Y/N] [Default=N]: "
    if /i "!do_pull!"=="Y" (
        call :log "[INFO] Pulling updates from !PULL_REMOTE!..."
        
        REM Detect branch [main or master]
        git fetch !PULL_REMOTE! main --quiet 2>nul
        set "BR=master"
        if !errorlevel! equ 0 set "BR=main"
        
        git pull !PULL_REMOTE! !BR! >> "%LOGFILE%" 2>&1
        if !errorlevel! neq 0 (
            echo.
            echo [WARNING] Git pull failed. This usually means you have local changes.
            set /p force_pull="Would you like to DISCARD your local changes and force update? [Y/N]: "
            if /i "!force_pull!"=="Y" (
                call :log "[INFO] Forcing update [reset --hard]..."
                git reset --hard !PULL_REMOTE!/!BR! >> "%LOGFILE%" 2>&1
                git pull !PULL_REMOTE! !BR! >> "%LOGFILE%" 2>&1
            ) else (
                call :log "[SKIP] User declined force update. Manual resolution required."
            )
        )
    )
)


call :log "[5/7] Setting up Environment..."
if exist "venv" (
    call :log "[INFO] Legacy 'venv' detected."
    echo.
    echo This project now uses UV and '.venv' [with a dot].
    set /p del_venv="Would you like to delete the old 'venv' to save space? [Y/N]: "
    if /i "!del_venv!"=="Y" (
        call :log "[INFO] Removing legacy venv..."
        rmdir /s /q venv >> "%LOGFILE%" 2>&1
    )
)

call :log "[INFO] Pinning Python 3.13 and syncing..."
REM 3.13 to match requires-python in pyproject.toml. This also rewrites a stale
REM .python-version left by an older checkout - that file is gitignored, so a
REM git pull never updates it, and a leftover 3.12 makes every uv sync fail.
uv python pin 3.13 >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 (
    call :log "[ERROR] 'uv python pin 3.13' failed. Python 3.13 may be unavailable."
    call :log "[ERROR] Install did NOT complete. See the log: %LOGFILE%"
    pause && exit /b 1
)

uv sync >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 (
    call :log "[ERROR] 'uv sync' failed - the .venv is NOT usable."
    call :log "[ERROR] Install did NOT complete. See the log: %LOGFILE%"
    pause && exit /b 1
)
call :log "[OK] Environment ready."

REM --- [6/7] WEIGHTS SECTION [Parenthesis Safe] ---
echo.
echo =========================================================
echo MODEL WEIGHTS DOWNLOAD
echo =========================================================
echo The core models, about 22 GB, into the weights folder:
echo   Stable Video Diffusion img2vid-xt 1.1, DepthCrafter, StereoCrafter.
echo.
echo Stable Video Diffusion is GATED. Before saying Y:
echo   1. accept its terms at https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt-1-1
echo   2. make a READ token at https://huggingface.co/settings/tokens
echo   and answer Y when asked to log in to Hugging Face.
echo.
echo These models are for academic, research and education use only - see License-Code.txt.
echo.
set /p get_weights="Would you like to download weights now? [Y/N]: "

if /i "!get_weights!"=="Y" (
    call :log "[INFO] Starting weight downloads..."
    if not exist "weights" mkdir weights

    set /p hf_login="Do you need to log in to Hugging Face? [Y/N]: "
    if /i "!hf_login!"=="Y" (
        echo [PROMPT] Please paste your Hugging Face Access Token below:
        uv run hf auth login
    )

    call :log "[INFO] Downloading Models..."
    set "WEIGHTS_FAILED="
    uv run hf download stabilityai/stable-video-diffusion-img2vid-xt-1-1 --local-dir weights/stable-video-diffusion-img2vid-xt-1-1
    if !errorlevel! neq 0 set "WEIGHTS_FAILED=1"
    uv run hf download tencent/DepthCrafter --local-dir weights/DepthCrafter
    if !errorlevel! neq 0 set "WEIGHTS_FAILED=1"
    uv run hf download TencentARC/StereoCrafter --local-dir weights/StereoCrafter
    if !errorlevel! neq 0 set "WEIGHTS_FAILED=1"

    if defined WEIGHTS_FAILED (
        call :log "[ERROR] One or more weight downloads FAILED - see the log."
        call :log "[ERROR] Most often: Stable Video Diffusion's terms not accepted, or no Hugging Face token."
        call :log "[ERROR] Fix that and run the installer again - finished downloads are kept."
    ) else (
        call :log "[SUCCESS] Weights downloaded."
    )
) else (
    call :log "[SKIP] User skipped weights."
)

call :log "[7/7] Finalizing..."
echo.
echo Other models are fetched only for the tools that use them - SAM 3 and ROSE from inside Studio,
echo Scene Cut and Inpainting V2 as the README describes.
echo.
if defined WEIGHTS_FAILED (
    echo INSTALLATION COMPLETED WITH ERRORS - some model weights did not download.
    echo The app is installed; re-run this script to retry the downloads.
) else (
    echo INSTALLATION SUCCESSFUL
)
echo.
echo To start StereoCrafter Studio, run:  "StereoCrafter Studio.bat"
echo Log location: %LOGFILE%
call :log "[FINISH] Session complete."

pause
exit /b

REM --- THE LOGGING FUNCTION ---
:log
echo %~1
REM Use double quotes around the logfile path to handle spaces in folder names
echo [%time%] %~1 >> "%LOGFILE%"
exit /b