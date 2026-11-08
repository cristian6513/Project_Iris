# Correo: Gmail en Iris OS

[← Índice](../README.md) · [Conectar Google](google-connections.md)

Revisión documental: **2026-10-26**. Correo está incluido en QA16; la validación
instalada y con cuentas reales sigue el alcance del informe de ese candidato.

Abre **Apps → Correo**. La ventana sigue el diseño de Iris: carpetas y cuentas a
la izquierda, mensajes al centro y detalle a la derecha. En ventanas estrechas
los paneles se apilan; cada control sigue siendo alcanzable con teclado.

## Empezar

1. En **Conexiones**, importa tu cliente de Google y conecta una cuenta con
   permiso de Correo. Puedes conectar varias cuentas independientes.
2. Pulsa **Actualizar Gmail** para actualizar la bandeja. Abrir Correo, buscar
   o pasar de página consulta la copia local. **Seleccionar un mensaje consulta
   su texto en Gmail**, con el permiso de solo lectura ya concedido.
3. Selecciona una cuenta o **Todas las cuentas**. Usa Bandeja de entrada,
   Destacados, Enviados, Borradores o Todo el correo. Son vistas de las etiquetas
   que Gmail ya tenía; los botones no cambian ninguna etiqueta.
4. **No leídos** e **Importantes** filtran esa copia. La búsqueda encuentra texto
   en remitente, destinatario, asunto y extracto; no busca en todo el servidor.
5. **Anterior/Siguiente** recorren la copia en páginas de 40 mensajes, del más
   reciente al más antiguo. Si una sincronización cambia los resultados, Iris
   vuelve a la primera página y lo explica, evitando saltos silenciosos.

## Qué se muestra

La lista usa **remitente, destinatario, asunto, fecha, etiquetas y extracto**
guardados por F10. Al seleccionar un correo, el detalle muestra primero ese
extracto mientras consulta el **cuerpo completo en texto**. El encabezado cambia
de «Extracto guardado» a «Mensaje» solo al recibirlo.

Si hay versiones de texto y HTML, Iris prefiere el texto. Si solo hay HTML,
lo convierte a texto y lo indica: no conserva su diseño, imágenes o botones.
No ejecuta scripts, carga imágenes remotas o abre enlaces del mensaje. No
ofrece descargar ni abrir archivos adjuntos; no verlos aquí no significa que
el correo no los tenga. Gmail puede incluir datos MIME en la respuesta completa;
Iris descarta los de archivos y recursos binarios. Si Gmail guarda el propio
texto aparte, recupera solo esa parte textual, nunca un archivo adjunto.

El cuerpo consultado permanece únicamente en la vista: no se guarda en disco
para leerlo sin conexión. Ocultar/cerrar la ventana o cambiar de mensaje cancela
la lectura anterior; un refresco visual no vuelve a pedir el cuerpo. Sin conexión,
queda el extracto y se ofrece **Reintentar lectura**. Los correos cifrados o sin
texto compatible, los cuerpos vacíos y los errores se distinguen expresamente.

La lectura tiene límites de seguridad: mensaje estimado de hasta 16 MiB,
1 MiB de partes textuales decodificadas y 200 000 caracteres, con hasta 200 partes
y 20 niveles MIME. Si los supera, Iris explica que solo puede mostrar el extracto;
no presenta un recorte como si fuera el cuerpo completo. Un adjunto grande puede
hacer que se supere el límite del mensaje aunque su texto sea pequeño.

La lectura inicial de F10 se limita a hasta 200 mensajes recientes de 30 días.
Las lecturas siguientes aplican el historial incremental a esa copia; esta puede
crecer y contener mensajes más antiguos. **Todo el correo** significa todo lo
guardado, no que se haya descargado todo tu buzón. La app muestra como máximo
50 000 registros por lectura local y avisa si la vista queda incompleta.

La fecha se presenta en la zona de este equipo. Una fecha ausente o no válida se
indica expresamente. Las carpetas pueden estar vacías porque sus mensajes no
están en la copia inicial, aunque sí existan en Gmail.

## Privacidad y estados

- Abrir un mensaje **no lo marca como leído** en Gmail.
- No hay envío, respuesta, archivado, borrado, creación de eventos ni descarga
  de adjuntos. Tampoco se habilitan las acciones de IA que aparecen en el mockup.
