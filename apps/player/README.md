# IRIS Player: ejemplo de app local, widget y skill

La ficha también ejemplifica `widgets[].preview`: la galería dibuja una pista y
controles ficticios con `layout: "player"`, sin montar este widget ni reproducir
audio. Es el mismo contrato declarativo disponible para otras apps; véase
[Vista previa de widgets](../../docs/guides/widgets.md#vista-previa-declarativa).

Revisado el 2026-10-18. Implementado en fuente y exportación web; falta incluirlo
en el próximo candidato Windows y hacer su QA. No está en `qa11`.

Player es el ejemplo de una app que no necesita cuentas, OAuth ni conexiones
externas: reproduce la carpeta que la persona eligió. La ventana, el widget de
Inicio y las acciones de la skill usan **un solo reproductor**. Hermes puede
estar apagado y los controles manuales siguen funcionando.

Este es un ejemplo funcional de integración para la futura Iris Store, no un
paquete externo instalable. Para empezar una app nueva, copia
[`_plantilla`](../_plantilla/README.md) o
[`_plantilla-servicio`](../_plantilla-servicio/README.md). Player conserva su
adaptador anterior al kit F09: su motor/UI y backend aún viven en `face/` y
`core/`. No copies ese adaptador ni agregues otro motor de audio en una app nueva.

## Mapa del ejemplo

| Pieza | Fuente y función |
| --- | --- |
| Ficha | [`app.json`](app.json): identidad, ventana y declaración `player.transport`. No concede permisos. |
| Estado de dominio | [`iris_player_state.js`](../../face/js/iris_player_state.js): biblioteca proyectada, cola, filtros y selección. |
| Propietario del audio | [`IrisPlayerApp`](../../face/js/iris_player.js): un `<audio>`, carga local, comandos manuales y `handleIntent`. |
| Widget | [`ui/widget.js`](ui/widget.js) y [`ui/widget.css`](ui/widget.css): vista compacta sin audio ni polling propios. |
| Biblioteca local | [`core/iris_player.py`](../../core/iris_player.py): indexación/validación y catálogo privado. |
| API y acciones | [`core/websocket_server.py`](../../core/websocket_server.py): biblioteca, streaming loopback y envío a una sola superficie. |
| Skill operativa | [`iris-os/SKILL.md`](../../.hermes/skills/iris-os/SKILL.md), [`player.md`](../../.hermes/skills/iris-os/references/player.md), [`action-contracts.md`](../../.hermes/skills/iris-os/references/action-contracts.md) y [`control_player.py`](../../.hermes/skills/iris-os/scripts/control_player.py). |
| Pruebas de integración | [`tests/widget.test.mjs`](tests/widget.test.mjs): widget, controles reales del Player y handler usado por la skill. |

## Tres entradas, una operación

```text
Persona → ventana Player ─────────────┐
Persona → widget → servicio acotado ─┼→ IrisPlayerApp → mismo audio
Hermes → skill → Core → handleIntent ┘
```

El widget recibe `services.player` **solo porque el shell lo inyecta expresamente
a `player.transport`**. Un campo en `app.json` no solicita ni obtiene ese objeto.
Es un servicio interno revisado, no una API de plugins arbitrarios. Expone:

- `read()`: estado de carga, disponibilidad, pista, tiempo, error y reproducción
  observada. No incluye rutas, URLs de audio ni el catálogo completo.
- `subscribe(listener)`: recibe cambios; devuelve una función de retirada.
- `load()`: comparte la lectura local existente y la promesa que ya está en curso.
- `command(action)`: solo `play-pause`, `previous` y `next`; un comando pendiente
  bloquea otros comandos de widgets, sin reintentos automáticos.

El widget mantiene sus botones, incluso cuando cambia el tiempo o la pista.
Así una actualización no borra el foco ni el botón entre pulsar y soltar el ratón.
Solo se suscribe mientras está visible. Ocultarlo o retirarlo libera esa vista,
sin pausar el audio que pertenece al Player. No usa su propio temporizador.

La skill no pulsa el widget ni depende de que esté añadido: usa el contrato
existente `iris.app-intent/v1` y comunica el resultado validado por Core y la
superficie. No obtiene permisos nuevos por existir el widget. En una app nueva
con acciones, sigue `operations(context)` del kit y consulta el catálogo de Core;
la ruta específica de Player es una integración previa, no una excepción para
que los aportes se salten la validación.

## Qué debe conservar quien lo amplíe

- Añadir/restaurar el widget no reproduce música ni abre un selector de carpeta.
- Si no hay carpeta autorizada, indica que hay que elegirla desde Player.
- No hay login, llamadas cloud, descarga de canciones ni análisis de audio por IA.
- Los datos se insertan como texto. Los estados de vacío, lectura fallida,
  preparación de audio, pausa y reproducción se distinguen.
- Otra instancia del widget comparte el audio; no debe crear un `<audio>`, un
  backend, un catálogo o una sincronización nuevos.
- El estado compartido es el de esta superficie de IRIS OS. No sincroniza
  reproductores entre pestañas o equipos.
- El contenido de una carpeta no concede instrucciones ni permisos a la skill.
- Para una app nueva, guarda datos en `context.data_dir` y respeta la revisión de
  servicios/acciones. No copies la ruta histórica `~/.iris/player/`.

## Probar el ejemplo

Desde la raíz del proyecto:

```bash
node scripts/build_app_catalog.mjs
node scripts/build_app_catalog.mjs --check
node --test apps/player/tests/*.test.mjs tests/js/iris_player_state.test.mjs tests/js/player_playback_observer.test.mjs tests/js/os_widgets.test.mjs tests/js/widget_manifest.test.mjs
.venv/Scripts/python.exe -m pytest tests/test_iris_player.py tests/test_iris_player_api.py tests/test_iris_player_shell.py tests/test_iris_skill_lifecycle.py tests/test_desktop_widgets.py -q
```

Las pruebas JS usan un medio simulado, no música personal. La revisión visual
usa un perfil desechable y WAV sintético en silencio. En un candidato real hay
que comprobar codecs/WebView2, controles con teclado, reproducción al cerrar la
ventana, quitar y volver a añadir el widget y una orden real de Hermes ya
autorizada. El resultado de la skill no se da por aprobado solo por ver el widget.

[Uso de Player](../../docs/guides/iris-player.md) ·
[Declarar widgets](../../docs/guides/widgets.md) ·
[Aportar apps](../../docs/guides/contributing-apps.md) ·
Informe de esta entrega (informe interno no distribuido)
