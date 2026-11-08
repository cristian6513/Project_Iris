# Iris Desktop e instalador de Windows

Entrada de compilación personal: [COMPILAR.cmd](../COMPILAR.cmd), con
[requisitos y receta completa](../docs/guides/build-from-source.md).
Construye en una copia separada; no requiere el instalador publicado ni la
clave privada del canal oficial.

Revisión de esta entrada: 2026-10-25. El paquete integral de Iris se valida en
Windows x64 y A08.9 sigue abierto. El candidato más reciente es `qa16`;
las secciones históricas siguientes no acreditan cambios posteriores. Consulta
[Estado actual](../docs/estado-actual.md) y el informe A08.9 antes de ejecutar QA.

## Vista previa de desarrollo

La aplicación principal es independiente del avatar flotante Companion. Esta primera
vista previa abre el espacio de Iris y el configurador en una ventana nativa Tauri 2
con WebView2. Linux es un objetivo posterior: primero CachyOS y Bazzite.

## Abrir la vista previa

Generar desde el proyecto con `node scripts/build_desktop.mjs` y abrir
`dist/desktop/Iris.exe`. Para el usuario de esta instalación basta el doble clic;
no tiene que abrir una consola para iniciar los servicios.

**Esta carpeta aún no es un instalador autónomo.** `runtime.json` utiliza rutas
relativas al proyecto y a su `.venv`. Hermes procede de la instalación actual.
Conservar la carpeta dentro de `dist`; no distribuir ese ejecutable por separado.
El build de release falla de forma visible si falta el descriptor o Python.
No se instala software ni se cambia el inicio de sesión de Windows automáticamente.

El ejecutable se compila como build de producción de Tauri (`--features
custom-protocol`, igual que `tauri build`) y con `--remap-path-prefix`. Sin la
primera, Tauri compila en modo desarrollo y graba la carpeta del código. Sin la
segunda, los mensajes internos de Rust guardan las rutas locales de la caché de
Cargo, que incluyen el nombre del usuario que compiló.

## Carpeta distribuible (A08.7)

`scripts/build_staging.py` une el runtime privado verificado (A08.4–A08.6) y
`Iris.exe` en una carpeta que se puede mover o copiar entera. Es la entrada del
instalador A08.9. El staging por sí solo no es un instalador. La firma del canal
de actualizaciones (A08.8) no sustituye una firma Authenticode del ejecutable.

```text
python scripts/build_staging.py --runtime <candidato A08.4> --cargo-target-dir <carpeta de build> --output <carpeta nueva>
python scripts/build_staging.py --verify <carpeta>
```

- `runtime.json` es el descriptor `packaged` con rutas relativas. Junto a él están
  `runtime-inventory.json`, `iris-staging.json` (huellas de lo que añade la
  carpeta: `Iris.exe`, descriptor, `LEEME.txt` y `desktop/`) y
  `desktop/licenses.json`. Este último lista los crates de Rust enlazados en
  `Iris.exe`, sacados de `cargo metadata` sin conexión, con sus licencias.
- **Límite de ruta.** Sin rutas largas habilitadas en Windows, ningún archivo
  puede superar 259 caracteres. El descriptor declara la ruta relativa más larga
  de la carpeta (`install.max_relative_path`). `Iris.exe` y el Python incluido
  rechazan una carpeta de instalación más larga que `258 − ese valor`: lo
  indican al abrirse y no inician nada. El máximo depende del descriptor de
  esa versión: no reutilices el límite numérico de un staging antiguo ni asumas
  que cualquier nombre de perfil cabe.
- El generador falla si algún archivo graba la ruta donde se construyó, la del
  repositorio o la del perfil del usuario. También falla si el Python incluido no
  acepta el descriptor generado.
- El build exige avisos y procedencia de los componentes. Los pendientes
  históricos de crates y del cargador WebView2 fueron tratados en A08.9;
  comprobar el inventario del candidato nuevo, no trasladar «cero pendientes»
  desde un EXE anterior. La licencia de Iris no sustituye las de terceros.

## Instalación versionada y actualizaciones (A08.8)

La instalación guarda cada versión completa en su propia carpeta y nunca
escribe sobre la que está en uso:

```text
<instalación>/Iris.exe        lanzador: abre la versión activa
<instalación>/install.json    versión activa y anterior, activación en curso, último resultado
<instalación>/v/<id>/         una carpeta distribuible A08.7 por versión
<instalación>/updates/        descargas, extracción y registro de activación
```

