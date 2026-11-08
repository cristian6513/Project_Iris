@echo off
rem Renueva el manifiesto del canal beta de Iris OS (A08.8): mismas versiones,
rem secuencia siguiente y 90 dias de vigencia. Hazlo antes de que venza.
rem Uso manual: doble clic, o desde cmd. Para probar sin publicar: renovar_canal_beta.cmd --dry-run
rem Firma con tu clave (solo tu usuario de Windows la abre) y publica con tu sesion de gh.
setlocal
chcp 65001 >nul
set REPO_DIR=%~dp0..
set RELEASE=%USERPROFILE%\IrisOS-Release
"%REPO_DIR%\.venv\Scripts\python.exe" -X utf8 "%REPO_DIR%\scripts\publish_update_channel.py" ^
  --channel-url https://github.com/cristian6513/Project_Iris/releases/download/channel-beta/beta.json ^
  --repo cristian6513/Project_Iris --tag channel-beta ^
  --key "%REPO_DIR%\scripts\update_keys\iris-update-production-1.json" ^
  --private "%RELEASE%\signing\iris-update-production-1.key" ^
  --gh "%RELEASE%\tools\gh\bin\gh.exe" ^
  --work "%RELEASE%\channel-beta" ^
  --expires-days 90 %*
set CODE=%ERRORLEVEL%
echo.
if not "%CODE%"=="0" echo La renovacion NO se completo. Revisa el mensaje de arriba.
pause
exit /b %CODE%
