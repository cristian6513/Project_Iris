<#
.SYNOPSIS
  QA del instalador de Iris OS (A08.9). No necesita Python, Git ni Hermes.

.DESCRIPTION
  Comprueba lo que el instalador deja en Windows. Ejecútalo con tu usuario, en una
  ventana normal de PowerShell (no desde Claude: su aplicación virtualiza AppData y
  el registro del usuario y el resultado no valdría).

  Fases:
    instalado       tras instalar, reparar o actualizar (solo lee)
    abrir           abre Iris y espera su ventana (el primer uso pasa por el asistente;
                    el Core aún no arranca y no se exige)
    nucleo          tras configurar: el Core en 127.0.0.1 debe ser el de ESTA
                    instalación (proceso, versión, revisiones y perfil)
    preparar-datos  antes de desinstalar o reparar: escribe datos de prueba con hashes
                    conocidos en el perfil de Iris y en la carpeta de Hermes
    desinstalado    tras desinstalar sin borrar datos: compara esos datos (solo lee)

  Cada prueba queda como ok, falla o manual. "manual" es una observación que solo
  puedes confirmar tú (por ejemplo, que no apareció ninguna ventana de consola);
  el script nunca la da por aprobada.

  Escribe un informe JSON en la carpeta actual (qa-iris-<fase>-<fecha>.json). Los
  hashes de preparar-datos se guardan en Documentos\iris-qa\datos-de-prueba.json.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\verificar-instalacion.ps1 -Fase instalado -Instalador .\IrisOS-0.9.2-beta-x64-sin-firma-setup.exe
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("instalado", "abrir", "nucleo", "preparar-datos", "desinstalado")]
  [string]$Fase,
  [string]$Instalador = "",
  [string]$Carpeta = "",
  [string]$CarpetaQa = "",
  [int]$Puerto = 8765,
  [int]$EsperaSegundos = 180
)

