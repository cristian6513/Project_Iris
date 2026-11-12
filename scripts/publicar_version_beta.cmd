@echo off
rem Publica una version nueva en el canal beta de Iris OS (A08.8).
rem Uso: publicar_version_beta.cmd 0.9.2 F:\ruta\iris-os-0.9.2-windows-x86_64.zip
rem 1) crea el release v<version>-beta con el paquete (si ya existe, usa el que tiene);
rem 2) firma beta.json con la secuencia siguiente y 90 dias, pasa check (descarga el paquete
rem    sin autenticacion y lo verifica) y reemplaza beta.json de forma segura.
setlocal
chcp 65001 >nul
if "%~2"=="" (
  echo Uso: %~nx0 ^<version^> ^<paquete.zip^>
  exit /b 1
)
if not exist "%~2.release.json" (
  echo Falta %~2.release.json ^(lo crea build_update_package.py package^).
  exit /b 1
)
set REPO_DIR=%~dp0..
set RELEASE=%USERPROFILE%\IrisOS-Release
set GH="%RELEASE%\tools\gh\bin\gh.exe"
set REPO=cristian6513/Project_Iris
set TAG=v%~1-beta
set URL=https://github.com/%REPO%/releases/download/%TAG%/%~nx2
%GH% release view %TAG% --repo %REPO% >nul 2>&1
if errorlevel 1 (
  echo 1/2 Creando el release %TAG% con el paquete ^(puede tardar segun tu conexion^)...
  %GH% release create %TAG% "%~2" --repo %REPO% --prerelease --latest=false --title "Iris OS %~1 beta" --notes "Actualizacion beta de Iris OS %~1. Se instala desde Iris OS en Ajustes > Sistema; no es un instalador. Iris la descarga solo si el manifiesto firmado del canal beta la lista y su hash coincide."
  if errorlevel 1 (
    echo No se pudo crear el release %TAG%; el canal no cambio.
    goto end
  )
) else (
  echo 1/2 El release %TAG% ya existe; se usa el paquete que tiene ^(check lo verifica^).
)
echo 2/2 Firmando y publicando el canal beta...
"%REPO_DIR%\.venv\Scripts\python.exe" -X utf8 "%REPO_DIR%\scripts\publish_update_channel.py" ^
  --channel-url https://github.com/%REPO%/releases/download/channel-beta/beta.json ^
  --repo %REPO% --tag channel-beta ^
  --key "%REPO_DIR%\scripts\update_keys\iris-update-production-1.json" ^
  --private "%RELEASE%\signing\iris-update-production-1.key" ^
  --gh %GH% --work "%RELEASE%\channel-beta" --expires-days 90 ^
  --release "%~2=%URL%"
if errorlevel 1 echo El canal NO se actualizo. Revisa el mensaje de arriba.
:end
echo.
pause
