# Apps de la comunidad con Python y skills (SDK 2)

Revisión documental: **2026-10-28**. **SDK 2 base incluido en QA15, QA16 y QA17.** QA14 admite
solo interfaz y widgets. La presencia del SDK no valida automáticamente una app:
comprueba su paquete y sus operaciones con el worker real de Iris.

La ampliación del 28 de octubre está aprobada en fuente **posterior a QA17**:
`audience`, avisos de apps, «Avisar a Iris» y datos en `openApp` desde ventanas.
Está incluida en el candidato QA18, con pruebas del runtime aislado aprobadas;
la QA instalada con WebView2 y modelo real sigue pendiente. No está en esos
instaladores anteriores. El número `sdkVersion: 2` se
conserva; prueba estas funciones en una versión de Iris que contenga la ampliación.

Una app instalable puede tener interfaz, widgets, lógica Python y una skill
opcional para Hermes. Todo se distribuye en el mismo ZIP o carpeta. Parte del
[Contador completo](../../examples/apps/contador/README.md) para crear la tuya.
Las apps de interfaz del SDK 1 siguen funcionando.

## Ficha y archivos

Mantén `schema: "iris.app-manifest/v1"` y usa `sdkVersion: 2`. Añade este bloque a
la ficha normal de tu ventana:

```json
"extension": {
  "schema": "iris.app-extension/v1",
  "python": "main.py",
  "skill": true,
  "operations": [
    {"action": "status", "label": "Consultar contador", "effect": "read", "arguments": {}},
    {"action": "increment", "label": "Sumar al contador", "effect": "local", "arguments": {"amount": "integer"}}
  ]
}
```

`python` nombra un archivo de `backend/`, sin rutas. `skill` añade
`skill/SKILL.md`; es opcional y por defecto es `false`. Una app también puede
incluir solo la skill, sin Python ni operaciones. No uses `services` para este
contrato: ese campo pertenece al camino de integración revisada de F09.

Cada operación declara `action`, una etiqueta para la persona, efecto (`read`,
`local` o `external`) y sus argumentos. Hasta 16 operaciones, con hasta 16
argumentos por operación. Los tipos son `string`, `integer`, `number`, `boolean`,
`object` y `array`; todos los argumentos declarados son obligatorios y los
desconocidos se rechazan. Valida también los límites propios de tu dominio en Python.

Los nombres de `action` y de cada clave de `arguments` deben cumplir
`[a-z][a-z0-9_]{0,47}`: entre 1 y 48 caracteres, empiezan con letra minúscula y
solo admiten minúsculas, números y guion bajo. Usa `for_iris`, no `forIris`.
Esta regla se aplica a los nombres declarados de operaciones y argumentos;
las claves de los datos internos de la app pueden seguir otra convención.

### Quién puede llamar cada operación

Una operación puede declarar `audience`:

| `audience` | Quién la llama |
| --- | --- |
| `all` (por defecto) | La ventana, los widgets e Iris. |
| `ui` | Solo la ventana y los widgets de la app. Nunca entra en el catálogo de Iris. |
| `iris` | Solo Iris. La ventana y los widgets no pueden llamarla. |

```json
{"action": "sync", "label": "Sincronizar la ventana", "effect": "local", "arguments": {}, "audience": "ui"}
```

Core asigna el origen según el camino por el que llega la llamada, nunca según
lo que diga la app ni el modelo. Si llega una operación declarada para el otro
público, Core la rechaza antes de ejecutar el backend, aunque se conozca su
nombre. Úsalo para lo que solo sirve a la interfaz, como entregar fotogramas o
sincronizar una ventana, o para lo que solo debe pedir Iris.

### Datos opcionales

Todas las claves declaradas en `arguments` deben enviarse en cada llamada y
cumplir su tipo. El SDK actual no declara argumentos opcionales ni tipos anulables:
`null` no sirve como valor de un argumento declarado `string`, `integer`, `number`,
`boolean`, `object` o `array`.

