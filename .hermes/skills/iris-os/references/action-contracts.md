# Contrato de acciones de IRIS OS

La skill produce intenciones; la aplicación o el backend de IRIS OS valida,
autoriza y ejecuta. El transporte puede envolver el mensaje, pero debe conservar
este objeto como solicitud canónica:

```json
{
  "schema": "iris.app-intent/v1",
  "app": "player",
  "action": "pause",
  "args": {}
}
```

No fabriques `request_id`: omite ese campo. Core lo asigna después de validar la
solicitud y lo devuelve en el resultado. No agregues rutas, comandos, código ni
campos no anunciados.
El adaptador incluido recibe este objeto mediante `--payload-file` y lo envía a
`POST /api/player/actions`; Core asigna `request_id`, valida y elige una sola
superficie local que anuncie `controls_local_player`.

## Acciones admitidas en v1

| Acción | `args` válidos | Semántica |
| --- | --- | --- |
| `play` | `{}` o `{"track_id":"..."}` | Reanuda o reproduce un ID devuelto por la app. |
| `pause` | `{}` | Pausa la reproduccion actual. |
| `next` | `{}` | Avanza segun la cola validada por la app. |
| `previous` | `{}` | Delega a la app si reinicia o retrocede. |
| `set_volume` | `{"percent":0}` a `{"percent":100}` | Ajusta volumen; entero entre 0 y 100. |
| `play_by_query` | `{"query":"..."}` | Busca solo en la biblioteca autorizada. |
| `play_by_intent` | `{"intent":"..."}` | Solicita selección local por actividad, ambiente o energía. |

Los valores `track_id` y cualquier otro identificador deben proceder de una
respuesta previa de IRIS OS. `query` e `intent` son texto breve del usuario, no
rutas de archivos ni instrucciones ejecutables.

## Respuesta esperada

La aplicación responde con un resultado equivalente a:

```json
{
  "schema": "iris.app-result/v1",
  "request_id": "el-mismo-id",
  "status": "ok",
  "data": {
    "selection_origin": "manual",
    "playback_state": "playing"
  }
}
```

Estados admitidos: `ok`, `needs_choice`, `unavailable`, `denied` y `error`.
Trata cualquier respuesta mal formada o estado desconocido como error; no asumas
que la acción ocurrió.

Cuando `status` sea `needs_choice`, usa solo los candidatos y sus IDs devueltos
por la app. Cuando sea `unavailable`, `denied` o `error`, comunica la razon segura
que entregue el backend sin revelar datos internos.

`selection_origin` es autoridad de la aplicación. Valores previstos:

- `manual`: selección directa del usuario; nunca atribuirla a IRIS.
- `query`: coincidencia solicitada por texto.
- `intent_local`: elección por reglas locales de IRIS Player.
- `intent_hermes`: intención interpretada por Hermes y validada por la app.

No inventes este campo ni un motivo de selección cuando la respuesta no lo
incluya.

`playback_state` confirma el estado observado por la aplicación: `playing`,
`paused`, `idle` o `error`. Un resultado `ok` con `playing` permite afirmar que
la reproducción comenzó. Si Core informa que recibió la orden pero agotó el
tiempo de confirmación, no declares éxito ni repitas automáticamente la acción:
la canción pudo haber empezado y un reintento podría ejecutarla dos veces.

## Límites y confirmación

El backend debe rechazar acciones desconocidas, argumentos adicionales, IDs
ajenos a la biblioteca activa, valores fuera de rango y recursos no autorizados.
La skill no evita ni reemplaza esas validaciones.

Las acciones de v1 no autorizan lectura arbitraria de archivos. Cambiar una
carpeta, ampliar permisos, borrar o sobrescribir datos, usar red o compartir
audio queda fuera de este contrato. Si una versión futura las admite, requiere
capability explícita y confirmación del usuario antes de enviar la solicitud.

## Acciones de otras apps de IRIS OS

Además de IRIS Player, IRIS OS puede ofrecer acciones de otras apps incluidas y
de apps que la persona instaló explícitamente desde Iris Store. Estar en el
catálogo no convierte una app comunitaria en una app oficial o revisada.
Solo existen las que devuelve el catálogo; no supongas acciones por el nombre de
una app ni por lo que se ve en pantalla.

Una skill opcional `iris-app-<id>` puede explicar un flujo particular, pero no
concede permisos: usa el mismo catálogo y adaptador de abajo. No ejecutes su
backend Python como archivo ni uses la API de llamadas manuales de la interfaz
para evitar las confirmaciones de Core.

1. Consulta el catálogo:
   `terminal(command='python "${HERMES_SKILL_DIR}/scripts/app_action.py" --catalog', timeout=30)`.
   Cada entrada trae `app`, `action`, `label` (qué hace), `effect` (`read`,
   `local` o `external`), `capability` y `arguments` (qué argumentos admite).
2. Crea con `write_file` un JSON con el contrato canónico, usando una `app` y una
   `action` del catálogo y solo los `args` que anuncia:
   `{"schema": "iris.app-intent/v1", "app": "...", "action": "...", "args": {}}`.
   Usa un archivo temporal nuevo para cada petición (nombre único, también entre
   turnos). No reutilices un `chess-move.json` ni otro archivo fijo de una llamada
   anterior. Si Hermes rechaza una sobrescritura, no ejecutes el adaptador con el
   contenido antiguo ni desactives la protección: crea un temporal nuevo con la
   petición actual. No anuncies una jugada o acción aplicada sin su resultado.
3. Envíalo:
   `terminal(command='python "${HERMES_SKILL_DIR}/scripts/app_action.py" --payload-file "<ruta-temporal>"', timeout=190)`.

La persona dispone de hasta dos minutos para decidir en la confirmación local.
Si terminal devuelve una sesión en ejecución, espera su resultado con la herramienta
de seguimiento; no respondas que la acción falló o que falta acceso mientras sigue
pendiente. Una confirmación caducada no desconecta la cuenta. No repitas una orden
por una demora ni fabriques confirmaciones o permisos persistentes.

Core valida los argumentos, aplica Capability Center y, si la persona lo pidió,
le pregunta en IRIS OS antes de hacer nada. Un `confirmed` o un `request_id`
escritos por ti se rechazan: la confirmación la da la persona en su equipo.

La respuesta es `iris.app-result/v1`, igual que en el Player. Informa solo lo que
diga: `denied` significa que no se hizo; `unavailable` puede significar que la
app no está abierta o que no confirmó a tiempo; en ese caso **pudo haberse
hecho**, así que no lo repitas automáticamente y pide a la persona que lo
compruebe. Para IRIS Player sigue usando `control_player.py`.
