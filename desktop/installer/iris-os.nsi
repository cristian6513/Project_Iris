; Iris OS: instalador de Windows por usuario (A08.9).
;
; Lo compila scripts/build_installer.py con el NSIS revisado (scripts/desktop_license_sources.json)
; y estas definiciones:
;   VERSION     versión de Iris (0.9.2)        VIVERSION  la misma con cuatro partes (0.9.2.0)
;   VERSION_ID  id de la versión (8 hex)       STAGED     carpeta de extracción, _ + id[1:]
;   STAGED_TAIL id[1:]; "-" + STAGED_TAIL es la carpeta temporal al reemplazar una copia dañada
;   PAYLOAD     carpeta de la versión verificada (build_staging.py)
;   MAX_ROOT    longitud máxima de la carpeta de instalación (presupuesto MAX_PATH de A08.8)
;   OUTFILE     instalador que se genera       ICON       icono
;
; Este script solo coloca archivos y muestra páginas. Las decisiones con reglas de Iris
; (verificar la versión, instalar, reparar, actualizar, cerrar, desinstalar) las toma
; core/iris_installer.py con el Python de la propia versión. Nada se ejecuta con consola
; visible y nada se descarga: si falta WebView2, el instalador se detiene y lo explica.
;
; RMDir /r de NSIS sigue las junctions. Por eso este script nunca lo usa sobre una carpeta
; que no acaba de crear él mismo: una extracción anterior se aparta con Rename (mueve el
; enlace, no entra en él) y la borra Python; la copia que reemplaza una reparación la borra
; commit; y antes de borrar la instalación, uninstall quita todos los enlaces que contenga.

Unicode true
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
CRCCheck force

!include MUI2.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include WinVer.nsh
!include x64.nsh
!include nsDialogs.nsh

!define PRODUCT "Iris OS"
!define PUBLISHER "Iris OS"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\IrisOS"
!define UNINSTALLER "Desinstalar Iris OS.exe"
!define WEBVIEW2_GUID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
!define WEBVIEW2_PAGE "https://developer.microsoft.com/microsoft-edge/webview2/"
!define PY_FLAGS "-E -s -B -P -X utf8 -m core.iris_installer"

Name "${PRODUCT}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Iris OS"
BrandingText "${PRODUCT} ${VERSION} · beta sin firma"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${VIVERSION}"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileDescription" "Instalador de ${PRODUCT} ${VERSION} (beta, sin firma)"
VIAddVersionKey "FileVersion" "${VIVERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

Var Existing        ; 1 si ya hay una instalación en $INSTDIR
Var Mode            ; install | repair | upgrade
Var DesktopShortcut ; estado de la casilla del escritorio
Var DesktopCheck
Var DeleteData
Var DeleteCheck
Var VersionPython   ; Python de una versión instalada (cerrar Iris, desinstalar)
Var ClosedIris      ; 1 si el instalador cerró Iris o el Companion (se reabre si falla)
Var ExistingNote
; Texto exacto de runtimes\iris\python311._pth (scripts/prepare_private_runtime.py,
; interpreter_paths); build_installer.py comprueba que el runtime empaquetado lo cumple.
!define IRIS_PTH ".$\nDLLs$\nLib$\nLib/site-packages$\nimport site$\n"
!define IRIS_PTH_BYTES 41

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Instalar ${PRODUCT} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instala ${PRODUCT} ${VERSION} para tu usuario de Windows, sin permisos de administrador.$\r$\n$\r$\nEs una beta sin firma digital: Windows puede mostrar el aviso de SmartScreen antes de abrirla.$\r$\n$\r$\nIris necesita Microsoft Edge WebView2 Runtime, que Windows 11 ya trae. Si falta, el instalador te indicará cómo instalarlo desde Microsoft.$\r$\n$\r$\nTu perfil (configuración, memoria y avatares) se guarda fuera de la carpeta de instalación y se conserva al reparar, actualizar o desinstalar."
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE DirectoryPre
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirectoryLeave
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\Iris.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Abrir ${PRODUCT}"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishShow
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.DataPage un.DataLeave
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Spanish"

; ── Comprobaciones previas ────────────────────────────────────────────────

