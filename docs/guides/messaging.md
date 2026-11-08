# Mensajería de Hermes desde Iris

El wizard obtiene las integraciones de la instalación de Hermes que utiliza Iris.
El catálogo incluye sus plataformas y extensiones; ya no depende de una lista
fija de casillas en el navegador. La cantidad depende del motor y las extensiones
instaladas; algunas entradas son servicios técnicos y no chats personales.

Mensajería es **opcional**. En la fuente posterior a `qa11`, el Gateway se
presenta después de ella y exige un proveedor principal listo antes de arrancar
o reiniciar. Guardar un canal no concede esa preparación ni inicia Hermes.
Consulta [Estado actual](../estado-actual.md) para el candidato que lo contiene.

## Configurar un canal

1. Abre **Hermes → Canales** en el wizard. Usa **Actualizar catálogo** después
   de instalar o actualizar una extensión de Hermes.
2. Abre el canal y completa los campos publicados por Hermes. La guía del canal
   explica cómo obtener sus credenciales y qué componentes adicionales necesita.
3. Marca **Activar este canal** y pulsa **Guardar canal**. El guardado modifica
   la configuración nativa de Hermes; no hace falta terminar todo el wizard.
4. Pulsa **Reiniciar Hermes para aplicar** cuando puedas interrumpir la sesión
   actual. Después actualiza el catálogo y usa **Comprobar estado**.

Un campo vacío conserva lo que ya está guardado. Para eliminarlo, utiliza
**Borrar el valor guardado**. Las credenciales existentes no se devuelven al
navegador. Los cambios sin guardar se conservan en memoria mientras mantengas
abierta esa página, incluso al guardar otro canal.

«Guardado», «configurado» y «conectado» son estados diferentes. La comprobación
consulta el estado que informa Hermes; no envía un mensaje de prueba ni garantiza
que una cuenta concreta pueda recibirlo. La conexión API que utiliza Iris se
administra desde su propia sección y no se puede desactivar en este panel.

Algunas integraciones no publican campos editables o requieren instalaciones,
servicios, permisos o procedimientos propios. Se muestran con su guía, sin
presentarlas como listas para funcionar solamente por activar una casilla.
Las antiguas claves de mensajería guardadas únicamente en la configuración de
Iris se conservan por compatibilidad; no se activan ni migran automáticamente.
El panel nuevo muestra la configuración que realmente tiene Hermes.

## Campos obligatorios, recomendados y opcionales

La fuente posterior a QA16 muestra **Obligatorio** u **Opcional** según lo que
declara el catálogo de Hermes. Si no aporta ese dato, indica **Según configuración**.
En Telegram, **Usuarios autorizados** se marca **Recomendado** para facilitar el
primer uso, sin convertirlo en un requisito del backend.
La ayuda de cada campo explica su uso; estas etiquetas no cambian los requisitos
del canal ni guardan valores automáticamente.

En Telegram, el token del bot se obtiene de @BotFather. Para autorizar tu cuenta:

