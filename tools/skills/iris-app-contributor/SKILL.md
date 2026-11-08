---
name: iris-app-contributor
description: "Crea, prueba y empaqueta apps de IRIS OS: interfaz, widgets y Python/acciones/skill opcionales con el SDK 2 de Iris Store; también contribuciones revisadas al producto. Úsalo para desarrollar apps, no para operar una app durante una conversación ni aceptar un release."
---

# Crear una app de IRIS OS

Localiza el checkout por `pyproject.toml`, `apps/`, `face/` y `core/`. Las rutas de abajo son relativas a su raíz. Esta skill sirve para desarrollar; no se instala en Hermes. La skill que usa Hermes dentro de Iris es `.hermes/skills/iris-os`.

## Antes de escribir código

1. Lee [apps/README.md](../../../apps/README.md), que es el contrato vigente, y la [guía para colaboradores](../../../docs/guides/contributing-apps.md).
2. Si algo no coincide con el código, manda el código: `face/js/os_app_manifest.js`, `core/app_packages.py`, `core/app_extensions.py` y `core/app_actions.py`. No inventes campos, imports ni endpoints. Consulta `docs/estado-actual.md` para distinguir fuente de instalador.
3. Decide qué necesita la app y elige la plantilla:

   | Necesita | Plantilla |
   | --- | --- |
   | Solo interfaz | `apps/_plantilla` |
   | Python, acciones y/o skill, instalable desde archivo | `examples/apps/contador`; lee [SDK 2](../../../docs/guides/community-apps-python.md) |
   | Servicio incluido con Iris mediante revisión | `apps/_plantilla-servicio` (`operations` + `handleIntent`) |

4. Si el encargo es grande, propón primero el alcance: qué hace, qué puede pedir Iris, datos y permisos, qué sigue al cerrar la ventana. No conviertas una app en un framework ni en una Store.

La ampliación del 28 de octubre (`audience`, avisos, `askIris` y datos en `openApp`
desde ventanas) está incluida en el candidato QA18, con pruebas del runtime aislado
aprobadas y QA instalada pendiente. Los instaladores QA15–QA17 traen el SDK 2 base,
no estas funciones nuevas. `sdkVersion: 2` no identifica por sí
solo ese corte: comprueba la versión de Iris usada en la prueba. No inventes un campo
de compatibilidad en `app.json`.

## Crear la app

1. Para integrar una app en el producto, copia la plantilla a `apps/<id>/`. Para distribuirla desde archivo, usa una carpeta propia fuera de `apps/` y pruébala mediante la Store, sin añadirla al catálogo predeterminado. Reemplaza el id en ficha, CSS, widgets y skill; debe ser estable, en minúsculas con guiones. Las plantillas revisadas y el Contador son caminos distintos: no mezcles sus backends.
2. `app.json` solo lleva datos:
   - `name`, `description`, `version`;
   - `icon`: un nombre de `face/js/os_icons.js`;
   - `accent`, `presentation`, `window`, `launcher`;
   - `ui`: `{ "entry": "main.js", "style": "style.css" }`.

   Los campos desconocidos, las rutas y las URL se rechazan. `requestedCapabilities` y `services` no conceden nada.
3. `ui/main.js` exporta `createApp(context)`, que devuelve el controlador:
   - **Obligatorios:** `mount(root)` y `dispose()`.
   - **Opcionales:** `refresh({ force, opening })`, `setVisibility({ visible })`, `navigate(payload)` y `handleIntent({ action, args })`.
   - **`context`:** `id`, `app`, `isVisible()`, `openApp(id, datos)`, `setBrief(texto)`, `confirm(opciones)` (el diálogo de Iris; `true` solo si la persona acepta), `askIris({ text, data })` (solo desde un clic de la persona) y `signal`. Las apps instaladas desde la Store también reciben `notify`, `cancelNotice` y `scheduledNotices`; las que tienen Python, `call`. Todo está en `apps/README.md`.
4. `ui/style.css`: una sola hoja, con todas las reglas bajo una clase propia y los colores del tema (`--iris-os-accent`, `--iris-os-accent-2`).
5. App externa: usa `scripts/app_package_tool.py check CARPETA` y `pack CARPETA SALIDA.zip` con el Python de desarrollo; no ejecutan el backend ni instalan nada. App integrada: `node scripts/build_app_catalog.mjs` publica su interfaz y actualiza el catálogo. Si falla, corrige la app; no el validador.

## Reglas de la interfaz

