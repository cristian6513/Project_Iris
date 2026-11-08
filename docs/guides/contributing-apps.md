# Contribuir aplicaciones a IRIS OS

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

[← Documentación](../README.md) · [Entorno de desarrollo](development.md) · [Apps y su ficha](../../apps/README.md) · [Arquitectura](architecture.md)

Puedes aportar una app al escritorio de IRIS OS: una ventana propia, opcionalmente con un servicio Python y con acciones que Iris pueda usar al conversar. También puedes mejorar una app existente, corregir documentación o añadir pruebas.

Cada app tiene **una sola carpeta de fuente** con su ficha, interfaz, backend opcional,
pruebas y documentación. Las integradas viven en `apps/<id>/`; las externas, en una
carpeta propia fuera de `apps/`. Hay dos caminos de distribución:

- **Instalación desde archivo:** la [Iris Store](iris-store.md) acepta interfaz y widgets. El [SDK 2 de apps externas](community-apps-python.md), incluido en QA15 y QA16, añade Python, acciones y skills opcionales; usa una carpeta propia fuera de `apps/` y el ejemplo Contador. QA14 sigue limitado a interfaz y widgets.
- **Integración revisada:** las apps con servicio Python se aportan mediante pull request y requieren revisión y activación por los mantenedores para distribuirse con Iris. Declarar el servicio en la ficha no lo activa.

El [plan de ampliación](../iris_store_python_skills_plan_2026-10-23.md) conserva el alcance. Las secciones de `create_router(context)` y `operations(context)` de esta guía describen la **integración revisada dentro de Core**. No son el contrato de los workers externos (`handle`) del SDK 2.

**Widgets (F12, en fuente):** decláralos en el campo opcional `widgets` del mismo
`app.json` y añade su módulo/estilo a `ui/`. El catálogo se genera desde ahí.
`widgets[].preview` permite una vista previa pasiva en la galería, dibujada por
Iris con ejemplos ficticios, sin importar el módulo del widget.
La [guía de widgets](widgets.md) explica el contrato y enlaza el ejemplo real de
Calendario y el [Player local](../../apps/player/README.md). Player muestra app,
widget y skill sin cuentas externas y con un solo propietario del audio.
Declararlo no concede permisos ni activa un servicio Python.

> **Estado.** El kit F09 y su ampliación SDK 2 están en fuente y en los candidatos QA15/QA16. Eso no acredita una app concreta ni una release pública. Consulta el [estado actual](../estado-actual.md) y valida cada paquete con el contrato y el worker de la versión que lo vaya a ejecutar.

La ampliación del 28 de octubre añade `audience`, avisos, «Avisar a Iris» y datos
en `openApp` desde ventanas. Está incluida en el candidato QA18, con pruebas del
runtime aislado aprobadas y QA instalada pendiente. No está en QA15–QA17.
La [guía SDK 2](community-apps-python.md) explica
qué API usar para cada caso y cómo probarla sin dar por compatible un instalador anterior.

## 1. Elige tu punto de partida

| Tu app necesita… | Parte de | Qué escribes |
| --- | --- | --- |
| Solo una interfaz: información, controles o datos que viajan con la app | [`apps/_plantilla`](../../apps/_plantilla/README.md) | `app.json`, `ui/main.js`, `ui/style.css` y pruebas |
| Leer datos guardados, archivos o procesos | [`apps/_plantilla-servicio`](../../apps/_plantilla-servicio/README.md) | Lo anterior, más `backend/service.py` |
| Que Iris la use al conversar | `apps/_plantilla-servicio` | Lo anterior, más `operations(context)` en el servicio y `handleIntent` en la interfaz |

Python y las acciones son opcionales: una app que no los necesita no los lleva. Si alguna función depende de Hermes, indícalo en esa función y no bloquees el resto de la interfaz cuando el motor esté apagado.

