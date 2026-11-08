# Apps de IRIS OS

Cada app tiene su propia carpeta `apps/<id>/` con una ficha `app.json`. Es la fuente del registro de apps (F09): el escritorio abre ventanas y pestañas, arma el lanzador y elige los tamaños iniciales a partir de estas fichas.

```text
apps/
  _plantilla/             plantilla: app de solo interfaz
  _plantilla-servicio/    plantilla: app con servicio Python de lectura
  player/
    app.json              ficha de la app (solo datos)
    ui/widget.js          widget del mismo reproductor local
    ui/widget.css
    tests/widget.test.mjs
    README.md             ejemplo: app, widget y skill operativa
  ...
```

**¿Quieres crear una app?** Copia una plantilla y sigue su README: [`_plantilla`](_plantilla/README.md) si solo necesita interfaz, o [`_plantilla-servicio`](_plantilla-servicio/README.md) si lee datos con Python. La [guía para colaboradores](../docs/guides/contributing-apps.md) explica todo el recorrido.

**Apps externas instalables (SDK 2, fuente posterior a QA14):** usa el
[Contador completo](../examples/apps/contador/README.md) y la
[guía de Python/acciones/skills](../docs/guides/community-apps-python.md). Vive
fuera de `apps/`, se valida con `scripts/app_package_tool.py` y se instala desde
la Store. Su worker no usa la plantilla de servicios revisados dentro de Core.

**Ejemplo sin cuentas externas:** [IRIS Player](player/README.md) conecta ventana,
widget y skill a un solo estado. Su adaptador de ventana es anterior al kit;
el README distingue esa integración de las plantillas para apps nuevas.

## La ficha `app.json`

```json
{
  "schema": "iris.app-manifest/v1",
  "sdkVersion": 1,
  "id": "mi-app",
  "name": "Mi app",
  "version": "0.1.0",
  "description": "Plantilla de una app de IRIS OS",
  "icon": "sparkle",
  "accent": "cyan",
  "presentation": "window",
  "window": { "width": 780, "height": 560 },
  "launcher": { "visible": true, "order": 900 },
  "ui": { "entry": "main.js", "style": "style.css" }
}
```

| Campo | Qué indica |
| --- | --- |
| `id` | Identificador estable. Debe coincidir con el nombre de la carpeta y no cambiar nunca, porque guarda la posición de las ventanas. |
| `name`, `description` | Título de la ventana y texto breve del lanzador. |
| `icon` | Nombre de un icono de `face/js/os_icons.js`. |
| `accent` | `cyan`, `magenta` o `amber`: el acento de la ventana y de su botón en el lanzador. |
| `presentation` | `window` (ventana del escritorio) o `tab` (vista completa, como Cámara y Configuración). |
| `window` | Tamaño inicial de la ventana. El escritorio lo ajusta al espacio disponible. |
| `launcher` | Si aparece en el lanzador, en qué orden y, opcionalmente, con una etiqueta más corta. |
| `ui` | Dónde está la interfaz; ver la tabla siguiente. |
| `requestedCapabilities`, `services` | Opcionales. Describen necesidades; **no conceden permisos**. |
| `widgets` | Opcional (F12). Vistas compactas de la app, con id, nombre, entry local, style opcional, size, configVersion y preview declarativa opcional para la galería. Véase [Widgets](../docs/guides/widgets.md). |
| `extension` | Solo SDK 2: Python, operaciones y skill opcional para un paquete externo; véase [contrato](../docs/guides/community-apps-python.md). No activa servicios dentro de Core. |

La ficha solo contiene datos. No lleva código, rutas ejecutables ni URLs: el validador rechaza los campos desconocidos.

| `ui` | Para qué apps |
| --- | --- |
| `{ "entry": "main.js", "style": "style.css" }` | **Apps nuevas.** La interfaz vive en `apps/<id>/ui/`; `style` es opcional. |
| `{ "module": "automation_app.js" }` | Apps de Iris migradas cuyo código sigue en `face/js` (Automatizaciones). |
| `{ "adapter": "legacy" }` | Apps de Iris cuya vista y carga siguen dentro del shell. |

## El contrato de una app con código propio

`ui/main.js` (o el módulo de `face/js`) exporta `createApp(context)`, que devuelve un controlador:

| Método | Cuándo lo llama el escritorio |
| --- | --- |
| `mount(root)` | Una vez, con la vista vacía de la app. Obligatorio. |
| `refresh({ force, opening })` | Al abrir o restaurar la ventana, o al mostrarla como pestaña. |
| `setVisibility({ visible, reason })` | Cuando se ve u oculta: minimizar, cerrar, cambiar de pestaña, ocultar la página. |
| `navigate(payload)` | Cuando otra parte de Iris la abre en un punto concreto, por ejemplo el historial de una regla. |
| `dispose()` | Al retirarla del todo. Debe liberar sus timers y listeners y quitar su vista. Obligatorio. |

