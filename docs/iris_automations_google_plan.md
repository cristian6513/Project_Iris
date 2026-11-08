# Iris OS — Automatizaciones por presencia, Gmail y Calendario

Fecha original: 2026-09-29. Actualización documental: **2026-10-17**.
F04–F08, F10 y F11 tienen implementación en fuente. Correo F13 ya presenta
la caché de F10 y consulta el cuerpo en texto al seleccionar ([guía](guides/mail.md)); F14 añade clasificación opcional de correos nuevos en fuente (informe (informe interno no distribuido)); F15 añade preparación manual de reuniones en fuente (informe (informe interno no distribuido)). F08 separa
observación Win32 y resúmenes hablados opt-in (informe (informe interno no distribuido)). La integración
del próximo candidato no queda aprobada por este diseño; véase
[Estado actual](estado-actual.md).

El usuario confirmó que este diseño corresponde a lo que quiere para Iris. Queda como referencia de requisitos técnicos. El orden y el estado de implementación se centralizan en el plan coordinador de A08 (informe interno no distribuido): primero distribución, después las entregas funcionales correspondientes. La confirmación del diseño no equivale a activar rutinas ni conectar cuentas personales.

Este documento concreta la propuesta del usuario durante la preparación de A08. No crea tareas reales, conecta cuentas ni cambia los motores actuales. A08 sigue abierto: la distribución de Iris/Hermes y estas ampliaciones funcionales tienen criterios de aceptación separados. Véase alcance de A08 (informe interno no distribuido).

La app Calendario tendrá una vista compacta para la pantalla principal, consumiendo el mismo estado/eventos. El [plan de apps y widgets](iris_apps_widgets_plan.md) propone el registro común, lifecycle y preparación para una futura Iris Store; no sustituye los servicios Google ni el motor durable de este documento.

## Comportamiento solicitado

| Función | Configuración de ejemplo | Comportamiento esperado |
| --- | --- | --- |
| Tarea durante la ausencia | «Después de 5 minutos, realiza la investigación que encargué». | Ejecutar una vez por episodio, guardar el resultado y presentarlo al regresar. |
| Gmail | Permitir revisar correos nuevos según criterios personales. | Sugerir relevancia, explicar el motivo y agrupar los avisos; no clasificar todo el historial por defecto. |
| Calendario | Reunión a las 16:00; aviso 60 minutos antes y al inicio. | Avisar a las 15:00; incluir preparación si está habilitada. A las 16:00 mostrar el evento y su botón «Unirse» cuando tenga enlace. |

El ejemplo de investigación financiera describe el funcionamiento de una rutina, no solicita una investigación ni una operación real.

## Una función «Automatizaciones» dentro de Iris OS

La interfaz propuesta conserva las ventanas, temas y controles existentes. Cron horario, tareas por inactividad y reglas de correo/calendario pueden aparecer en la misma aplicación, con el tipo de disparador visible. No se simulará la ausencia creando un cron que ejecute la tarea cada cinco minutos.

Cada regla tendrá:

- Nombre, estado activado/pausado y condición: horario, inactividad, ausencia observada, correo nuevo o evento de calendario.
- Tiempo de espera o anticipación; fuente de presencia cuando corresponda.
- Acción, contexto permitido, perfil y modelo de IA, herramientas disponibles y límites de ejecución/coste.
- Entrega: inmediata, al regresar o cuando haya una superficie disponible; texto siempre y voz opcional, con horario de silencio.
- Historial: pendiente, en ejecución, finalizada o fallida; resultado, fuentes cuando existan y última entrega.

Por defecto, una regla por ausencia ejecuta una vez por episodio, tiene una ejecución concurrente como máximo y conserva el trabajo cuando el usuario regresa. Si aún no terminó, muestra «En curso» y entrega el resultado después. No interrumpe el chat activo ni sustituye su modelo. La repetición periódica durante una ausencia sería una opción explícita con límites, no el comportamiento inicial.

