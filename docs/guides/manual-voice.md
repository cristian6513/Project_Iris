# Micrófono manual y Wake

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

[← Funcionalidades](features.md#voz-y-activación)

La habitación y el Companion tienen un único acceso rápido **Hablar**. Wake
permanece en **Configuración → Activación por voz**, con su frase y motor nativos.
No es necesario que Hermes reconozca «Hey Iris» para usar el botón manual.

## Uso

1. En Capability & Permission Center, deja **Micrófono** en **Preguntar** si
   quieres autorizarlo mediante una acción explícita. **Denegar** bloquea Hablar.
2. Pulsa **Hablar**. El panel explica el destino del audio y permite elegir
   **Transcribir mientras hablo**. Pulsa **Hablar ahora** para abrir el micrófono
   en esa intervención; cambiar la opción no empieza una grabación.
3. Habla normalmente. Hermes termina la captura al detectar silencio; puedes
   pulsar el mismo botón, ahora **Enviar**, para terminarla antes.
4. **Cancelar** o **Escape** descartan la intervención pendiente. Ocultar o
   cerrar la ventana también solicita su cancelación. El límite de seguridad
   de IRIS es de 120 segundos por captura pendiente.

El texto reconocido se envía a la **conversación actual** de IRIS. No crea la
sesión nueva que puede crear Wake. Una vez enviada al agente, cancelar la
grabación no deshace esa orden; el panel indica que ya fue enviada.

## Cambiar la voz que responde

En QA22, **Guardar voz** aplica la selección a la siguiente respuesta, sin
cerrar la ventana ni detener Core. La primera frase con un modelo local puede
tardar mientras lo carga. Esto no cambia STT ni abre el micrófono. Consulta
[Guardar y cambiar la voz](configuration.md#guardar-y-cambiar-la-voz) para las
claves guardadas, errores y cancelación durante la carga.

## Transcripción en directo (F16)

Incluida en QA16; consultar la [matriz](../estado-actual.md) para su QA instalada.
La casilla guarda únicamente `stt.streaming` en el perfil de Hermes, conservando
su proveedor y credenciales. Se aplica también a sus otras capturas compatibles,
incluido Wake; no cambia el proveedor de chat, no instala nada y no activa Wake.
Sin una preferencia previa está apagada. No se puede cambiar durante una toma.

Con un proveedor compatible aparecen las palabras en un recuadro
**Transcripción provisional · aún no enviada**. El texto puede corregirse mientras
hablas. Solo la transcripción **final** se envía a la conversación; los parciales
no ejecutan herramientas, no se hablan y no se guardan en el historial de Iris.
Cancelar, ocultar/cerrar la ventana manual o perder conexión limpia ese borrador.
Recargar no recupera ni reenvía el texto provisional.

Hermes 0.21.6 ofrece directo con OpenAI, xAI, ElevenLabs y plugins que lo
implementen. La casilla solicita ese modo, **no certifica** que el proveedor o su
credencial estén disponibles. El OAuth del proveedor de chat no garantiza una
clave de STT; OpenAI Realtime necesita una ruta compatible y credenciales propias.
Con STT local o sin soporte de directo solo verás el resultado final. Si el
directo falla o devuelve vacío, Hermes intenta transcribir la grabación de esa
misma toma: puede suponer otra petición al proveedor STT, pero no otro turno de
chat. Si también falla, verás un error y no se enviará una orden.

El directo remoto puede transmitir audio **mientras hablas**, antes del envío
final al chat. Cancelar no retira el audio que el proveedor ya recibió. El
micrófono físico, los permisos de Windows y un proveedor real siguen pendientes
de la QA instalada del candidato usado. La entrega por voz de resultados de tareas es
[F08b](automations.md), una función distinta.

## Privacidad y dependencias

- La captura está apagada al iniciar y no se persiste como habilitada.
- Se reutilizan `voice.toggle` y `voice.record` del servidor nativo de Hermes.
  No se añade un grabador de audio en el navegador ni otro detector de frases.
- La transcripción utiliza el proveedor STT configurado en Hermes: **puede ser
  local o remoto**. No debe confundirse con la detección local de Wake.
- Una ventana no puede detener la captura manual de otra. Las peticiones están
  restringidas al equipo local y sus orígenes admitidos.
- Si Wake ya estaba activo, se pausa durante la intervención y puede reanudarse
  al terminar. Si estaba apagado, Hablar no lo activa. Apagar Wake desde ajustes
  durante la intervención impide su reanudación.
- Al **cancelar**, IRIS cierra su propio servidor aislado de voz para impedir
  que una transcripción tardía se convierta en una orden posterior. Esto deja
  Wake apagado en ese runtime; se puede reactivar en Configuración. No cierra
  otras terminales Hermes ni el Gateway principal del agente.
- Si se pierde la conexión y no puede confirmarse el cierre, no se muestra una
  liberación ficticia: se conserva el error y la posibilidad de cancelar.

La disponibilidad final depende del micrófono, permisos del sistema, instalación
de voz de Hermes y configuración/credenciales STT. La reparación de Wake no
sustituye la configuración de STT. Consulta la [guía oficial de voz de Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode).

## Verificación y entrega

Las regresiones están en `tests/test_manual_voice.py`, `tests/test_voice_streaming.py` y
`tests/js/manual_voice.test.mjs`. `scripts/verify_manual_voice.mjs` comprueba
consentimiento, envío, cancelación y encaje visual en ambas superficies con una
API simulada; no activa sensores ni usa cuentas reales.

Después de actualizar el código del programa, reinicia IRIS Core y recarga la
habitación. Ese paso de desarrollo no es necesario al guardar una voz.
La versión **instalada** del Companion contiene sus propios archivos: requiere
una nueva compilación/actualización nativa para recibir el botón. Ejecutar la
compilación web no sustituye por sí solo ese ejecutable.