Si un texto puede quedar vacío, la app puede definir `""` como «sin valor» y
documentarlo en su backend y skill. Para una edición parcial, declara por ejemplo
`{"id": "string", "changes": "object"}`: `id` y `changes` son obligatorios, pero
el backend decide qué campos internos de `changes` son opcionales:

```javascript
await context.call('update_task', { id: taskId, changes: { title: 'Título nuevo' } });
```

El backend valida las claves, tipos y límites dentro de `changes`, y define si
un campo omitido conserva su valor. Un `null` dentro de ese objeto puede pasar
el validador JSON, pero solo debe enviarse si el backend lo admite y documenta
su significado; no implica por sí mismo «vaciar» o «no modificar».

## Backend

`backend/main.py` exporta una función normal o async:

```python
def handle(action, args, context):
    if action == 'status':
        return {'count': 0}
    raise ValueError('Unknown action')
```

- Recibe solo el nombre declarado, sus argumentos ya comprobados,
  `context.data_dir`, una carpeta propia de la app en ese perfil, y
  `context.caller`: `ui` si llama su ventana o un widget, `iris` si lo pide Iris.
  Devuelve un diccionario de datos, no una respuesta HTTP ni un sobre de permisos.
- `context.caller` distingue los dos caminos de Iris. No es una barrera frente a
  otros programas de tu equipo: cualquier proceso con tus permisos alcanza Core.
- **Avisos desde Python.** Un resultado puede incluir `iris_notices`: una lista de
  hasta 8 avisos `{"title", "text", "at", "key"}`, donde `at` está en segundos
  desde 1970 y 0 significa ahora. Para quitar un aviso, envía `{"key", "cancel": true}`.
  - Core aplica cada aviso por separado y nombra la app por su instalación.
  - Core quita `iris_notices` de los datos y conserva un resumen interno de aceptados,
    cancelados y rechazados. **`context.call()` devuelve solo los datos de dominio**;
    ese resumen no forma parte de su resultado ni de los datos que recibe Hermes.
    Un `ok` de la operación no garantiza que el aviso se aceptó: la persona puede
    haber desactivado los avisos o la app puede haber alcanzado su cuota.
  - Así, una tarea con hora que Iris añade tiene su recordatorio aunque la
    ventana esté cerrada. Los límites y el control de la persona son los de
    `notify` en [apps/README.md](../../apps/README.md).
  - Al cancelar una llamada durante la persistencia, Core termina esa escritura
    antes de soltar el bloqueo de la app y propagar la cancelación. Desinstalar
    espera esa escritura y después cancela sus avisos futuros.
- Se permiten módulos propios `.py` y datos `.json`/`.txt` en `backend/`, con
  nombres en minúsculas y hasta dos niveles de carpetas. No incluyas entornos,
  ejecutables, `requirements.txt`, instaladores ni scripts de arranque global.
- El worker usa el mismo Python de Iris en un proceso aparte con `-I -S -B`:
  biblioteca estándar y módulos propios. No importa `core`, FastAPI ni paquetes
  instalados en el sistema. No modifica los entornos de Core o Hermes.
- No se heredan claves ni variables de proveedor. Esta separación **no es un
  sandbox de Windows**: el código sigue teniendo los permisos de tu usuario.
  No diseñes servicios hijos independientes del worker ni guardes claves en el paquete.
- Una operación a la vez por app, sin bloquear el bucle de Core. El worker tiene
  un límite de 15 segundos; timeout o cancelación mientras ejecuta la operación
  lo detienen. Si ya terminó y Core está guardando avisos, esa escritura finaliza
  antes de propagar la cancelación. No hay reintento automático: una modificación
  pudo haberse realizado antes del corte.
