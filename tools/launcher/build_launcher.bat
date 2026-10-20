@echo off
setlocal
rem Builds "StereoCrafter Studio.exe" in the repo root from StudioLauncher.cs,
rem with assets\StereoCrafter.ico as its icon. Uses the C# compiler that ships
rem with Windows (.NET Framework 4) -- nothing to install.
cd /d "%~dp0..\.."

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo  The .NET Framework 4 C# compiler was not found:
    echo    %CSC%
    pause
    exit /b 1
)

"%CSC%" /nologo /target:winexe /optimize+ /win32icon:"assets\StereoCrafter.ico" ^
    /reference:System.Windows.Forms.dll ^
    /out:"StereoCrafter Studio.exe" "tools\launcher\StudioLauncher.cs"
if errorlevel 1 (
    echo  Build failed.
    pause
    exit /b 1
)
echo  Built "StereoCrafter Studio.exe".
