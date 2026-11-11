# Documentación de IRIS OS

Este índice separa las guías de uso y desarrollo de los registros de diseño y
validación. **Consulta primero el [estado actual](estado-actual.md)** para saber
qué está en fuente, qué contiene cada candidato y qué falta por aprobar.
El candidato de referencia es QA22 (2026-11-06); su informe distingue pruebas
automáticas y QA manual pendiente. Las guías también identifican funciones
experimentales o aún no integradas.

## Guías

| Necesito… | Documento |
| --- | --- |
| Instalar IRIS o reparar Hermes | [Instalación y primeros pasos](guides/getting-started.md) |
| Elegir proveedor, modelo, voz o personalidad | [Configuración](guides/configuration.md) |
| Cambiar el modelo del chat o de visión | [Modelos](guides/models.md) |
| Crear reglas por inactividad y consultar sus resultados | [Automatizaciones y Bandeja](guides/automations.md) |
| Conectar WhatsApp, Telegram y otros canales de Hermes | [Mensajería](guides/messaging.md) |
| Entender qué puede hacer IRIS | [Funcionalidades](guides/features.md) |
| Configurar permisos y comprobar guards de Hermes | [Capability & Permission Center](guides/capability-permission-center.md) |
| Administrar perfiles y sesiones | [Continuity Center](guides/continuity-center.md) |
| Usar o extender el reproductor local | [IRIS Player](guides/iris-player.md) |
| Ejemplo comunitario sin cuentas: app, widget y skill | [Player](../apps/player/README.md) |
| Conectar cuentas de Google (Calendario y Correo, solo lectura) | [Conexiones](guides/google-connections.md) |
| Consultar eventos, recordatorios y preparación IA opcional (F11/F15, en fuente) | [Calendario](guides/calendar.md) |
| Leer la bandeja y el texto completo de Gmail (F13, en fuente) | [Correo](guides/mail.md) |
| Personalizar el escritorio y declarar widgets de una app (F12, en fuente) | [Widgets](guides/widgets.md) |
| Clic derecho, pulsación prolongada y galería con vistas previas | Interacción de Inicio (informe interno no distribuido) |
| Buscar apps, organizar el inicio y recuperar iconos ocultos (en fuente) | [Inicio y aplicaciones](guides/desktop-home.md) |
| Instalar o desinstalar apps desde un .zip o una carpeta (F17, en fuente) | [Iris Store](guides/iris-store.md) |
| Crear una app externa con Python, acciones, widget y skill opcional (SDK 2, incluido en QA22) | [SDK comunitario y Contador](guides/community-apps-python.md) |
| Revisar la propuesta de fuentes, transiciones y temas compartibles | [Theme Studio: diagnóstico y diseño](iris_theme_studio_extension_plan_2026-10-18.md) |
| Importar/exportar ajustes sin cargar imágenes externas (en fuente) | [Temas de Theme Studio](guides/theme-studio.md) |
| Importar, crear y compartir transiciones CSS/JSON (en fuente) | [Transiciones de Theme Studio](guides/theme-transitions.md) |
| Gestos, expresiones y 59 clips de fábrica | [Animaciones](guides/iris-animations.md) |
| Explorar la habitación, Vida propia y teletransporte | [Habitación 3D](guides/room.md) |
| Comprender los componentes y flujos | [Arquitectura](guides/architecture.md) |
| Revisar micrófono, cámara, claves y acceso LAN | [Privacidad y seguridad](guides/privacy-security.md) |
| Hablar con el micrófono sin usar una frase de activación | [Micrófono manual y Wake](guides/manual-voice.md) |
| Abrir la cámara y usar la asistencia visual | [Cámara y asistencia visual](guides/camera.md) |
| Probar la aplicación principal de Windows | [Iris Desktop · vista previa](../desktop/README.md) |
| Instalar o desarrollar la ventana flotante | [Desktop Companion](guides/desktop-companion.md) |
| Consultar las licencias de los componentes incluidos | [Licencias de terceros](guides/licenses.md) |
| Preparar el entorno, ejecutar pruebas o publicar | [Desarrollo y publicación](guides/development.md) |
| Revisar el código y construir tu propio instalador Windows | [Compilar desde fuente](guides/build-from-source.md) |
| Mantener las tres copias limpias para GitHub | [Repositorios de publicación](guides/publication-repositories.md) |
| Preparar recursos locales y el entorno de las pruebas de distribución | [Exportación web](guides/local-web-build.md) |
| Aportar una app, un servicio Python o acciones para Iris | [Contribuir aplicaciones](guides/contributing-apps.md) |
| Conocer el origen de Alice e IRIS | [Historia e inspiración](guides/project-history.md) |

## Registros técnicos

Estos documentos conservan decisiones, investigación y riesgos. Los informes
fechados describen la revisión que probaron; sus conteos y «siguientes pasos»
no son un estado vivo. El plan coordinador registra el alcance y la aceptación;
el [estado actual](estado-actual.md) facilita la lectura de las últimas entregas.