- `<id>` son los 8 primeros dígitos del SHA-256 de `iris-staging.json` de esa
  versión. Con el prefijo `\v\<id>`, la carpeta de instalación admite 73
  caracteres con el runtime actual.
- `python -m core.iris_updates install --staging <carpeta> --root <instalación>`
  crea esta estructura; es lo que usará el instalador (A08.9).
- **Canal.** Cada versión lleva en `channel.json` la dirección del canal y las
  claves públicas Ed25519 en las que confía (`build_staging.py --channel-url
  --channel-key`). El manifiesto del canal va firmado; Iris rechaza una firma
  ajena, un manifiesto vencido o anterior al último visto, otra plataforma y
  versiones que no puedan abrir el perfil. Solo HTTPS, salvo un canal de prueba
  en `127.0.0.1`.
- **Descarga.** Tamaño y SHA-256 exactos del canal, espacio en disco comprobado
  antes, extracción solo de archivos normales con nombres confinados (sin
  rutas absolutas, `..`, `\`, `:`, nombres de dispositivo ni enlaces), y
  verificación completa de la versión contra sus manifiestos antes de usarla.
- **Activación.** Un proceso auxiliar cierra la ventana y los servicios de esta
  instalación (nunca los de otra), comprueba que ningún escritor incompatible
  use el perfil y que los puertos estén libres, cambia `install.json` y abre la
  versión nueva. Si no confirma su arranque en 4 minutos, vuelve a la anterior.
  Si el formato del perfil o la versión de Hermes cambian, antes hace una copia
  verificada del perfil (`IRIS_HOME/.profile/backups`).
- **Lanzador.** `Iris.exe` de la raíz cuenta los arranques de una versión aún no
  confirmada; tras 3 abre la anterior, que recupera la activación.
- **Reversión.** Se conserva la versión anterior y el botón «Volver a …» la
  activa de la misma forma. Volver a un formato de perfil anterior restaura la
  copia hecha al actualizar.
- La vista previa y el desarrollo no cambian: siguen con Git y `hermes update`.

```text
python scripts/build_update_package.py keygen --private <clave.pem> --public <clave.json>
python scripts/build_update_package.py package --staging <carpeta> --output <iris-x.y.z.zip>
python scripts/build_update_package.py manifest --sequence <n> --private <clave.pem> --release <zip>=<URL> --output <stable.json>
python scripts/build_update_package.py check --manifest <stable.json> --channel-url <URL del canal> --key <clave.json> --package <zip> (--previous <publicado.json> | --first-publication)
```

La clave privada nunca va en el repositorio ni en una versión. `check` es la
comprobación antes de publicar un canal real. Sale con error si:

- el manifiesto lleva una firma de otra clave;
- la URL no es HTTPS hacia un servidor público;
- el manifiesto vence en menos de 7 días;
- la secuencia no supera la publicada;
- algún paquete no coincide con el manifiesto, tiene `test: true`, apunta a
  otro canal, confía en claves que no son las de producción o contiene una
  clave privada.

El canal beta está en GitHub Releases:
`https://github.com/cristian6513/Project_Iris/releases/download/channel-beta/beta.json`.

- Lo firma la clave de producción `134f4752bd9967f4`, creada con
  `keygen --protect`: la privada está cifrada con DPAPI fuera del repositorio.
  La pública está en `scripts/update_keys/`.
- Las descargas de GitHub llegan por redirección. El cliente sigue a lo sumo 5
  saltos, solo hacia hosts de GitHub, con HTTPS y servidor público en cada uno.
- Una versión nueva se publica con
  `scripts/publicar_version_beta.cmd <versión> <paquete.zip>`:
  1. crea el release con el paquete;
  2. firma la secuencia siguiente;
  3. pasa `check` descargando el paquete sin autenticación;
  4. reemplaza `beta.json` de forma segura.
- El manifiesto vence. Sin versiones nuevas, se renueva antes de que venza
  con `scripts/renovar_canal_beta.cmd`, que es manual:
  - firma las mismas versiones con la secuencia siguiente y 90 días de
    vigencia;
  - pasa `check` descargando cada paquete sin autenticación;
  - reemplaza `beta.json` subiéndolo con otro nombre e intercambiando los
    nombres después de verificarlo.

  `--dry-run` hace todo menos publicar.
- `scripts/respaldar_clave_produccion.cmd <destino>` crea la copia cifrada
  de la clave y la verifica.

## Comportamiento

- Una sola instancia por usuario de Windows; una segunda apertura enfoca la
  ventana existente. La vista previa y la versión instalada comparten
  identificador, así que solo una de las dos está abierta a la vez.
- Si el servicio de inicio no puede arrancar, la ventana explica el motivo.
  Por ejemplo, cuando otra versión de Iris usa el perfil o ya lo actualizó, o
  cuando la carpeta del perfil es demasiado profunda.
- Solo reutiliza servicios de la misma instalación y el mismo perfil: Core y el
  Gateway se acreditan por su ejecutable, su carpeta y su descriptor. Otra copia
  de Iris, aunque tenga la misma versión, o un Hermes ajeno que responda en el
  puerto se informan como conflicto. No se reutilizan ni se detienen.
- El controlador se ejecuta sin consola y escucha únicamente en `127.0.0.1`, en
  un puerto reservado por el sistema operativo. No ocupa el puerto 8000 del wizard.
- En un perfil nuevo se ofrece Configuración. Las cuentas se conectan mediante
  el wizard existente; no hay un segundo almacén de credenciales.
- Con configuración existente, comprueba Hermes, inicia el gateway si hace falta
  y después inicia o reutiliza Iris. Muestra errores y permite volver a comprobar
  un arranque lento sin crear otra copia.
- Face se abre por HTTP de loopback, incluido cuando la conexión LAN usa HTTPS.
  No se ignoran certificados. El iframe delega cámara y micrófono explícitamente.
- Configuración y el regreso a Face permanecen dentro de la aplicación. Los enlaces
  externos, incluidos los de autorización, se abren en el navegador del sistema.
- Cerrar la ventana la oculta en la bandeja. **Salir de Iris Desktop** cierra la
  ventana y su controlador; Iris/Hermes continúan para Companion, cron y mensajería.
  Los controles existentes administran esos servicios. No se terminan procesos
  por nombre ni se detienen servicios que ya estaban abiertos.

El log nativo está en el directorio de logs de Tauri para `os.iris.desktop`.
Los logs de los servicios siguen en `IRIS_HOME/logs`. La pantalla de error muestra
la ruta correspondiente; no publica credenciales.

## Asistente en la versión instalada

Con el descriptor `packaged`, el pie de la ventana muestra la versión instalada
en lugar de «vista previa», y Configuración abre el asistente adaptado al motor
incluido (A08.6): proveedor de IA, canales, voz y avatar, sin instalador ni
autostart externos. **Manual** y **Licencias** se abren dentro de la misma
ventana. Los controles se derivan de las capacidades de `/api/runtime-info`, no
de suposiciones de la interfaz: un runtime desconocido no muestra operaciones
que el backend rechazaría. El motor incluido no se inicia hasta que el perfil
acepta el aviso de seguridad de Hermes (`hermes_security_acknowledged`): si
falta, la ventana queda en Configuración, donde el asistente lo pide antes de
iniciar o reiniciar el motor. Tampoco abre una Iris que ya esté en marcha, porque
desde ella se podría iniciar el motor, y el backend rechaza cualquier arranque o
reinicio del motor incluido sin esa aceptación (`security_ack_required`).

## Consistencia con Iris OS

La ventana comparte `face/js/os_theme.js` con Theme Studio: Cyber, Sakura, Hacker y
Classic conservan sus colores originales. Usa los mismos fondos, insignias y fuentes
Syne/Outfit. El configurador puede previsualizar su edición en la barra de la ventana;
al volver a Face, Theme Studio publica su apariencia efectiva. Los colores, fondos
predefinidos, transparencia, desenfoque y movimiento reducido se conservan como una
copia visual en `IRIS_HOME/desktop_theme.json` para el siguiente arranque. No reemplaza
el almacenamiento de Theme Studio ni contiene cuentas o credenciales.

## Límites de confianza

La ventana no expone comandos nativos a Face ni al wizard. El documento inicial
tiene una capacidad Tauri vacía y no hay `invoke_handler`. El controlador exige
el host literal de loopback y el origen exacto para escrituras. La navegación
entre marcos valida tanto `event.source` como `event.origin`; no admite comandos
arbitrarios. Los enlaces externos solo aceptan HTTP(S), sin credenciales en la URL.

Los bloqueos de inicio usan archivos privados del perfil y locks del sistema
(`msvcrt` en Windows, `flock` en Linux). Se liberan al salir o fallar el proceso.
Hermes se identifica con su contrato `/health` (`platform=hermes-agent`, `status=ok`),
y Core con `/api/status` y el token privado del perfil, sin exponerlo a la interfaz;
que un puerto esté abierto no significa que esté listo.

## Verificación

```text
python -m pytest tests/test_desktop.py tests/test_desktop_gateway.py -q
node --test tests/js/desktop_host.test.mjs
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
python -m pytest tests/test_staging_build.py -q
python scripts/build_staging.py --verify <carpeta distribuible>
python -m pytest tests/test_iris_updates.py -q
python -m core.iris_updates verify <instalación>/v/<id>
node scripts/verify_desktop.mjs
node scripts/verify_desktop.mjs --native
```

Las verificaciones visuales necesitan Playwright (`PLAYWRIGHT_MODULE` puede señalar
su instalación) y Edge. `--native` requiere la vista previa compilada en Windows.
Usan perfiles aislados y cámara/micrófono virtuales, sin conectar cuentas ni mandar
mensajes. Ejecutan el wizard real; simulan las respuestas ready/error y la superficie
Face para comprobar navegación y permisos. No equivalen a probar todas las acciones
del avatar ni llamadas reales a cada proveedor en WebView2.

Los resultados se guardan en `artifacts/desktop-review`. El puerto de depuración
WebView2 solo se habilita mediante variables del proceso de prueba, nunca por defecto
en el ejecutable. Se usa el mecanismo oficial de
[Microsoft WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code).

## Próxima distribución con todo incluido

Hechos:

- runtime privado de Iris (Python 3.11) y Hermes 0.21.6
  (`818c13be1dc4fd28987e1e881a9408224afd4535`, Python 3.14) con sus licencias (A08.4, A08.4b);
- recursos web locales (A08.3);
- extras opcionales en el perfil (A08.5);
- asistente de la versión instalada (A08.6);
- carpeta distribuible movible arrancada por `Iris.exe` (A08.7);
- instalación versionada con actualización conjunta Iris + Hermes firmada y
  reversión (A08.8), validada con un canal de prueba local;
- canal beta real en GitHub Releases, firmado con la clave de producción (A08.8);
- licencias completas de `Iris.exe`, el Companion y el instalador, y la versión
  con el Companion dentro (A08.9, fases 1 y 2).

## Instalador de Windows (A08.9, en curso)

`scripts/build_installer.py --staging <versión> --output <carpeta> [--pin <pin>]`
envuelve una versión verificada en un instalador NSIS por usuario
(`desktop/installer/iris-os.nsi`):

- instala en `%LOCALAPPDATA%\Programs\Iris OS` sin permisos de administrador, con la
  estructura versionada de A08.8, acceso en el menú Inicio y, si se marca, en el
  escritorio;
- si falta Microsoft Edge WebView2 Runtime se detiene y ofrece la página oficial
  de Microsoft: no lo descarga ni lo instala (una instalación sin conexión
  necesita tenerlo de antes);
- volver a ejecutarlo repara la versión instalada; uno de una versión más nueva
  la actualiza con el ayudante de A08.8 (prueba y vuelta atrás automática);
- el desinstalador quita todas las versiones y el Companion instalado por Iris y
  conserva el perfil; borrarlo es una casilla aparte, desmarcada y confirmada. Los
  datos de Hermes (`%LOCALAPPDATA%\hermes`) nunca se borran;
- las decisiones con reglas de Iris las toma `core/iris_installer.py` con el Python
  de la propia versión, sin consola.

El instalador se marca **sin firma** (no hay certificado Authenticode): es un
candidato de QA, no el candidato público final. La QA ya realizada se documenta
por candidato; los cambios posteriores necesitan su validación en Windows real
y limpio (Windows 11 y Windows 10 22H2). VirtualBox es válido; Hyper-V no es un
requisito exclusivo. El instalador no se ejecuta desde las
herramientas de Claude, porque la virtualización de su app desvía AppData y el
registro del usuario.

## Linux más adelante

El controlador Python, los locks, el protocolo de arranque y el descriptor de rutas
son independientes de Windows. Las diferencias de creación de procesos y apertura
de enlaces están concentradas en funciones de plataforma. El build distribuible
está limitado explícitamente a Windows hasta que se verifique en Linux.

Para CachyOS y Bazzite faltan builds nativos, bibliotecas WebKitGTK, permisos de
audio/cámara y pruebas reales de Wayland. El empaquetado también debe considerar el
modelo de aplicaciones de Bazzite, que recomienda Flatpak:
[documentación de Bazzite](https://docs.bazzite.gg/Installing_and_Managing_Software/software-intro/).
El formato final sigue pendiente. Companion tiene además requisitos propios de
ventanas flotantes y movimiento que no deben confundirse con esta ventana principal.