$ErrorActionPreference = "Stop"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\IrisOS"
$DefaultRoot = Join-Path $env:LOCALAPPDATA "Programs\Iris OS"
# As the Core reads them (core/config.py, wizard/hermes_config.py): "~" is the user folder,
# and a relative HERMES_HOME starts there.
function UserPath([string]$Value) {
  $value = $Value.Trim()
  if ($value -eq "~" -or $value.StartsWith("~\") -or $value.StartsWith("~/")) { $value = $env:USERPROFILE + $value.Substring(1) }
  if (-not [IO.Path]::IsPathRooted($value)) { $value = Join-Path $env:USERPROFILE $value }
  return $value
}
$IrisProfile = if ("$env:IRIS_HOME".Trim()) { UserPath $env:IRIS_HOME } else { Join-Path $env:USERPROFILE ".iris" }
$HermesHome = if ("$env:HERMES_HOME".Trim()) { UserPath $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$StartMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Iris OS.lnk"
$Desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Iris OS.lnk"
$QaFolder = if ($CarpetaQa) { $CarpetaQa } else { Join-Path ([Environment]::GetFolderPath("MyDocuments")) "iris-qa" }
$Fixtures = Join-Path $QaFolder "datos-de-prueba.json"
$Checks = New-Object System.Collections.Generic.List[object]

function Check([string]$Name, $Ok, $Detail = "") {
  # A bare $true -eq "manual" is true in PowerShell: test the type first.
  $state = if ($Ok -is [string] -and $Ok -eq "manual") { "manual" } elseif ($Ok) { "ok" } else { "falla" }
  $Checks.Add([pscustomobject]@{ prueba = $Name; estado = $state; detalle = "$Detail" })
  $mark = @{ ok = "OK    "; falla = "FALLA "; manual = "MANUAL" }[$state]
  Write-Host "$mark $Name $(if ($Detail) { "· $Detail" })"
}

function WebView2Version {
  $guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
  foreach ($path in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
                      "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
                      "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$guid")) {
    $value = (Get-ItemProperty -Path $path -Name pv -ErrorAction SilentlyContinue).pv
    if ($value -and $value -ne "0.0.0.0") { return $value }
  }
  return ""
}

function InstallRoot {
  if ($Carpeta) { return $Carpeta }
  $location = (Get-ItemProperty -Path $UninstallKey -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
  if ($location) { return $location }
  return $DefaultRoot
}

function Sha256Text([string]$Text) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return -join ($hash | ForEach-Object { $_.ToString("x2") })
}

# Folder ids as the Core computes them (core/runtime_descriptor.py, runtime_service_identity):
# sha256 of os.path.normcase(Path(path).resolve(strict=False)). Resolving follows junctions and
# symlinks with the same Windows call Python uses, so a valid alias of the profile gets the id
# of its real folder; a missing tail is kept as written. Never compare written paths instead.
function FinalPath([string]$Path) {
  if (-not ("IrisQa.Native" -as [type])) {
    Add-Type -Namespace IrisQa -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern uint GetFinalPathNameByHandleW(Microsoft.Win32.SafeHandles.SafeFileHandle handle, System.Text.StringBuilder path, uint length, uint flags);
'@
  }
  # No access right is needed to ask for the name; FILE_FLAG_BACKUP_SEMANTICS opens folders too.
  $handle = [IrisQa.Native]::CreateFileW($Path, 0, 7, [IntPtr]::Zero, 3, 0x02000000, [IntPtr]::Zero)
  if ($handle.IsInvalid) { return $null }
  try {
    $buffer = New-Object Text.StringBuilder 32768
    $length = [IrisQa.Native]::GetFinalPathNameByHandleW($handle, $buffer, 32768, 0)
    if ($length -eq 0 -or $length -ge 32768) { return $null }
    $final = $buffer.ToString()
    if ($final.StartsWith('\\?\UNC\')) { return '\\' + $final.Substring(8) }
    if ($final.StartsWith('\\?\')) { return $final.Substring(4) }
    return $final
  } finally { $handle.Dispose() }
}

function FolderId([string]$Path) {
  $current = [IO.Path]::GetFullPath($Path)
  $tail = @()
  $resolved = FinalPath $current
  while (-not $resolved) {
    $parent = [IO.Path]::GetDirectoryName($current)
    if (-not $parent) { $resolved = $current; break }
    $tail = @([IO.Path]::GetFileName($current)) + $tail
    $current = $parent
    $resolved = FinalPath $current
  }
  foreach ($name in $tail) { $resolved = Join-Path $resolved $name }
  return Sha256Text $resolved.Replace('/', '\').ToLowerInvariant()
}

# Every listener of a port, IPv4 and IPv6, with its process: the same table netstat -ano reads.
# Get-NetTCPConnection shows a single row when two processes listen on the same address and
# port, which would hide exactly the ambiguous case.
function PortListeners([int]$Port) {
  if (-not ("IrisQa.Tcp" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Net;
using System.Runtime.InteropServices;
namespace IrisQa {
  public class Listener { public string LocalAddress; public uint OwningProcess; }
  public static class Tcp {
    [DllImport("iphlpapi.dll")]
    static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool order, int family, int kind, uint reserved);
    public static List<Listener> Listeners(int port) {
      var found = new List<Listener>();
      foreach (int family in new[] { 2, 23 }) {  // AF_INET, AF_INET6
        int size = 0;
        GetExtendedTcpTable(IntPtr.Zero, ref size, false, family, 3, 0);  // TCP_TABLE_OWNER_PID_LISTENER
        IntPtr table = Marshal.AllocHGlobal(size);
        try {
          uint error = GetExtendedTcpTable(table, ref size, false, family, 3, 0);
          if (error != 0) throw new System.ComponentModel.Win32Exception((int)error);
          int rows = Marshal.ReadInt32(table), width = family == 2 ? 24 : 56;
          for (int i = 0; i < rows; i++) {
            IntPtr row = new IntPtr(table.ToInt64() + 4 + (long)i * width);
            int portField = Marshal.ReadInt32(row, family == 2 ? 8 : 20);
            if ((((portField & 0xFF) << 8) | ((portField >> 8) & 0xFF)) != port) continue;
            IPAddress address;
            if (family == 2) {
              address = new IPAddress((long)(uint)Marshal.ReadInt32(row, 4));
            } else {
              var bytes = new byte[16];
              Marshal.Copy(row, bytes, 0, 16);
              address = new IPAddress(bytes);
            }
            uint pid = (uint)Marshal.ReadInt32(row, family == 2 ? 20 : 52);
            found.Add(new Listener { LocalAddress = address.ToString(), OwningProcess = pid });
          }
        } finally { Marshal.FreeHGlobal(table); }
      }
      return found;
    }
  }
}
'@
  }
  return @([IrisQa.Tcp]::Listeners($Port))
}

# Which process serves http://127.0.0.1:<port>, from the listeners of that port. Windows gives
# the connection to a listener bound to 127.0.0.1 itself, or else to one bound to the wildcard
# address (0.0.0.0, or :: when it also takes IPv4); a listener on another address (127.0.0.2...)
# never serves it. Two processes at the same level are ambiguous: no owner, never approved.
function ListenerOwner($Listeners) {
  $listeners = @($Listeners)
  $detail = ($listeners | ForEach-Object { "$($_.LocalAddress) (proceso $($_.OwningProcess))" }) -join ", "
  foreach ($level in @("127.0.0.1", "0.0.0.0 ::")) {
    $addresses = $level -split " "
    $owners = @($listeners | Where-Object { $addresses -contains "$($_.LocalAddress)" } |
      ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)
    if ($owners.Count -eq 1) { return [pscustomobject]@{ id = $owners[0]; detail = $detail } }
    if ($owners.Count -gt 1) { return [pscustomobject]@{ id = $null; detail = "ambiguo: $detail" } }
  }
  return [pscustomobject]@{ id = $null; detail = "nadie escucha en 127.0.0.1 ni en todas las direcciones: $detail" }
}

function ActiveRecord([string]$Root) {
  $statePath = Join-Path $Root "install.json"
  if (-not (Test-Path $statePath)) { return $null }
  $state = Get-Content -Raw -Encoding UTF8 $statePath | ConvertFrom-Json
  return [pscustomobject]@{ id = $state.active; record = $state.versions.($state.active); state = $state }
}

function InsideRoot([string]$Path, [string]$Root) {
  if (-not $Path) { return $false }
  $full = [IO.Path]::GetFullPath($Path).ToLowerInvariant()
  $base = [IO.Path]::GetFullPath($Root).TrimEnd('\').ToLowerInvariant() + '\'
  return $full.StartsWith($base)
}

# Processes started from the installation, and every descendant of them.
function IrisProcessTree([string]$Root) {
  $all = @(Get-CimInstance Win32_Process)
  $tree = @{}
  foreach ($p in $all) { if (InsideRoot $p.ExecutablePath $Root) { $tree[[int]$p.ProcessId] = $p } }
  do {
    $added = 0
    foreach ($p in $all) {
      if (-not $tree.ContainsKey([int]$p.ProcessId) -and $tree.ContainsKey([int]$p.ParentProcessId)) {
        $tree[[int]$p.ProcessId] = $p; $added++
      }
    }
  } while ($added -gt 0)
  return $tree.Values
}

function FixtureFiles {
  return @(
    @{ home = "iris"; path = "qa-a089\notas.txt"; text = "Datos de prueba A08.9: perfil de Iris.`r`n" },
    @{ home = "iris"; path = "qa-a089\anidado\config-ficticia.json"; text = '{"proveedor": "ficticio", "clave": "TEST_ONLY"}' },
    @{ home = "hermes"; path = "qa-a089\credencial-ficticia.env"; text = "TEST_ONLY=hermes-fixture`r`n" },
    @{ home = "hermes"; path = "qa-a089\anidado\memoria-ficticia.md"; text = "Memoria ficticia de Hermes A08.9.`r`n" }
  )
}

function DataHome([string]$Name) { if ($Name -eq "iris") { return $IrisProfile } else { return $HermesHome } }

$os = Get-CimInstance Win32_OperatingSystem
$report = [ordered]@{
  fase = $Fase; fecha = (Get-Date).ToString("s"); windows = "$($os.Caption) $($os.Version) $($os.OSArchitecture)"
  usuario = $env:USERNAME; webview2 = (WebView2Version); perfil = $IrisProfile; hermes = $HermesHome
}

switch ($Fase) {
  "instalado" {
    $root = InstallRoot
    $report.instalacion = $root
    Check "Carpeta de instalación" (Test-Path $root) $root
    Check "Longitud de la ruta (máximo 73)" ($root.Length -le 73) $root.Length
    Check "Lanzador Iris.exe" (Test-Path (Join-Path $root "Iris.exe"))
    Check "Desinstalador" (Test-Path (Join-Path $root "Desinstalar Iris OS.exe"))
    $active = ActiveRecord $root
    Check "install.json" ($null -ne $active)
    if ($active) {
      $report.version_activa = $active.id
      $report.iris = $active.record.iris
      $report.hermes_incluido = $active.record.hermes
      Check "Versión activa presente" (Test-Path (Join-Path $root "v\$($active.id)\iris-staging.json")) "$($active.id) · Iris $($active.record.iris) · Hermes $($active.record.hermes)"
      Check "Sin cambio de versión pendiente" ($null -eq $active.state.pending) ($active.state.pending | ConvertTo-Json -Compress)
      $versions = @(Get-ChildItem (Join-Path $root "v") -Directory | Select-Object -ExpandProperty Name)
      $report.versiones_en_disco = $versions
      Check "Sin carpetas temporales del instalador" (-not ($versions | Where-Object { $_ -notmatch '^[0-9a-f]{8}$' })) ($versions -join ", ")
      $links = @(Get-ChildItem $root -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)
      Check "Ningún enlace dentro de la instalación" ($links.Count -eq 0) (($links | Select-Object -First 5 -ExpandProperty FullName) -join ", ")
      $pyc = @(Get-ChildItem (Join-Path $root "v\$($active.id)") -Recurse -Filter *.pyc -ErrorAction SilentlyContinue).Count
      Check "Sin bytecode dentro de la instalación" ($pyc -eq 0) $pyc
      Check "Companion incluido en la versión" (@(Get-ChildItem (Join-Path $root "v\$($active.id)\companion") -Filter "*setup.exe" -ErrorAction SilentlyContinue).Count -eq 1)
    }
    $entry = Get-ItemProperty -Path $UninstallKey -ErrorAction SilentlyContinue
    Check "Entrada en Aplicaciones instaladas (HKCU)" ($null -ne $entry)
    if ($entry) {
      Check "Nombre y editor" ($entry.DisplayName -eq "Iris OS" -and $entry.Publisher -eq "Iris OS") "$($entry.DisplayName) · $($entry.Publisher)"
      Check "Versión mostrada = versión que corre" ($entry.DisplayVersion -eq $report.iris) "$($entry.DisplayVersion) frente a $($report.iris)"
      Check "Sin modificar ni reparar desde Aplicaciones" ($entry.NoModify -eq 1 -and $entry.NoRepair -eq 1)
      Check "Marcado como beta sin firma" ($entry.Comments -like "*sin firma*") $entry.Comments
    }
    Check "Nada registrado para todo el equipo (HKLM)" (-not (Test-Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\IrisOS"))
    Check "Acceso en el menú Inicio" (Test-Path $StartMenu)
    $report.acceso_escritorio = (Test-Path $Desktop)
    Write-Host "       Acceso en el escritorio: $($report.acceso_escritorio) (depende de la casilla)"
    Check "WebView2 presente" ($report.webview2 -ne "") $report.webview2
    if ($Instalador) {
      $signature = Get-AuthenticodeSignature -LiteralPath $Instalador
      $report.firma_instalador = "$($signature.Status)"
      Check "Instalador marcado sin firma (NotSigned)" ("$($signature.Status)" -eq "NotSigned") $signature.Status
      $report.sha256_instalador = (Get-FileHash -Algorithm SHA256 -LiteralPath $Instalador).Hash.ToLower()
    }
  }
  "abrir" {
    $root = InstallRoot
    $launcher = Join-Path $root "Iris.exe"
    Check "Lanzador presente" (Test-Path $launcher) $launcher
    $started = Get-Date
    Start-Process -FilePath $launcher
    $window = $null
    while (((Get-Date) - $started).TotalSeconds -lt $EsperaSegundos -and -not $window) {
      Start-Sleep -Seconds 2
      $window = Get-Process -Name Iris -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and (InsideRoot $_.Path $root) } | Select-Object -First 1
    }
    $seconds = [int]((Get-Date) - $started).TotalSeconds
    $report.ventana_segundos = $seconds
    Check "Ventana de Iris abierta desde esta instalación" ($null -ne $window) "$seconds s · $($window.MainWindowTitle)"
    Start-Sleep -Seconds 5
    $visible = @(IrisProcessTree $root | Where-Object {
      $_.Name -in @("conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "python.exe", "py.exe") } |
      ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
      Where-Object { $_.MainWindowHandle -ne 0 })
    $report.consolas_detectadas = @($visible | ForEach-Object { "$($_.ProcessName) ($($_.Id))" })
    if ($visible.Count -gt 0) {
      Check "Ventanas de consola en el árbol de procesos de Iris" $false ($report.consolas_detectadas -join ", ")
    }
    Check "No apareció ninguna ventana de consola (obsérvalo tú)" "manual" "el árbol de procesos de Iris no mostró consolas: $($visible.Count -eq 0)"
  }
  "nucleo" {
    $root = InstallRoot
    $active = ActiveRecord $root
    Check "Instalación encontrada" ($null -ne $active) $root
    $started = Get-Date
    $answer = $null
    while (((Get-Date) - $started).TotalSeconds -lt $EsperaSegundos -and -not $answer) {
      try { $answer = Invoke-RestMethod -Uri "http://127.0.0.1:$Puerto/api/status" -TimeoutSec 3 } catch { Start-Sleep -Seconds 3 }
    }
    Check "Algo responde en 127.0.0.1:$Puerto" ($null -ne $answer)
    if ($answer -and $active) {
      # The process behind the address just asked, never just the first listener of the port.
      $listener = ListenerOwner (PortListeners $Puerto)
      $owner = if ($null -ne $listener.id) { Get-Process -Id $listener.id -ErrorAction SilentlyContinue } else { $null }
      # The Core runs from the active version's runtime: another version under the same root is not it.
      Check "El proceso que escucha es de la versión activa de esta instalación" ($null -ne $owner -and (InsideRoot $owner.Path (Join-Path $root "v\$($active.id)"))) "$(if ($owner) { $owner.Path }) · $($listener.detail)"
      Check "Responde Iris en ejecución" ($answer.name -eq "IRIS" -and $answer.status -eq "running") "$($answer.name) · $($answer.status)"
      Check "Versión del Core = versión activa" ($answer.version -eq $active.record.iris) "$($answer.version) frente a $($active.record.iris)"
      Check "Runtime empaquetado" ($answer.runtime.mode -eq "packaged") $answer.runtime.mode
      Check "Revisión de Iris = la de la versión activa" ($answer.runtime.iris_revision -eq $active.record.iris_revision)
      Check "Revisión de Hermes = la de la versión activa" ($answer.runtime.hermes_revision -eq $active.record.hermes_revision)
      Check "Usa el perfil esperado" ($answer.runtime.profile_id -eq (FolderId $IrisProfile)) $IrisProfile
      Check "Usa la carpeta de Hermes esperada" ($answer.runtime.hermes_profile_id -eq (FolderId $HermesHome)) $HermesHome
    }
  }
  "preparar-datos" {
    New-Item -ItemType Directory -Force -Path $QaFolder | Out-Null
    $records = @()
    foreach ($file in FixtureFiles) {
      $path = Join-Path (DataHome $file.home) $file.path
      New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
      [IO.File]::WriteAllText($path, $file.text, (New-Object Text.UTF8Encoding $false))
      $records += [pscustomobject]@{ home = $file.home; path = $file.path; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() }
    }
    $records | ConvertTo-Json | Set-Content -Encoding UTF8 -Path $Fixtures
    Check "Datos de prueba escritos en el perfil de Iris y en Hermes" ($records.Count -eq 4) $Fixtures
  }
  "desinstalado" {
    $root = if ($Carpeta) { $Carpeta } else { $DefaultRoot }
    $location = (Get-ItemProperty -Path $UninstallKey -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
    Check "Entrada de Aplicaciones eliminada" ($null -eq $location)
    Check "Carpeta de instalación eliminada" (-not (Test-Path $root)) $root
    Check "Acceso del menú Inicio eliminado" (-not (Test-Path $StartMenu))
    Check "Acceso del escritorio eliminado" (-not (Test-Path $Desktop))
    if (Test-Path $Fixtures) {
      # Windows PowerShell 5.1 emits a JSON array as a single object: unroll it.
      $records = @((Get-Content -Raw -Encoding UTF8 $Fixtures | ConvertFrom-Json) | ForEach-Object { $_ })
      foreach ($kind in @("iris", "hermes")) {
        $mine = @($records | Where-Object { $_.home -eq $kind })
        $bad = @($mine | Where-Object {
          $path = Join-Path (DataHome $kind) $_.path
          -not (Test-Path $path) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -ne $_.sha256 })
        $label = if ($kind -eq "iris") { "Perfil de Iris conservado (datos de prueba idénticos)" } else { "Datos de Hermes conservados (datos de prueba idénticos)" }
        Check $label ($mine.Count -gt 0 -and $bad.Count -eq 0) "$($mine.Count - $bad.Count)/$($mine.Count) iguales $(($bad | ForEach-Object { $_.path }) -join ', ')"
      }
    } else {
      Check "Datos de prueba preparados antes de desinstalar (fase preparar-datos)" $false $Fixtures
    }
    Check "WebView2 conservado" ((WebView2Version) -ne "") (WebView2Version)
  }
}

$report.pruebas = $Checks
$fallas = @($Checks | Where-Object { $_.estado -eq "falla" }).Count
$manuales = @($Checks | Where-Object { $_.estado -eq "manual" }).Count
$report.resultado = if ($fallas -gt 0) { "$fallas fallas" } elseif ($manuales -gt 0) { "ok, con $manuales comprobaciones manuales pendientes" } else { "ok" }
$file = Join-Path (Get-Location) ("qa-iris-$Fase-" + (Get-Date).ToString("yyyyMMdd-HHmmss") + ".json")
$report | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path $file
Write-Host ""
Write-Host "Resultado: $($report.resultado). Informe: $file"
