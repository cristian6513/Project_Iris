# Modo juego en el Companion: plan

Claude, 2026-11-21; implementación por Codex el 21–22 de noviembre, autorizada por el usuario. Las fases 1–3 están implementadas en la fuente, con pruebas Python, JavaScript, Rust y Edge con vídeo sintético. La fase 0 con un juego y el EXE real sigue pendiente: estas comprobaciones no la sustituyen. QA27 no incluye estas fases.

## Objetivo

Que Iris acompañe al usuario mientras juega a **juegos de fuera de IRIS** (por ejemplo, Wuthering Waves o Clair Obscur: Expedition 33):
- mira la ventana del juego;
- responde por voz cuando le preguntan;
- si el usuario quiere, comenta sola de vez en cuando.

Vive en el **Companion**, que acompaña al usuario fuera de la habitación.

**Fuera del alcance:**
- **Shooters y reacciones al instante:** los modelos no llegan a tiempo.
- **Leer la memoria del juego, inyectar código o mandar teclas o clics:** solo se mira la imagen, como al compartir pantalla.
- **Obligar a instalar un modelo local:** se usa el proveedor que el usuario ya tenga, si admite imágenes.

**Dónde encaja mejor:**
- Juegos por turnos o con pausas, como Expedition 33: hay tiempo para mirar y aconsejar en el turno del jugador.
- En juegos de acción, como WuWa, sirve para equipos, ecos, puzles, exploración, jefes entre intentos e historia.

## Lo que hay hoy (comprobado en el código)

**El Companion:**
- Es una ventana sin marco y transparente. «Siempre encima» viene activado por defecto, pero se puede desactivar (`companion_always_on_top`, aplicado también por `face/companion/companion.js`). Comprobarlo antes de la prueba del juego.
- Ya sabe pedir capturas (`face/companion/companion.js` usa `ScreenSnapshotRequests`).

**La captura actual no sirve en plena partida** (`face/js/screen_snapshot.js`):
- Cada vez pide un clic en un aviso de IRIS, el selector de Windows y, salvo con «Permitir», una revisión de la imagen.
- Luego cierra la transmisión.
- Se cancela si la página queda oculta.
- Está hecho así a propósito: «La elección de pantalla siempre la inicia un clic del usuario».

**Los permisos actuales lo dicen:**
- `screen.snapshot` aclara «No autoriza … captura continua».
- `screen.ai_request` permite una captura por turno que Iris decide pedir (`core/capability_permissions.py`).

**Voz y palabra de activación:** solo funcionan con una superficie local visible, la habitación o el Companion (`_visible_voice_owner` en `core/websocket_server.py`).

**El análisis:** una captura va al modelo de visión configurado, por la ruta de `core/vision_model.py`. Esa ruta nunca salta a otro proveedor (`core/hermes_vision_worker.py`).

**Conclusión:** hace falta una **sesión de captura propia del modo juego**, con su permiso. El resto (voz, palabra de activación, modelo de visión y Companion) ya existe.

## Qué hacen otros (noviembre de 2026)

| Producto | Cómo funciona | Código |
|---|---|---|
| Tencent E-CY (Eciyuan) | La prensa describe una beta china de acompañamiento visual por voz. No se pudo consultar la web oficial en esta revisión; modelo, privacidad y compatibilidad quedan sin verificar. | Sin código comprobado en esta revisión |
| Razer AVA | Razer describe «PC Vision Mode», un compañero con dispositivo propio y una arquitectura prevista para admitir otras plataformas de IA. No se presupone exclusividad de Grok ni una fecha de entrega. | Producto comercial |
| Microsoft Gaming Copilot | Microsoft documenta voz, capturas y asistencia proactiva en Game Bar. No demuestra que capture únicamente al pulsar una tecla. | Producto comercial |
| NVIDIA G-Assist | Repositorio oficial de plugins y SDK para integración con el PC. La existencia del SDK no prueba observación de juegos genéricos. | Plugins y SDK abiertos |
| AIRI (moeru-ai/airi) | Avatar, voz y varios proveedores. Minecraft usa Mineflayer para obtener contexto y ejecutar acciones; anuncian migración a Fabric. No es un ejemplo exclusivamente de captura y visión. | Licencia principal MIT; comprobar cada componente y recurso antes de reutilizar |

