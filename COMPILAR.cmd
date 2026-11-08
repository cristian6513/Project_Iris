@echo off
setlocal
pushd "%~dp0" || exit /b 1
where uv >nul 2>nul
if errorlevel 1 (
    echo Falta uv. Consulta docs\guides\build-from-source.md para preparar el equipo.
    popd
    if "%~1"=="" pause
    exit /b 1
)
if "%~1"=="" (
    call uv run --locked --extra dev --python 3.11 python -B scripts/build_from_source.py --build
) else (
    if /I "%~1"=="--check" (
        call uv run --offline --no-project --no-python-downloads --python 3.11 python -B scripts/build_from_source.py %*
    ) else if /I "%~1"=="--plan" (
        call uv run --offline --no-project --no-python-downloads --python 3.11 python -B scripts/build_from_source.py %*
    ) else (
        call uv run --locked --extra dev --python 3.11 python -B scripts/build_from_source.py %*
    )
)
set "IRIS_BUILD_RESULT=%ERRORLEVEL%"
popd
if "%~1"=="" pause
exit /b %IRIS_BUILD_RESULT%
