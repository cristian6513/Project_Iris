# Desktop Companion

[← Índice de documentación](../README.md)

IRIS Desktop Companion es la presencia flotante opcional de IRIS OS. No
reemplaza la interfaz completa y no es necesario para utilizar Hermes.

## Estado actual

Revisión documental: 2026-11-06. La fuente y el manifiesto de release declaran
**Companion 0.1.8**, incluido en el candidato QA22. La QA nativa del candidato
sigue pendiente. Debe distinguirse del ejecutable realmente instalado y de
la versión de Iris: consulta [Estado actual](../estado-actual.md).

- plataforma empaquetada: Windows x64;
- shell: Tauri 2 + WebView2;
- instalación por usuario;
- ventana transparente, redimensionable y movible desde su barra superior;
- autoarranque separado del Gateway;
- artefacto local validado antes de instalar o reparar.

Otros sistemas pueden usar IRIS OS desde el navegador mientras se valida un
empaquetado nativo.

### Registro histórico: actualización local 0.1.2 — 2026-09-06

Companion 0.1.2 incorpora los cambios de Vision V2 de los pasos 1–6: señales
locales coordinadas, saludo contextual, cadencia de análisis configurable,
calibración voluntaria y diagnóstico escalar. Los ajustes se administran desde
**IRIS OS → Notification Center → Adapta Vision a ti**; no añade un panel
completo de configuración dentro de la ventana flotante.

Se construyó el paquete nativo Windows x64 y se verificó la actualización
local desde 0.1.1: versión/arquitectura reconocidas, SHA-256 del instalador
correcto y preferencias conservadas. Pasaron 76 pruebas Python de Companion
y 7 pruebas Rust. No se abrió Core ni se activaron sensores o autoinicio.

La compilación continúa **sin firma digital**. La actualización en este equipo
no certifica una máquina limpia ni la captura con cámara física prolongada.
El protocolo y los límites están en [Vision V2](../vision_v2_contracts.md).

## Qué muestra

El Companion reutiliza el avatar y el protocolo de superficies:

- idle;
- escucha;
- pensamiento;
- habla;
- error;
- animación o gesto contextual;
- controles compactos de micrófono y cámara.

El encabezado indica cuándo Iris está usando una herramienta. La burbuja se
reserva para su respuesta: no muestra comandos ni argumentos internos del
progreso. El detalle de herramientas sigue disponible en Iris OS. Este ajuste
del 26 de octubre requiere recompilar Companion; el instalado con QA16 conserva
la presentación anterior.

En el código actual, el acceso **Hablar** sustituye el botón de Wake: graba una
intervención con Hermes, permite enviarla o cancelarla y mantiene Wake en los
ajustes de IRIS OS. Véase [Micrófono manual y Wake](manual-voice.md). Este cambio
requiere actualizar el paquete nativo; no forma parte automáticamente de una
instalación 0.1.2 anterior.

Sin un modelo propio, el Companion muestra a IRIS desde su propio paquete, también
antes de que responda Core. Si el modelo propio no carga al arrancar, muestra a IRIS
y lo avisa.

Los cambios de avatar guardados desde Face se notifican al Companion abierto.
El nuevo modelo se carga antes de retirar el anterior y renueva las referencias
de animación. Una versión del archivo permite detectar reemplazos con el mismo
nombre. Al reconectar, recuperar el foco o cada 15 segundos mientras está visible,
Companion comprueba la selección guardada; si la carga falla, conserva el modelo
anterior. La vista previa GLTF de una sola sesión en Face no cambia el avatar guardado.

Esta sincronización requiere el código actualizado de Core y una nueva compilación
de Companion; recargar Face no actualiza un ejecutable instalado anteriormente.

Prueba de integración aislada: `node scripts/verify_companion_avatar.mjs --vrm <modelo.vrm> --vrm2 <otro-modelo.vrm> --playwright-module <ruta>`.
Usa la interfaz compilada, eventos WebSocket y modelos reales, sin cambiar la
configuración del usuario ni encender sensores.

Las acciones se adaptan a la ventana. El backend conserva la intención original
del JSON; el Companion decide una animación viable cuando la acción depende de
la habitación completa.

## Activación

El Companion se abre y administra desde Iris OS. Desde QA18, instalarlo o repararlo desde Iris no crea accesos directos propios
en Windows; QA22 conserva ese comportamiento. Si una instalación anterior
ya dejó un icono del Companion en el escritorio, puedes eliminar ese acceso:
no desinstala el componente ni afecta al acceso principal de Iris OS.

