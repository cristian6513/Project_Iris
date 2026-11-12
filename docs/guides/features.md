# Funcionalidades

[← Índice de documentación](../README.md)

IRIS OS no reemplaza a Hermes Agent. Organiza sus capacidades dentro de una
experiencia visual con identidad, voz, memoria y varias superficies.

Revisión documental: 2026-11-06. Esta guía describe la **fuente actual**, no una
release pública aprobada. Consulta [Estado actual](../estado-actual.md) para
distinguir QA22, la fuente posterior y las comprobaciones pendientes.

## Wizard y Hermes Doctor

- detección de SO, arquitectura, CPU, RAM y GPU;
- instalación nueva, recorrido rápido y reparación;
- estado separado para launcher, runtime, configuración y Gateway;
- catálogo actualizable de proveedores y modelos;
- comprobación de OAuth, API keys y endpoints locales;
- proveedor principal plegable y credenciales adicionales sin reemplazarlo;
- mensajería opcional y Gateway después de ella, condicionado a proveedor listo;
- diagnóstico redactado y acciones de reintento.

El wizard no instala Ollama, LM Studio ni modelos locales. Tampoco marca una
función como lista solo porque exista un archivo de configuración.

## IRIS OS

La interfaz principal incluye:

- escritorio y temas;
- centro de comandos;
- chat y actividad de herramientas;
- estado del agente;
- memoria e identidad;
- configuración rápida;
- Gateway Manager;
- Marketplace independiente para descubrir Skills y MCP;
- IRIS Player local por carpeta autorizada;
- Continuity Center para perfiles y sesiones nativos de Hermes;
- Capability & Permission Center para política local, toolsets, MCP y approvals;
- administración separada de Skills y MCP ya instalados;
- paleta de comandos;
- comandos slash compatibles con Hermes.

El registro F09 agrupa las apps propias y sus ciclos de carga/visibilidad;
`apps/` contiene los manifiestos y las plantillas. La [Iris Store](iris-store.md)
instala apps de interfaz y widgets. El [SDK 2](community-apps-python.md), incluido
en QA22, añade Python en proceso propio, acciones y una skill opcional
para Hermes. La QA instalada se registra para cada candidato. No hay catálogo remoto ni sandbox
de Windows para ese código. Los modales integrados y las apps
siguen el tema; el Command Center conserva su presentación sencilla por decisión
de producto. La barra de ventanas muestra la activa y un botón **+N** para las
demás, reservando espacio junto al dock o encima según el ancho.

La [personalización del inicio](desktop-home.md) incluye el cajón con
búsqueda, accesos removibles y ordenados junto a widgets, y la app Aplicaciones
para recuperar iconos ocultos. Ocultar conserva funciones; no hay desactivación
ni restablecimiento de la organización. Está incluida en QA16; eso no acredita
por sí solo la experiencia instalada ni las pruebas táctiles reales.

## Modelos, automatizaciones y Google

- [Modelos](models.md): selección por conversación y predeterminado de Hermes
  separados; la app Modelos configura también la ruta auxiliar de visión.
- [Automatizaciones y Bandeja](automations.md): reglas guardadas en pausa,
  ejecución por inactividad de Iris, permiso independiente, historial y entrega
  visual al volver. F08 añade la observación opcional de tu sesión de
  Windows y resúmenes de resultados por voz con consentimiento global y por tarea;
  están incluidos en QA16 y requieren sus comprobaciones de hardware/QA instalada.
- [Conexiones](google-connections.md): varias cuentas Google, lectura de
  Calendario/Gmail y JSON de cliente personal protegido por perfil. No envía datos
  a la IA por conectar. La clasificación requiere su propia autorización F14.
- [Calendario](calendar.md): Día/Semana/Mes/Agenda y recordatorios visuales
  opcionales. Comparte sus datos con el [widget F12](widgets.md), añadido en
  fuente junto con la personalización del escritorio. F15 añade preparación manual
  de reuniones, con preview y autorización independiente para cada petición.
  Guarda resumen, puntos, preguntas y fuentes; no crea eventos ni lee correo relacionado.