## Qué significa «no estoy» y «regresé»

| Señal elegible | Qué mide | Cómo finaliza el episodio |
| --- | --- | --- |
| Inactividad de Iris | Tiempo sin interacción dentro de Iris. | Una interacción nueva en una superficie local de Iris. |
| Inactividad del escritorio | Tiempo sin entrada de teclado/mouse del sistema, mediante un adaptador nativo nuevo. | Nueva entrada del sistema; no requiere cámara. |
| Ausencia observada | Persona previamente presente y después ausencia continua ante la cámara, con confianza suficiente. | Presencia nuevamente estable. |

No teclear puede significar estar leyendo: las tres señales se presentan con su nombre y no se intercambian silenciosamente. Cabeza quieta no demuestra inactividad. La presencia de cámara tampoco identifica quién regresó; el diseño inicial no promete reconocimiento de identidad.

Cámara apagada, permiso perdido, confianza baja, pestaña oculta sin observación, desconexión y señal caducada significan **desconocido**. No cuentan como ausencia. Al suspender o reiniciar el equipo, se revalida la condición; el intervalo sin observar no completa automáticamente los cinco minutos.

Actualmente Face detiene la cámara al ocultarse. La observación de cámara en segundo plano necesita una capacidad explícita y pruebas de escritorio; no se cambia esa conducta de privacidad por añadir una rutina. La opción de inactividad del escritorio puede funcionar con Iris minimizado cuando exista su adaptador. Las tareas locales requieren que Core/Hermes sigan funcionando: un equipo apagado o suspendido no garantiza ejecución ni avisos a tiempo.

## Base histórica del diseño y trabajo entonces pendiente

La tabla siguiente conserva el corte de septiembre. Los módulos
`automation_*`, `inbox_*`, `google_*` y `calendar_*` ya existen en la fuente
actual. Las guías de [Automatizaciones](guides/automations.md),
[Conexiones](guides/google-connections.md) y [Calendario](guides/calendar.md)
describen el comportamiento implementado; no confundir esta tabla con la
lista de pendientes vigente.

| Área | Reutilizable | Falta |
| --- | --- | --- |
| Cron Hermes | `core/hermes_cron.py` crea/lista/ejecuta jobs horarios y normaliza sus resultados. | Un disparador por condición y persistencia de cada ocurrencia. La API actual de creación exige `schedule`. |
| Runs y agentes | `core/async_agents.py` y las rutas de runs de `core/websocket_server.py`. | Correlación con la regla, claims y reconciliación si el envío queda ambiguo. |
| Vision | `core/vision_return.py`, `vision_assistant.py`, políticas y calibración. | Señal continua apta para disparar al cumplir el umbral. Hoy se calcula ausencia al regresar. |
| Actividad | `core/surface_registry.py` conoce superficies y actividad para audio. | No es un detector de inactividad del sistema. Necesita fuente y caducidad propias. |
| Notificaciones | API, revisiones/conflictos, leído/pospuesto/descartado y elección de presentador. | Inbox general: el contrato actual es `VisionNotification` y no debe recibir mensajes Google ni resultados de trabajos. |
| Resultados cron | El panel consulta resultados mientras está abierto/visible y utiliza IDs locales. | Entrega durable independiente del panel; sustituir esa narración solo cuando la nueva ruta la cubra. |
| Apps | Registro de ventanas en `face/js/hermes_activity.js`. | Registrar Gmail, Calendario y Automatizaciones con sus allowlists y tamaños. |
| Google Hermes | Skill `google-workspace` de la revisión compatible, con cliente Python/gws. | Login integrado, permisos por función y adaptadores fiables. El wrapper actual omite campos de conferencia/recurrencia y paginación necesarios. |

Estas funciones serán consumidores nuevos y expresamente activados. No convierten las señales locales existentes de Vision en permiso automático para ejecutar herramientas, iniciar conversaciones o transmitir imágenes.

## Ejecución durable y entrega al regresar