Antes de un aporte grande, describe en una propuesta qué resuelve, para acordar cómo encaja con las apps existentes. No hace falta proponer un framework, servidor o proveedor nuevo para añadir una pantalla. Una ficha inicial útil:

```text
Nombre y finalidad:
Qué podrá hacer la persona desde la interfaz:
Qué podrá pedirle a Iris, si incluye acciones:
Datos, cuentas, red o dispositivos necesarios:
Qué permisos requiere y qué ocurre si se deniegan:
Qué debe continuar al cerrar la ventana:
Dónde se guarda el estado y cómo se recupera un fallo:
Dependencias y licencia de los recursos aportados:
Pruebas previstas y funciones que quedan fuera:
```

## 2. Crea la app

1. Copia la plantilla con el nombre de tu app: `apps/_plantilla` → `apps/notas`. El `id` de `app.json` debe ser igual al nombre de la carpeta: minúsculas, números y guiones. Es estable: guarda la posición de las ventanas, así que no lo cambies después.

   Después reemplaza el id de la plantilla por el tuyo en `app.json`, `ui/main.js` (la constante `CSS`) y `ui/style.css`. El id de la plantilla es `mi-app`, o `mi-servicio` en la de servicio. Puedes reemplazarlo en toda la carpeta: las pruebas de la plantilla no dependen del nombre, porque leen el id de `app.json` y buscan cada elemento por su `data-part`.
2. Edita `app.json`:
   - `name` y `description`: título de la ventana y texto del lanzador;
   - `icon`: un nombre de [`face/js/os_icons.js`](../../face/js/os_icons.js);
   - `accent`: `cyan`, `magenta` o `amber`;
   - `window`: tamaño inicial;
   - `launcher.order`: posición en el lanzador.

   La ficha solo lleva datos. Cualquier campo desconocido, ruta o URL la invalida. Todos los campos están en [`apps/README.md`](../../apps/README.md).