- Los argumentos y el resultado son JSON finito de hasta 32 KiB y profundidad
  ocho. Usa datos pequeños: hasta 128 elementos por lista, 64 claves por objeto
  y textos de hasta 8192 caracteres. No devuelvas archivos, binarios ni secretos.
- `print` se desvía fuera del protocolo. Para el error se devuelve su clase,
  sin publicar tracebacks ni texto arbitrario de excepciones.

### Cómo medir los 32 KiB

Estos límites se cumplen a la vez: que un texto tenga hasta 8192 caracteres no
garantiza que el objeto completo quepa en 32 KiB. Con acentos o emojis puede
alcanzar el límite de bytes mucho antes que el de caracteres.

El validador actual (`core/app_extension_contract.py`, `bounded_json`) mide por
separado el objeto completo de argumentos y el diccionario completo devuelto por
`handle()`. La fórmula exacta es:

```python
import json

def sdk_json_bytes(value):
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8'))

# Cada objeto admitido debe medir como máximo 32768 bytes.
```

Conserva los separadores por defecto de `json.dumps`: no uses
`separators=(',', ':')` para calcular este límite. Se cuentan también las claves,
comillas, espacios y demás estructura del objeto; medir solo el texto no basta.
El sobre interno del worker lo añade Iris: la app mide el diccionario que devuelve,
sin construir ese sobre.

Los caracteres no ASCII cuentan escapados: `ñ` ocupa 6 bytes de contenido
(`\u00f1`) y `😀` ocupa 12 (`\ud83d\ude00`), sin contar las comillas ni el resto
del objeto. Medir el JSON con `ensure_ascii=False`, contar caracteres o medir
solo el UTF-8 de `JSON.stringify()` puede dar un tamaño menor al que acepta Iris.

Pagina las listas y recorta los resúmenes usando esa medición, con margen para
los campos de la respuesta completa. Comprueba también que un elemento individual
quepa y mantén los demás límites de forma, profundidad y longitud. Incluye casos
con acentos y emojis; un emulador de pruebas debe reproducir las reglas del código
vigente y no sustituye la prueba con el worker real.

Python y la skill suman hasta 1 MiB; un archivo de componente ocupa como máximo
256 KiB. Se conservan los límites existentes de interfaz (2 MiB) y del paquete
(6 MiB de petición, 400 archivos). No se admiten dependencias descargadas por app
en esta entrega.

## Ventana y widget

Una ventana instalada con Python recibe `context.call(action, args)`. El widget
recibe `call` junto a `root`, `config`, `signal` y `openApp`. Ambos devuelven el
diccionario del backend o lanzan un error si la operación no se completa.
No necesitas construir URLs ni iniciar tu propio servidor.

El límite de 32 KiB también se aplica al objeto `args` que la ventana o el widget
envían por `call(action, args)`, antes de ejecutar el backend. Por ejemplo, divide
una importación grande en tandas que cumplan tanto ese tamaño como los máximos
de elementos y profundidad; comprueba cada tanda completa antes de enviarla.
La misma medición de la sección anterior sirve para entradas y resultados.

La llamada queda ligada a la instalación que creó esa vista: después de una
actualización o desinstalación, una respuesta antigua no puede convertirse en
un resultado de la versión nueva. El `signal` se cancela al retirar la vista.
No repitas automáticamente operaciones que cambian algo.

### Avisos que sobreviven al cierre

Ventana y widget reciben `notify`, `cancelNotice` y `scheduledNotices` al instalar
la app desde la Store, también si no trae Python. No construyas las rutas HTTP ni
una identidad de instalación a mano: el contexto ya queda ligado a la versión instalada.

```javascript
await context.notify({
    title: 'Enfoque terminado',
    text: 'Es hora de descansar.',
    at: Date.now() + 25 * 60 * 1000,
    key: 'sesion',
});
```