Separar definición de regla, ocurrencia, ejecución y entrega. Guardar todo en el perfil mutable, fuera de la instalación. Contratos propuestos:

```text
AutomationDefinition
  id, revision, enabled, title
  trigger: kind, source, threshold/offset, repeat, cooldown
  execution: kind, prompt/context, profile/provider/model, tool_allowlist,
             timeout, max_runs_per_day, token_budget, concurrency
  delivery: when, voice, quiet_hours, expiration, return_during_run

AutomationOccurrence
  rule_id, rule_revision, occurrence_key, state, correlation_id,
  run_id, timestamps, error, result_id

Delivery
  result_id, status, presented_at, read_at, voice_attempt/receipt
```

Para inactividad/ausencia, la clave de ocurrencia identifica un episodio. Una restricción única `(rule_id, rule_revision, occurrence_key)` impide duplicar trabajo por polling, reinicio o Face/Companion. La edición de una regla invalida candidatos antiguos y no dispara nuevamente por el mismo episodio sin una nueva evaluación explícita.

Reclamar dentro de una transacción con lease; revalidar señal, revisión, permisos y presupuesto inmediatamente antes de enviar. Estados mínimos: candidata, reclamada, enviada, en ejecución, completada, fallida, cancelada y envío ambiguo. Un timeout de POST no prueba que Hermes rechazó el trabajo: reconciliar con su run antes de reintentar, evitando ejecuciones duplicadas.

Guardar resultado normalizado, entrada del inbox y estado de entrega en una transacción. Mostrar solo la proyección de texto de `core/response_text.py`; no mostrar JSON interno ni reproducir de nuevo acciones del avatar almacenadas. El saludo debe referirse a resultados realmente completados, con fecha y enlace al detalle; resultados fallidos o todavía activos tienen su estado real.

«Presentado», «leído» y «hablado» son estados distintos. Una llegada agrupa pendientes relevantes y no marca todos leídos. Reclamar un solo presentador entre Face/Companion, respetar silencio, Focus y conversación activa. Registrar intentos y recibos de voz; ante entrega ambigua, no repetir automáticamente por otra superficie. Los presupuestos de trabajo y voz son independientes.

## Servicio Google compartido por las apps

La implementación F10 utiliza un servicio propio de Iris por perfil/cuenta,
compartido por sus apps. Se evaluó la skill Google Workspace del Hermes fijado,
pero sus tokens no se importan, copian ni comparten implícitamente con Iris.
Tokens y cliente privado nunca van al frontend ni a prompts. Se expone cuenta,
estado, permisos efectivos, última lectura y errores recuperables.

Primer alcance: **Gmail lectura y Calendario lectura**. No heredar automáticamente todos los permisos amplios del skill existente. Conectar Google no activa el análisis IA. La configuración distingue acceso a datos, revisión automática y envío de contenido al modelo seleccionado. Desconectar o revocar detiene workers y candidatos; resultados tardíos de una generación anterior no se publican en otra cuenta/perfil.

La vía elegida el 2026-10-16 es importar o pegar el JSON del cliente OAuth
Desktop propio desde Conexiones. La fuente lo valida y protege por perfil con
DPAPI; el cliente compartido oficial de Iris queda reservado y desactivado.
OAuth sigue en el navegador del sistema con retorno local, no dentro de WebView.
La QA real previa acredita el login, no el instalador con esta UI nueva ni una
exención de las políticas de Google. Véase [Conexiones](guides/google-connections.md).

La ampliación futura a otros servicios se diseña en el
[kit de conexiones](iris_connections_sdk_plan_2026-10-17.md): Conexiones sigue
como administrador común y las apps consumen el servicio autorizado. No cambia
el login/almacén Google actual ni importa credenciales de Hermes; Notion queda
como siguiente ejemplo a evaluar, sin implementación ni nuevas cuentas activadas.

## Gmail: relevancia configurable