!macro SingleInstance
  System::Call 'kernel32::CreateMutex(p 0, i 0, t "IrisOS-Setup") p .r1 ?e'
  Pop $0
  ${If} $0 = 183
    MessageBox MB_ICONEXCLAMATION|MB_OK "Ya hay un instalador o desinstalador de ${PRODUCT} en marcha." /SD IDOK
    Abort
  ${EndIf}
!macroend

; WebView2 Runtime: instalado para todo el equipo (vista de 32 o 64 bits) o para el usuario.
Function WebView2Version
  StrCpy $0 ""
  SetRegView 64
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_GUID}" "pv"
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_GUID}" "pv"
  ${EndIf}
  SetRegView 32
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_GUID}" "pv"
  ${EndIf}
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\${WEBVIEW2_GUID}" "pv"
  ${EndIf}
  ${If} $0 == "0.0.0.0"
    StrCpy $0 ""
  ${EndIf}
FunctionEnd

Function .onInit
  !insertmacro SingleInstance
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "${PRODUCT} necesita Windows de 64 bits." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP|MB_OK "${PRODUCT} necesita Windows 10 o Windows 11." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  Call WebView2Version
  ${If} $0 == ""
    ; Se detecta e informa; nunca se descarga ni se instala por el usuario (decisión A08.9).
    MessageBox MB_ICONEXCLAMATION|MB_YESNO "${PRODUCT} necesita Microsoft Edge WebView2 Runtime, un componente gratuito de Microsoft que este equipo no tiene.$\r$\n$\r$\nInstálalo desde la página oficial de Microsoft (allí aceptas sus términos) y vuelve a ejecutar este instalador. Sin conexión a Internet, instálalo antes desde otro medio.$\r$\n$\r$\n¿Abrir ahora la página de Microsoft?" /SD IDNO IDNO webview2_abort
    ExecShell "open" "${WEBVIEW2_PAGE}"
    webview2_abort:
    SetErrorLevel 3
    Abort
  ${EndIf}
  SetShellVarContext current
  StrCpy $Existing 0
  ReadRegStr $0 HKCU "${UNINST_KEY}" "InstallLocation"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0\install.json"
    StrCpy $INSTDIR $0
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\install.json"
    StrCpy $Existing 1
  ${EndIf}
  StrCpy $DesktopShortcut ${BST_UNCHECKED}
  ${If} ${FileExists} "$DESKTOP\${PRODUCT}.lnk"
    StrCpy $DesktopShortcut ${BST_CHECKED}
  ${EndIf}
FunctionEnd

Function DirectoryPre
  ${If} $Existing == 1
    Abort ; una instalación existente se repara o actualiza en su sitio
  ${EndIf}
FunctionEnd

Function DirectoryLeave
  StrLen $0 $INSTDIR
  ${If} $0 > ${MAX_ROOT}
    MessageBox MB_ICONEXCLAMATION|MB_OK "La ruta de instalación tiene $0 caracteres y Windows solo admite ${MAX_ROOT} para ${PRODUCT}. Elige una carpeta con una ruta más corta."
    Abort
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\install.json"
    StrCpy $Existing 1
  ${ElseIf} ${FileExists} "$INSTDIR\*.*"
    ${DirState} "$INSTDIR" $1
    ${If} $1 = 1
      MessageBox MB_ICONEXCLAMATION|MB_OK "La carpeta elegida no está vacía y no es una instalación de ${PRODUCT}. Elige una carpeta vacía o nueva."
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "Accesos directos" "El menú Inicio siempre tendrá ${PRODUCT}."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateCheckbox} 0 0 100% 12u "Crear también un acceso directo en el escritorio"
  Pop $DesktopCheck
  ${NSD_SetState} $DesktopCheck $DesktopShortcut
  ${If} $Existing == 1
    ${NSD_CreateLabel} 0 24u 100% 36u "${PRODUCT} ya está instalado. Si está abierto, o su Companion, se cerrará al empezar para que la instalación no compita con él; guarda lo que estés haciendo. Al actualizar, ${PRODUCT} se abre solo al terminar."
    Pop $ExistingNote
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function OptionsLeave
  ${NSD_GetState} $DesktopCheck $DesktopShortcut
FunctionEnd

Function FinishShow
  ${If} $Mode == "upgrade"
    ; El cambio de versión abre Iris por sí mismo cuando termina.
    ShowWindow $mui.FinishPage.Run ${SW_HIDE}
    ${NSD_Uncheck} $mui.FinishPage.Run
  ${EndIf}