El wizard muestra el estado del paquete, la aplicación, WebView2 y el Core.
Solo permite abrir el Companion cuando la instalación está lista. Si detecta
una instalación parcial, ofrece diagnóstico o reparación.

La barra superior permite arrastrar la ventana y contiene un control para
ocultarla sin terminar el proceso. `Mostrar ventana` la recupera desde IRIS OS
o desde la bandeja. `Cierra el Companion` sí termina únicamente el ejecutable
validado y deja Hermes e IRIS Core funcionando.

El autoarranque:

- es opt-in;
- puede desactivarse;
- no enciende automáticamente el micrófono;
- no implica iniciar Hermes Gateway si esa preferencia está desactivada.

### Recuperación y actualización desde 0.1.0

IRIS normaliza la ruta `InstallLocation` escrita por NSIS y puede reconocer una
instalación por usuario que haya terminado correctamente aunque una versión
anterior dejara el estado `APP REPAIR`. El journal se retira únicamente cuando
producto, arquitectura y versión del ejecutable registrado coinciden con la
operación interrumpida.

Las instalaciones y reparaciones nuevas entregan `/D=` con la sintaxis exacta
requerida por NSIS, incluida una ruta de perfil con espacios. Abrir la ventana y
activar el autoarranque continúan bloqueados hasta que el diagnóstico muestre
`APP READY`. La causa y la corrección están registradas en
[BUG-029 del documento maestro](../iris_os_master.md#bug-029-el-instalador-del-desktop-companion-termina-pero-iris-no-reconoce-el-ejecutable).

## Vida propia en Companion

En el panel **IRIS en ventana** de Iris OS elige **Vida propia → Apagada,
Tranquila o Activa**. La preferencia es propia del Companion, se guarda por
perfil y su valor inicial es Tranquila. No es el mismo almacenamiento local de
la habitación. El modo silencioso suspende sus rutinas; también se respeta
movimiento reducido y la visibilidad.

| Intención de la habitación | Alternativa de pie |
| --- | --- |
| Leer en el centro, sofá o cama | Leer con el libro |
| Tablet o trabajar en el PC | Usar la tablet |
| Sentarse en sofá, cama o silla | Estirarse |
| Acostarse | Reposo de pereza |
| Mirar por la ventana | Reposo |

Al hablarle guarda el objeto para responder. Si falta el clip o modelo de un
objeto, usa el reposo disponible. Estas rutinas no mueven la ventana por Windows;
la locomoción del apartado siguiente es una opción distinta. No hay
teletransporte de sala en Companion.

La preferencia también se lee al arrancar sin Core; un valor no válido vuelve
a Tranquila. La comprobación nativa completa y con voz real sigue en la guía de
QA22 (informe interno no distribuido). La secuencia con los clips
empaquetados se comprobó en navegador; no acredita todos los VRM.

## Movimiento por el escritorio

La locomoción es opcional y local al Companion. Debe:

- respetar monitores y áreas de trabajo;
- detenerse durante conversación, escucha o interacción;
- mantener la ventana recuperable;
- recordar una posición válida;
- desactivarse sin afectar el avatar.

## Privacidad

El Companion registra sus capacidades ante el Core. Cámara y audio se conceden
por superficie para que la ventana flotante y el navegador no compitan.
Consulta [Privacidad y seguridad](privacy-security.md).

## Desarrollo

Requisitos: Node.js, Rust y las dependencias de
[Tauri 2](https://v2.tauri.app/start/prerequisites/).

```bash
npm --prefix companion ci
npm --prefix companion run build
cargo test --manifest-path companion/src-tauri/Cargo.toml
npm --prefix companion run tauri build
```

`node_modules/`, `companion/dist/` y `companion/src-tauri/target/` son salidas
reproducibles y no se versionan. El modelo requerido de MediaPipe y los avisos
de terceros sí forman parte del código fuente publicable.

## Pendientes de release

- firma digital;
- QA de instalación, actualización y desinstalación en una máquina limpia;
- métricas de consumo prolongado;
- validación física con varios monitores y escalados;
- evaluación de paquetes para otras plataformas.

El estado técnico detallado vive en el
[plan del Desktop Companion](../iris_desktop_companion_plan.md).