Estas soluciones no comparten necesariamente arquitectura ni alcance. Para IRIS se elige observar píxeles y responder, sin controlar el juego. Referencias oficiales: [AIRI y proveedores](https://github.com/moeru-ai/airi), [Minecraft](https://airi.moeru.ai/docs/en/docs/integrations/minecraft), [licencia MIT](https://github.com/moeru-ai/airi/blob/main/LICENSE), [Razer AVA](https://www.razer.com/razer-ava), [Microsoft](https://blogs.windows.com/windowsexperience/2025/08/06/gaming-copilot-beta-available-on-game-bar-for-windows-pc-for-xbox-insiders/), [NVIDIA](https://github.com/NVIDIA/g-assist).

**La combinación que ofrece IRIS (sin afirmar exclusividad de estas características):**
- usa el proveedor del usuario, no uno atado a un modelo o un aparato;
- funciona en español;
- Iris ya tiene avatar, voz, habitación y Companion.

## Diseño

### 1. Sesión de juego

1. En el Companion local: **Modo juego → Elegir ventana del juego**. Core comprueba permisos y muestra el proveedor/modelo de visión real, que puede diferir del chat. El usuario confirma y abre el selector. La API exige una elección nueva cada vez que se abre la transmisión; no por cada fotograma mientras siga abierta. Solo se acepta una ventana, no monitor completo ni pestaña.
2. La transmisión **queda abierta mientras el modo está activo**, y de ella salen los fotogramas. Mientras dura:
   - el Companion muestra un indicador claro, por ejemplo «Mirando: Wuthering Waves» y un ojo;
   - Windows muestra su propio aviso de que se comparte.
   - Se solicita al capturador una cadencia local de 2 fps, máximo 5, para reducir trabajo innecesario. Esto no son envíos a la IA: solo sale un fotograma al preguntar o en un comentario autorizado. La cadencia efectiva y el impacto en FPS deben medirse en WebView2.
3. **Se termina:**
   - con «Salir del modo juego»;
   - con el atajo de detener;
   - al cerrar la ventana del juego o perder la transmisión;
   - al cerrar el Companion;
   - al ocultarlo, perder conexión con Core o dejar de estar disponible la ventana capturada;
   - al cambiar de conversación, perfil, destino de visión o los permisos de captura/modo juego;
   - al pasar 3 horas, para no olvidarla abierta.

   Volver a empezar exige elegir la ventana otra vez.
4. **No se graba vídeo ni se mantiene un archivo de capturas de la sesión.** Se solicita un fotograma fresco al preguntar o cuando toca un comentario autorizado, reducido a 1280 px. La ruta actual `vision_model.analyze` usa imágenes temporales en la caché de Hermes y las elimina al terminar o cancelar. Un cierre forzado o fallo del equipo puede impedir esa limpieza; no se promete procesamiento íntegramente en RAM. La autorización lo explica. Texto y respuestas siguen las políticas del historial/proveedor; el comentarista no crea turnos de usuario ficticios.
5. **Nombre del juego:** se toma del título de la ventana elegida, si el sistema lo da. Si no, el usuario lo escribe una vez. Se usa en las preguntas al modelo.

### 2. Dos maneras de ayudar (Codex)

**A. «Ayudar cuando pregunto»** (por defecto):
- El usuario pregunta con la palabra de activación, un atajo de pulsar para hablar o el chat del Companion.
- IRIS toma el último fotograma, lo analiza y responde **por voz con una o dos frases**, y en un globo del Companion.
- Debe ser un fotograma nuevo solicitado para esa pregunta, no una imagen guardada. Con la voz silenciada, la pregunta puede seguir recibiendo texto sin TTS. No se abre otra conversación.

**B. «Comentar automáticamente»** (opcional, apagado al empezar):
- Cada cierto tiempo (por defecto 45 s; ajustable de 20 s a 5 min) mira un fotograma, solo si Iris está libre. Espera el intervalo completo tras terminar un comentario o una pregunta; no recupera ciclos perdidos. El planificador comprueba aproximadamente cada 2 s.
- Comenta solo si tiene algo útil o simpático. **Puede quedarse callada:** el modelo de visión devuelve JSON con `speak: false`. Salidas mal formadas y comentarios idénticos al anterior tampoco se leen. El análisis ya solicitado cuenta igualmente.
- **Hay una sola consulta en curso**, ligada a sesión, tarea y generación. Cambiar de juego, conversación, destino o cancelar invalida su resultado. Límite: 15 s para obtener el fotograma y 30 s desde su recepción para análisis/voz automáticos; una respuesta fuera de plazo se descarta. A petición conserva el límite de 90 s del turno.
- Una pregunta del usuario siempre tiene prioridad: cancela el comentario antes de esperar el bloqueo del chat. También cede al empezar la captura de voz. No analiza durante conversación continua por voz, otra respuesta, síntesis de voz ni cuando otro dispositivo tiene el audio.
- Usa directamente visión y la voz existentes, sin herramientas, búsquedas, acciones sobre el juego ni turnos ficticios en Hermes. La fase 3 añade objetivo, notas y contexto reciente temporal, sin llamar a otro modelo para resumir.
- Un error de captura, análisis o voz pausa B para evitar reintentos que sigan consumiendo cuota. Se reactiva explícitamente desde el panel.

**Advertencia al activar B**, con el texto del usuario:

> «El modo comentarista mira tu pantalla cada pocos segundos y envía cada captura a tu proveedor de IA. Puede gastar tus tokens o tu límite de uso mucho más rápido de lo normal.»

La voz también puede consumir cuota, según el servicio (por ejemplo, Fish Audio).

### 3. Controles pensados para jugar

- **Sin spoilers**, activado por defecto: se pide al modelo evitar adelantos no solicitados. Es una preferencia, no una garantía absoluta.
- **Frecuencia** del comentarista e **intervalo**.
- **Contador de intentos de análisis** visible. Aumenta cuando se despacha el trabajo a la ruta de visión, incluidos intentos fallidos; no mide dinero ni confirma qué cobró el proveedor. Límite de sesión para B: 100 por defecto, de 0 a 1000 (0 sin límite). Incluye también las preguntas manuales; al alcanzarlo pausa B y A sigue disponible. Puede ampliarse, pero no se pone el contador a cero. A 45 s hay hasta 80 análisis automáticos por hora, menos el tiempo de trabajo/pausas. Que Iris calle no evita el análisis ya solicitado.
- **Silenciar voz:** corta el turno/voz en curso y evita TTS en las siguientes respuestas del modo A; el texto sigue disponible. También pausa B, que no se reactiva al volver a habilitar voz. **Salir del modo juego** libera además la transmisión e invalida las operaciones pendientes. Silenciar y Salir permanecen visibles en Solo avatar; la fase 3 añade atajos opcionales en Windows. Ninguna respuesta cancelada puede sonar después de reanudar.
- **Estilo:** «Consejos» o «Charla».

### 4. Lo que se le pide al modelo

Una instrucción corta y fija de IRIS, con:
- el nombre del juego;
- «Sin spoilers» si está activo;
- la pregunta del usuario, o «¿hay algo útil o divertido que decir?» en el comentarista;
- el fotograma.

Responde en español, en una o dos frases, sin inventar lo que no se ve, y «nada» si no aporta.

En juegos nuevos o que se actualizan mucho, Iris puede buscar en internet si el usuario lo pide. Hermes ya tiene la herramienta web.

### 5. Permiso nuevo en Permisos

`screen.game_session`, «Modo juego: mirar la ventana de un juego mientras está activo»:
- riesgo alto, «Preguntar» por defecto;
- la nota explica que la transmisión sigue abierta mientras dura el modo y que el comentarista envía capturas periódicas.

No hereda autorización de `screen.snapshot` o `screen.ai_request`: permitir una foto no autoriza una sesión. Bloquear `screen.snapshot` también bloquea el modo juego; permitirla no lo activa. La confirmación de inicio es por sesión, incluso en «Permitir», para identificar destino y ventana. Core vuelve a comprobar dueño local, conversación, permiso y destino antes de enviar imágenes y antes de entregar la respuesta. Revocar o cambiar estas políticas detiene la sesión pendiente/activa. Telegram, LAN, apps y automatizaciones no pueden iniciarla.

## Fases

**Fase 0. Prueba en el EXE (sin código nuevo).** Con QA27 y un juego en ventana sin bordes, comprobar:
- si el Companion visible sobre el juego basta para que funcionen la palabra de activación, el micrófono y la voz con el juego en primer plano;
- cuánto tarda hoy una respuesta con una captura manual (que sí roba el foco), para tener una medida real;
- que la partida no se resiente (FPS) con el Companion encima;
- qué pasa en pantalla completa exclusiva.

No comprueba captura continua en segundo plano: la captura de QA27 se cierra tras una imagen. Esa prueba requiere el prototipo de fase 1. Anotar fecha, versión/hash del EXE, juego, modo de ventana, proveedor/modelo de chat y visión, servicio de voz, FPS sin/con Companion y demora hasta la primera respuesta audible. No declarar aceptación sin ese registro.

**Fase 1. Sesión de captura y modo A.**
- Construir la sesión de juego en el Companion, el permiso `screen.game_session`, el análisis del último fotograma y la respuesta por voz.
- Pruebas automáticas.
- Revisión de Codex.
- Prueba del usuario con Expedition 33 y WuWa.
- Implementación autorizada a Codex el 21 de noviembre. Fuentes nuevas: `core/game_session.py` y `face/js/game_capture.js`; integración con turnos, visión y permisos existentes. Pasan 367 pruebas Python relacionadas, 1.625 JavaScript (22 omitidas) y ocho pasos de UI en Edge con vídeo sintético. Son 28 pruebas Python y 11 JS nuevas. EXE, proveedor/voz y juegos reales pendientes. Informe de implementación y validación (informe interno no distribuido).

**Fase 2. Modo B (comentarista): implementada en fuente, sin compilar.** Intervalo, estilo, una sola consulta, silencio, descarte de respuestas viejas, contador, límite, consentimiento de cuota y Silenciar en Solo avatar. El nuevo `core/game_commentary.py` coordina el planificador con chat, permisos, visión y voz. Véase evidencia de fase 2 (informe interno no distribuido). EXE, juegos, proveedor y voz reales siguen pendientes.

**Fase 3. Pulido: implementada en fuente el 22 de noviembre, sin instalador nuevo.**
- Atajos Windows opcionales: pedir ayuda `Ctrl+Alt+F8`, silenciar `Ctrl+Alt+F9` y salir `Ctrl+Alt+F10`, configurables. No capturan teclas generales ni abren el micro. Solo se registran con sesión activa; se liberan al salir/ocultar/desconectar. Las combinaciones ocupadas muestran error. Se recuerdan los nombres de teclas, pero no se restaura su activación al reiniciar Companion.
- Globo sobre el avatar, texto desplazable, cierre accesible y tiempo de lectura de 6–25 s después de hablar. Reserva espacio del renderizador para no cubrir la cara; permanece en Solo avatar durante el juego.
- Objetivo (240 caracteres), notas (800) y hasta seis entradas recientes acotadas en RAM, tratadas como datos no verificados. Se borran al salir o con Borrar memoria; no borra historial normal ni datos del proveedor. Cambiar/borrar contexto cancela resultados anteriores que podrían reponerlo.
- Se contrastaron captura y tareas de visión de AIRI como referencia. Se conserva la captura propia de IRIS: no se copió código ni añadió Electron/Vue. Fuentes y decisión en el informe de fase 3 (informe interno no distribuido).
- [Guía general de pruebas](qa/iris_pruebas_funcionales_2026-11-22.html): 115 casos y 20 rápidos. Falta la validación nativa con juego/proveedor/voz reales.

## Riesgos y preguntas abiertas

- **Antitrampas:** mirar la ventana es lo mismo que compartirla para un stream, pero no está demostrado con todos los juegos. Hay que probarlo juego por juego y decirlo con prudencia.
- **Pantalla completa exclusiva:** puede que no se vea el Companion, y la captura puede salir negra. Se recomendará ventana sin bordes.
- **Ventana de IRIS oculta:** un Companion sin foco no equivale necesariamente a oculto. La fase 0 mide voz/visibilidad y la fase 1 debe probar la transmisión en WebView2 con el juego delante. Ocultar realmente Companion termina la sesión por diseño.
- **Demora:** depende del proveedor y de la voz. Se medirá; no se promete una cifra.
- **Coste:** depende del modelo y del intervalo. Por eso hay contador, límite y advertencia.
- **Conocimiento del juego:** los modelos pueden no conocer contenido reciente. Iris lo dice y, si el usuario quiere, busca.
- **Privacidad:** la ventana elegida puede mostrar chats o datos personales del juego. Solo se envía el fotograma solicitado al destino de visión autorizado. No se graba vídeo. Hay temporales de análisis y puede persistir texto en el historial; no se promete ausencia de almacenamiento en el proveedor.
- **Audio:** la voz de Iris se mezcla con la del juego. Se recomienda bajar un poco la música del juego; no se toca su audio.

## Quién hace qué

- **Codex:** fases 1–3 implementadas en fuente; pruebas y evidencia registradas. Preparar un candidato solo cuando se solicite.
- **Revisión independiente:** contrastar los cambios y las pruebas antes de preparar un candidato.
- **Usuario:** la fase 0 con sus juegos, decidir los valores por defecto (intervalo, límite, estilo) y aprobar los textos.

## Cuándo está bien hecho

- Tras la elección inicial, las preguntas no abren selectores ni diálogos que roben el foco. Comparar FPS medio y tiempos de cuadro con/sin Companion en la misma escena; acordar el umbral con el hardware medido antes de afirmar que no hay impacto notable.
- Una pregunta por voz recibe una respuesta útil en un tiempo razonable, que se medirá en la fase 0.
- Silenciar y salir detienen voz/trabajo pendiente. Una respuesta de una sesión anterior no aparece ni suena al reanudar. La instrucción sin spoilers se aplica, sin prometer infalibilidad del modelo.
- El contador refleja cada análisis enviado.
