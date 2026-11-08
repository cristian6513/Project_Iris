# Vision Assistant V2: contratos, tiempo y política local

> Referencia de contratos Vision V2. La foto a petición F03, la Bandeja F07
> y Calendario F11 amplían el producto sin sustituir este contrato original.
> Consulta [Estado actual](estado-actual.md) para integración y QA por candidato.

Estado: pasos 1 a 6 implementados en el alcance descrito abajo; falta el paso 7 de QA física.
Las reglas locales pueden entregar avisos
persistentes a Notification Center (`stage=notifications`), sujetos a disponibilidad
explícita, observación fiable, retención y presupuesto del
[plan maestro](iris_os_master.md#98-backlog-acordado-y-entregas-de-vision-assistant-v2).
Se conserva el historial V1. No se inicia chat, voz ni herramientas; el saludo usa
las animaciones existentes y solo una superficie local puede intentar presentarlo.

## Qué cambia

`core/vision_session.py` contiene los modelos estrictos y la contabilidad temporal.
`core/vision_assistant.py` integra estos modelos con la estabilización V1 y su estado
local. Una señal sigue teniendo exclusivamente `present`, `confidence` y `motion`.
Los tiempos provienen de Core; las superficies no pueden enviar duraciones ni fechas.

El campo adicional `v2` está disponible en el ACK de una señal local y en
`GET /api/vision-assistant/settings`. Las respuestas antiguas siguen conservando
`accepted`, `event`, estado, preferencias y privacidad.

| Contrato | Contenido | Uso actual |
|---|---|---|
| `iris.vision-session/v2` | ID, fechas, tiempo confirmado y ventana agregada | Generado durante observación autorizada |
| `iris.vision-candidate/v2` | Tipo, sesión, duración y confianza | Reglas de sesión larga y poco movimiento de cabeza |
| `iris.vision-decision/v2` | Resultado, razón y entrega | Observar, elegible, suprimido o entregado; `delivery=inbox` solo tras escritura durable |
| `iris.vision-runtime/v2` | Cooldown y agregado persistido de sesión | Migración compatible desde V1 |
| `iris.vision-policy-state/v1` | IDs, tipos y fechas de entregas confirmadas | Presupuesto persistido junto al buzón |
| `iris.notification/v1` | Texto local, fecha, leído y evidencia agregada | API sin medios, comandos ni contenido del proveedor |
| `iris.notification-inbox/v1` | Revisión y hasta 100 avisos | Buzón compartido persistente, no una memoria del agente |

Los objetos rechazan campos desconocidos, multimedia, valores no finitos, duraciones
negativas y tipos incorrectos. El candidato permanece `null` si no hay observación
fiable o no se alcanza un umbral. Cumplir una regla no equivale a mostrar un aviso.

## Semántica temporal

- Solo se suma el intervalo entre dos señales consecutivas de presencia estable con
  confianza suficiente. La estabilización V1 conserva su mínimo de dos muestras.
- Un intervalo mayor de 8 segundos no se cuenta: una cámara sin señales no implica
  ausencia ni presencia del usuario.
- Las pausas, señales dudosas y cambios de superficie rompen la continuidad de medida.
- Una interrupción breve conserva el total; después de 5 minutos sin presencia fiable
  comienza una sesión nueva. Estos valores son constantes internas, no ajustes de UI
  ni recomendaciones de bienestar.
- El reloj monotónico mide duración. Si el reloj de calendario y el monotónico divergen
  más de 5 segundos, o el tiempo retrocede, se descarta ese intervalo.
- La ventana de movimiento comprende los últimos 10 minutos de intervalos medidos.
  Guarda segundos en `still`, `low` y `active`, con confianza ponderada por duración.
  Se vacía al interrumpir la observación; una consulta nunca extrapola tiempo.
- `basis=head_pose` significa movimiento observado de cabeza. No equivale a estar
  sentado, mirar una pantalla, trabajar, cansancio o movilidad de todo el cuerpo.
- Los segmentos temporales viven únicamente en RAM, con un máximo de 2048 entradas.
  No contienen coordenadas ni imágenes; una sobrecarga puede reducir cobertura,
  nunca inventar segundos observados.

## Persistencia y recuperación

Se usa el mismo archivo `vision_events_state.json` que contenía el cooldown V1.
La migración ocurre al procesar señales; leer configuración no crea una sesión
ni abre la cámara. Se conserva `last_action_at`.

El agregado de sesión contiene ID, fechas y tiempo acumulado; sus segmentos de
movimiento no se escriben en disco ni se restauran. Un aviso sí conserva las métricas
agregadas que justificaron su entrega. La sesión se guarda aproximadamente cada 60 segundos y
en transiciones/cambios de retención. Un cierre abrupto puede perder hasta el último
minuto no guardado. Al reiniciar, solo se recupera un agregado con menos de 5 minutos
de antigüedad y se esperan muestras nuevas: el tiempo apagado nunca cuenta.

Con retención cero no se persisten sesión ni nuevos avisos y se purga el buzón.
Borrar historial elimina agregado y avisos en memoria y disco, conservando los
cooldowns y recibos que evitan repeticiones. Los fallos de
escritura se reflejan en `state_persisted=false`; no hacen fallar la señal y los
reintentos periódicos están limitados para evitar escrituras continuas.

## Entrega y siguientes pasos

Las consultas, señales y broadcasts de Vision permanecen en loopback. Un dispositivo
LAN emparejado sigue recibiendo eventos generales, pero no el historial de presencia
ni las actualizaciones de sesión.

Las decisiones explican tanto el estado de observación como la regla aplicada y
su supresión. Notification Center guarda avisos silenciosos y distingue entrega al
buzón de lectura explícita. El paso 4 añade acciones reversibles, snooze y un
borrador de conversación que solo se envía al pulsar Enviar en el chat.
Voz, conversaciones y herramientas
no se disparan desde esta capa. La carga de nuevas skills se pospone hasta cerrar Vision V2.

El paso 5 reemplaza el saludo automático de V1: sus eventos son informativos
(`actionable=false`). El regreso solo puede saludar tras una entrega durable V2,
con opt-in, avisos pendientes y presupuesto compartido. Un cooldown V1 reciente
se migra como recibo de regreso; no se concede un presupuesto nuevo al actualizar.

## Verificación

`tests/test_vision_session.py` cubre contratos, ventanas, señales dudosas, cámara
interrumpida, pausas, cambios de superficie, saltos de reloj, suspensión, restart,
migración V1, retención cero, borrado, JSON dañado, escrituras acotadas, fallos de
disco, API y filtrado LAN de broadcasts.

Se ejecuta junto con las regresiones de Vision V1, Surface Registry y Capability
Center. Validación del paso 4 del 2026-09-05 en Windows/Python 3.11:

- Suite Python completa: **690 aprobadas y 3 omitidas**.
- Suite JavaScript completa: **86 aprobadas**.
- Build web de Companion y sintaxis de `face/js/app.js`: correctos.
- Lint focal de Vision, símbolos de integración y `git diff --check`: correctos.

Las pruebas nuevas de política e integración cubren 82 casos: límites, datos
desconocidos, relojes, reinicios, prioridades, deduplicación, presupuesto deslizante,
actividad entre superficies, opt-in, rechazo LAN y guardado parcial. Cinco pruebas
JavaScript verifican resúmenes sin contenido y emisión condicionada a opt-in/pausa.
La compilación mantiene el aviso de tamaño del chunk de Three.js (>500 kB);
no se reconstruyó el ejecutable nativo instalado ni se ejecutó aquí la matriz Linux.

La calidad de señales y el funcionamiento sostenido con una cámara física
requieren QA posterior; estos pasos validan contratos, contabilidad y reglas, no
precisión clínica ni perceptiva del hardware.

El paso 3 añade 25 pruebas Python y 7 JavaScript: disco bloqueado, rollback del
presupuesto, archivo dañado al inicio o durante ejecución, recuperación consistente,
instancias concurrentes, retención, lectura, confirmación estricta y API local.
El fixture [Notification Center](../tests/fixtures/notification_center.html) usa el
componente y CSS reales con datos simulados. Se verificó en Edge headless: lectura,
filtros, error/reintento, rechazo/aceptación/revocación de disponibilidad, borrado,
tema cian/Sakura y ancho compacto de 390 px, sin overflow horizontal ni errores de
navegador. Esto no certifica el shell completo ni una sesión física con cámara.


## Paso 2: política determinista y explicable

`core/vision_policy.py` evalúa solo agregados locales. No llama a Hermes ni crea
otro agente, scheduler o canal de cámara. Cada evaluación incluye candidato, decisión,
evidencia y presupuesto en el objeto `v2`; no escribe un historial por cada muestra.

| Regla o límite | Valor inicial | Condición |
|---|---|---|
| Sesión larga | 45 minutos | Tiempo de presencia confirmado, no tiempo con IRIS abierto |
| Poco movimiento de cabeza | 40 minutos de sesión | Al menos 9 minutos observados en la ventana de 10, con 90% `still/low` |
| Confianza | 0,70 | Mínimo de señal; para movimiento también se usa la confianza de la ventana |
| Presupuesto | 2 por hora | Ventana deslizante de entregas confirmadas, no horas del calendario |
| Cooldown global | 25 minutos | Reutiliza `vision_assistant_cooldown_minutes` |
| Cooldown por tipo | 45 minutos | Independiente para cada regla |
| Horarios silenciosos | Ninguno | Hora local de Core; soporta cruce de medianoche |

Son valores iniciales de producto, no recomendaciones médicas. Los umbrales de
sesión admiten 1 minuto a 12 horas; la cobertura, 30 a 600 segundos; la proporción,
0,5 a 1; la confianza, 0,55 a 1; el presupuesto, 0 a 6; los cooldowns, 1 a 180 minutos.
Un presupuesto de cero bloquea sugerencias sin apagar la observación. Un horario
con inicio y fin iguales significa silencio todo el día.

Las dos reglas se evalúan en orden: poco movimiento de cabeza tiene prioridad sobre
sesión larga; se elige como máximo una elegible y se conserva la razón de las demás.
Los IDs dependen de sesión, tipo e intervalo de umbral, no de la ventana que solicita
la evaluación. Una sugerencia ya confirmada no se vuelve a entregar en ese intervalo.

### Actividad conocida frente a actividad desconocida

Face y Companion reutilizan la cadencia de 1,5 segundos y envían `vision_activity`
solo con Vision habilitado y sin pausa. Esto no requiere que ambas ventanas posean
la cámara: una puede comunicar que se escribe mientras la otra observa.

El contrato acepta exclusivamente booleanos o `null`: `visible`, `typing`,
`speaking`, `listening`, `quiet`, `focus`, `gaming`, `presenting` y `meeting`.
No acepta texto escrito, títulos de ventanas, procesos, capturas, audio ni vídeo.
Solo las superficies Face/Companion registradas en loopback pueden enviarlo; no se
difunde a otros clientes ni se persiste. El contexto caduca a los 8 segundos y
ocupa un máximo de 16 entradas. Perderlo implica silencio, no usuario libre.

Un campo editable enfocado se considera escritura de forma conservadora, sin leer
su contenido. Voz/escucha proceden del estado de IRIS y Quiet del ajuste confirmado
de Companion. Focus, reuniones, juegos y presentaciones aún no tienen un conector
fiable ni controles dedicados en este paso: permanecen `null`, no se detectan
leyendo otras aplicaciones. Por eso `activity_unknown` es una salida esperada,
no una autorización para intervenir. En el paso 3 se añade una declaración explícita
de disponibilidad de 15 minutos: completa los indicadores desconocidos, nunca los
positivos ni una visibilidad desconocida. No inspecciona otras aplicaciones.

Se combina el contexto de las superficies locales visibles y de la propietaria de
cámara. Cualquier indicador positivo bloquea aunque otra ventana diga lo contrario;
todos deben ser conocidos y negativos para pasar. Además, los locks de conversación,
voz y el turno activo de Core bloquean aunque un cliente declare reposo.

### Ajustes, presupuesto y recuperación

`GET /api/vision-assistant/settings` expone `policy` y el diagnóstico `v2`.
El POST existente admite cambios parciales bajo `policy`; modificar el cooldown de
un tipo conserva el otro. Los valores desconocidos, fuera de rango o de tipo erróneo
producen 422 antes de guardar. Cambiar estas reglas no activa Vision ni concede cámara.

Consultar no consume presupuesto. Una señal fiable puede entregar un aviso elegible
si se cumplen los controles del paso 3. Solo el método interno
`confirm_delivery()` puede registrar una oferta elegible, vigente durante 8 segundos,
una vez. No existe un endpoint público de confirmación de entrega.
Un cambio de actividad, una reevaluación suprimida o una interrupción invalidan
la oferta. La inserción del aviso y su recibo comparten una escritura atómica;
un fallo revierte ambos en memoria y no consume presupuesto.

Los recibos guardan solo ID, tipo y fecha: máximo 128, durante hasta 24 horas, dentro
del mismo checkpoint local. No incluyen texto, imágenes ni contexto de actividad.
Se preservan al borrar historial o poner retención cero para no reiniciar límites;
la sesión y su agregado sí se eliminan según el contrato del paso 1. Reiniciar no
cuenta tiempo apagado ni borra recibos válidos. Saltos de reloj invalidan ofertas y
aplican una guarda de 60 segundos sin eliminar cooldowns medidos monotónicamente.
La deduplicación supera el umbral máximo de 12 horas.

El paso 3 implementa entrega durable, recuperación conservadora y reintentos
limitados a partir de nuevas señales. No se promete visualización exactamente una
vez: varias ventanas pueden consultar la misma bandeja y la lectura sigue siendo
explícita. No hay toast ni animación V2 que requiera un propietario visual.


## Paso 3: bandeja local y entrega durable

### Uso y permisos

Abre **Apps → Notification Center**. La ventana usa los temas y el gestor de
ventanas existentes de IRIS. Carga la bandeja al abrirla, actualizarla, recibir
un cambio o vencer un plazo mientras está visible; cerrada no consulta periódicamente la API.
Los broadcasts solo anuncian revisión, contador y disponibilidad, sin el historial.

El control **Estoy disponible · 15 min** requiere confirmación explícita.
Completa temporalmente los indicadores desconocidos de actividad; una señal positiva
de escritura, voz, Focus/Quiet, juego, reunión o presentación siempre bloquea.
La visibilidad desconocida o las señales caducadas también bloquean. No lee procesos,
pantallas, títulos de ventanas ni texto escrito. La declaración vive solo en RAM:
caduca, puede revocarse y se pierde al reiniciar Core, pausar o liberar la observación.
No habilita Vision, cámara ni micrófono, y no sustituye sus permisos.

El tiempo de presencia puede acumularse antes de esta declaración. Declararse
disponible no reduce el umbral de 40/45 minutos ni garantiza un aviso: todas las reglas
y cooldowns siguen aplicándose. Con retención cero se desactiva el control y no se
guardan avisos nuevos.

### Guardado, lectura y fallos

- Cada candidato tiene un ID estable. Su aviso y el recibo que consume presupuesto
  se escriben juntos en `vision_events_state.json`, mediante archivo temporal,
  flush/fsync y reemplazo atómico. Se recuperan de la misma versión del archivo.
- `delivered` significa **guardado en la bandeja**, no leído, mostrado en pantalla
  ni confirmado por el navegador. Solo **Marcar leído** cambia el estado de lectura;
  se puede deshacer. Abrir, filtrar o consultar no marca avisos ni consume presupuesto.
- Se conservan como máximo 100 avisos, según la retención configurada (hasta 30 días).
  La evidencia contiene duración, confianza, regla y proporción agregada de movimiento,
  no coordenadas, imágenes, vídeo, audio ni conversaciones.
- Vaciar bandeja conserva los recibos antirrepetición. Borrar el historial de Vision
  también vacía el buzón y actualiza los contadores; mantiene los límites preventivos.
- Un fallo al guardar revierte aviso y recibo en memoria. Se espera al menos 60 segundos
  antes de volver a intentar una entrega desde señales nuevas; no hay cola de acciones
  ni reproducción automática de una intervención al reconectar.
- El archivo admite hasta 512 KiB. Un estado dañado, desconocido o con presupuesto
  inválido bloquea nuevas escrituras sin sobrescribir el original. Un cambio de revisión
  desde otra instancia evita sobrescribir una bandeja más reciente: se debe reiniciar
  Core para cargarla. No se autorrepara borrando datos.
- Si hay corrupción, conservar una copia y restaurar un checkpoint válido antes de
  reiniciar Core. La interfaz indica almacenamiento no verificable; una API de
  reparación específica queda fuera de este corte.
- Retención y borrado requieren disco escribible. Si falla la purga, las filas vencidas
  se ocultan de la respuesta y el almacenamiento se marca no disponible, pero no se
  afirma que hayan desaparecido físicamente.

### API y alcance

| Ruta local | Operación |
|---|---|
| `GET /api/notifications` | Bandeja, no leídos, retención, disponibilidad y diagnóstico |
| `POST /api/notifications/read` | `id` y `read` booleano; 404 si ya no existe |
| `POST /api/notifications/clear` | Vaciar, exige `confirm: true` booleano estricto |
| `POST /api/notifications/availability` | `available` y `confirm: true`; activar devuelve 409 con Vision apagado/pausado |

Se reutilizan las restricciones de administración local y origen confiable de Core.
No se exponen estos datos a dispositivos LAN emparejados; el modo de proxy local
explícitamente confiable conserva las reglas existentes de despliegue.

Esta entrega solo conecta **Vision** (sesión larga y poco movimiento de cabeza).
No consulta cron, runs, agentes ni proveedores; no duplica memoria o schedulers de
Hermes. Sus fuentes se conectarán detectando las capacidades oficiales correspondientes.
**Ahora no**, snooze, borrador de conversación y controles de tipos están implementados
en el paso 4. Regreso y coordinación visual Face/Companion quedan para el paso 5.


## Paso 4: elecciones reversibles y conversación explícita

### Qué puede hacer el usuario

| Control | Efecto real | Reversión |
|---|---|---|
| Ahora no | Marca ese aviso descartado y lo saca del contador activo; no desactiva su regla | Descartadas → Restaurar aviso |
| Posponer 10/30/60 min | Oculta temporalmente el mismo ID; no añade recibos ni crea una tarea | Pospuestas → Mostrar ahora |
| Silenciar nuevas sugerencias · 30 min | Bloquea nuevas entregas de Vision; no pausa sensores ni elimina avisos existentes | Quitar silencio temporal |
| No sugerir este tipo | Bloquea esa regla antes de elegir candidato; no impide otra regla elegible | Habilitar el tipo desde los controles |
| Preparar conversación | Tras confirmación, abre el chat con un borrador editable y genérico | Editar o borrar sin enviarlo |

El descarte, los plazos y los tipos viven en el checkpoint existente. Los campos
nuevos tienen valores predeterminados para migrar un buzón del paso 3 sin perder
avisos ni recibos. Vaciar bandeja y poner retención cero eliminan avisos/posposiciones,
pero conservan tipos desactivados, silencio temporal y límites antirrepetición.
El plazo no evita la caducidad del historial: un aviso vencido por retención no vuelve.

Un aviso pospuesto deja de contar como no leído activo hasta vencer. Durante ese plazo
también se suprimen nuevas sugerencias de su tipo. Al vencer reaparece como el **mismo
aviso**, no como otra intervención: no consume presupuesto de nuevo, no reproduce voz,
no abre chat y no representa una evaluación nueva de presencia.

Los plazos usan la hora de Core y sobreviven a reinicios. La interfaz usa la referencia
temporal enviada por Core, no asume que ambos relojes coinciden. Un cambio manual del
reloj del sistema puede adelantar o atrasar un plazo de calendario; no ejecuta acciones.
Se conserva la protección monotónica de la política para entregas nuevas.

### Carga y recuperación

Mientras la bandeja está visible, un único temporizador despierta en el próximo
vencimiento conocido y consulta el estado. No hay un bucle de sondeo ni un servicio
programador nuevo. Si la ventana se oculta, el temporizador no consulta; al reabrir
se obtiene el estado actualizado. Un error de red no crea reintentos infinitos:
conserva lo mostrado y ofrece Actualizar. El contador de una ventana cerrada se
reconcilia al recibir cambios o abrir la bandeja, no promete avisos de sistema.

Las mutaciones de acciones/preferencias requieren `expected_revision` y
`confirm: true` estricto. Dos ventanas con la misma revisión no pueden sobrescribirse
ni extender un snooze por repetir una petición: la segunda recibe 409 y debe actualizar.
Los fallos de guardado conservan el estado anterior en RAM y disco y devuelven 503.
La selección del plazo y el foco de teclado se preservan al refrescar cuando el control
sigue existiendo. Ninguna respuesta de error se presenta como cambio guardado.

### Paso al chat: sin envío implícito

`POST /api/notifications/conversation` solo devuelve al navegador solicitante un
borrador local (`sent=false`), basado en dos plantillas de IRIS. No incluye medidas,
confianza, fechas, imágenes o contexto de cámara; tampoco marca el aviso leído.
Los avisos borrados, caducados, descartados o aún pospuestos no preparan un borrador.

El compositor rechaza el traspaso si ya hay texto, adjuntos o archivos cargándose.
No envía el borrador, no lo inserta en otra ventana ni lo transforma en slash command.
El usuario revisa el mensaje y pulsa **Enviar** en el chat existente; recién entonces
se usa el pipeline normal, con identidad, historial, voz y permisos previamente
configurados. Este paso no concede permisos ni crea otra sesión, memoria o agente.

Se revisó la [API oficial de Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
y su [sistema de tareas programadas](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron).
Se conserva el envío existente hacia Hermes: las llamadas al agente pueden usar sus
herramientas, por lo que preparar un borrador **no** realiza esa llamada. Posponer
una fila de la bandeja no equivale a programar trabajo de un agente; no se crea cron.

### API añadida y pruebas

| Ruta local | Cuerpo |
|---|---|
| `POST /api/notifications/action` | `id`, `action=dismiss/restore/snooze`, `expected_revision`, `confirm`; solo snooze acepta `minutes=10/30/60` |
| `POST /api/notifications/preferences` | `action=mute/unmute/disable_kind/enable_kind`, revisión y confirmación; minutos solo para mute, tipo solo para enable/disable |
| `POST /api/notifications/conversation` | `id`, `confirm: true`; devuelve borrador sin llamar a Hermes |

Se mantienen las restricciones de administración local y origen confiable. Los tests
nuevos cubren 32 casos Python y 8 JavaScript: reinicios, descarte/restauración, vencimiento
sin doble entrega, contratos estrictos, revisión conflictiva, guardado fallido,
prioridad entre reglas, retención, borrador no enviado y preservación de texto/adjuntos
también durante su carga. El QA en Edge headless recorrió los controles, rechazo de
confirmación, borrador ya ocupado, reintento y temas cian/Sakura a 1120 y 390 px.

La validación visual usa el componente real con datos simulados; no sustituye QA de
cámara prolongada, varias pantallas, el ejecutable nativo ni una conversación con
proveedor real. El siguiente apartado documenta la entrega del paso 5.

## Paso 5 — resumen de regreso y presentación de propietario único

En Notification Center → **Al regresar**, el usuario puede habilitar o deshabilitar
el resumen. La preferencia `return_summary=false` es el valor inicial, también
al cargar una bandeja de los pasos anteriores. Cambiarla no activa Vision,
cámara, micrófono, chat ni herramientas.

El resumen requiere:

- Presencia previa y al menos 30 segundos de ausencia observada continuamente.
  Señales dudosas, huecos mayores de 8 segundos, cambios de reloj y transferencias
  no se convierten en ausencia. Arrancar o recargar no equivale a regresar.
- Avisos de Vision activos, sin leer, no descartados, no pospuestos y de tipos
  habilitados. No se incluyen avisos futuros ni IDs ya incluidos en otro resumen
  retenido. Un resumen no se resume a sí mismo.
- La misma disponibilidad explícita, actividad fiable, retención, silencio,
  presupuesto por hora y cooldown de las demás reglas. Regreso tiene prioridad 30
  y cooldown de tipo inicial de 45 minutos.

`ReturnEvidence` guarda duración agregada, confianza e IDs relacionados, no
imágenes ni resultados del proveedor. El aviso y su recibo de presupuesto se
escriben juntos; un fallo revierte ambos. El texto describe lo que estaba pendiente
al regresar, no afirma que haya ocurrido durante la ausencia. **Ver avisos
relacionados** filtra la bandeja, sin marcar leído. Descartar, restaurar, posponer
y preparar un borrador conservan los contratos del paso 4.

El alcance actual es el buzón de Vision. Rutinas, runs, Gateway y privacidad aún
no lo alimentan. Se revisaron las APIs oficiales de
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
y [cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/):
esta entrega no añade un planificador, no consulta resultados en cada señal y no
ejecuta una conversación para fabricar el resumen.

### Coordinación y compatibilidad

- El evento V1 `vision_assistant_event` conserva el historial, pero nunca solicita
  un saludo. Esto evita que clientes anteriores saluden al recibir un broadcast.
- Las superficies nuevas anuncian `presents_vision`. Core selecciona un único
  avatar local y visible usando la prioridad de foco/interacción del registro
  existente. No modifica la concesión de cámara ni la propiedad de TTS.
- `vision_assistant_presentation` se envía solo a ese socket y caduca a los
  8 segundos. Revalida preferencias, disponibilidad y actividad. No se redirige
  tras un fallo ambiguo ni se reenvía al conectar.
- El cliente deduplica por ID y omite el gesto estando oculto, ocupado, editando,
  en modo silencioso, con movimiento reducido o con otra animación activa.
  Usa clips ya disponibles o el fallback existente, sin recarga asíncrona que
  pueda desplazar una orden posterior de Hermes. Su JSON de animaciones no cambia.
- El recibo significa **entrega durable a la bandeja**, no prueba de lectura ni
  de reproducción del gesto. Todas las ventanas pueden actualizar el contador;
  solo una intenta el saludo. Los pospuestos reaparecen sin otro saludo.
- Renovar la misma concesión de cámara conserva la observación. Transferirla
  descarta señales transitorias y disponibilidad declarada, pero mantiene
  presupuesto e historial y conserva el agregado según el contrato de sesión.
  No se suma el intervalo desconocido de transferencia.

Preferencias: `POST /api/notifications/preferences` acepta `enable_return` y
`disable_return`, siempre con `confirm:true` y `expected_revision`. No admite
`minutes` ni `kind` para esas dos acciones. Se conservan 409 por revisión
desfasada, 503 por disco y la restricción a administración local.

Para usar el cambio hay que reiniciar Core y recargar Face. El build web de
Companion incluye el módulo, pero actualizar el ejecutable ya instalado requiere
un nuevo empaquetado nativo. Un Companion antiguo no anuncia esta capacidad y no
recibe el nuevo gesto.

### Validación del paso 5 — 2026-09-06

- Suite Python completa: **732 aprobadas, 3 omitidas**. Dos omisiones requieren
  privilegios para symlinks; la otra depende de distinguir mayúsculas en rutas,
  algo que Windows no hace en este entorno.
- Suite JavaScript completa: **93 aprobadas**.
- Nuevas regresiones: 42 Python y 7 JavaScript para duración fiable, primer
  arranque, relojes, opt-in, historial relevante, presupuesto compartido, rollback,
  recuperación, propietario único, envío fallido, replay, movimiento reducido,
  animación ocupada y renovación/transferencia de cámara.
- Build web de Companion correcto; continúa el aviso conocido del chunk de
  Three.js mayor de 500 kB. No se reconstruyó ni instaló el ejecutable nativo.
- QA en Edge headless usando el fixture aislado: confirmación aceptada/rechazada,
  activar/desactivar, explicación de ausencia, filtro de IDs relacionados,
  descarte/restauración, refresh, fallo/reintento, cian/Sakura y 390 px. Sin
  errores de navegador ni overflow horizontal.

Esta validación no certifica cámara física prolongada, varias pantallas, sistema
operativo Linux ni un proveedor real. El paso 6 se describe a continuación;
la QA física prolongada corresponde al paso 7.

## Paso 6 — ajustes locales, calibración y diagnóstico

Disponible en **IRIS OS → Notification Center → Adapta Vision a ti**. No abre
sensores al entrar, no cambia permisos y no inicia herramientas ni conversaciones.
Se reutiliza la política existente, no un planificador paralelo de Hermes.

### Perfiles, accesibilidad y feedback

| Perfil | Máximo por hora | Sesión larga desde | Separación global mínima | Poco movimiento |
|---|---:|---:|---:|---|
| Personalizado | Política existente | Política existente | Ajuste existente | Ajuste existente |
| Silencioso | 0 | Sin entregas | Ajuste existente | Sin entregas |
| Compañía | 1 | 60 min | 45 min | Desactivado por perfil |
| Bienestar | 2 | 45 min | 30 min | Desde 40 min, con ventana fiable |
| Trabajo | 1 | 90 min | 60 min | Desactivado por perfil |

Los perfiles son overrides explícitos; Personalizado recupera la política original.
Se conservan horas silenciosas, confianza, disponibilidad, actividad y recibos.
Un cooldown global más estricto del usuario prevalece. Los tipos desactivados
manualmente no se rehabilitan por cambiar de perfil, y regreso sigue siendo opt-in.

**Sugerencias físicas** puede apagarse sin declarar ninguna condición de salud.
Bloquea poco movimiento y adapta el aviso/borrador de sesión larga a prioridades.
Compañía y Trabajo también usan ese lenguaje. **Gesto de saludo** es independiente
del resumen; movimiento reducido y animaciones ocupadas continúan teniendo prioridad.

**Útil** registra un voto; **Muy pronto** añade 15 minutos al cooldown de ese tipo
(máximo efectivo 180); **No aplica** desactiva el tipo o el resumen de regreso.
Cada aviso acepta un voto: no se aplica dos veces por reintento. Restablecer intervalos
conserva votos, tipos desactivados y presupuesto. Preferencias, feedback y recibos
comparten revisión y escritura atómica; un fallo revierte memoria y disco.

### Calibración y rendimiento

La calibración requiere un clic confirmado y una cámara **ya autorizada y activa**
en una superficie local visible con capacidad explícita `tunes_vision`.
La orden es dirigida, de un solo uso y caduca en 5 segundos. No abre ni transfiere
la cámara. Enviar la solicitud no confirma su ejecución: consulta **Actualizar
diagnóstico**. Clientes antiguos reciben un mensaje de actualización necesaria.

La captura mide unos 15 segundos de ruido de movimiento: al menos 40 muestras,
continuidad de señal, p90 y umbrales acotados (still 0.3–0.6; active 1.2–2.4).
Si falta señal, se interrumpe o hay demasiado movimiento, no aplica un ajuste nuevo.
Las muestras se descartan; los umbrales solo viven en RAM durante esa captura.
Cerrar cámara, apagar Vision o restablecer borra la calibración. No identifica
personas ni calibra postura, emociones o salud. La eficacia física aún requiere QA.

Los perfiles de inferencia son **Ahorro** (hasta 5/s), **Equilibrado** (hasta 15/s,
predeterminado) y **Fluido** (hasta 30/s). No son FPS del avatar ni un límite de
captura del dispositivo. Solo limitan el análisis cuando Vision está habilitado.
Se pausa inferencia al ocultarse y durante 2 s después de tres inferencias lentas
consecutivas (≥200 ms). Una pausa de inferencia no garantiza cerrar el dispositivo:
la liberación de cámara sigue a cargo de la concesión y controles existentes.
No hay medición automática de batería ni del uso global de CPU/GPU.

Calibración, señal obsoleta y sobrecarga no se transforman en una ausencia.
También se corrigió el arranque asíncrono: cerrar cámara mientras carga MediaPipe
ya no permite reiniciar análisis después de cancelar.

### Observabilidad y privacidad

El mensaje `vision_sensor_diagnostics` admite solo contadores, latencia, estado,
progreso y umbrales escalares acotados, del dueño local de cámara con Vision activo.
No transporta imágenes, poses ni audio; un informe caduca tras 8 s. Un socket vivo
sin inferencia reciente muestra motor no disponible, no observación funcionando.

Hasta 100 transiciones de decisión se conservan **solo en RAM de Core**, con
filtros por tipo, resultado y superficie. Su contador de decisiones evitadas no
es un total histórico ni una estimación de precisión. El feedback y pendientes
se calculan sobre avisos retenidos, sin asumir que un aviso sin leer fue ignorado.
La exportación JSON es manual, filtrada y sin medios. Borrar diagnóstico conserva
avisos y límites; si Vision continúa activo, podrán aparecer nuevas decisiones.
Retención 0 elimina diagnóstico y bloquea informes nuevos.

Abrir el panel o consultarlo no genera decisiones ni inferencias. No hay polling
oculto: los informes aprovechan el ciclo de señales autorizado y la vista de
diagnóstico se actualiza bajo demanda. Los eventos normales de bandeja mantienen
su sincronización visible existente.

### Validación del paso 6 — 2026-09-06

- Python completo: **778 aprobadas, 3 omitidas** por symlinks/case de Windows.
- JavaScript completo: **105 aprobadas**. Nuevas regresiones: 46 Python y 12 JS.
- Ruff en módulos tocados, comprobación de whitespace y build web de Companion
  correctos. Continúa el aviso conocido de Three.js >500 kB.
- Dos pasadas de QA aislada en Edge: perfiles, confirmaciones, accesibilidad,
  rendimiento, calibración simulada, feedback, filtros/exportación/borrado,
  desconexión/reintento y temas cian/Sakura en escritorio y 390 px.

Para cargar estos cambios, reinicia Core y recarga Face. No se modificó la
configuración real del usuario ni se activaron cámara, micrófono o Hermes durante
las pruebas. En esa pasada solo se construyó la web; la actualización nativa
posterior queda registrada a continuación.
Paso 7: cámara física prolongada, baja luz/oclusión, equipos modestos, cambio de
pantalla/suspensión y traspaso real Face/Companion antes de declarar V2 certificada.

### Actualización nativa posterior — Companion 0.1.2

El 2026-09-06 se empaquetó Windows x64/NSIS y se actualizó la instalación local
0.1.1 a **0.1.2**, sin abrir Core, activar sensores ni modificar preferencias.
La instalación quedó reconocida y sin journal interrumpido; Vision y autoinicio
permanecieron desactivados. Se verificaron calibración, diagnóstico y cadencia
en el bundle, 76 pruebas Python de Companion y 7 pruebas nativas Rust.

El SHA-256 del instalador coincide entre manifiesto, build y paquete preparado.
El ejecutable instalado coincide byte a byte con el compilado tras considerar
exclusivamente el marcador documentado de Tauri `__TAURI_BUNDLE_TYPE_VAR_`
(`UNK` en el binario base, `NSS` en NSIS); no se alteró ninguno de los archivos.
Se conservó el instalador anterior en la salida original del build.

No se probó la ventana nativa con cámara física en esta actualización. El paquete
sigue sin firma digital; esta validación local no sustituye la QA del paso 7
ni la instalación/desinstalación en máquina limpia.
