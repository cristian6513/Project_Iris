# Cámara y asistencia visual

[← Índice](../README.md) · [Estado por candidato](../estado-actual.md)

Revisión: 2026-10-16. Los controles F03 y sus arreglos deben contrastarse con el
candidato instalado; recargar Face no reconstruye el ejecutable del Companion.

El acceso principal a Cámara abre la vista. Si la cámara ya está encendida,
volver a pulsarlo no la apaga ni desactiva Vision Assistant.

Cerrar el panel, cambiar de sección o cerrar Iris OS oculta la vista previa y
conserva la captura dentro de la página visible. Al volver, la vista usa el mismo
flujo y reanuda la reproducción. El indicador de cámara permanece activo.
El botón **Apagar cámara** del panel principal cierra la captura y desactiva el
asistente de visión. **Cerrar vista** en Iris OS libera únicamente el uso de esa
vista; el asistente puede seguir usando la cámara si está activo.

Ocultar la pestaña del navegador, perder el dispositivo o revocar el permiso
continúa deteniendo la captura. Ninguna solicitud cancelada puede abrir una
cámara después ni sustituir una captura más reciente.

El modo Espejo se retiró de Face y Companion: ya no se copian la cabeza, los
parpadeos ni las expresiones del usuario al avatar. La percepción local para
Vision Assistant y las animaciones propias de Iris siguen disponibles.

## Mirar una foto cuando lo pides

Puedes pedir «¿qué tengo en la mano?» o usar la acción de cámara del chat. Iris
solicita **una foto nueva para ese turno**, no una observación continua.
Antes de capturar muestra el destino de análisis y pide el permiso de foto a
petición, salvo que hayas recordado expresamente ese mismo destino.

En fuente posterior a QA16, la confirmación usa el diálogo común de Iris: foco
de teclado dentro del diálogo, Escape para cancelar y una casilla inicialmente
vacía para recordar el destino cuando Core lo permite. Después de confirmar
permanece un indicador cancelable durante la preparación/captura.
Una pregunta que menciona pantalla o escritorio ya no se interpreta por sí sola
como una solicitud de cámara; sigue el flujo normal del chat. La acción explícita
de foto del compositor conserva su origen de cámara.

La foto usa el modelo/ruta de **Modelos → Visión**. Puede ser un proveedor
remoto: que la captura se haga en tu PC no significa que el análisis ocurra allí.
El chat recibe la descripción correspondiente al flujo de foto; no se concede
al modelo acceso continuo a la cámara. Un error de permiso, dispositivo o modelo
debe mostrarse sin inventar qué había en la imagen.

El permiso para capturar (`camera.capture`), el contexto perceptivo
(`vision.context`) y la foto (`vision.snapshot`) son independientes. Puedes
cancelar antes de tomarla. La configuración y sus límites están en
[Modelos](models.md) y [Privacidad](privacy-security.md).

## Compartir una captura de pantalla

En fuente posterior a QA16 puedes pulsar **Compartir una captura de pantalla**
en el chat o pedir «Mira mi pantalla y ayúdame con este error». No usa la cámara.
Preguntas como «¿Cómo comparto pantalla en Zoom?» o «¿Qué monitor me recomiendas?»
siguen directamente al chat. Mencionar una pantalla o un escritorio no solicita
por sí solo una captura.

1. Iris indica qué modelo de **Modelos → Visión** analizará la imagen.
2. Pulsa **Elegir ventana o pantalla** y selecciona el origen en el selector del
   navegador o de Windows. No se solicita audio del sistema.
3. El flujo de vídeo se cierra después de obtener una imagen. Revisa la vista
   previa y pulsa **Enviar esta captura** o **Cancelar**.

El permiso **Capturas de pantalla a petición**, en **Permisos**, es independiente
de cámara y **Control del equipo**. Incluso en «Permitir» eliges el origen y
apruebas la imagen en cada petición. Compartirla no autoriza mover el ratón ni
escribir; no se inicia una observación continua. Iris no guarda la captura como
archivo, pero el proveedor de visión elegido puede procesarla fuera del equipo.