Usa esa misma `key` para reprogramar un aviso que todavía es futuro. Al reiniciar
la sesión, `await context.cancelNotice('sesion')` cancela el pendiente; devuelve
`false` si ya no existe uno futuro. Los avisos ya entregados permanecen en la Bandeja.
`scheduledNotices()` devuelve los futuros como `{ id, key, title, at }`, con `at`
en milisegundos.

Si la operación también puede venir de Iris, pide el aviso desde su resultado
Python, por ejemplo este diccionario JSON, en lugar de programarlo otra vez en la UI:

```json
{"ok": true, "iris_notices": [{"title": "Tarea pendiente", "text": "Revisa tu tarea.", "at": 0, "key": "tarea"}]}
```

En `iris_notices`, `at` usa **segundos**, no los milisegundos de JavaScript.
Las preferencias y cuotas se aplican a ambos caminos. Un aviso guarda información
en la Bandeja y puede mostrar una tarjeta; no envía por sí solo un mensaje a Iris.

### Enviar información a Iris y abrir otra app

Prepara el texto y los datos antes del clic. Un botón HTML con su manejador de
`click` sirve también para Enter y Espacio; no necesitas duplicar el envío en
`keydown` y `click`.

```javascript
button.addEventListener('click', async () => {
    try {
        const result = await context.askIris({
            text: 'Te toca: jugué e4.',
            data: { last_move: 'e2e4' },
        });
        status.textContent = result.sent ? 'Mensaje enviado a Iris.'
            : result.reason === 'cancelled' ? 'Envío cancelado.'
            : result.reason === 'refused' ? result.message : 'Iris no respondió.';
    } catch (error) {
        status.textContent = error.message;
    }
});
```

`button` y `status` son elementos de tu interfaz. El gesto debe ocurrir en la
ventana o widget de esa app, se consume una vez y caduca a los 3 segundos. Una tecla
y el clic derivado del navegador cuentan como un solo gesto. «Enviar siempre»
omite el diálogo, no ese requisito. No intentes enviar desde un temporizador ni
después de un proceso largo. La persona puede revocar la opción en Administrar aplicaciones.

Iris muestra el texto y permite revisar todos los datos antes de aprobar. `text`
tiene de 1 a 300 caracteres y no empieza por `/`; `data` es JSON opcional de hasta
4096 bytes según la medición de Core explicada arriba. Mantén también los límites
de forma del JSON. Se recibe como información de la app, no como órdenes ni permisos.
`sent: true` confirma que Iris recibió el mensaje, no que el modelo ya haya respondido.
Si Iris lo rechaza, por ejemplo porque la app se abrió desde otro dispositivo, el
resultado es `{ sent: false, reason: 'refused', message }`. `offline` indica que no
hay conexión o no se recibió la confirmación en 5 segundos; **no demuestra que el
mensaje no llegara**. No lo reenvíes automáticamente. Si la aceptación llega tarde,
el chat refleja ese mensaje una sola vez, aunque el resultado anterior de la app
siga siendo `offline`.

El shell comprueba la forma y el tamaño antes del diálogo; un rechazo previo lanza
el error sin pedir aprobación. Core sigue siendo quien confirma la aceptación final.
La medición incluye la notación científica que Core usa para decimales pequeños:
por ejemplo, `0.000001` se cuenta como `1e-06`. El caso de paridad que rechazaba
datos válidos cerca del límite quedó corregido; el límite de Core sigue siendo 4096 bytes.

Para abrir un detalle en otra ventana, usa `context.openApp(id, datos)`. La copia
llega a `navigate(datos)` del destino cuando termina de cargar. No es una llamada
al backend ni devuelve el controlador de esa ventana; el destino valida los datos.

Las llamadas manuales de la interfaz son controles propios de la app; confirma
acciones destructivas o externas con el diálogo de Iris antes de pedirlas. Las
órdenes de Hermes pasan por el registro, Capability Center y sus confirmaciones.
Una etiqueta `effect` o una skill no transforma código de terceros en código aislado.