FunctionEnd

; ── Utilidades ────────────────────────────────────────────────────────────

; $R8 = 1 si la ruta $R9 es un enlace (symlink o junction: punto de reanálisis), si no 0.
!macro DEFINE_IS_LINK PREFIX
Function ${PREFIX}IsLink
  System::Call 'kernel32::GetFileAttributesW(w r19) i .r18'
  ${If} $R8 = -1
    StrCpy $R8 0
  ${Else}
    IntOp $R8 $R8 & 0x400
    ${If} $R8 <> 0
      StrCpy $R8 1
    ${EndIf}
  ${EndIf}
FunctionEnd
!macroend
!insertmacro DEFINE_IS_LINK ""
!insertmacro DEFINE_IS_LINK "un."

; Una extracción anterior se aparta (Rename no entra en enlaces); plan la borra sin seguirlos.
Function SetAsideStaged
  ${ForEach} $3 0 3 + 1
    ${Select} $3
      ${Case} 0
        StrCpy $4 "~"
      ${Case} 1
        StrCpy $4 "!"
      ${Case} 2
        StrCpy $4 "="
      ${CaseElse}
        StrCpy $4 "+"
    ${EndSelect}
    ${IfNot} ${FileExists} "$INSTDIR\v\$4${STAGED_TAIL}"
      ClearErrors
      Rename "$INSTDIR\v\${STAGED}" "$INSTDIR\v\$4${STAGED_TAIL}"
      ${IfNot} ${Errors}
        Return
      ${EndIf}
    ${EndIf}
  ${Next}
  MessageBox MB_ICONSTOP|MB_OK "Quedan restos de instalaciones interrumpidas en $INSTDIR\v que no se pueden apartar. Ciérralos o reinicia Windows y vuelve a ejecutar el instalador." /SD IDOK
  SetErrorLevel 6
  Abort
FunctionEnd

; Ejecuta core.iris_installer con el Python de la carpeta de versión $R0 (argumentos en $R1).
; Devuelve el código en $0 y la salida (UTF-16) en $1. Sin consola: nsExec.
Function RunVersionPython
  System::Call 'kernel32::SetEnvironmentVariable(t "IRIS_RUNTIME_DESCRIPTOR", t "$R0\runtime.json")'
  System::Call 'kernel32::SetEnvironmentVariable(t "IRIS_INSTALLER_OUTPUT", t "utf-16")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PYTHONNOUSERSITE", t "1")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PYTHONDONTWRITEBYTECODE", t "1")'
  SetOutPath "$R0\runtimes\iris"
  nsExec::ExecToStack '"$R0\runtimes\iris\python.exe" ${PY_FLAGS} $R1'
  Pop $0
  Pop $1
  SetOutPath "$TEMP"
FunctionEnd

!macro RenameRetry SOURCE TARGET
  StrCpy $2 0
  ${Do}
    ClearErrors
    Rename "${SOURCE}" "${TARGET}"
    ${IfNot} ${Errors}
      ${Break}
    ${EndIf}
    IntOp $2 $2 + 1
    ${If} $2 >= 50
      MessageBox MB_ICONSTOP|MB_OK "No se pudo mover ${SOURCE}: otro programa (quizá el antivirus) lo está usando. Vuelve a ejecutar el instalador." /SD IDOK
      Abort
    ${EndIf}
    Sleep 200
  ${Loop}
!macroend

; $R5 = 1 si el texto $R6 contiene $R7 (sin distinguir mayúsculas); usa $R2, $R3 y $R4.
Function StrContains
  StrCpy $R5 0
  StrLen $R4 $R7
  StrCpy $R3 0
  ${Do}
    StrCpy $R2 $R6 $R4 $R3
    ${If} $R2 == ""
      ${ExitDo}
    ${EndIf}
    ${If} $R2 == $R7
      StrCpy $R5 1
      ${ExitDo}
    ${EndIf}
    IntOp $R3 $R3 + 1
  ${Loop}
FunctionEnd

