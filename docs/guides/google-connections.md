# Conexiones: cuentas de Google

[← Índice](../README.md) · [Estado y candidatos](../estado-actual.md)

Revisión documental: 2026-10-16. La app y la configuración de cliente personal
se encuentran en fuente; su integración en el próximo instalador sigue pendiente.
La QA real previa del login no acredita todavía esa instalación.

**Conexiones** es la app de IRIS OS donde conectas cuentas de Google para que Calendario y Correo puedan leerlas. También se abre desde **Ajustes → Cuentas conectadas**.

## Qué hace y qué no hace

- **Solo lectura.** Iris pide permisos de lectura de Calendario (`calendar.calendarlist.readonly` y `calendar.events.readonly`) o de Correo (`gmail.readonly`), además de la identidad de la cuenta (`openid` y `email`). Nunca crea eventos, envía correo, borra ni marca nada como leído.
- **Abrir la ventana no conecta ni lee nada.** La autorización empieza solo cuando pulsas «Conectar Google». La lectura empieza solo con «Actualizar datos» o, si la activas, con la actualización automática de un servicio.
- **La autorización ocurre en tu navegador**, en la página de Google. Iris no te pide la contraseña ni un código. Mientras tanto, Iris espera en `127.0.0.1`, en tu equipo.
- **Puedes conceder solo una parte.** Conexiones muestra lo que Google concedió de verdad: «Permisos parciales» cuando falta un servicio.
- **Conectar Google no activa IA.** Correo F13 presenta la copia local y consulta el texto de un mensaje al seleccionarlo. La [clasificación opcional F14](mail.md#relevancia-con-ia-f14), disponible en fuente, requiere su propio consentimiento para cuentas, campos y destino; solo al activarla puede enviar correo al modelo. La [preparación de reuniones F15](calendar.md#preparar-una-reunión-con-ia-f15) requiere revisar fuentes y autorizar cada petición por separado.
- **Varias cuentas.** Cada una es independiente. Una cuenta ya conectada no se puede añadir dos veces, y reconectarla exige la misma cuenta de Google.

## Cómo se guardan las credenciales

- Están en tu perfil de Iris (`connections/google/`), cifradas con la protección de Windows (DPAPI) para tu usuario de Windows y para ese perfil.
- Si restauras el perfil en otro usuario u otro equipo, tendrás que volver a conectar las cuentas.
- Ninguna ventana, registro ni mensaje de Iris muestra un token.
- La sesión que la skill Google Workspace de Hermes pueda tener por su cuenta es independiente: Iris no la lee, no la copia y no la usa.

## Desconectar y revocar

- **Desconectar** hace que Iris deje de usar la cuenta en este equipo. El permiso que diste sigue en tu cuenta de Google.
- **Revocar también el acceso en Google** pide además a Google que retire el permiso de Iris. El resultado se muestra tal cual: revocado, no revocado o no confirmado. Revocar puede afectar a otras instalaciones de Iris conectadas con la misma cuenta.
- **Borrar los datos** elimina lo que Iris leyó de esa cuenta. Si no lo marcas, los datos se conservan.
- **Quitar de la lista** olvida una cuenta desconectada.
- Mientras una revocación está pendiente, Iris espera antes de permitir otra autorización. Quitar la cuenta de la lista no detiene una petición ya enviada a Google. Si Iris se cerró sin obtener el resultado, lo muestra como **no confirmado** y no vuelve a enviar la revocación por su cuenta. Google puede tardar en aplicar una revocación, incluso después de responder.

## Alcance de las lecturas

- **Calendario:** Iris lee la lista de calendarios y conserva los eventos de cada uno con sus cambios posteriores. Las series recurrentes se guardan sin expandirlas indefinidamente.
- **Correo:** la lectura inicial busca hasta 200 mensajes de los últimos 30 días. Después aplica el historial de cambios de Gmail a la caché local: esta puede superar los 200 mensajes, conservar mensajes que envejecen e incorporar uno antiguo si cambia, por ejemplo, una etiqueta. No es una ventana móvil limitada a 30 días o 200 mensajes.
- El recuento de Conexiones corresponde a los datos guardados actualmente. Los campos de cobertura `window_days`, `max_messages`, `messages` y `complete` describen la **lectura inicial**; `limits_apply_to: initial_read` y `maintained_window: false` lo indican expresamente. No significan que se haya leído todo el buzón.
- Desconectar no borra esta caché salvo que marques **Borrar los datos**. Nada de lo leído se entrega a la IA por conectar o actualizar.

## Configurar tu cliente de Google

Iris usa un cliente OAuth de Google de tipo **escritorio** («installed»), con los endpoints de Google. Iris rechaza cualquier otro tipo de cliente y cualquier otro servidor.

**Estado:** esta configuración personal está incluida en QA16; su QA instalada
conserva el alcance del candidato. La conexión con un cliente oficial
compartido de Iris queda reservada y desactivada.

1. Abre **Conexiones → Configura el acceso a Google**.
2. Pulsa **Importar archivo JSON** para seleccionar el archivo descargado de
   Google Cloud, o pega su contenido en el cuadro **Contenido JSON del cliente OAuth**.
3. Pulsa **Guardar configuración**. Se valida antes de sustituir la anterior;
   un archivo incorrecto no la borra. El límite es de 64 KB.
4. Elige Calendario o Correo y pulsa **Conectar Google**. El JSON identifica la
   aplicación; después autorizas tu cuenta en el navegador de Google.

El archivo se procesa localmente. Los campos necesarios quedan cifrados con
DPAPI dentro de tu perfil de Iris, fuera de la carpeta web y del instalador.
Tras guardar se limpia el formulario. Una actualización de Iris no requiere
volver a copiar el JSON al programa instalado.

**Cambiar configuración** permite importar otro cliente. Primero debes desconectar
las cuentas activas y terminar las autorizaciones, lecturas o revocaciones
pendientes. Los tokens pertenecen a su cliente original; no se transfieren a otro.
Después conecta de nuevo con el cliente elegido. **Quitar configuración** pide
confirmación y conserva los datos de las cuentas ya desconectadas.

Para desarrollo se conserva `IRIS_GOOGLE_OAUTH_CLIENT` como alternativa inicial,
con un archivo fuera del repositorio y de los assets web. Una configuración
personal guardada tiene prioridad; retirarla no reactiva ese archivo por sorpresa.
La versión instalada ignora esa variable y no importa un cliente compartido del
instalador mientras esa función permanezca desactivada.

**Tu proyecto de Google Cloud necesita:**
- la API de Google Calendar y la API de Gmail activadas;
- una pantalla de consentimiento con los permisos de arriba y la cuenta de prueba añadida como usuario de prueba, mientras el proyecto esté en modo de pruebas;
- un cliente de tipo «Aplicación de escritorio».

**Límites de Google que conviene conocer:**
- En modo de pruebas, Google puede caducar los permisos a los 7 días; después hay que reconectar.
- `gmail.readonly` es un permiso restringido. La distribución pública requiere
  revisar los requisitos y las excepciones aplicables de Google; importar un
  cliente propio o pasar una QA no acredita esa verificación. Véanse los
  [requisitos oficiales](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

## Consultar desde el chat

Cuando se lo pides, Iris puede consultar Conexiones, listar extractos de Correo, leer el texto de un mensaje seleccionado y listar hasta 31 días de Calendario mediante su puente de apps. Usa las cuentas de Conexiones; no requiere otro inicio de sesión en Hermes.

El permiso **Correo y Calendario en el chat** se pide por separado: conectar Google no lo concede. El contenido seleccionado pasa al proveedor de IA de esa conversación. Puedes cancelarlo o denegarlo en el centro de permisos. El puente no envía mensajes, no marca como leído, no cambia eventos ni abre imágenes o adjuntos.

Las listas usan la copia sincronizada de Iris y muestran sus avisos de cobertura. Si necesitas datos recientes, usa Actualizar en Correo o Calendario. No se activa una sincronización nueva simplemente por abrir el chat.
