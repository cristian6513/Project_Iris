# IRIS Player

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

[← Índice de documentación](../README.md)

IRIS Player es la aplicación de música local de IRIS OS. Su primera versión
funcional reproduce archivos de una carpeta elegida explícitamente por el
usuario y continúa funcionando aunque Hermes esté desconectado.

## Flujo local

1. Abre **IRIS OS → Player**.
2. Pulsa **Seleccionar carpeta**. Si el selector nativo no está disponible,
   abre **Ruta manual avanzada** y escribe una ruta absoluta.
3. IRIS indexa únicamente esa carpeta y sus subcarpetas.
4. Busca, reproduce, pausa, cambia de canción, ajusta volumen y marca favoritos
   desde la ventana del Player.

## Widget de Inicio y ejemplo para la comunidad

En la fuente del 2026-10-18 puedes añadir **Widgets → Añadir IRIS Player** o
**opciones de IRIS Player → Agregar widget**. Muestra la pista, el progreso y
anterior/reproducir/pausar/siguiente. **Abrir Player** abre su ventana existente.
La ventana, el widget y la skill de Hermes controlan el mismo audio: cerrar la
ventana o quitar el widget no corta la música. Añadirlo no la inicia ni elige
una carpeta; sin biblioteca, te indica que la selecciones desde Player.

El widget se guarda con las preferencias de tu perfil. No sincroniza la
reproducción con otras pestañas o equipos, no requiere una cuenta externa y no
necesita Hermes para los controles manuales. Está incluido en QA16; la QA
instalada conserva el alcance del [candidato](../estado-actual.md).

Para aportar una app, el [ejemplo de Player](../../apps/player/README.md) enlaza
su ficha, widget, backend, skill y pruebas, y explica qué se puede reutilizar y
qué conserva del adaptador anterior al kit F09. La [Store sencilla](iris-store.md)
permite instalar apps de interfaz y widgets, y [SDK 2](community-apps-python.md)
añade backend Python y skill opcional por su contrato externo, incluidos en
QA15/QA16. Los servicios integrados dentro de Core conservan la revisión de F09.

La carpeta autorizada y el catálogo privado se guardan en
`~/.iris/player/library.json`. La API web no entrega la ruta absoluta ni la ruta
relativa de los archivos: el navegador recibe IDs opacos y URLs acotadas de
streaming.

## Privacidad y permisos

