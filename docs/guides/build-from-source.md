# Compilar tu propio instalador de Iris

Puedes revisar el código y construir un instalador Windows x64 sin utilizar el
EXE publicado por Iris. La entrada es **`COMPILAR.cmd`**, en la raíz del código
principal. Usa una copia congelada de tus archivos, ejecuta las pruebas y crea
un instalador personal completo con sus Python privados, Hermes y Companion.

**Entrada nueva:** están comprobados el plan, los requisitos, la copia de
fuentes y las pruebas automatizadas de la receta. La primera ejecución completa
de esta entrada, desde una máquina de desarrollo preparada hasta el EXE final,
sigue pendiente; no se presenta como una reconstrucción ya verificada de QA16.

Las dependencias externas siguen formando parte de la confianza del build:
compiladores, bibliotecas, paquetes Python/npm y fuentes fijadas por revisión y
hash. Compilarlo tú mismo no elimina esa confianza. La receta no promete un EXE
idéntico byte por byte al publicado: otra herramienta, metadatos o firma pueden
cambiar su hash. Los hashes generados identifican tu resultado.

## Preparar Windows

Instala las herramientas desde sus fuentes oficiales:

- Windows **x64**, con acceso a internet para la construcción completa.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.11 y
  [Git](https://git-scm.com/downloads/win).
- [Node.js](https://nodejs.org/en/download), versión mayor indicada por
  [`.node-version`](../../.node-version), incluido npm.
- [Rust mediante rustup](https://rustup.rs/), con el toolchain de
  [`rust-toolchain.toml`](../../rust-toolchain.toml). Si falta, ejecuta
  `rustup toolchain install VERSION` sustituyendo VERSION por ese valor.
- [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/),
  herramientas C++ x64. El binding de audio requiere **MSVC 14.50.35717** y
  **Windows SDK 10.0.26100.0**, fijados en
  [`pyav_build_sources.json`](../../scripts/pyav_build_sources.json). Una versión
  distinta necesita revisar esa receta; no desactives su comprobación.
- [7-Zip](https://www.7-zip.org/), para extraer el compilador portable aprobado.

Prepara al menos **40 GiB libres**. El builder usa una carpeta nueva y corta,
por defecto `IrisBuilds` en la unidad del código; puedes elegir otra, como
`C:\IrisBuilds\personal-01`. Un SSD rápido ayuda con las muchas copias y hashes.
La primera construcción puede tardar horas: también compila FFmpeg y PyAV.

El programa instalado necesita WebView2; la receta conserva el comportamiento
del instalador, que lo detecta y muestra su enlace oficial si falta. Para los
requisitos de desarrollo de Tauri, consulta
[su documentación de Windows](https://v2.tauri.app/start/prerequisites/#windows).

## Comprobar y construir

Abre una terminal en la carpeta del código:

```text
COMPILAR.cmd --check
COMPILAR.cmd --plan
COMPILAR.cmd --build --work C:\IrisBuilds\personal-01
```

`--check` inspecciona herramientas, versiones, pins, espacio y destino, sin
descargar ni compilar. El lanzador necesita `uv` y un Python 3.11 ya disponible
para esa comprobación; usa el modo sin conexión y no prepara el entorno de
desarrollo. También puedes ejecutar directamente
`python scripts/build_from_source.py --check` con Python 3.11.

`--plan` muestra los pasos. `--build` prepara el entorno de desarrollo con
`uv.lock`, descarga las dependencias y construye. El doble clic en
`COMPILAR.cmd` inicia ese modo completo y conserva la consola al terminar.
No instala herramientas del sistema, no modifica el antivirus y no limpia
cachés. Una construcción fallida conserva sus registros; usa un destino nuevo
para reintentar.

Si Visual Studio, el SDK o 7-Zip están en un lugar diferente:

```text
COMPILAR.cmd --build --work C:\IrisBuilds\personal-02 --msvc "C:\ruta\VC\Tools\MSVC\14.50.35717" --windows-sdk "C:\Program Files (x86)\Windows Kits\10" --seven-zip "C:\Program Files\7-Zip\7z.exe"
```

No hacen falta claves de API, OAuth ni la clave privada de publicaciones de
Iris. Las pruebas usan perfiles propios de la construcción. El build no abre
una cuenta, no ejecuta el instalador resultante y no publica nada.

## Qué construye

1. Copia las fuentes seleccionadas a `source/` y registra sus SHA-256 en
   `source-files.json`. Funciona con un checkout Git o con el ZIP del código.
   Credenciales, perfiles, cachés y artefactos quedan fuera.
2. Instala npm desde su lock y verifica catálogo de apps y contrato web. Un
   contrato desactualizado detiene el build; no se regenera para ocultar el fallo.
3. Ejecuta pytest, las pruebas JS y las pruebas Rust. Un fallo detiene los pasos
   siguientes. Consulta los logs también para conocer las pruebas omitidas.
4. Compila Companion y los paquetes Python desde esa copia; verifica sus archivos.
5. Reconstruye FFmpeg de audio, codecs y el binding PyAV con las recetas
   existentes y las fuentes fijadas. Descarga los Python privados y la revisión
   de Hermes, y prepara Bash con sus fuentes correspondientes. No descarga un
   instalador Iris ni requiere el caché privado del mantenedor.
6. Monta y verifica el runtime, compila `Iris.exe`, comprueba el staging y crea
   el instalador mediante el NSIS revisado.

Los hashes de los componentes reconstruidos se guardan en el pin de **esa
construcción**; las versiones, fuentes, locks y controles de licencias se
conservan. Las fuentes originales no reciben los cambios de manifiestos ni las
salidas de compilación. Las descargas de entrada se conservan en
`download-cache/`, junto a la carpeta del build, y se verifican antes de reutilizarlas.

## Resultado y comprobación

Dentro del destino encontrarás:

| Ruta | Contenido |
| --- | --- |
| `installer/*-setup.exe` | Instalador personal sin firma Authenticode. |
| `installer/iris-private-shell-sources.zip` | Fuentes correspondientes del Bash incluido; conserva este archivo junto al instalador si lo distribuyes. |
| `installer/SHA256SUMS.txt` | Huellas del EXE y del archivo de fuentes del shell. |
| `installer/*.exe.json` | Identidad, procedencia y comprobaciones del instalador. |
| `build-result.json` | Resultado y pasos ejecutados; `completed: true` solo al finalizar. |
| `logs/` y `native/` | Registros de compilación y reconstrucción nativa. |
| `source-files.json` | Archivos exactos y huella de la fuente congelada. |

Para recalcular una huella en PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\IrisBuilds\personal-01\installer\NOMBRE-setup.exe'
```

La compilación personal no incorpora el canal oficial de actualizaciones ni
está firmada por el mantenedor. Su construcción automática no equivale a QA
instalada: pruébala primero en una VM y comprueba la identidad del candidato,
la instalación y las funciones que utilices. Consulta también
[Desktop](../../desktop/README.md) para los contratos del paquete.

Esta entrada es independiente de `scripts/build_release.py`, que genera wheel,
sdist y Companion; ese script por sí solo no crea el instalador integral.
