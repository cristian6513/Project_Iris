# IRIS Player

IRIS Player es un reproductor local-first. La aplicación conserva la autoridad
sobre permisos, biblioteca, cola, reproducción y estado; Hermes solo interpreta
la intención del usuario y solicita acciones admitidas.

## Requisitos previos

Antes de actuar, confirma que la sesión anuncia IRIS Player y la acción concreta.
La existencia de esta referencia no demuestra que el Player, su backend o una
biblioteca autorizada esten disponibles.

Si falta la capacidad, informa la limitación real. Si no hay carpeta o biblioteca
autorizada, indica que el usuario debe configurarla desde la interfaz de IRIS
Player. No abras, recorras ni indexes rutas por tu cuenta.

## Mapeo de intenciones

| Petición | Acción |
| --- | --- |
| Reanudar o reproducir la selección actual | `play` |
| Pausar | `pause` |
| Siguiente pista | `next` |
| Pista anterior o reinicio según el estado de la app | `previous` |
| Ajustar el volumen | `set_volume` |
| Reproducir una canción, artista o álbum descrito | `play_by_query` |
| Elegir música por ambiente, actividad o energía | `play_by_intent` |

Usa búsqueda por texto solo dentro de la biblioteca autorizada. Un texto que
parezca una ruta no se convierte en acceso directo: pide al usuario agregar esa
ubicación desde la interfaz o trátala como consulta solo si lo confirma.

`play` sin `track_id` significa exclusivamente reanudar la selección actual. Si
el usuario menciona un título, artista o álbum —por ejemplo, “reproduce
Believer”— usa siempre `play_by_query` con ese texto; no reutilices la pista
actual.

## Resultados y ambigüedad

- Espera la respuesta de la aplicacion antes de afirmar que algo se reprodujo.
- Con `status: ok` y `playback_state: playing`, confirma al usuario qué canción
  comenzó a reproducirse usando el título devuelto por la aplicación.
- Si la respuesta indica que la orden fue recibida pero no confirmada a tiempo,
  comunica la incertidumbre y no reintentes automáticamente.
- Si la respuesta requiere elegir entre coincidencias, presenta las opciones
  devueltas y reutiliza únicamente el ID seleccionado.
- Conserva la atribución que informe la aplicación. `manual` significa que eligió
  el usuario; no lo reformules como una elección o recomendación de IRIS.
- Explica una selección de IRIS solo si el resultado declara un origen de
  intención. No inventes el motivo: usa el motivo devuelto, si existe.

## Fuera de alcance en esta versión

No hay acciones para seleccionar carpetas, escanear rutas, modificar archivos,
descargar música, reproducir URLs, conectar servicios externos, editar playlists
ni borrar datos. No las improvises mediante terminal, navegador u otra API.

Estas operaciones podrán incorporarse solo cuando IRIS OS anuncie la capacidad,
defina validación y permisos, y solicite confirmación cuando corresponda.