; Recuerda en $ClosedIris si un `close` ($0 código, $1 salida) cerró Iris o el Companion.
; Contrato actual: código 3. Contrato de qa9 y anteriores: código 0 siempre, y la salida
; JSON lista las ventanas cerradas y los procesos terminados (vacías: no había nada abierto).
Function RecordClose
  ${If} $0 = 3
    StrCpy $ClosedIris 1
    Return
  ${EndIf}
  ${If} $0 != "0"
    Return
  ${EndIf}
  StrCpy $R6 $1
  StrCpy $R7 '"windows":'
  Call StrContains
  ${If} $R5 = 0
    Return ; no es la salida de close
  ${EndIf}
  StrCpy $R7 '"windows": []'
  Call StrContains
  StrCpy $R1 $R5
  StrCpy $R7 '"terminated": []'
  Call StrContains
  ${If} $R1 = 0
  ${OrIf} $R5 = 0
    StrCpy $ClosedIris 1
  ${EndIf}
FunctionEnd

; Si el instalador cerró Iris y se detiene sin cambiar de versión, abre otra vez la que corría.
Function ReopenClosedIris
  ${If} $ClosedIris == 1
  ${AndIf} ${FileExists} "$INSTDIR\Iris.exe"
    Exec '"$INSTDIR\Iris.exe"'
    StrCpy $ClosedIris 0
  ${EndIf}
FunctionEnd

; ── Instalación ───────────────────────────────────────────────────────────