Si cancelas antes de enviarla, se revoca el permiso o cambia la conversación
mientras se prepara, no se usa esa captura.
Si no se comparte una imagen (por cancelación, permiso bloqueado o falta de modelo
de visión), Iris conserva la pregunta y responde por texto, dejando claro que no
vio la pantalla. Un turno sustituido o un cambio de perfil sí cancela esa respuesta
para que la pregunta anterior no pase a otra conversación.
Solo se solicita desde el chat o la voz locales; una conexión LAN no puede pedir
capturas de este PC. Si la vista no dispone de la API de compartir pantalla,
Iris lo explica y puedes adjuntar una captura manualmente. La prueba del selector
en el ejecutable WebView2 queda pendiente del próximo instalador; las pruebas de
interfaz usan una pantalla sintética en Edge.

## Cuando Iris decide mirar

Las palabras clave solo reconocen peticiones claras y leen el principio del
mensaje. En fuente posterior a QA16, Iris también puede **pedir ella misma** una
foto de la cámara o una captura de pantalla cuando la necesita para responder.
Por ejemplo: un texto largo que termina con «¿qué opinas de lo que tengo
abierto?», o «¿se ve bien mi peinado?».

- Core ofrece a cada turno local, de chat o de voz en este equipo, un código de
  un solo uso. Iris lo usa con la skill `iris-os` (`scripts/visual_request.py`).
  Telegram, las automatizaciones, las tareas programadas y las conexiones LAN no
  reciben código, así que no pueden pedir imágenes de este PC.
- Una imagen por turno, compartida con el camino de palabras clave y botones. Si
  la cancelas, Iris no vuelve a intentarlo en ese turno y responde por texto.
- El diálogo lo escribe Iris OS: «Iris pide ver tu cámara» o «Iris pide ver tu
  pantalla», con tu mensaje y el modelo de visión que analizará la imagen.
- Iris recibe la descripción del modelo de visión, nunca la imagen, y responde
  en el mismo turno.

Hay dos permisos propios en **Permisos**, inicialmente en «Preguntar». Permitir
tus propias fotos o capturas no los concede:

| Permiso | Preguntar | Permitir |
|---|---|---|
| **Fotos que Iris decide pedir** | Aviso de Iris OS antes de cada foto | Foto sin aviso si la cámara ya está encendida; apagada, siempre hace falta tu clic |
| **Capturas que Iris decide pedir** | Eliges ventana o pantalla y revisas la imagen | Eliges ventana o pantalla con un clic, que Windows siempre exige, y se envía sin revisión |

Bloquear cualquiera de los dos impide que Iris lo pida. Bloquear **Fotos a
petición** o **Capturas de pantalla a petición** también lo impide. Cada vez que
Iris mira, el chat muestra una línea, por ejemplo «Iris tomó una foto con la cámara
sin preguntarte, porque lo permitiste en Permisos, para responderte; la analizó
gpt-5.5 (openai-codex)». Cambiar un permiso mientras se elige o se toma la imagen
la descarta.

Algunos modelos de chat usan mal las herramientas; con ellos, los botones de
cámara y pantalla del chat siguen funcionando igual.

## Carga del análisis local

La imagen de la cámara se muestra independientemente de la preparación del
detector. Los estados de carga o error del análisis no apagan el vídeo ni cambian
la preferencia del asistente. El modelo se conserva en memoria al cerrar una
vista para evitar inicializarlo de nuevo.

Face puede reutilizar los recursos de visión que ya instala Companion:

```console
node scripts/prepare_face_vision.mjs
```

Requiere las dependencias de `companion/node_modules` y el modelo existente en
`companion/vendor/mediapipe/`. Copia el motor fijado en 0.10.35 y el modelo
verificado a `face/vendor/mediapipe/`, sin descargar archivos. Este directorio es
generado y se incluye al empaquetar Face si fue preparado. Una instalación sin
esos recursos, desde fuente, puede conservar la carga remota fijada a la misma
versión. Los candidatos A08 usan la exportación web verificada con recursos
locales; una vista previa sin preparar no acredita ese comportamiento offline.
Companion utiliza su propio paquete local.

## Pruebas

`tests/js/camera_lifecycle.test.mjs` cubre las solicitudes concurrentes,
cancelaciones, reaperturas y respuestas de configuración atrasadas.
`scripts/verify_camera_lifecycle.mjs` usa la interfaz real, un MediaStream
generado y MediaPipe local para verificar la reproducción y la reapertura sin
abrir dispositivos físicos. Sus informes quedan en `artifacts/camera-lifecycle/`.