- QA22: construcción, comprobaciones y límites (informe interno no distribuido)
- Cambio de voz en vivo: revisión y cierre (informe interno no distribuido)
- Rutinas de sala, Companion y teletransporte (informe interno no distribuido)
- Avatar V12 y alcance de su revisión (informe interno no distribuido)
- Actualización documental y NeuroMapa del 2026-11-06 (informe interno no distribuido)
- [Documento maestro de producto y arquitectura](iris_os_master.md)
- Medición del avatar Ligera v10: rendimiento, memoria y alcance (informe interno no distribuido)
- [Ideas diferidas: cuenta Iris, créditos y Android](iris_future_services_notes_2026-10-26.md)
- Actualización documental del 2026-10-16 (informe interno no distribuido)
- Actualización de manual, guías y NeuroMapa del 2026-10-26 (informe interno no distribuido)
- Nous en la VM de QA16: causa TLS confirmada y corrección en fuente (informe interno no distribuido)
- Canales: campos opcionales, requisitos y ayuda de Telegram (informe interno no distribuido)
- QA 4: donaciones, voz, cámara, correo y diagnóstico de Calendario (informe interno no distribuido)
- Capturas de pantalla a petición y límite de FPS (informe interno no distribuido)
- Iris decide mirar: foto o captura pedida por el modelo, una por turno local (informe interno no distribuido)
- Tareas en segundo plano: Iris delega encargos largos y sigue conversando (informe interno no distribuido)
- A08: plan coordinador por entregas — distribución Windows y ampliaciones posteriores (informe interno no distribuido)
- F09: kit de apps propias y contribuciones — diseño por etapas (informe interno no distribuido)
- [Ampliación de Iris Store: alcance de Python, acciones y skills](iris_store_python_skills_plan_2026-10-23.md)
- Iris Store SDK 2: implementación en fuente, pruebas y límites (informe interno no distribuido)
- F10: conexiones Google — correcciones de la revisión de Codex (informe interno no distribuido)
- F10: QA aislada con dos cuentas reales de Google (informe interno no distribuido)
- F10: configuración con tu propio cliente OAuth (informe interno no distribuido)
- F11: Calendario, estado compartido y recordatorios (informe interno no distribuido)
- F12: escritorio personalizable y widget Calendario (informe interno no distribuido)
- F13: bandeja y lectura local de Gmail (informe interno no distribuido)
- F13: lectura del cuerpo completo en texto (informe interno no distribuido)
- F14: clasificación opcional de correo en fuente; pruebas y límites (informe interno no distribuido)
- F15: preparación manual de reuniones, fuentes y consentimiento (informe interno no distribuido)
- F16: transcripción provisional de voz manual y Wake (informe interno no distribuido)
- [Cajón de apps e inicio personalizable: diseño solicitado](iris_desktop_personalization_plan_2026-10-17.md)
- Escritorio personalizable: implementación y validación (informe interno no distribuido)
- Inicio: Calendario compacto, arrastre, navegación y fuentes WOFF2 (informe interno no distribuido)
- Cabecera compacta, idioma y dock del escritorio: implementación y validación (informe interno no distribuido)
- Asistente: historial de la reestructuración; apariencia inicial y cinco pasos en QA16 (informe interno no distribuido)
- KittenTTS: evaluación y mediciones, sin integración (informe interno no distribuido)
- [Kit de conexiones compartidas entre apps: diseño para futuros servicios como Notion](iris_connections_sdk_plan_2026-10-17.md)
- [Plan técnico del Desktop Companion](iris_desktop_companion_plan.md)
- [Vision V2: contratos, notificaciones, regreso y ajustes locales — pasos 1 a 6](vision_v2_contracts.md)
- [Investigación de voz y presencia digital](iris_voice_research_update_2026-07-30.md)
- [Plan del bridge WebRTC](webrtc_bridge_plan.md)
- [Auditoría de limpieza y compatibilidad legacy](iris_cleanup_audit.md)
- Auditoría de código sin usar y mejoras (2026-10-02) (informe interno no distribuido)
- [Guía interna legacy de mantenimiento](iris_hermes_os_guide.md) — registro
  histórico; puede mencionar rutas anteriores.

## Convenciones

- **Implementado en fuente** significa que existe código; no demuestra que el
  instalador usado lo contenga.
- **Incluido en un candidato** identifica un artefacto concreto y su versión.
- **Validado** indica el alcance de pruebas descrito en su informe; no equivale
  a una release pública aprobada.
- **Opcional** significa que requiere consentimiento, hardware, cuenta o
  dependencia adicional.
- **Experimental** significa que puede cambiar y no debe presentarse como una
  promesa estable.
- Hermes es el motor de agente. IRIS es la experiencia, identidad y conjunto de
  superficies que se conectan con él.

[← Volver al README principal](../README.md)