Section "${PRODUCT}" SecMain
  SetShellVarContext current
  CreateDirectory "$INSTDIR\v"
  StrCpy $R9 "$INSTDIR\v"
  Call IsLink
  ${If} $R8 = 1
    MessageBox MB_ICONSTOP|MB_OK "La carpeta $INSTDIR\v es un enlace a otro lugar; no se instaló nada." /SD IDOK
    SetErrorLevel 6
    Abort
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\v\${STAGED}"
    Call SetAsideStaged
  ${EndIf}

  ; Iris y su Companion abiertos dejaban sin procesador a la copia y la comprobación: una
  ; actualización tardó más de 30 minutos (qa9). La versión instalada los cierra antes de
  ; copiar nada, con sus propias reglas y las mismas comprobaciones que usa el desinstalador.
  StrCpy $ClosedIris 0
  ${If} $Existing == 1
    Call FindVersionPython
    ${If} $VersionPython != ""
      DetailPrint "Cerrando ${PRODUCT} y el Companion…"
      StrCpy $R0 $VersionPython
      StrCpy $R1 'close --root "$INSTDIR"'
      Call RunVersionPython
      ${If} $0 = 1
        MessageBox MB_ICONSTOP|MB_OK "No se pudo cerrar ${PRODUCT}; no se cambió nada.$\r$\n$1" /SD IDOK
        SetErrorLevel 4
        Abort
      ${EndIf}
      Call RecordClose
    ${EndIf}
  ${EndIf}

  DetailPrint "Copiando ${PRODUCT} ${VERSION}…"
  SetDetailsPrint none
  SetOutPath "$INSTDIR\v\${STAGED}"
  File /r "${PAYLOAD}\*.*"
  SetDetailsPrint both
  SetOutPath "$TEMP"

  ; La versión instalada puede ser anterior a este cierre (la de qa9 no cierra el Companion):
  ; la versión nueva, con el mismo Python que comprobará sus archivos, cierra lo que siga
  ; abierto antes de esa comprobación. Solo ejecuta programas de versiones instaladas e íntegras.
  ${If} $Existing == 1
    DetailPrint "Cerrando lo que siga abierto de ${PRODUCT}…"
    StrCpy $R0 "$INSTDIR\v\${STAGED}"
    StrCpy $R1 'close --root "$INSTDIR"'
    Call RunVersionPython
    ${If} $0 = 1
      RMDir /r "$INSTDIR\v\${STAGED}"
      Call ReopenClosedIris
      MessageBox MB_ICONSTOP|MB_OK "No se pudo cerrar ${PRODUCT}; no se cambió nada.$\r$\n$1" /SD IDOK
      SetErrorLevel 4
      Abort
    ${EndIf}
    Call RecordClose
  ${EndIf}

  DetailPrint "Comprobando cada archivo de la versión. Puede tardar varios minutos; la barra no avanza durante este paso…"
  StrCpy $R0 "$INSTDIR\v\${STAGED}"
  StrCpy $R1 'plan --root "$INSTDIR" --staged "$INSTDIR\v\${STAGED}"'
  Call RunVersionPython
  StrCpy $R2 "$INSTDIR\v\${VERSION_ID}"
  StrCpy $R3 "$INSTDIR\v\-${STAGED_TAIL}"
  ${Switch} $0
    ${Case} 10
      !insertmacro RenameRetry "$INSTDIR\v\${STAGED}" "$R2"
      StrCpy $Mode "install"
      ${Break}
    ${Case} 11
      RMDir /r "$INSTDIR\v\${STAGED}"
      StrCpy $Mode "repair"
      ${Break}
    ${Case} 12
      ${If} ${FileExists} "$R2"
        !insertmacro RenameRetry "$R2" "$R3"
      ${EndIf}
      !insertmacro RenameRetry "$INSTDIR\v\${STAGED}" "$R2"
      StrCpy $Mode "repair"
      ${Break}
    ${Case} 13
      !insertmacro RenameRetry "$INSTDIR\v\${STAGED}" "$R2"
      StrCpy $Mode "upgrade"
      ${Break}
    ${Case} 14
      RMDir /r "$INSTDIR\v\${STAGED}"
      StrCpy $Mode "upgrade"
      ${Break}
    ${Case} 15
      ${If} ${FileExists} "$R2"
        !insertmacro RenameRetry "$R2" "$R3"
      ${EndIf}
      !insertmacro RenameRetry "$INSTDIR\v\${STAGED}" "$R2"
      StrCpy $Mode "upgrade"
      ${Break}
    ${Default}
      RMDir /r "$INSTDIR\v\${STAGED}"
      Call ReopenClosedIris
      ${If} $0 == 1
        MessageBox MB_ICONSTOP|MB_OK "$1" /SD IDOK
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "No se pudo comprobar la versión ($0).$\r$\n$1" /SD IDOK
      ${EndIf}
      SetErrorLevel 4
      Abort
  ${EndSwitch}

  DetailPrint "Registrando la versión ($Mode)…"
  StrCpy $R0 "$R2"
  StrCpy $R1 'commit --root "$INSTDIR" --version ${VERSION_ID} --mode $Mode'
  Call RunVersionPython
  ${If} $0 != 0
    Call ReopenClosedIris
    MessageBox MB_ICONSTOP|MB_OK "No se pudo completar la instalación.$\r$\n$1" /SD IDOK
    SetErrorLevel 5
    Abort
  ${EndIf}

  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\${UNINSTALLER}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\Iris.exe" "" "$INSTDIR\Iris.exe" 0
  ${If} $DesktopShortcut == ${BST_CHECKED}
    CreateShortcut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\Iris.exe" "" "$INSTDIR\Iris.exe" 0
  ${Else}
    Delete "$DESKTOP\${PRODUCT}.lnk"
  ${EndIf}
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${PRODUCT}"
  ${If} $Mode != "upgrade"
    ; Una actualización muestra su versión cuando se confirma (core.iris_install.sync_windows_entry);
    ; si vuelve atrás, sigue mostrando la que corre.
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  ${EndIf}
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\Iris.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\${UNINSTALLER}"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTALLER}" /S'
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://github.com/cristian6513/Project_Iris"
  WriteRegStr HKCU "${UNINST_KEY}" "Comments" "Beta sin firma digital"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" $0
  ; El acceso de Inicio es independiente del icono opcional del escritorio.
  ; Notifica su carpeta tras registrar la app: Explorer/Buscar puede conservar
  ; una vista anterior si nadie anuncia el cambio. No reinicia ni borra el índice.
  System::Call 'shell32::SHChangeNotify(i 0x00001000, i 0x00002005, w "$SMPROGRAMS", p 0)'
  ${If} $Mode == "upgrade"
    DetailPrint "Iris OS cambiará a la versión ${VERSION} y se abrirá en unos segundos."
  ${EndIf}
SectionEnd

; ── Desinstalación ────────────────────────────────────────────────────────