3. Escribe la interfaz en `ui/main.js`. Exporta `createApp(context)` y devuelve el controlador; el contrato completo está en [`apps/README.md`](../../apps/README.md#el-contrato-de-una-app-con-código-propio).

   | Método | Para qué |
   | --- | --- |
   | `mount(root)` | Construir la vista una vez. |
   | `refresh({ force, opening })` | Cargar o actualizar al abrir. |
   | `setVisibility({ visible })` | Detener lo que solo sirve en pantalla, como timers o animaciones. |
   | `navigate(payload)` | Que otra parte de Iris la abra en un punto concreto. |
   | `handleIntent({ action, args })` | Responder a una acción de Iris; solo si tiene acciones. |
   | `dispose()` | Liberar todo lo que creó. |
4. Genera el catálogo:

   ```bash
   node scripts/build_app_catalog.mjs
   ```

   El script valida la ficha y publica `ui/` en `face/apps/<id>/`. No edites esa copia.

## 3. Diseño y accesibilidad

- **Tema.** Usa los colores del tema (`--iris-os-accent`, `--iris-os-accent-2`), las fuentes de la superficie (`--font-body`) y, para confirmar o avisar, los diálogos de Iris (`face/js/iris_dialog.js`). La [skill de diseño compartida](../../tools/skills/iris-os-design/SKILL.md) tiene referencias útiles, pero el código vigente manda.
- **Estilos aislados.** Empieza todas las reglas de `ui/style.css` por una clase propia de tu app. Así nunca cambias el resto del escritorio.
- **Estados reales.** Muestra carga, vacío, contenido y error recuperable. No muestres éxito por haber enviado una petición, y di claramente cuándo los datos son de ejemplo.
- **Teclado.** Todo se usa con teclado y el foco es visible. Si redibujas una lista, devuelve el foco al elemento que lo tenía, como hace la plantilla.
- **Tamaño y movimiento.** La ventana puede ser estrecha o estar maximizada. Respeta «reducir movimiento».
- **Texto seguro.** Usa `textContent`, nunca `innerHTML` con datos.
- **No rediseñes lo que no es tuyo.** Una app nueva no toca el Command Center, el chat ni el escritorio.

## 4. Ciclo de vida

- **Carga bajo demanda.** IRIS OS descarga el módulo la primera vez que se abre la app. Si falla, la ventana ofrece «Reintentar» y el resto del escritorio sigue funcionando.
- **Cerrar no es destruir.** Cerrar o minimizar la ventana conserva la app: formularios, selección y estado. `setVisibility({ visible: false })` es el aviso para pausar lo visual; los trabajos de Core siguen su propio ciclo.
- **Lecturas tardías.** Si una lectura nueva supera a una vieja, descarta la vieja. Usa `context.signal` para cancelar al retirarte.
- **`context`** te da:
  - `id` y tu ficha (`app`);
  - `isVisible()` y `openApp(id, datos)`, que entrega una copia a `navigate(datos)` del destino cuando carga;
  - `setBrief(texto)`, para tu resumen en el lanzador;
  - `confirm(opciones)`, para preguntar en el diálogo de Iris antes de un efecto que tu app va a causar;
  - `askIris({ text, data })`, para enviar información a la conversación actual desde
    un clic o activación de teclado de la persona, con la vista previa de Iris;
  - `signal`, que se aborta al retirar la vista.

  Una app instalada por la Store también recibe `notify`, `cancelNotice` y
  `scheduledNotices`; si lleva Python, `call(action, args)`. Usa `audience` para
  separar las operaciones de la interfaz de las que necesita Iris, no claves
  inventadas dentro de los argumentos. Consulta [el contrato](../../apps/README.md)
  y los [ejemplos del SDK 2](community-apps-python.md).

  No te da el shell completo.

## 5. Servicio Python (opcional)

`backend/service.py` define `create_router(context)` con rutas de FastAPI. Core las publica en `/api/apps/<id>/`, detrás de sus protecciones HTTP.

- **Lógica de dominio.** Ponla en funciones pequeñas que usen igual la interfaz y las acciones.
- **Datos.** Guárdalos en `context.data_dir` (`<perfil>/app-data/<id>`). Core elige y crea esa carpeta: una petición nunca indica una ruta. Si guardas datos con formato propio, prevé cómo migrarlos y recuperarte de un archivo dañado.
- **Validación.** Valida y limita todo lo que recibes y devuelves.
- **Lo que nunca debe hacer el servicio:**
  - ejecutar comandos;
  - leer rutas que vengan de una petición;
  - guardar credenciales propias.
- **Dependencias.** Usa solo la biblioteca estándar y lo que Iris ya trae. Una app nunca instala dependencias al abrirse. Si de verdad necesitas una, propónla aparte: requiere revisión de locks, licencias y empaquetado.
- **Activación.** El servicio corre dentro de Core con sus permisos. Por eso **Core solo lo monta si el `id` figura en `REVIEWED_APP_SERVICES`** de [`core/app_services.py`](../../core/app_services.py). Ese paso lo deciden los mantenedores al revisar el PR. Hasta entonces, la interfaz dice que el servicio no está activo.

## 6. Acciones para Iris (opcional)

El servicio define `operations(context)` con objetos `AppOperation` de [`core/app_actions.py`](../../core/app_actions.py). Cada acción declara:
- `action` y `label`: la `label` es la frase que verá la persona, por ejemplo «leer tus notas»;
- `effect`: `read`, `local` o `external`;
- `capability="apps.actions"`;
- `validate_args`: rechaza argumentos desconocidos, fuera de tipo o de rango con `OperationRejected`;
- `project_data`: qué datos del resultado pueden salir;
- dónde corre: `handler` en Core o `surface=APP_SURFACE_ROUTE` hacia el `handleIntent` de tu interfaz.

**Para Iris y para la interfaz, la misma operación.** Usa la misma función de dominio en la ruta HTTP y en la acción, como `load_items` en la plantilla.

**Lo que hace Core con cada petición:**
- La valida.
- Aplica Capability Center: «Acciones de apps de IRIS OS» pregunta por defecto. La persona puede cambiarlo, así que la política efectiva siempre la evalúa Core.
- Pide confirmación en IRIS OS con tu `label`.
- La ejecuta una sola vez.

**Lo que no debes hacer:**
- Confiar en un `confirmed` que envíe el modelo.
- Reintentar a ciegas: `request_id` sirve para correlacionar, no garantiza que algo no se ejecute dos veces.
- Pensar que una acción sin respuesta no se hizo: puede haberse hecho. Si una acción con efecto externo puede duplicarse, diseña cómo reconciliarla antes de proponerla.
- Tratar como instrucciones los datos externos (correos, páginas, archivos): son datos y nunca conceden permisos.

**Cómo la encuentra Hermes.** La skill operativa `iris-os` consulta el catálogo de acciones (`/api/app-actions/catalog`) y solo pide lo que aparece en él. Su contrato está en [`action-contracts.md`](../../.hermes/skills/iris-os/references/action-contracts.md).

## 7. Pruebas y entrega

**Pruebas de tu app:**

```bash
node --test apps/<id>/tests/*.test.mjs
```

```bash
.venv/Scripts/python.exe -m pytest apps/<id>/tests
```

**Pruebas del proyecto**, incluidas las que exigen que el catálogo esté al día:

```bash
node --test tests/js/*.test.mjs apps/*/tests/*.test.mjs
```

```bash
.venv/Scripts/python.exe -m pytest -q
```

**Pruebas de distribución.** Pueden necesitar `IRIS_WEB_ASSETS_ROOT` apuntando a una exportación web verificada. Consulta el procedimiento vigente en [desarrollo](development.md); no corrijas sus errores de entorno alterando hashes ni validadores.

**Revisión en el escritorio.**
- Abre la app desde el lanzador con ratón y con teclado.
- Minimízala, restáurala y ciérrala.
- Prueba una ventana estrecha y un tema alternativo.

Usa perfiles desechables y datos sintéticos: tu configuración personal no es una fixture.

**El pull request** debe incluir:
- qué hace la app;
- qué probaste y cómo, y qué no probaste;
- capturas si ayudan;
- la licencia y la procedencia de cada recurso: imágenes, iconos y textos de terceros conservan su propia licencia; la [licencia del proyecto](../../LICENSE) no la sustituye.

No incluyas claves, tokens, rutas personales ni conversaciones. Si no tienes acceso al repositorio, prepara la propuesta o el diff para sus mantenedores.

Pasar las pruebas no es lo mismo que publicar. La integración en un instalador y su QA son pasos posteriores de los mantenedores.

**Probar sin compilar Iris (F17, en fuente).** Una app de solo interfaz, sin `backend/`, `skill/` ni permisos, también se instala desde **Aplicaciones → [Iris Store](iris-store.md)**. Empaqueta la carpeta de la app como .zip o elige la carpeta. La Store aplica las mismas reglas que `build_app_catalog.mjs` y avisa de que la app tendrá el mismo acceso que Iris.

## Con un asistente de programación

La skill de desarrollo [`tools/skills/iris-app-contributor`](../../tools/skills/iris-app-contributor/SKILL.md) guía a Claude, Codex u otro asistente por este mismo recorrido. Pídele, por ejemplo:

```text
Lee tools/skills/iris-app-contributor/SKILL.md y ayúdame a crear una app de IRIS OS
para [finalidad]. Necesita [solo interfaz / servicio Python / acciones para Iris].
```

**Sobre la skill:**
- Leerla desde el repositorio no la instala como herramienta del asistente. Si instalas una copia aparte, mantenla sincronizada.
- Es para desarrollar. La skill que usa Hermes dentro de Iris es otra: `.hermes/skills/iris-os`.