- **Ciclo de vida.** Cerrar o minimizar no destruye la app: conserva su estado. Lo visual (timers, animaciones) solo corre mientras `setVisibility` dice visible. `dispose` libera todo lo propio y vacía la vista.
- **Estados.** Muestra carga, vacío, contenido y error con reintento. No anuncies éxito por haber enviado algo. Si los datos son de ejemplo, dilo.
- **Teclado.** Foco visible y todo usable con teclado. Si redibujas una lista, devuelve el foco al elemento que lo tenía.
- **Ventana y movimiento.** Funciona en ventana estrecha y maximizada, y con «reducir movimiento».
- **Datos.** Solo con `textContent`, nunca `innerHTML`. Descarta respuestas tardías y cancela con `context.signal`.
- **Alcance.** No toques el shell (`hermes_activity.js`), el Command Center ni el chat para añadir una app: si hiciera falta, el kit tiene un fallo y hay que proponerlo aparte.

## Elegir las funciones del SDK

- **Quién llama.** En operaciones externas, usa `audience: "ui"` para consultas y
  sincronización propias de ventana/widget; `"iris"` para lo que solo pide Iris;
  `"all"` (por defecto) si ambos necesitan la misma operación. Core comprueba la
  audiencia y asigna `context.caller`; no lo deduzcas de un argumento que envía la
  app ni inventes una llave para distinguir estos dos caminos. Esto no aísla código
  de terceros frente a otros procesos con los permisos del usuario.
- **Recordatorios.** Una app de la Store usa `notify`, `cancelNotice` y
  `scheduledNotices`. Si la tarea también puede crearla Iris, programa desde el
  backend con `iris_notices`, sin duplicarlo desde la ventana. Usa una `key` estable
  para mover o cancelar un aviso futuro. En JavaScript `at` va en milisegundos o
  `Date`; en Python, en segundos. Core conserva el aviso: no mantengas un temporizador
  de ventana para que sobreviva al cierre. Respeta la preferencia por app.
- **Avisar a Iris.** Usa un botón que llame a `context.askIris({ text, data })` desde
  la acción real de la persona, con los datos ya preparados. Un gesto en esa app se
  consume una vez y caduca a los 3 s; Enter y su clic nativo son el mismo gesto.
  «Enviar siempre» solo omite la vista previa. No lo llames desde timers, avisos ni
  respuestas del backend. Un `notify` no inicia un turno de Iris. Solo `sent: true`
  confirma que Iris lo recibió; con `reason: "refused"`, muestra el `message` de Iris.
  `offline` también puede significar que la confirmación no llegó en 5 s: no es
  prueba de que el mensaje no llegó ni permiso para reenviarlo automáticamente.
- **Abrir en un punto.** `openApp(id, datos)` entrega una copia al `navigate(datos)`
  del destino después de cargarlo. Valida los datos al recibirlos; no pases funciones
  ni supongas que `openApp` devuelve el controlador o que esperarlo acredita la carga.

Los límites, ejemplos y resultados están en [Apps y su ficha](../../../apps/README.md)
y [SDK 2](../../../docs/guides/community-apps-python.md). `call()` devuelve los datos
de dominio: el resumen interno de `iris_notices` no llega por ese helper. No afirmes
que se aceptó un recordatorio solo porque la operación devolvió `ok`.

## Python y skill en una app externa

Lee la [guía SDK 2](../../../docs/guides/community-apps-python.md) y copia el [Contador probado](../../../examples/apps/contador/README.md). Usa `sdkVersion: 2`, el bloque `extension`, `backend/main.py` con `handle(action, args, context)` y operaciones declaradas. Ventana y widget usan `call(action, args)` y el mismo estado en `context.data_dir` del backend. No importes Core ni modifiques la allowlist.