; Las comprobaciones siguientes las usan el desinstalador y el instalador, que antes de copiar
; una versión nueva cierra Iris con el Python de la versión instalada (qa9).
!macro DEFINE_VERSION_PYTHON PREFIX
; Python de una versión instalada (v\<8 hex>, sin enlaces en el camino) que traiga
; core/iris_installer.py; esa versión verifica y cierra las demás antes de ejecutarlas.
Function ${PREFIX}FindVersionPython
  StrCpy $VersionPython ""
  StrCpy $R9 "$INSTDIR\v"
  Call ${PREFIX}IsLink
  ${If} $R8 = 1
    Return
  ${EndIf}
  FindFirst $0 $1 "$INSTDIR\v\*"
  ${DoWhile} $1 != ""
    StrCpy $5 $1 1
    StrLen $6 $1
    ${If} $6 = 8
    ${AndIf} $5 != "_"
    ${AndIf} $5 != "-"
    ${AndIf} $5 != "~"
    ${AndIf} $5 != "!"
    ${AndIf} $5 != "="
    ${AndIf} $5 != "+"
    ${AndIf} $5 != "."
      StrCpy $2 "$INSTDIR\v\$1"
      Call ${PREFIX}SafeVersion
      ${If} $R7 = 0
        StrCpy $VersionPython $2
        ${Break}
      ${EndIf}
    ${EndIf}
    FindNext $0 $1
  ${Loop}
  FindClose $0
FunctionEnd

; $R7 = 0 si la versión $2 se puede ejecutar sin que Python cargue nada de fuera de la
; instalación. Se comprueba antes de ejecutar nada: el módulo no puede protegerse a sí mismo
; después de importarse. Medido con el runtime real: python311._pth fija sys.path (., DLLs,
; Lib, Lib\site-packages), ignora el entorno y la carpeta actual, y site lee los .pth de
; runtimes\iris y de site-packages. pyvenv.cfg se busca junto a python.exe y en la carpeta
; superior. Por eso:
;   - existen el programa, su DLL, su ruta de búsqueda y el módulo que se importará;
;   - no hay ningún enlace en la versión, en runtimes ni en todo el runtime de Iris
;     (cualquier carpeta o archivo enlazado podría llevar un import fuera);
;   - python311._pth es exactamente el del runtime y no hay otro ._pth, ni .pth, ni pyvenv.cfg;
;   - ningún otro paquete core está antes que el de site-packages.
Function ${PREFIX}SafeVersion
  StrCpy $R7 0
  ${ForEach} $8 0 4 + 1
    ${Select} $8
      ${Case} 0
        StrCpy $R9 "python.exe"
      ${Case} 1
        StrCpy $R9 "python311.dll"
      ${Case} 2
        StrCpy $R9 "python311._pth"
      ${Case} 3
        StrCpy $R9 "Lib\site-packages\core\__init__.py"
      ${CaseElse}
        StrCpy $R9 "Lib\site-packages\core\iris_installer.py"
    ${EndSelect}
    ${IfNot} ${FileExists} "$2\runtimes\iris\$R9"
      IntOp $R7 $R7 + 1
    ${EndIf}
  ${Next}
  ${If} $R7 <> 0
    Return
  ${EndIf}
  StrCpy $R9 "$2"
  Call ${PREFIX}IsLink
  IntOp $R7 $R7 + $R8
  StrCpy $R9 "$2\runtimes"
  Call ${PREFIX}IsLink
  IntOp $R7 $R7 + $R8
  ${If} $R7 <> 0
    Return
  ${EndIf}
  StrCpy $R9 "$2\runtimes\iris"
  Call ${PREFIX}TreeHasLinks
  IntOp $R7 $R7 + $R8
  Call ${PREFIX}ShippedSearchPath
  IntOp $R7 $R7 + $R8
  ${If} ${FileExists} "$2\runtimes\iris\pyvenv.cfg"
  ${OrIf} ${FileExists} "$2\runtimes\pyvenv.cfg"
    IntOp $R7 $R7 + 1
  ${EndIf}
  ; Ningún otro core antes que el de site-packages (., DLLs y Lib se buscan primero).
  ${If} ${FileExists} "$2\runtimes\iris\core*"
  ${OrIf} ${FileExists} "$2\runtimes\iris\DLLs\core*"
  ${OrIf} ${FileExists} "$2\runtimes\iris\Lib\core*"
    IntOp $R7 $R7 + 1
  ${EndIf}
FunctionEnd