- [Correo](mail.md): bandeja local, varias cuentas, búsqueda y lectura del cuerpo
  como texto sin marcar leído. F14 añade clasificación opcional de mensajes
  nuevos, con consentimiento, modelo/criterios, límites y corrección local.

Conexiones, Calendario, Correo y F14–F16 están incluidos en QA16. La validación de
cuentas, voz y hardware conserva el alcance del candidato y de cada informe.
La clasificación está apagada por defecto; no procesa historial ni concede
herramientas al modelo.

## Apps existentes y capacidades de Hermes

La disponibilidad de skills, MCP, delegación o rutinas se negocia con Hermes.
La UI debe ocultar o degradar lo que el backend no anuncie.

Mission Board y Async Agents sondean periódicamente su estado solo mientras su
superficie es visible; minimizar, ocultar la página o cerrar el OS lo detiene.
Routines y Gateway Doctor cargan historial y logs tanto en pestañas como en
ventanas. Consulta la [verificación de aplicaciones](os-app-verification.md)
para las correcciones, pruebas y límites de esta revisión.

Capability & Permission Center combina política `Bloquear / Preguntar /
Permitir` con el estado oficial de toolsets, MCP, egress y approvals de Hermes.
Permitir una capacidad nunca activa sensores ni evita guards nativos. Consulta
la [guía del Capability & Permission Center](capability-permission-center.md).

El chat acepta imágenes, texto y PDF. Los PDF con texto seleccionable se
procesan en el Core local con límites de tamaño, páginas y caracteres; el
archivo bruto no se conserva ni se envía al proveedor. Un PDF escaneado sin
texto muestra que necesita capturas/OCR. Office, ZIP y otros binarios siguen
marcados como referencias no leídas para evitar promesas falsas.

Marketplace consulta el Skills Hub real de Hermes por páginas y usa su búsqueda
remota para no limitar los resultados a la primera carga. Permite buscar y
filtrar por tipo, categoría y estado, y distingue con claridad lo disponible de
lo instalado. Los comandos usan el `identifier` exacto publicado por Hermes,
incluso cuando el nombre visible está truncado o se repite entre fuentes.
IRIS solo muestra una acción ejecutable cuando el catálogo aporta un comando
seguro y verificable; copiar un comando no equivale a haber instalado ni
configurado la capacidad. Skills Hub y MCP Tools se reservan para administrar
lo que Hermes ya conoce.

## Continuity Center

Continuity Center lista, crea y activa perfiles mediante `hermes profile`, y
consulta sesiones únicamente cuando el Gateway anuncia la Session API. Permite
buscar, renombrar y fijar según las capacidades declaradas por la versión
instalada. Cuando Hermes anuncia historial y continuidad autenticada, también
puede crear, reanudar y ramificar conversaciones sustituyendo el transcript
visual completo. Distingue el perfil persistente del perfil servido por el
Gateway y oculta el CLI local ante un Gateway remoto. IRIS no abre `state.db`,
no copia memoria por su cuenta y no mezcla instalaciones con credenciales
distintas. Consulta la [guía de Continuity Center](continuity-center.md).

## IRIS Player

IRIS Player reproduce música de una única carpeta seleccionada de forma
explícita. No escanea el equipo completo ni envía audio a Hermes. Mantiene
favoritos e historial local, ofrece búsqueda, cola, controles de reproducción y
recomendaciones explicables incluso cuando el Gateway está desconectado.

La integración opcional con Hermes usa la skill administrada `iris-os` y
acciones estructuradas. Core valida cada acción y espera confirmación de la
superficie antes de informar éxito. Consulta la
[guía de IRIS Player](iris-player.md) para formatos, privacidad y límites.

## Identidad y personalidad

IRIS puede tener un nombre, perfil, backstory e instrucciones propios. Las
plantillas permiten combinar rasgos y sirven como ejemplos editables. El
objetivo es una identidad coherente en IRIS OS, Hermes CLI y otros canales
conectados al mismo workspace.