Los nombres declarados de acciones y argumentos cumplen `[a-z][a-z0-9_]{0,47}`:
usa `for_iris`, no `forIris`. Para los 32 KiB, mide por separado el objeto completo
de argumentos y el diccionario completo devuelto con
`len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8'))`, sin
cambiar los separadores por defecto. Los acentos y emojis cuentan escapados; un
`😀` ocupa 12 bytes de contenido, además de comillas y estructura. La
[guía explica la medición y paginación](../../../docs/guides/community-apps-python.md#cómo-medir-los-32-kib);
contrasta estas reglas con `core/app_extension_contract.py` si cambia el SDK.

Los límites se cumplen a la vez: 8192 caracteres por texto no garantizan que el
objeto quepa en 32 KiB. El tope también alcanza los `args` enviados por `call()`;
divide y mide las tandas de importación antes de enviarlas. Todos los argumentos
declarados son obligatorios y tipados: `null` no sustituye uno de ellos. Para datos
opcionales, documenta un valor vacío válido del tipo o campos internos opcionales
en un objeto como `changes`, validados por el backend; consulta
[datos opcionales](../../../docs/guides/community-apps-python.md#datos-opcionales).

Solo biblioteca estándar y módulos propios; no `pip`, entornos globales ni dependencias descargadas. El worker separado no es un sandbox. Una skill propia es opcional: `iris-os` ya descubre las acciones. Si hace falta un flujo específico, añade `skill/SKILL.md` con `name: iris-app-<id>` y referencias Markdown, sin scripts ni cambios a SOUL.md. Reutiliza el adaptador de acciones de `iris-os`.

Prueba primero en un perfil desechable: inspección sin ejecución, llamadas manuales y de Hermes, error/timeout, actualización abierta y desinstalación con datos conservados/eliminados. No repitas automáticamente una operación con efecto incierto. La guía identifica qué sigue pendiente de candidato Windows; no presentes una prueba desde fuente como QA del instalador.

## Servicio Python integrado mediante revisión

- **Rutas.** `backend/service.py` define `create_router(context)` con FastAPI. Core lo monta en `/api/apps/<id>/`, detrás de sus protecciones.
- **Datos.** Solo en `context.data_dir`. Ninguna ruta llega en una petición.
- **Prohibido:** ejecutar comandos y guardar credenciales propias.
- **Dependencias.** Solo la biblioteca estándar y lo que Iris ya trae; no instales dependencias.
- **Activación.** Core monta el servicio solo si el `id` está en `REVIEWED_APP_SERVICES` (`core/app_services.py`). No lo añadas tú: es la decisión de los mantenedores al revisar. Para probar, usa un perfil desechable.

## Acciones integradas para Iris

- `operations(context)` devuelve `AppOperation` (`core/app_actions.py`). Cada una lleva `action`, una `label` que verá la persona, `effect` (`read`/`local`/`external`), `capability="apps.actions"`, `validate_args` (lanza `OperationRejected`) y `project_data`.
- **Dónde corre:** con `handler`, en Core; con `surface=APP_SURFACE_ROUTE`, en el `handleIntent` de la interfaz.
- **Una sola operación de dominio.** La ruta HTTP y la acción usan la misma función.
- **Confirmación.** Core valida, aplica Capability Center (la política efectiva la evalúa Core) y pide confirmación en IRIS OS. Nunca aceptes un `confirmed` del modelo.
- **Sin reintentos a ciegas.** `request_id` correlaciona; no es idempotencia. Sin respuesta, la acción pudo haberse hecho. Una acción `external` que pueda duplicarse necesita reconciliación diseñada.
- **Resultado honesto.** `handleIntent` devuelve `{ status, message, data }` con lo que realmente pasó; `status` es `ok`, `needs_choice`, `unavailable`, `denied` o `error`.
- **Cámara y fotos** tienen su propio controlador y permiso: no las uses desde una app.
- **Límites del kit.** El kit organiza código propio revisado; no es un sandbox para código de terceros.

## Probar y entregar

```bash
node --test apps/<id>/tests/*.test.mjs
```

```bash
.venv/Scripts/python.exe -m pytest apps/<id>/tests
```

```bash
node --test tests/js/*.test.mjs apps/*/tests/*.test.mjs
```

```bash
.venv/Scripts/python.exe -m pytest -q
```

- **Pruebas de la app:** sigue el modelo de `apps/_plantilla*/tests`. Prueba comportamiento (carga, error, foco, cancelación, validación), no copias literales del CSS.
- **App externa:** antes de darla por compatible, ejecuta `scripts/app_package_tool.py check` sobre la carpeta y el ZIP final. Si lleva Python, prueba sus operaciones con el worker real de Iris en un perfil desechable, también con acentos y emojis cerca de los límites. Un emulador propio o un import directo del backend no sustituyen esa comprobación. Si no tienes acceso a Iris con SDK 2, declara esas validaciones pendientes.
- **Pruebas asíncronas:** espera un resultado o estado observable con un tiempo máximo, no una pausa fija que presuponga cuánto tarda el equipo en cargar Python o dibujar la vista.
- **Revisión en el escritorio.** Ábrela desde el lanzador con ratón y teclado; minimiza, restaura y cierra; prueba una ventana estrecha y un tema alternativo.
- **Funciones nuevas, si las usas:** comprueba el rechazo de la audiencia equivocada;
  un aviso con la app cerrada y su retirada al desinstalar; «Avisar a Iris» con clic,
  Enter y Espacio, con un solo envío por gesto y rechazo desde un temporizador; y
  `openApp` con un destino que todavía está cargando. Usa Core y el escritorio reales
  para las garantías de origen, persistencia y gesto; un objeto de contexto simulado
  solo cubre la lógica de tu app.
- **Distribución.** Las pruebas de distribución pueden necesitar `IRIS_WEB_ASSETS_ROOT`: consulta `docs/guides/development.md` y no fijes una carpeta `web-N`.
- **La entrega:**
  - qué hace la app;
  - qué se probó y cómo, distinguiendo lectura de código, prueba automatizada, revisión visual y QA empaquetada;
  - qué no se probó;
  - la licencia de cada recurso de terceros.
- **Fuera de la entrega:** claves, tokens, rutas personales y conversaciones.
- **Autorizaciones.** Respeta las del encargo para cuentas, datos, configuración, commits y publicación.
