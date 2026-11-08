# Automatizaciones y Bandeja

[← Índice](../README.md) · [Estado y candidatos](../estado-actual.md)

## Crear una regla

Abre **IRIS OS → Automatizaciones → Nueva regla**. Define nombre, tarea para
Hermes, minutos sin usar Iris, modelo, plazo y máximo diario. La regla se guarda
**en pausa**. Revisa el texto y pulsa Activar: la confirmación explica el trabajo
y el límite. El permiso **Automatizaciones** se administra aparte en Capability
Center; la app no lo concede por crear o activar una regla.

Una automatización puede ejecutar herramientas y consumir cuota del proveedor
de Hermes. Empieza con una tarea inocua y un límite bajo. Pausa la regla cuando
ya no la necesites. Cambiar el modelo de la regla no cambia el del chat.

## Qué significa inactividad

**Iris sin usar** mide la interacción dentro de Iris. **Windows sin usar**
(F08a, incluido en QA16) consulta el tiempo de la última
entrada de teclado/ratón de tu sesión de Windows. Son fuentes distintas: leer
sin tocar nada puede contar como inactividad y ninguna demuestra ausencia física.

La observación de Windows empieza apagada. Actívala en la tarjeta
**Inactividad de Windows**, confirma y elige esa condición en tu regla. No lee
lo que escribes, títulos de ventanas ni imágenes. No requiere cámara o micrófono.
Funciona minimizado mientras Core siga abierto. Solo suma muestras continuas
posteriores al permiso; bloqueo, desconexión de sesión, suspensión o fallo
cortan la continuidad y no completan el tiempo de espera.

Desactivar la observación impide nuevas ejecuciones por esa fuente; no detiene
una tarea que Hermes ya recibió. **Ausencia ante la cámara** sigue sin ofrecerse
como disparador habilitable en este corte.

El motor comprueba condición, permiso y presupuesto antes de enviar el trabajo.
No recrea la tarea cada cinco minutos por el simple hecho de seguir inactivo.
Un periodo no observado, una suspensión o una cámara apagada no demuestra
ausencia. Core y Hermes deben seguir funcionando para ejecutar la regla;
cerrar la app Automatizaciones solo cierra la vista.

## Historial y cancelación

Cada ejecución tiene su propio resultado: en preparación, en curso, completada,
fallida, cancelada o sin confirmar, entre otros estados. **Deteniéndose** indica
que se pidió parar, no que Hermes haya confirmado el fin. Un timeout o respuesta
perdida tampoco demuestra que no se ejecutó; Iris no debe reenviarla a ciegas.

El historial muestra el resultado legible y las acciones que admite esa
ejecución. Una cancelación local no deshace efectos que una herramienta ya haya
realizado. El pin actual no proporciona todos los límites de herramientas o
tokens que describe el diseño futuro; la UI explica lo que está disponible.

## Bandeja y regreso

En **Notification Center → Bandeja** se consultan resultados y avisos. Mostrar
un aviso no lo marca como leído. Posponer, descartar, restaurar y silenciar
tienen efectos distintos y se conservan según el contrato del origen.

La entrega al volver elige una superficie presentadora y no abre una conversación.
Por defecto es visual. Para leer un resumen de resultados (F08b, incluido en QA16):

1. Activa **Entregas por voz** en Automatizaciones y confirma el uso de tu motor
   de voz. Si el motor es remoto, recibirá ese resumen.
2. Marca **Leer un resumen al volver** en cada tarea que lo permita.
3. Mantén una superficie local actualizada, visible y con foco. No puede haber
   chat, escritura, micrófono o Hey Hermes activos. El silencio de Bandeja, los
   horarios de la regla y **Focus de Iris** bloquean el resumen hablado.

Focus de Iris silencia estos resúmenes, sin pausar trabajos ni borrar resultados.
También se consulta el estado de notificaciones disponible en Windows; no se
promete detectar todas las variantes de Focus/No molestar del sistema. Para
asegurar silencio usa el control de Iris.

El resumen es breve, de hasta tres tareas, y usa resultados ya guardados: no
pide otra respuesta a una IA. Los avisos de Vision y Calendario conservan su
entrega silenciosa. Si la superficie no está disponible, queda el texto; no se
crea una reproducción automática tardía. Un intento sin recibo, una recarga o
un fallo de autoplay no provoca reintentos. La Bandeja distingue reproducido,
fallido y sin confirmar; reproducir no marca leído ni prueba que lo hayas oído.

Los recordatorios
de [Calendario](calendar.md), añadidos en F11, comparten la Bandeja pero siguen
sus propios horarios y permisos. Conectar Google no crea reglas ni autoriza
enviar correos al modelo.

## Estado de distribución

QA16 incluye F04–F08 y las correcciones del almacén posteriores a `qa11`.
Los fallos C01/C02 del candidato antiguo conservan su registro histórico.
F08a/F08b tienen su validación propia (informe interno no distribuido);
la observación nativa de Windows y la reproducción real de voz requieren la QA
instalada correspondiente. F14/F15 (clasificación de correo/preparación IA) siguen separados. Consulta la
[matriz vigente](../estado-actual.md) y la LISTA-QA del candidato que estés usando.