Comparte el estado en el backend y actualiza las vistas al recibir el resultado.
Si consultas periódicamente, hazlo solo mientras esa vista esté visible. El ejemplo
Contador muestra la sincronización sin mantener dos contadores independientes.

## Skill opcional para Hermes

```markdown
---
name: iris-app-mi-app
description: Explica cuándo usar las operaciones de Mi app.
license: MIT
---

Usa el catálogo de acciones de la skill general iris-os para consultar Mi app.
```

El nombre debe ser `iris-app-<id>` y la descripción es obligatoria. En esta
entrega el encabezado admite `name`, `description` y `license` opcional, cada uno
en una línea; usa texto simple o comillas JSON. Los valores que YAML interpretaría
como números, booleanos, listas o fechas, y los que contienen `: ` o ` #`, necesitan
comillas: por ejemplo, `description: "Consulta: actualiza el contador"`. La Store
rechaza un encabezado cuyo texto cambiaría al leerlo en Hermes. Añade instrucciones
reales después del encabezado. Puedes incluir referencias `.md` en `skill/`, sin scripts.

La skill describe flujos, no concede permisos. Reutiliza el adaptador
`app_action.py` de `iris-os` para las operaciones declaradas. No copies rutas
personales, claves, SOUL.md ni archivos de configuración al paquete. Las apps
sencillas no necesitan una skill propia: `iris-os` ya consulta el catálogo.

Iris registra el propietario y perfil de la skill. No sobrescribe una skill
ajena con el mismo nombre ni una copia administrada que hayas editado. Una
actualización con conflicto se rechaza; al desinstalar se conserva la copia
editada y se avisa de que sus acciones ya no están disponibles.

## Validar, distribuir y retirar

```text
.venv/Scripts/python.exe scripts/app_package_tool.py check carpeta-de-mi-app
.venv/Scripts/python.exe scripts/app_package_tool.py pack carpeta-de-mi-app artifacts/mi-app.zip
.venv/Scripts/python.exe scripts/app_package_tool.py check artifacts/mi-app.zip
```

La herramienta usa el lector real de la Store. No ejecuta el backend, no instala
dependencias y no sobrescribe un ZIP existente. La app externa no se añade a
`apps/` del producto para probarla: se instala con la Store en un perfil desechable.

Antes de entregar una app como compatible, ejecuta el `check` oficial sobre la
carpeta y el ZIP final. Para una app con Python, instálala en ese perfil desechable
y prueba sus operaciones a través del worker real de Iris, incluidos datos con
acentos y emojis cerca de los límites. Importar `handle()` directamente o pasar
un emulador propio no comprueba esa integración. Si no tienes acceso a una fuente
de Iris con SDK 2, indica la validación oficial y la ejecución real como pendientes.

En las pruebas asíncronas, espera un resultado o un estado observable (por ejemplo,
que desaparezca «Cargando…») con un tiempo máximo. No supongas que Python o la
interfaz habrán terminado tras una pausa fija de 60 ms; tampoco esperes sin límite.

La inspección enumera Python, skill y operaciones. Instalar crea los componentes
administrados; Python arranca con la primera llamada. La actualización cierra su
worker y reutiliza el catálogo en vivo de la Store. Las skills usan un registro
de cambio recuperable: tras una interrupción manda la identidad confirmada en
la base de datos, y se restaura o completa la copia correspondiente.

Desinstalar retira la app, sus acciones, worker y skill administrada. Conserva
los datos por defecto; puedes marcar **Eliminar también sus datos al desinstalar**.
Una carpeta modificada o con enlaces se conserva con aviso. Reinstalar recupera
los datos conservados. Los componentes Python nunca se sirven como archivos web.

El almacén de paquetes pasa a formato 2 para proteger su ciclo de vida: una
versión anterior de Iris, como QA14, lo rechaza sin modificarlo. Prueba esta
ampliación en un perfil nuevo mientras validas la actualización y reversión.