La [voz guardada](configuration.md#guardar-y-cambiar-la-voz) se aplica a la
siguiente respuesta sin reiniciar Core; no activa el micrófono.

## Avatar y escena

- IRIS viene incluida. Puedes elegir tu propio VRM desde el asistente o desde
  **Configuración rápida → Usar mi propio avatar** y volver a IRIS cuando quieras.
  Puedes elegir un ZIP de hasta 128 MiB con modelos PMX y sus texturas o
  arrastrarlo a la ventana principal. Si el ZIP trae varios modelos, elige
  una variante. Iris guarda solo la elegida con sus texturas, hasta 64 MiB.
- Los avatares compatibles se guardan localmente y se sincronizan con Companion.
- emociones y visemas;
- animaciones procedurales y [59 clips de fábrica](iris-animations.md);
- lectura/tablet con objetos, visitas a muebles y ventana;
- [Vida propia y teletransporte](room.md#vida-propia), con prioridades de conversación y movimiento reducido;
- FPS y calidad gráfica configurables en Ajustes → Rendimiento;
- acciones de escena;
- cámara y percepción local opcionales;
- adaptación de animaciones al escritorio o al Companion.

En Face, **Visual y Audio → Brisa (cabello y ropa)** añade movimiento suave a
las físicas del VRM. Su resultado depende de las cadenas físicas del modelo;
no añade simulación a prendas sin físicas. Recuerda la preferencia y respeta
movimiento reducido. No mueve la cara ni requiere cámara.

Los modelos PMX con huesos humanoides compatibles pueden usar las animaciones
de IRIS y las expresiones que incluya el modelo. Si el PMX trae físicas MMD
compatibles, Iris simula el cabello y la ropa en segundo plano. Mientras está
acostada boca arriba en la cama, el cabello del PMX sigue a la cabeza con la
forma del modelo, sin física: así no se arquea sobre el colchón ni se enreda
debajo de ella, aunque la parte larga queda oculta en el colchón. La ropa
conserva su física. Boca abajo, el cabello vuelve a caer libre sobre la espalda.
Al levantarse se suelta cuando ya no toca ningún mueble. Al cargar un VRM, usa
las físicas de ese formato.

Desde el 28 de octubre de 2026 ya no existen el avatar geométrico ni el de
partículas. El VRM de IRIS (`face/animations/iris.vrm`) es un recurso de fábrica,
fijado por hash en `scripts/release_assets.json` junto a sus animaciones, con su
propia licencia (no MIT, ver [Licencias](licenses.md)). Si el modelo de la persona
no se puede cargar, se muestra IRIS y se avisa.

Los avatares de los usuarios y los paquetes de animación de terceros no forman
parte del repositorio por privacidad, tamaño y licencias. Si falta una animación,
IRIS utiliza una alternativa procedural segura.

La habitación incluye telas adaptadas al tema, detalles de sofá y cama, luz de
descanso localizada y vistas de inspección ajustables a ventanas estrechas.
Consulta [Habitación 3D](room.md) para uso, validación y límites del acabado
visual. La tablet del escritorio refleja también el estado confirmado
del Player local y su pista actual, sin iniciar un segundo reproductor.
Los ambientes manuales Original, Trabajo, Descanso y Noche ajustan las luces
existentes, conservan el tema y recuerdan la elección por navegador. No activan
audio, sensores ni cambios por horario.

### Dónde descargar avatares

- [Aplaybox](https://www.aplaybox.com/) reúne modelos para MMD. El sitio está en
  chino (el navegador puede traducirlo) y a veces tarda en cargar. Busca un
  `.pmx` humanoide con sus texturas. Si viene en un RAR, extráelo y crea un ZIP
  conservando la estructura de carpetas. Carga el ZIP desde **Configuración
  rápida → Usar mi propio avatar**.
  - El ZIP puede pesar hasta 128 MiB.
  - Iris guarda solo el modelo elegido y las texturas que usa, y eso puede pesar
    hasta 64 MiB.
- [VRoid Hub](https://hub.vroid.com/en/models?is_downloadable=1&is_other_users_available=1)
  muestra avatares con descarga habilitada. Inicia sesión y revisa las
  condiciones de uso del modelo antes de descargarlo.
- [BOOTH](https://booth.pm/en/browse/3D%20Models?q=VRM) tiene modelos gratuitos y
  de pago. Comprueba en la ficha que la descarga incluya un archivo `.vrm`.

Puedes cargar un `.vrm` desde el asistente inicial o desde Configuración rápida.
Revisa las condiciones del autor antes de usar un avatar en vídeos o compartirlo.

## Voz y activación

IRIS integra STT/TTS y la activación nativa de Hermes. **Hey Hermes** utiliza
el preset admitido por Hermes; **Hey + perfil** depende de que el motor
instalado pueda procesar una frase personalizada.

La activación está apagada por defecto. El micrófono no se abre al guardar una
frase y el audio no debe salir hacia una superficie o proveedor antes de una
acción explícita.

El acceso rápido de la habitación y del Companion es **Hablar**, una captura
manual independiente de la frase. El mismo botón permite **Enviar**, con
cancelación visible; Wake sigue disponible en Configuración. La transcripción
puede ser local o remota según el STT de Hermes. Consulta
[Micrófono manual y Wake](manual-voice.md) para permisos, límites y actualización
del Companion instalado.

F16 añade en fuente **Transcribir mientras hablo**, una opción del perfil STT
de Hermes. Los parciales son provisionales y solo se muestra/envía al chat un
resultado final. Si el proveedor no admite directo, se conserva la transcripción
final habitual. Está incluida en QA16 y requiere su QA real con micrófono/proveedor.

Antes de sintetizar una respuesta, IRIS omite emojis y marcas de formato visual,
sustituye las barras invertidas por espacios y lee el texto de los enlaces.
El chat, los subtítulos y la memoria conservan el mensaje original. Esta limpieza
se aplica desde Core tanto a Face como al Companion y a cualquier proveedor TTS;
las respuestas compuestas solo por emojis no generan audio. Se activa al
reiniciar Core después de actualizarlo.

## Memoria

La memoria local conserva contexto útil sin convertir cada conversación en una
biografía fija. Hermes mantiene sus propios contratos de sesión y workspace;
IRIS añade la capa de identidad, preferencias y presentación.

## Desktop Companion

La aplicación opcional para Windows muestra a IRIS en una ventana flotante:

- siempre visible cuando el usuario lo decide;
- estados de idle, escucha, pensamiento, habla y error;
- movimiento opcional por el escritorio;
- controles visibles de micrófono y cámara;
- adaptación contextual de las animaciones recibidas.

Consulta [Desktop Companion](desktop-companion.md).

## Vision y acceso remoto

La cámara, la percepción local, WebRTC y el acceso LAN son opcionales. La cámara ya no copia la cabeza ni las expresiones en el avatar. Algunas
partes continúan experimentales y requieren pruebas en navegador, red y
hardware reales. IRIS debe solicitar permisos por superficie y evitar que dos
ventanas compitan por la misma cámara.

Vision Assistant permanece apagado y bloqueado por defecto. El vídeo se procesa
en la superficie local que posee la cámara; Core recibe únicamente señales
resumidas de presencia y movimiento. El contexto emocional derivado tiene un
permiso independiente (`vision.context`): solo puede incorporarse al contexto
del proveedor si el usuario lo habilita expresamente en Capability Center.
Permitir la cámara no autoriza capturas remotas ni envío automático de imágenes.

Los pasos 1 a 6 de V2 añaden contabilidad de presencia, política determinista y
**Notification Center**, disponible como aplicación de IRIS OS. Guarda avisos de
sesión larga y poco movimiento de cabeza, con explicación, filtros, leído/sin leer
y borrado. No abre chat, reproduce voz ni ejecuta herramientas automáticamente.

En **Notification Center → Adapta Vision a ti** hay perfiles Silencioso, Compañía,
Bienestar, Trabajo y Personalizado; límites efectivos visibles; controles separados
de sugerencias físicas y gesto de saludo; y feedback Útil/Muy pronto/No aplica.
Los perfiles no activan sensores ni reinician el presupuesto.

El análisis dispone de perfiles Ahorro/Equilibrado/Fluido y calibración voluntaria
del ruido con una cámara ya activa. La calibración solo dura esa captura y no es
biométrica ni médica. El diagnóstico reciente tiene filtros, exportación manual
y borrado; no transmite medios ni crea herramientas de Hermes. Más detalles y
limitaciones en [los contratos del paso 6](../vision_v2_contracts.md#paso-6--ajustes-locales-calibración-y-diagnóstico).
El registro de actualización 0.1.1 → 0.1.2 del 2026-09-06 es histórico. La
fuente/manifiesto actual del Companion declara 0.1.8; la versión que realmente
usas depende de su EXE. La calibración física prolongada sigue siendo una
verificación aparte; instalarlo no la certifica.

La entrega requiere Vision autorizado, señales fiables y disponibilidad declarada
por 15 minutos desde la bandeja, además de presupuesto y retención. Sin esa declaración,
los indicadores externos desconocidos bloquean la sugerencia. Una actividad positiva
detectada siempre tiene prioridad. Declararse disponible no activa cámara ni micrófono.
Cada aviso permite **Ahora no** (restaurable), **Posponer** 10/30/60 minutos y
**No sugerir este tipo**. También puedes silenciar nuevas sugerencias durante 30 minutos
y revertirlo. Estas elecciones sobreviven a reinicios y no reinician el presupuesto.

**Preparar conversación** pide confirmación y coloca un borrador genérico en el chat
existente, sin medidas de la cámara. No reemplaza texto, adjuntos ni archivos en proceso.
Revisa o edita el borrador y pulsa Enviar para iniciar el turno normal de IRIS/Hermes,
con los ajustes y permisos ya configurados. Posponer solo oculta temporalmente el mismo
aviso en esta bandeja; no crea un cron ni envía mensajes al vencer.

**Resumen de regreso** se habilita por separado en Notification Center → Al regresar
y está apagado por defecto. Requiere al menos 30 segundos de ausencia fiable tras
haber observado presencia, avisos de Vision pendientes sin resumir, disponibilidad
y presupuesto. Permite ver los avisos relacionados sin marcarlos como leídos.
Solo un avatar visible de Face/Companion puede saludar; no interrumpe voz, escritura,
modo silencioso ni animaciones en curso. No se reintenta el saludo ni se reproduce
al recargar. Los cambios de cámara conservan el presupuesto, sin contar el intervalo
de transferencia ni convertirlo en ausencia.

El buzón original de Vision mantiene su contrato. La Bandeja general F07 añade
resultados de automatizaciones y F11 añade avisos de Calendario. Eso no incorpora
automáticamente todos los runs, logs del Gateway o avisos de Correo. Consulta
[Automatizaciones](automations.md), [Calendario](calendar.md) y los
[contratos históricos de Vision V2](../vision_v2_contracts.md).

### Foto a petición

Pedir «¿qué tengo en la mano?» o usar la acción de foto puede solicitar una
captura nueva para ese turno. Requiere su permiso propio y el destino de visión
autorizado; no se toma continuamente por abrir la cámara. La foto puede enviarse
a un proveedor remoto, mientras que las señales de presencia/movimiento siguen
calculándose localmente. Véase [Cámara y asistencia visual](camera.md).

## Degradación esperada

IRIS debe continuar funcionando cuando falta una función opcional:

- sin GPU: proveedor remoto o modelos pequeños;
- sin micrófono: chat escrito;
- sin cámara: avatar sin percepción visual;
- sin Companion: interfaz web normal;
- sin animaciones externas: poses procedurales;
- sin proveedor conectado: diagnóstico y modo informativo limitado.

## Siguientes lecturas

- [Arquitectura](architecture.md)
- [Configuración](configuration.md)
- [Privacidad y seguridad](privacy-security.md)