- Ningún mensaje se envía a un modelo salvo que actives expresamente la
  clasificación opcional descrita abajo. Conectar Google no la autoriza.
- La autorización y los tokens siguen en el servicio Google existente, protegidos
  por perfil; la app no maneja credenciales ni crea otra sesión de Google.
- Una cuenta desconectada o sin permiso no aporta mensajes visibles. Si conservaste
  sus datos al desconectar, siguen en el almacén, pero no aparecen aquí.
- Cada cuenta muestra si necesita reconexión, primera lectura, está actualizando
  o conserva una copia con error. «Actualización solicitada» no significa terminada.
- El refresco visual solo consulta la caché mientras la ventana está visible.
  La lectura automática de Google se configura por separado en Conexiones.
- Las rutas de Correo solo responden a este equipo y se sirven sin caché HTTP.

En fuente posterior a QA16, la lista indica si la actualización automática está
apagada, activa para todas las cuentas seleccionadas o solo para algunas, junto
con su última lectura correcta. **Configurar actualización automática** abre
Conexiones: no activa nada por sí mismo. Allí se puede autorizar una lectura cada
15 minutos mientras Iris permanece abierto. **Actualizar Gmail** sigue disponible
para solicitar una lectura manual.

La QA real previa de OAuth no acredita esta nueva app ni un instalador nuevo.
Consulta el informe F13 (informe interno no distribuido) y su
cierre de lectura completa (informe interno no distribuido).

## Relevancia con IA (F14)

Está apagada por defecto.
En el panel derecho abre **Configurar clasificación**:

1. Elige las cuentas y carpetas que autorizas: entrada, destacados o importantes.
2. Elige uno de tus proveedores conectados y su modelo en los selectores.
   Si aún no guardaste una elección para esta función, Iris propone el modelo
   que ya configuraste. No vuelvas a introducir credenciales: no se cambia el
   modelo del chat. Los modelos fuera de tu plan aparecen bloqueados. Si falta
   alguno, pulsa **Actualizar modelos**. Los transportes no compatibles se rechazan.
   Se usa el perfil principal de Iris, aunque abras otro perfil en el chat.
3. Indica qué consideras importante. Elige extracto o extracto más hasta 20 000
   caracteres del texto, el intervalo mínimo y el máximo de intentos diarios UTC.
4. Pulsa **Revisar destino**. Revisa proveedor, modelo, dirección del servicio y
   campos seleccionados; marca la autorización y pulsa **Autorizar y activar desde ahora**.
   Cambiar un campo requiere revisar y autorizar otra vez.

Después de activar, **la primera actualización correcta de Gmail establece una
base y no se clasifica**. Espera a que termine antes de enviar el correo de
prueba. Después solo se admiten altas nuevas del historial de Gmail; cambiar
una etiqueta de un mensaje antiguo no lo convierte en nuevo aunque no estuviera
en la caché. El historial anterior y las recuperaciones de una sincronización
antigua no se clasifican en bloque. F14 observa la caché: para
actualizarla sin pulsar Actualizar Gmail, activa su lectura automática en
Conexiones. No hay clasificación de historial por fechas en esta primera versión.

Cada resultado incluye importante, normal o incierto, un resumen y el motivo.
La UI distingue una petición en curso de un resultado no confirmado. Los
importantes aparecen también en **Bandeja**, con un botón para volver al mensaje.
**Marcar normal/importante/incierto en Iris** es una corrección local: no cambia
Gmail ni vuelve a llamar al modelo. La clasificación original queda conservada.

**Pausar clasificación** recoge el trabajo en curso y detiene los siguientes.
Una petición cancelada puede haber llegado al proveedor y consumir cuota; no se
repite sola. El tope cuenta intentos, incluso fallidos, y no garantiza un precio
o número exacto de tokens. El SDK no reintenta errores ni cambia de proveedor.
Sin herramientas, enlaces, adjuntos, imágenes o acciones tomadas del correo.

Reconectar una cuenta, cambiar sus servicios o cambiar el perfil/destino de
Hermes puede requerir revisar la autorización. Los resultados se guardan en tu
perfil; los cuerpos consultados no. Se muestran los 200 resultados y 300 avisos
más recientes. Hay un máximo de 10 000 intentos retenidos; al alcanzarlo se
detienen los nuevos, sin borrar los anteriores silenciosamente.

Detalles y límites: informe F14 (informe interno no distribuido).
