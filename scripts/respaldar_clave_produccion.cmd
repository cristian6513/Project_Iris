@echo off
rem Copia de seguridad cifrada de la clave de produccion del canal de Iris OS (A08.8).
rem Uso: respaldar_clave_produccion.cmd E:\iris-update-production-1.backup.pem
rem Pide una frase (minimo 12 caracteres) que solo tu conoces; sin ella la copia no sirve.
rem Despues abre la copia con esa frase y comprueba que es la misma clave publica.
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Indica donde guardar la copia, por ejemplo: %~nx0 E:\iris-update-production-1.backup.pem
  exit /b 1
)
set REPO_DIR=%~dp0..
set KEY=%USERPROFILE%\IrisOS-Release\signing\iris-update-production-1.key
set PUBLIC=%REPO_DIR%\scripts\update_keys\iris-update-production-1.json
set PY="%REPO_DIR%\.venv\Scripts\python.exe" -X utf8 "%REPO_DIR%\scripts\build_update_package.py"
echo 1/2 Creando la copia cifrada en %~1
%PY% backup-key --private "%KEY%" --output "%~1"
if errorlevel 1 goto failed
echo.
echo 2/2 Verificando la copia: escribe la misma frase
%PY% verify-key --private "%~1" --key "%PUBLIC%"
if errorlevel 1 goto failed
echo.
echo Copia verificada. Guardala fuera de este PC y no guardes la frase junto a ella.
exit /b 0
:failed
echo.
echo La copia NO quedo verificada. No borres ninguna otra copia de la clave.
exit /b 1