**Ciclo de vida:**
- El módulo se descarga la primera vez que se abre la app.
- **Cerrar o minimizar la ventana no la destruye**: conserva su estado, y la siguiente apertura reutiliza la misma instancia.
- Si el módulo no carga, la ventana muestra el error con un botón «Reintentar», y el resto del escritorio sigue funcionando.

**El `context` que recibe la app.** No le da acceso al shell completo; solo:
- su `id` y su ficha (`app`);
- `isVisible()`;
- `openApp(id, datos)`, para abrir otra app. Con `datos` (solo datos simples: se copian), la otra app los recibe en su `navigate(datos)` cuando ya cargó. Así se abre una tarea, una partida o un temporizador concretos; la app de destino valida lo que recibe;
- `setBrief(texto)`, para su resumen en el lanzador;
- `confirm({ title, message, question, confirmLabel, cancelLabel, tone })`, para preguntar en el diálogo de Iris antes de un efecto que la app va a causar (`tone: 'danger'` para algo que no se deshace). Devuelve `true` solo si la persona acepta. Solo pasan textos, que se muestran como texto;
- `askIris({ text, data })`, el botón «Avisar a Iris». Llámala desde el manejador del clic de la persona en tu ventana o tu widget. Cada clic, o Enter o Espacio, dentro de la app autoriza un solo mensaje durante 3 segundos. Una tecla y el clic que el navegador genera para ella cuentan como una sola pulsación, y mantenerla apretada también. Sin él, o con un clic en otra parte del escritorio, falla. Iris muestra en su propio diálogo qué se envía (todos los datos, en «Ver los datos de la app») y desde qué app, salvo que la persona haya elegido «Enviar siempre desde» esa app; aun así, cada mensaje necesita su clic. El mensaje va a la conversación actual como información de la app, no como órdenes. `text` tiene de 1 a 300 caracteres, sin empezar por «/»; `data` es JSON opcional de hasta 4 KB. Devuelve `{ sent: true }` cuando Core confirmó la recepción, no cuando el modelo terminó de responder. Con `{ sent: false }`, `reason` distingue `cancelled` (la persona no aprobó), `refused` (Core lo rechazó; `message` dice por qué) y `offline` (sin conexión o sin confirmación en 5 segundos). La falta de confirmación no demuestra que el mensaje no llegara: no lo reenvíes automáticamente; una aceptación tardía se refleja una sola vez en el chat. El shell comprueba forma y tamaño antes del diálogo y Core decide la aceptación final. La persona puede revocar «Enviar siempre» en Administrar aplicaciones;
- `signal`, que se aborta al retirarla.
- Las apps externas instaladas con Python reciben además `call(action, args)`, ligada a esa instalación. El mismo helper llega a sus widgets; no se importa el backend en el navegador.
- Toda app instalada desde Iris Store, y sus widgets, reciben los **avisos**:
  - `notify({ title, text, at, key })` programa un aviso. `at` es una fecha (`Date`) o milisegundos; sin `at`, el aviso es para ahora.
  - Core lo guarda, lo deja en la Bandeja con el nombre de la app y ofrece una tarjeta cuando corresponde. La tarjeta depende de la preferencia de la persona y de que Iris esté visible y disponible; no es una garantía de presentación a la hora exacta. El registro persiste al cerrar la app y reiniciar Iris.
  - Una `key` nombra un recordatorio futuro: volver a enviarlo lo mueve, y `cancelNotice(key)` cancela ese pendiente y devuelve un booleano. Los avisos ya entregados permanecen en la Bandeja. `scheduledNotices()` lista solo los futuros como `{ id, key, title, at }`, con `at` en milisegundos.
  - `notify` va ligado a la instalación de la vista: una ventana que siga abierta de una versión ya actualizada o desinstalada no programa nada. Al desinstalar, los avisos futuros se cancelan; los que ya llegaron siguen en la Bandeja.
  - Límites: 120 caracteres de título y 1000 de texto. Cada app tiene como máximo 32 avisos futuros pendientes y 30 avisos nuevos por hora; un aviso idéntico en el mismo minuto cuenta como el mismo.
  - La persona elige por app en Administrar aplicaciones: «Mostrar y guardar en la Bandeja», «Solo en la Bandeja» o «Desactivados».
  - `notify` devuelve `{ status, id, at, shown }`. `at` está en milisegundos y `shown` indica el modo «Mostrar», **no un recibo de que la tarjeta ya se dibujó**. Un rechazo lanza un error; no anuncies que se avisó solo por haberlo intentado.

Estas ampliaciones del 28 de octubre están incluidas en el candidato QA18, con
pruebas del runtime aislado aprobadas y QA instalada pendiente. No están en
QA15–QA17 aunque esos candidatos ya admitan el SDK 2 base. Consulta
[SDK 2](../docs/guides/community-apps-python.md) para audiencias, avisos desde
Python y ejemplos de `askIris` y `openApp`. Avisar mediante una tarjeta y enviar
un mensaje a Iris son acciones distintas; un recordatorio no inicia una conversación.