1. Abre Telegram y envía `/start` a **@userinfobot** para consultar tu ID numérico,
   como explica la [guía de Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram#step-4-find-your-user-id).
2. Copia el ID de **tu cuenta personal** en **Usuarios autorizados de Telegram**.
   No uses tu `@usuario`, el ID de tu bot ni su token. Separa varios IDs con comas.
3. Pulsa **Guardar canal** y **Reiniciar IRIS para aplicar**; después escribe a tu bot.

Si no configuras esa lista y no hay usuarios ya aprobados, el bot puede responder
con un código de vinculación. **Iris todavía no tiene una opción para aprobar
esos códigos desde su interfaz**; Hermes permite hacerlo por terminal. Para
configurarlo desde Iris, añade el ID personal como se indica arriba. Los usuarios
ya aprobados pueden conservar su configuración sin rellenar esta lista.

**Secreto del webhook**
indica **Opcional · solo con webhook**: no hace falta para la conexión normal,
pero es obligatorio si se configuró una URL de webhook. En ese caso se crea un
secreto aleatorio propio, distinto del token del bot.

Un valor vacío conserva el guardado; para retirarlo se mantiene la acción
**Borrar el valor guardado**. Un campo opcional puede ser necesario al elegir
una función avanzada: su etiqueta no sustituye esa condición.

## WhatsApp

El canal WhatsApp ofrece **Vincular WhatsApp con QR**. Selecciona el modo de uso,
pulsa **Mostrar QR** y escanéalo desde los dispositivos vinculados de WhatsApp.
El primer inicio puede tardar mientras Hermes prepara su puente de WhatsApp.

Después de vincular la cuenta, pulsa **Guardar vinculación** y reinicia Hermes.
El QR se genera localmente en Iris. Una recarga del navegador recupera la
operación pendiente mientras siga abierto el backend del wizard; cerrar el
backend cancela las sesiones pendientes de ese proceso. Cancelar el proceso de
vinculación no equivale a desvincular una cuenta ya autorizada en WhatsApp.

## Canales disponibles en la versión instalada

Cada canal del catálogo indica si el motor que usa Iris puede iniciarlo ahora.
La comprobación es pasiva: mira los componentes ya instalados y los programas
que el canal necesita; nunca instala nada. En la versión instalada de Iris,
Hermes no instala paquetes al activar un canal y solo ve sus propios programas,
así que un canal sin sus componentes aparece como **No disponible**, con el
motivo. No se puede activar ni recibir valores nuevos; sí se puede desactivar
o borrar lo que tuviera guardado de una instalación anterior. El backend aplica
la misma regla aunque se omita la interfaz.

Con Hermes 0.21.6 en Windows (igual que con 0.21.4), la versión instalada deja fuera estos canales:

| Canal | Motivo |
| --- | --- |
| WhatsApp (QR) | El puente con Node.js no forma parte del paquete. **WhatsApp Cloud API** sí está disponible. |
| Matrix | Hermes no lo admite en Windows (cifrado sin wheel para Windows). |
| Microsoft Teams, Google Chat, DingTalk, Feishu / Lark, WeCom (app) | Sus paquetes no forman parte del runtime incluido. |
| Photon (iMessage), Raft | Necesitan Node.js o la CLI de Raft, que el paquete no ofrece. |

Telegram, Discord, Slack, WhatsApp Cloud API, Signal (con su puente REST),
correo, SMS, Mattermost y el resto del catálogo se configuran normalmente.
Hermes 0.21.6 retiró el canal de Home Assistant; ya no aparece en el catálogo.
En desarrollo, Hermes puede instalar componentes al iniciar un canal; esos
canales se muestran disponibles con esa advertencia.

## Texto legible y avatar

La identidad compartida conserva la personalidad y pide texto natural en los
chats. El esquema de voz, emoción y animación sigue formando parte de las
peticiones de Iris a la API de Hermes.

Al guardar un canal o pulsar **Preparar mensajes legibles**, Iris instala su
extensión `iris-messaging` en el perfil utilizado por Hermes. Esta extensión
extrae `texto_hablado` de las respuestas que contienen el envoltorio del avatar.
Conserva el JSON ordinario, el texto solicitado por el usuario y los marcadores
de adjuntos. Si reconoce un envoltorio incompleto, muestra un aviso legible.

La preparación desactiva la presentación incremental y los mensajes intermedios
en los canales humanos, para que el envoltorio no aparezca durante la generación.
El streaming del modelo y la API del avatar conservan su configuración. Hace falta
reiniciar Hermes para cargar la extensión. La preparación no reescribe mensajes
ya enviados ni el historial de sesiones antiguas.

## Verificación y mantenimiento

- `tests/test_messaging.py`: extracción, separación del avatar, protección local,
  credenciales, QR y separación entre guardar y reiniciar.
- `scripts/verify_messaging_native.py`: catálogo, escritura y borrado, validación
  previa, hooks, envío directo interceptado y ciclo QR con el Hermes instalado.
  Usa un perfil temporal, bloquea la red y sustituye el puente de WhatsApp.
- `scripts/verify_messaging.mjs`: HTML y JavaScript reales del wizard en Edge,
  con respuestas HTTP simuladas; incluye recarga, cancelación y vista móvil.

Los contratos del adaptador se deben volver a comprobar al actualizar Hermes:
usa las APIs de su dashboard y un punto interno para entregas directas. Estas
pruebas no sustituyen una prueba con una cuenta real de cada proveedor.

[← Índice de documentación](../README.md)
