@echo off
:: SAP Firecracker Windows-to-WSL wrapper
:: Translates Windows paths to WSL /mnt/ paths and delegates to the Linux binary

setlocal enabledelayedexpansion
set "ARGS="
:loop
if "%~1"=="" goto run
set "ARG=%~1"
if "!ARG!"=="--api-sock" (
    set "ARGS=!ARGS! --api-sock"
    shift
    for %%A in ("%~1") do set "WP=%%~fA"
    for /f "tokens=1 delims=:" %%D in ("!WP!") do set "DRIVE=%%D"
    set "REST=!WP:~2!"
    set "REST=!REST:\=/!"
    set "WSLPATH=/mnt/!DRIVE!!REST!"
    set "ARGS=!ARGS! !WSLPATH!"
    shift
    goto loop
)
if "!ARG!"=="--config-file" (
    set "ARGS=!ARGS! --config-file"
    shift
    for %%A in ("%~1") do set "WP=%%~fA"
    for /f "tokens=1 delims=:" %%D in ("!WP!") do set "DRIVE=%%D"
    set "REST=!WP:~2!"
    set "REST=!REST:\=/!"
    set "WSLPATH=/mnt/!DRIVE!!REST!"
    set "ARGS=!ARGS! !WSLPATH!"
    shift
    goto loop
)
set "ARGS=!ARGS! !ARG!"
shift
goto loop

:run
wsl firecracker !ARGS!