1. Conectar la cuenta y mostrar una previsualización de lectura.
2. Activar voluntariamente revisión de correos nuevos; elegir etiquetas/carpetas, criterios, modelo, frecuencia y presupuesto. El usuario puede ampliar después un rango histórico concreto.
3. Producir una sugerencia `importante`, `normal` o `incierto`, con razón breve, resumen, referencias del mensaje y versión de criterios/modelo. Permitir corregirla.
4. Agrupar avisos según preferencias. Acciones iniciales: ver correo y explicación/corregir relevancia. Envío, borrado, etiquetas y borradores remotos quedan fuera de este primer alcance.

Consultar no marca leído. No cargar imágenes remotas, seguir enlaces ni analizar adjuntos por defecto. El texto del correo es dato no confiable: no puede cambiar permisos ni ordenar al agente ejecutar herramientas.

Deduplicar por perfil, cuenta, ID del mensaje y revisión de clasificación; separar clasificación de notificación para no avisar otra vez al sincronizar. Persistir resultados y cursores por lote. Gmail admite sincronización incremental por historial, con recuperación cuando el cursor expiró; una recuperación técnica no autoriza clasificar de nuevo todo el historial. [Sincronización oficial](https://developers.google.com/workspace/gmail/api/guides/sync).

La primera versión puede consultar cambios a intervalos controlados mientras Iris funciona. Push de Google sería una entrega posterior con infraestructura y credenciales propias; no es necesario prometerlo para el primer escritorio.

## Calendario: recordatorios y preparación

Los avisos a −60 minutos y al inicio se calculan por horario sin depender de una respuesta del modelo. La preparación IA es una acción opcional separada: selecciona evento y contexto autorizado, genera puntos a considerar y conserva las fuentes. Si la preparación falla, el aviso horario sigue funcionando.

Sincronizar calendarios seleccionados con paginación y cursores. Preservar ID de calendario/evento, instancia recurrente, inicio/fin UTC, zona horaria, estado, versión, URL del evento y datos de conferencia. La [API de eventos](https://developers.google.com/workspace/calendar/api/v3/reference/events) proporciona estos campos, incluidos `conferenceData`, `hangoutLink`, `recurringEventId` y `originalStartTime`; el wrapper local actual debe ampliarse o sustituirse para no perderlos.

Deduplicar cada fase por cuenta/calendario/instancia/inicio. Reprogramar al cambiar la hora y cancelar pendientes si se cancela el evento. Cambios de título actualizan el contenido, no crean otro aviso. Definir política expresa para eventos de todo el día, recurrencias, zonas horarias, suspensión y avisos vencidos: no narrar en bloque reuniones que ya terminaron al encender el PC.

El aviso ofrece «Ver Calendario» y, si existe, «Unirse». El enlace de unión proviene de los datos de conferencia del evento y se valida como HTTPS; no lo inventa el modelo ni se ejecuta un enlace arbitrario del texto. Abrir por clic. Si en el futuro se ofrece abrir automáticamente al inicio, será una opción expresa distinta.

## Archivos previstos y orden seguro

Esta tabla registra nombres previstos en septiembre. Varios ya están
implementados: comprobar su estado en el coordinador y sus contratos en fuente
antes de crear otro módulo para la misma responsabilidad.

| Entrega | Archivos previstos | Condición de salida |
| --- | --- | --- |
| 1. Contratos y persistencia | Nuevos `core/automation_contracts.py`, `automation_store.py`, `presence_state.py`; migraciones y tests. | Ocurrencias únicas, reloj inyectable, reinicios y revisiones sin duplicación. |
| 2. Señales y motor | Nuevo `core/automation_engine.py`; adaptación acotada de `vision_return.py`, `vision_assistant.py`, `surface_registry.py`. | Umbral observable, señal desconocida segura y motor probado sin trabajos reales. |
| 3. Ejecutor | Nuevo `core/automation_executor.py`, `automation_api.py`; integración con runs/cron y `websocket_server.py`. | Correlación, límites, reconciliación de envío ambiguo y resultados normalizados. |
| 4. Inbox y UI | Store/envelope general nuevo, adaptadores de `notification_api.py`/acciones; `notification_center.js`, nueva UI Automatizaciones y registro de apps. | Vision v1 compatible, resultados al regreso y un solo presentador. |
| 5. Presencia nativa y voz | Adaptador de inactividad de Desktop/puente; Face/Companion y outbox de voz. | Minimizado/reanudación probados y voz expresamente activada sin repetir entregas conocidas. |
| 6. Google y Gmail | Broker OAuth/sync y app Gmail nuevos; integración controlada con el pin Hermes. | Lectura por cuenta, permisos acotados, clasificación voluntaria y paginación recuperable. |
| 7. Calendario | Adaptador y app Calendario nuevos; recordatorios en motor común. | Cambios/cancelaciones/recurrencias correctos, preparación separada y botón de unión validado. |

Contratos/persistencia, motor y ejecutor comparten interfaces, pero se implementan en entregas separadas F04/F05/F06 del coordinador, cada una con sus pruebas y condición de salida. La tabla anterior agrupa áreas técnicas de referencia; no autoriza implementarlas juntas. Inbox y presentación se conectan después de tener resultados durables. Gmail/Calendario comparten OAuth, sincronización y bandeja, pero clasificación de correo, preparación de reuniones y trabajos por ausencia mantienen opciones y pruebas separadas. El adaptador nativo de inactividad y la observación de cámara en segundo plano también se verifican por separado.

La base de distribución A08 (runtimes incluidos, perfil mutable, recursos locales y actualizaciones conjuntas) continúa su propio orden. Preparar este diseño no habilita el instalador ni cierra A08.

## Pruebas y criterios de aceptación

- Presencia: 299/300 segundos, lectura sin teclear, cámara apagada/confianza baja/huecos, Face oculto, múltiples superficies, cambio de reloj, suspensión y reinicio. Una ejecución por episodio; llegada antes/después del claim; candidatos cancelados al reactivar.
- Ejecución: dos reclamantes, lease caducado, presupuesto/concurrencia, fallo de almacenamiento, POST ambiguo y reconciliación. No relanzar un run aceptado ni presentar un éxito inexistente.
- Entrega: llegada repetida, resultados completos/en curso/fallidos, JSON interno, acciones del avatar, silencio/Focus/conversación, voz ocupada y recibo ambiguo. Sin duplicar saludos por Face/Companion ni marcar leído por mostrar.
- Google: state/PKCE/cancelación, permisos parciales, refresh concurrente, revocación, cuentas/perfiles separados; ningún token en API pública, logs o prompts.
- Sync: varias páginas, orden distinto, cursor inválido, fallo intermedio, cuotas/backoff y desconexión. No confirmar cursores con páginas sin guardar ni publicar resultados de una cuenta desconectada.
- Gmail: mensaje duplicado, revisión de criterios, relevancia incierta/corrección, instrucciones maliciosas, imágenes/adjuntos remotos y ausencia de marcado leído o escritura.
- Calendario: aviso previo/al inicio, recurrencias, DST/zonas, cancelación/reprogramación, todo el día, suspensión y expiración. «Unirse» ausente, enlace válido y rechazo de esquemas/hosts engañosos.
- Regresiones: política/calibración Vision, Notification Center v1, cron manual/horario, async agents, proyección de texto, permisos HTTP/LAN y coexistencia de Desktop/Face/Companion.

Usar reloj inyectable y clientes falsos en tests unitarios; después suites relacionadas y general. QA física de cámara/Windows y una cuenta de prueba Google autorizada son pasos posteriores de integración, no pruebas realizadas en esta revisión.

## Cambios de esta fase

Solo documentación de alcance y diseño. No hubo cambios de producción, ejecuciones de nuevas suites, cron/runs reales, conexiones de cuentas ni acceso a cámara/micrófono. La inspección del skill Google fue de referencia, sin ejecutar su setup ni leer credenciales.