## Carpeta de la interfaz (`ui/`)

- **Qué se publica.** Al generar el catálogo, `ui/` se copia a `face/apps/<id>/`: solo archivos `.js`, `.css`, `.svg`, `.png`, `.webp` y `.json`, con nombres en minúsculas, hasta dos subcarpetas y como mucho 512 KB por archivo y 2 MB en total.
- **Las hojas de estilos.** La ventana usa `ui.style`; los widgets pueden declarar sus propias hojas en `widgets[].style`. Se convierten en módulos y se adoptan al cargar su vista. Todas deben aislar sus reglas con una clase propia.
- **Lo que se queda en la carpeta.** `backend/`, `tests/`, `README.md` y `app.json` nunca llegan a Face.
- **La copia es generada.** `face/apps/` solo contiene lo que genera el script: no la edites.

## Servicio Python (`backend/service.py`)

- **Qué es.** Una app puede traer `backend/service.py` con `create_router(context)`.
- **Dónde se publica.** Core lo monta en `/api/apps/<id>/`, detrás de sus protecciones HTTP, y le da su carpeta de datos `<perfil>/app-data/<id>`.
- **Cuándo se activa.** **Solo si su `id` figura en `REVIEWED_APP_SERVICES`** de [`core/app_services.py`](../core/app_services.py), un paso de revisión. Ninguna ficha ni carpeta activa un servicio por sí misma.

Detalles en [`_plantilla-servicio`](_plantilla-servicio/README.md).

## Acciones para Iris (`operations(context)`)

Para que Iris pueda usar la app al conversar, `backend/service.py` define `operations(context)`. Devuelve operaciones (`AppOperation` de [`core/app_actions.py`](../core/app_actions.py)), y cada una declara:
- su `action` y una `label` (lo que verá la persona en la confirmación);
- su efecto: `read`, `local` o `external`;
- un validador de argumentos y la proyección pública de su resultado;
- dónde corre: en Core (`handler`) o en la interfaz de la app (`surface=APP_SURFACE_ROUTE`, que llega a su `handleIntent({ action, args })`);
- opcionalmente, `describe(args)`: dice sobre qué actúa, a partir de los argumentos ya validados (por ejemplo, «Elemento: dos»). Nunca incluye rutas ni secretos.

**Cómo se ejecuta una acción:**
1. Core valida la petición y aplica Capability Center. Todas las acciones de apps usan la capacidad **«Acciones de apps de IRIS OS» (`apps.actions`)**, que por defecto pregunta.
2. La persona confirma en IRIS OS. El diálogo muestra la `label`, el resumen de `describe` y el efecto. Ese texto lo escribe código revisado. Un argumento que venga de la IA solo aparece validado y entre comillas, como en «Buscar: «jazz tranquilo»».
3. La acción se ejecuta una sola vez y se devuelve solo lo que proyecta su definición.

   Un `handler` en Core tiene `timeout_s` para todo su trabajo. Si devuelve algo que hay que esperar, esa espera cuenta dentro del mismo límite. Si se pasa, el resultado dice que la acción pudo haberse hecho, y no se reintenta.

**Cómo la encuentra Hermes:** la skill operativa consulta el catálogo (`/api/app-actions/catalog`) y envía la acción (`/api/app-actions`).

**Límites:**
- Una app solo registra acciones propias, siempre con `apps.actions`.
- Solo puede ejecutarlas en Core o en IRIS OS, nunca en la ruta de otra app.
- Como el servicio, sus acciones se activan solo si la app figura en `REVIEWED_APP_SERVICES`.

## Después de editar una app

```bash
node scripts/build_app_catalog.mjs
```

El script valida todas las fichas, exporta las carpetas `ui/` y regenera `face/js/os_app_catalog.js`, el catálogo que carga el escritorio.
Desde F12 también genera allí los imports/metadatos de widgets y
`core/widget_catalog.py` para validar las preferencias guardadas desde Core.
Estos archivos se generan desde `app.json`; no se editan a mano.

Falla, en lugar de omitir la app en silencio, si:
- una ficha es inválida;
- su `id` no coincide con la carpeta;
- un `id` está repetido;
- falta el módulo de entrada o la hoja de estilos;
- la carpeta `ui/` trae algo que no se publica.

Con `--check` solo comprueba; las pruebas exigen que todo esté al día.

Las carpetas que empiezan por `_` o `.` no son apps (las plantillas), y los archivos sueltos de `apps/` son documentación.

Pruebas de todas las apps:

```bash
node --test tests/js/*.test.mjs apps/*/tests/*.test.mjs
```

Más contexto en el plan F09 (informe interno no distribuido).