; $R8 = 1 si $R9, o cualquier cosa dentro de él, es un enlace o no se puede leer. Recorre el
; árbol con la pila de NSIS ($7 = carpetas pendientes) y la deja como estaba.
Function ${PREFIX}TreeHasLinks
  Call ${PREFIX}IsLink
  ${If} $R8 <> 0
    Return
  ${EndIf}
  Push $R9
  StrCpy $7 1
  ${DoWhile} $7 > 0
    Pop $3
    IntOp $7 $7 - 1
    ${If} $R8 = 0
      ClearErrors
      FindFirst $5 $6 "$3\*"
      ${If} ${Errors}
        StrCpy $R8 1
      ${EndIf}
      ${DoWhile} $6 != ""
        ${If} $R8 <> 0
          ${ExitDo}
        ${EndIf}
        ${If} $6 != "."
        ${AndIf} $6 != ".."
          StrCpy $R9 "$3\$6" ; por registro: un nombre nunca se interpreta como sintaxis
          System::Call 'kernel32::GetFileAttributesW(w r19) i .r4'
          IntOp $9 $4 & 0x400
          ${If} $4 = -1
          ${OrIf} $9 <> 0
            StrCpy $R8 1
          ${Else}
            IntOp $9 $4 & 0x10
            ${If} $9 <> 0
              Push "$3\$6"
              IntOp $7 $7 + 1
            ${EndIf}
          ${EndIf}
        ${EndIf}
        FindNext $5 $6
      ${Loop}
      FindClose $5
    ${EndIf}
  ${Loop}
FunctionEnd

; $R8 = 0 si la ruta de búsqueda de Python es exactamente la del runtime: python311._pth con
; el texto que escribe scripts/prepare_private_runtime.py (interpreter_paths), sin otro ._pth
; que Python pudiera leer, y sin archivos .pth en las carpetas que lee site.
Function ${PREFIX}ShippedSearchPath
  StrCpy $R8 1
  ClearErrors
  FileOpen $3 "$2\runtimes\iris\python311._pth" r
  ${If} ${Errors}
    Return
  ${EndIf}
  FileSeek $3 0 END $4
  FileSeek $3 0 SET
  StrCpy $6 ""
  ${Do}
    ClearErrors
    FileRead $3 $5
    ${If} ${Errors}
      ${ExitDo}
    ${EndIf}
    StrCpy $6 "$6$5"
  ${Loop}
  FileClose $3
  ; Mismo tamaño y mismo texto: ningún byte nulo ni línea de más puede esconderse.
  ${If} $4 <> ${IRIS_PTH_BYTES}
  ${OrIf} $6 S!= "${IRIS_PTH}"
    Return
  ${EndIf}
  FindFirst $5 $6 "$2\runtimes\iris\*"
  ${DoWhile} $6 != ""
    StrCpy $4 $6 "" -5
    StrCpy $3 $6 "" -4
    ${If} $4 == "._pth"
    ${AndIf} $6 != "python311._pth"
      StrCpy $R8 2
    ${EndIf}
    ${If} $3 == ".pth"
      StrCpy $R8 2
    ${EndIf}
    FindNext $5 $6
  ${Loop}
  FindClose $5
  FindFirst $5 $6 "$2\runtimes\iris\Lib\site-packages\*"
  ${DoWhile} $6 != ""
    StrCpy $3 $6 "" -4
    ${If} $3 == ".pth"
      StrCpy $R8 2
    ${EndIf}
    FindNext $5 $6
  ${Loop}
  FindClose $5
  ${If} $R8 = 1
    StrCpy $R8 0
  ${EndIf}
FunctionEnd
!macroend
!insertmacro DEFINE_VERSION_PYTHON ""
!insertmacro DEFINE_VERSION_PYTHON "un."

Function un.onInit
  !insertmacro SingleInstance
  SetShellVarContext current
  StrCpy $DeleteData ${BST_UNCHECKED}
  ${IfNot} ${FileExists} "$INSTDIR\install.json"
  ${AndIfNot} ${FileExists} "$INSTDIR\v\*.*"
    MessageBox MB_ICONSTOP|MB_OK "Esta carpeta no parece una instalación de ${PRODUCT}; no se borrará nada." /SD IDOK
    Abort
  ${EndIf}
  Call un.FindVersionPython
FunctionEnd

