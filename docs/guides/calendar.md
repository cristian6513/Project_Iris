# Calendario de IRIS OS

**Incluido en QA16.** La validación automática y la QA instalada en Windows
tienen el alcance que registra el informe del candidato.

Revisión documental: 2026-10-26. El [estado actual](../estado-actual.md) y el
informe F11 (informe interno no distribuido) identifican la
implementación y sus límites de validación.

## Abrir y consultar

Abre **Apps → Calendario**. Conecta tu cuenta desde **Conexiones** y concede
Calendario en modo lectura. Puedes conectar más de una cuenta; Calendario usa las
mismas conexiones y los datos guardados por ese servicio.

Elige **Día, Semana, Mes o Agenda**. Puedes avanzar de periodo, volver a **Hoy**,
buscar dentro del periodo visible y seleccionar una fecha en el minicalendario.
La Agenda abarca 31 días desde la fecha elegida. La columna derecha muestra los
eventos del día y los detalles del evento seleccionado.

**Mis calendarios** permite ocultar calendarios individuales. El selector de
cuenta filtra la vista. Elige tu zona horaria para ver las horas locales; los
eventos de todo el día conservan sus fechas de calendario. Las preferencias se
guardan en tu perfil de Iris.

Abrir la app solo consulta la caché local. **Actualizar desde Google** solicita una
lectura a Google de las cuentas conectadas que concedieron Calendario. No activa
la sincronización periódica ni cambia los permisos de Correo. Los datos guardados
incluyen la fecha de su última lectura. Un error de conexión y una vista parcial
se muestran como tales, sin presentarlos como un calendario vacío.

Esta conexión muestra **eventos**, no tareas de Google Tasks. Google permite ver
ambos en su propia web, pero son datos distintos. Una tarjeta con **Mis tareas**
y **Marcar como completada** es una tarea: no aparecerá por actualizar eventos.
Para un cumpleaños en esta integración, créalo en Google como evento de todo el
día o cumpleaños, no como tarea. Iris no convierte ni modifica la tarea existente.
Véase la [ayuda de Google sobre tareas en Calendar](https://support.google.com/calendar/answer/9901136).

En fuente posterior a QA16, el botón muestra **Actualizando desde Google…**
mientras Core confirma que la lectura sigue en curso. **Actualizar notas de IA**
solo relee las preparaciones guardadas; no solicita eventos a Google.
Una excepción antigua fuera del periodo visible ya no deja incompleta esa vista.
Si falta el evento original de una repetición dentro del periodo, Iris lo explica
y conserva el aviso; no inventa el evento ausente.

## Recordatorios opcionales

Pulsa **Activar recordatorios** y confirma. Están apagados inicialmente.
Iris prepara un aviso visual **60 minutos antes** y otro **al inicio** de cada
evento con hora. No hay voz, llamada a un modelo ni apertura automática de la
cámara. Los eventos de todo el día no generan estos avisos.

Los recordatorios se evalúan en Core aunque cierres la ventana Calendario. Iris
debe seguir ejecutándose; no es un servicio de Windows que funcione con Iris
cerrado. Después de una suspensión solo se admite un aviso hasta cinco minutos
después de su hora prevista y antes de su vencimiento. No se reproducen reuniones
ya terminadas ni todos los avisos antiguos al volver.

Los avisos quedan en **Notification Center → Bandeja → Calendario**, donde puedes
marcarlos como leídos, posponerlos, descartarlos o restaurarlos. Mostrar un aviso
o abrir Calendario no lo marca como leído. El silencio temporal de Bandeja también
se respeta. Si un envío no se puede confirmar, su estado queda sin confirmar y no
se reenvía automáticamente a otra ventana.

Desactivar los recordatorios, ocultar un calendario, desconectar su cuenta o
recibir la cancelación de un evento retira sus avisos activos cuando el estado
correspondiente está disponible. Para conocer cambios de Google hace falta una
lectura actualizada; Iris no puede detectar una cancelación remota sin ella.

## Preparar una reunión con IA (F15)

Selecciona un evento y pulsa **Preparar con IRIS**. Esta función es opcional y
necesita una autorización por petición. No se activa al conectar Google ni junto
con los recordatorios.

1. Elige uno de tus proveedores conectados y su modelo en los selectores.
   Al empezar se propone el modelo que ya configuraste en Iris. No hay que volver
   a escribir credenciales ni cambia el modelo del chat o de visión. Puedes pulsar
   **Actualizar modelos**; los que no incluye tu plan están bloqueados.
   Los transportes no compatibles se rechazan antes de enviar contexto.
   Se usa el perfil principal de Iris, aunque abras otro perfil en el chat.
2. Añade tus notas si quieres (hasta 4 000 caracteres) y elige si incluyes la
   descripción del evento (hasta 12 000). Por defecto solo se incluye título y
   horario. No se envían correo, adjuntos, asistentes, ubicación ni enlace de reunión.
3. Pulsa **Revisar fuentes y destino**. Comprueba el texto y la dirección del
   proveedor que aparecen. Esta revisión no llama al modelo. Editar un campo
   invalida la revisión; vence después de cinco minutos.
4. Pulsa **Generar notas con estas fuentes** y confirma. Puede gastar cuota del
   proveedor elegido. Iris hace una petición, sin herramientas ni acciones en Google.

Las notas guardadas contienen un resumen, puntos a considerar, preguntas y las
fuentes usadas. Revisa las sugerencias de la IA. **Ver fuentes guardadas** muestra
la copia que se envió, que puede diferir del evento actual. Sin seleccionar un
evento puedes abrir **Ver preparaciones guardadas** para consultar el historial.
Las fuentes y notas quedan en tu perfil local; no se afirma cifrado del contenido.

Puedes cerrar la ventana mientras trabaja; Core debe seguir abierto. **Cancelar
preparación** detiene su trabajo propio y descarta resultados tardíos, aunque
la cuota ya puede haberse consumido. Si falla, cambia el evento o se desconecta la
cuenta, los recordatorios horarios siguen funcionando. Iris solo conoce cambios
remotos una vez actualizada la caché de Google.

El máximo es de 10 intentos diarios UTC por perfil, incluidos fallos y cancelaciones.
Un envío interrumpido puede quedar **sin confirmar** al reiniciar; no se repite
solo. Para pedir otra preparación tienes que revisar y autorizar una petición nueva.
La vista conserva 200 resultados recientes y el almacén admite 10 000 intentos.
En **Bandeja → Preparación IA** puedes marcar leído o descartar el resultado;
descartarlo no elimina sus fuentes ni modifica Google. No hay voz automática.

El informe F15 (informe interno no distribuido)
identifica las pruebas y límites. Su QA instalada sigue pendiente.

## Unirse y límites actuales

**Unirse a la reunión** aparece cuando el evento incluye un enlace de conferencia
HTTPS válido. Solo se abre al pulsarlo. No se deduce de la descripción del evento
ni lo inventa una IA. Su validación comprueba el formato y el dominio público;
no verifica el contenido del destino ni sus redirecciones.

Esta entrega permite leer eventos. No crea ni edita reuniones en Google. El
[widget del escritorio](widgets.md) está implementado en fuente con F12; comparte
los datos y preferencias de esta app. La preparación IA F15 vive en la app y
requiere su propio consentimiento. Las recurrencias incompatibles o demasiado costosas se omiten con un aviso
de vista parcial; no se promete interpretar cualquier regla de recurrencia.

[← Documentación](../README.md)