La [tablet de la habitación](room.md#la-habitación-y-el-player) muestra el estado
real de este Player y su pista actual. Es una vista pasiva del mismo elemento de
audio, sin reproducción automática, acceso a otra carpeta ni sincronización
entre pestañas. No sustituye los controles de la aplicación.

Si no hay preferencia de volumen guardada se utiliza el 80 %. Un volumen cero
elegido y guardado por el usuario se respeta al volver a abrir IRIS.

- IRIS no recorre discos ni carpetas que el usuario no haya seleccionado.
- El catálogo, las acciones y el audio solo se sirven a clientes loopback.
- El acceso LAN no concede control de IRIS Player. Core fuerza esa capacidad a
  `false` tanto al registrar la superficie como en cada actualización posterior.
- No se sube audio a Hermes ni a un proveedor de IA.
- Cambiar la carpeta continúa siendo una acción visible de la interfaz; no está
  autorizada por la skill.
- Los enlaces simbólicos y las pistas que escapan de la carpeta autorizada se
  rechazan al indexar y al reproducir.

## Control mediante Hermes

El repositorio incluye la fuente de la skill `.hermes/skills/iris-os`. Al guardar
el wizard, IRIS la sincroniza de forma atómica con el perfil activo de Hermes y
solo actualiza una copia administrada por IRIS; una carpeta ajena con el mismo
nombre se conserva y produce una advertencia. La copia también forma parte del
wheel en `share/iris-os/hermes-skills/iris-os`.

En la validación local del 2026-08-26, Hermes 0.20.1 mostró `iris-os` con origen
`local`, confianza `local` y estado `enabled`. El adaptador prioriza el puerto
efectivo `IRIS_WEBSOCKET_PORT`, después `~/.iris/config.json`, y solo llama a
loopback.

La skill usa un adaptador local y el contrato `iris.app-intent/v1`. Core valida
la acción, elige una sola superficie que anuncie `controls_local_player`, espera
su respuesta y devuelve `iris.app-result/v1`. Si IRIS OS está cerrado, no hay
biblioteca o el navegador bloquea el audio automático, el resultado lo indica y
Hermes no debe simular éxito.

Acciones v1: reproducir o reanudar, pausar, siguiente, anterior, volumen,
búsqueda dentro de la biblioteca e intención musical local. Seleccionar
carpetas, borrar, descargar música, abrir URLs y conectar servicios externos no
forman parte del contrato.

## Correcciones verificadas de la segunda auditoría

- la búsqueda actualiza únicamente la lista y conserva el mismo campo, foco y
  posición del cursor durante escritura secuencial;
- cambiar carpeta detiene y limpia el audio anterior; refrescar conserva una
  pista sobreviviente y recarga su fuente, o detiene la reproducción si desaparece;
- cola, lista y controles de carpeta siguen disponibles en diseños de 900 y
  640 px mediante distribución apilada y desplazamiento;
- una superficie LAN no puede recuperar `controls_local_player` con
  `surface_state`;
- las cargas concurrentes comparten la misma promesa y los avisos se muestran
  inmediatamente;
- `play_by_intent` pondera las palabras de la petición contra metadatos locales
  disponibles, en vez de ignorar el texto.

El cierre técnico está registrado en
[BUG-030 del documento maestro](../iris_os_master.md#bug-030-iris-player-tiene-brechas-funcionales-y-de-integración-tras-la-primera-vertical).

## Formatos y límites actuales

El indexador reconoce AAC, AIFF, ALAC, FLAC, M4A, MP3, OGA/OGG, Opus, WAV y
WMA. La reproducción real depende de los códecs que admita el navegador o
WebView del equipo; que una extensión aparezca en el catálogo no garantiza que
ese navegador pueda decodificarla.

Las acciones solicitadas por Hermes esperan una confirmación real del Player.
Core concede hasta 20 segundos para que el navegador cargue el códec y confirme
el estado `playing`; el adaptador mantiene un margen adicional para entregar ese
resultado a Hermes. Si el plazo se agota, IRIS avisa que la orden pudo comenzar
pero no fue confirmada y evita recomendar un reintento automático.

La superficie no depende únicamente de que termine la promesa devuelta por
`HTMLMediaElement.play()`: algunos WebView pueden comenzar el audio y mantener
esa promesa pendiente. IRIS confirma también el evento observable `playing` y
usa un límite interno, de modo que una reproducción real no se reporta como
timeout. El contrato devuelve `playback_state` para que Hermes comunique el
estado comprobado, no una suposición.

Esta primera entrega todavía no extrae etiquetas ID3, duración, carátulas ni
géneros; utiliza el nombre del archivo como título. Tampoco incluye playlists
editables, ecualizador, letras, Spotify/Apple Music ni sincronización en nube.
Las recomendaciones son deterministas y locales, basadas en favoritos e
historial, y muestran una razón sin afirmar que se analizó el contenido de
audio. Aún no existe un límite explícito de pistas, por lo que conviene no
seleccionar la raíz de un disco o una biblioteca extraordinariamente grande.

## Evolución planificada

IRIS Player no se considera terminado: su primera vertical es funcional y la
iniciativa P2-022 organiza la evolución sin convertirla en un cliente cloud ni
debilitar el aislamiento local.

1. **Robustez de biblioteca:** límite configurable, indexación incremental y
   cancelable, detección de cambios/duplicados, recuperación de catálogo,
   reproducción prolongada y matriz real de códecs por navegador/WebView.
2. **Biblioteca enriquecida:** tags, duración, carátula embebida o sidecar,
   orden y filtros, playlists editables y cola persistente.
3. **Integración de sistema:** Media Session y controles globales, avisos en
   Notification Center y acciones Hermes versionadas cuyo resultado siempre
   proceda del estado observable del reproductor.

Ecualizador, letras y proveedores externos permanecen fuera del núcleo hasta
definir permisos, licencias, autenticación y un contrato de privacidad propio.
La hoja de ruta detallada se conserva en
[P2-022 del documento maestro](../iris_os_master.md#89-iniciativas-aceptadas-pendientes-de-diseño-2026-08-22-a-2026-09-01).

## Archivos principales

| Área | Archivo |
| --- | --- |
| Biblioteca y persistencia | `core/iris_player.py` |
| API, streaming y acciones | `core/websocket_server.py` |
| Estado puro de UI | `face/js/iris_player_state.js` |
| Controlador del reproductor | `face/js/iris_player.js` |
| Diseño | `face/css/iris_player.css` |
| Skill Hermes | `.hermes/skills/iris-os/` |

## Pruebas focalizadas

```bash
uv run pytest tests/test_iris_player.py tests/test_iris_player_api.py tests/test_iris_player_shell.py tests/test_iris_skill_lifecycle.py tests/test_surface_registry.py -q
node --test tests/js/iris_player_state.test.mjs
```