Function un.DataPage
  !insertmacro MUI_HEADER_TEXT "Tus datos" "La desinstalación conserva tu perfil salvo que pidas lo contrario."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 48u "Se quitan ${PRODUCT}, todas sus versiones y el Companion instalado por Iris. Tu perfil de Iris (configuración, memoria, avatares y extras) se conserva para una reinstalación.$\r$\n$\r$\nLos datos de Hermes (%LOCALAPPDATA%\hermes) nunca se borran: Hermes Agent y otras aplicaciones también los usan."
  Pop $0
  ${NSD_CreateCheckbox} 0 56u 100% 12u "Borrar también mi perfil de Iris"
  Pop $DeleteCheck
  ${NSD_SetState} $DeleteCheck ${BST_UNCHECKED}
  nsDialogs::Show
FunctionEnd

Function un.DataLeave
  ${NSD_GetState} $DeleteCheck $DeleteData
  ${If} $DeleteData == ${BST_CHECKED}
    StrCpy $R1 "profile"
    StrCpy $1 ""
    ${If} $VersionPython != ""
      StrCpy $R0 $VersionPython
      Call un.RunVersionPython
    ${EndIf}
    MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "Se borrará tu perfil de Iris y no se podrá deshacer:$\r$\n$1$\r$\n$\r$\n¿Borrarlo?" IDYES delete_confirmed
    ${NSD_Uncheck} $DeleteCheck
    Abort
    delete_confirmed:
  ${EndIf}
FunctionEnd

Function un.RunVersionPython
  System::Call 'kernel32::SetEnvironmentVariable(t "IRIS_RUNTIME_DESCRIPTOR", t "$R0\runtime.json")'
  System::Call 'kernel32::SetEnvironmentVariable(t "IRIS_INSTALLER_OUTPUT", t "utf-16")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PYTHONNOUSERSITE", t "1")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PYTHONDONTWRITEBYTECODE", t "1")'
  SetOutPath "$R0\runtimes\iris"
  nsExec::ExecToStack '"$R0\runtimes\iris\python.exe" ${PY_FLAGS} $R1'
  Pop $0
  Pop $1
  SetOutPath "$TEMP"
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  ${If} $VersionPython != ""
    DetailPrint "Cerrando ${PRODUCT} y quitando el Companion…"
    StrCpy $R0 $VersionPython
    StrCpy $R1 'uninstall --root "$INSTDIR"'
    ${If} $DeleteData == ${BST_CHECKED}
      StrCpy $R1 '$R1 --delete-profile'
    ${EndIf}
    Call un.RunVersionPython
    ${If} $0 = 2
      ; Desinstalado, pero algo se conservó o no se pudo limpiar: se muestra antes de terminar.
      MessageBox MB_ICONINFORMATION|MB_OK "$1" /SD IDOK
    ${ElseIf} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "No se pudo cerrar ${PRODUCT}; no se borró nada.$\r$\n$1" /SD IDOK
      SetErrorLevel 4
      Abort
    ${EndIf}
    SetOutPath "$TEMP"
    DetailPrint "Quitando los archivos de ${PRODUCT}…"
    ; uninstall ya quitó todos los enlaces de la instalación: RMDir /r no puede salir de ella.
    RMDir /r "$INSTDIR"
  ${Else}
    ; Sin una versión íntegra no se pueden quitar los enlaces con seguridad: la carpeta se deja.
    MessageBox MB_ICONEXCLAMATION|MB_OKCANCEL "No se encontró una versión íntegra de ${PRODUCT} para cerrarla con seguridad. Se quitarán los accesos y la entrada de Aplicaciones, pero la carpeta $INSTDIR se conservará: revísala y bórrala a mano." /SD IDOK IDOK no_python_continue
    Abort
    no_python_continue:
    SetOutPath "$TEMP"
    Delete "$INSTDIR\${UNINSTALLER}"
  ${EndIf}
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  System::Call 'shell32::SHChangeNotify(i 0x00001000, i 0x00002005, w "$SMPROGRAMS", p 0)'
  Delete "$DESKTOP\${PRODUCT}.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
  ${If} ${FileExists} "$INSTDIR\*.*"
    MessageBox MB_ICONEXCLAMATION|MB_OK "Algunos archivos de $INSTDIR seguían en uso y no se borraron." /SD IDOK
  ${EndIf}
SectionEnd
