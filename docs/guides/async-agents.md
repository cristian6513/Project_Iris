# Async Agents

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

El panel de Face tiene dos pestañas:

- **Creados por ti**: tareas independientes que se crean desde el formulario del panel. Permite consultar su estado, leer el resultado y solicitar su detención cuando Hermes ofrece esa capacidad.
- **Desde el chat**: delegaciones asíncronas reales iniciadas por el asistente. Muestra las de la identidad y el perfil actuales, incluso las de conversaciones anteriores de esa identidad. Una delegación puede contener varios agentes; la tarjeta indica cuántos.

Cada pestaña tiene sus contadores y recuerda su selección mientras el panel está abierto. Los estados se actualizan cada 12 segundos mientras la aplicación está visible, o al pulsar **Actualizar**. Al seleccionar una tarea se muestra su resultado. Este cambio no reproduce automáticamente los resultados por voz ni añade respuestas al chat.

## Tareas en segundo plano desde la conversación

En fuente posterior a QA16, Iris puede dejar un encargo largo trabajando y seguir
conversando contigo. Por ejemplo: «Busca las noticias más importantes de hoy». Iris
responde «Voy a revisarlas; puedes seguir hablando conmigo». Puedes preguntarle
la hora mientras tanto, y al terminar te presenta el resultado con su voz.

- **Lo decide Iris** por el tipo de encargo: investigar, comparar o revisar mucho
  contenido. Lo rápido y lo que ya está a mano lo responde directamente. No hace
  falta mencionar agentes.
- **Solo promete avisar cuando la tarea ya empezó.** Core la envía al motor de IA
  como una ejecución propia, con su propia sesión, y espera a que el motor la
  acepte.
- **«Trabajando en…»** aparece sobre el cuadro de texto del chat y en el
  Companion, con **Cancelar**. Cancelar detiene la tarea. Un mensaje nuevo o
  «Detener» solo detienen la respuesta en curso, nunca la tarea.
- **Entrega:**
  - Al terminar, Iris espera un hueco: nunca interrumpe una respuesta ni su voz.
    Entonces te presenta el resultado.
  - Si cambiaste de conversación o de perfil, o no hay una ventana de Iris
    visible, el resultado queda en la **Bandeja** con el origen «Tareas en
    segundo plano», sin mezclarlo en otra conversación.
  - Un fallo, una tarea que se detuvo por tardar demasiado o una cancelación
    también se te dicen.
  - Iris no ve esos avisos ni lo que quedó en la Bandeja. Core se lo cuenta una
    vez, en una nota al principio del siguiente mensaje de esa conversación; esa
    nota no se guarda en la memoria de Iris.
- **Límites:**
  - Una tarea a la vez, y 10 minutos como máximo.
  - Una tarea que espera una aprobación se detiene, porque en segundo plano
    nadie puede darla.
  - Delegar no da permisos nuevos.
  - El turno que presenta el resultado no puede iniciar otra tarea.
  - El informe del agente se trata como información, nunca como órdenes.
- **Inicio sin confirmar:** si el motor de IA no responde al iniciar una tarea,
  puede que esté en marcha igualmente. Iris lo dice así, sigue comprobándolo y el
  indicador muestra «Comprobando si empezó». Si la cancelas o pasa su plazo sin
  confirmarse, Iris ya no puede averiguarlo sin arriesgarse a crear la tarea.
  - El indicador muestra «Sin confirmar» con **Liberar**, que pide un segundo clic
    explicando que, si empezó, puede seguir en el motor de IA.
  - Hasta entonces no se puede iniciar otra tarea, ni siquiera con el paso del
    tiempo.
- **Reinicio:** las tareas y sus entregas se guardan en `background_tasks.db`.
  - Tras reiniciar, Iris sigue las que estaban en marcha.
  - Una que se estaba iniciando se da por interrumpida, sin volver a enviarla.
  - Una entrega que quedó a medias pasa a la Bandeja, así que no se anuncia dos
    veces.
- **Origen:** solo desde chat o voz en este equipo. Telegram, las
  automatizaciones y los dispositivos de la red no pueden iniciarlas.

Informe: tareas en segundo plano (informe interno no distribuido).

## Almacenamiento y compatibilidad

Las tareas manuales se registran en `async_agents.db`, dentro del directorio de datos de IRIS. El índice conserva sus identificadores y el último resultado consultado entre recargas y reinicios. Si Hermes no responde, el detalle puede mostrar la copia guardada. Las tareas manuales anteriores a este cambio, cuyos identificadores se perdieron, no se pueden recuperar mediante un listado global en las versiones de Hermes que no lo ofrecen.

Las delegaciones del chat se consultan en modo de solo lectura en la tabla `async_delegations` del `state.db` de Hermes, respetando el perfil activo. Su retención depende de Hermes; IRIS no altera estados, entregas ni contenido de ese registro. Esta integración requiere Hermes local. Una conexión remota muestra una explicación en lugar de consultar el registro de otro gateway.

Se muestran hasta 50 tareas recientes por origen. Los resultados se presentan como texto, sin ejecutar HTML ni mostrar metadatos de animación o transcripciones internas de los agentes.

Continuity Center, cron y Async Agents comparten la lectura del campo `texto_hablado` de las respuestas de IRIS, también cuando viene en bloques Markdown o dentro de resultados anidados. El contenido de usuario y los ejemplos de código o JSON ajenos a ese contrato se conservan. Los resultados manuales guardados se normalizan al consultarlos, sin migrar el historial ni volver a ejecutar tareas.

## Verificación

Pruebas de API y almacenamiento: `python -m pytest tests/test_async_agents.py tests/test_os_runtime.py -q`.

Pruebas de selección, concurrencia y ciclo de vida: `node --test tests/js/async_agents_panel.test.mjs tests/js/async_agents_state.test.mjs tests/js/os_app_lifecycle.test.mjs`.

Prueba de navegador con datos sintéticos: `node scripts/verify_async_agents.mjs --playwright-module <ruta-a-playwright>`. Usa Edge en Windows. Comprueba pestañas, teclado, resultados, contadores, creación manual, recarga y ancho móvil; guarda capturas en `artifacts/async-agents-qa`. No requiere iniciar Core ni ejecuta tareas reales en Hermes.
