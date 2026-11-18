# Iris OS · Ficha para Microsoft Store

Envío 1 en borrador, con el texto de la ficha en español.

Revisado el 2026-11-17 según las [políticas de la Store, versión 7.20](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies):
- **10.2.4:** la dependencia de un proveedor de IA va al principio.
- **10.2.2:** las ampliaciones con código quedan descritas.
- **11.16:** se declara la IA generativa y se ofrece una vía para reportar contenido.

Antes de enviarlo, revisar cada función contra el MSIX que se vaya a certificar. Las partes entre corchetes son decisiones pendientes y no se copian a Partner Center.

## Descripción breve

Asistente de IA para Windows con avatar 3D, chat y voz. Requiere una cuenta con un proveedor de IA compatible o un modelo local en tu equipo.

## Descripción

Iris OS necesita un proveedor de IA para conversar. Puedes iniciar sesión con la cuenta de un proveedor compatible, usar tu propia clave de API o configurar un modelo local si tu equipo lo admite. Los proveedores remotos requieren conexión a internet y pueden cobrar según su uso.

Iris OS es un asistente de escritorio con un avatar 3D animado. Habla con Iris por chat o por voz: te responde con su voz y sus gestos. Vive en su propia habitación, donde lee, trabaja o descansa, y lo deja para atenderte cuando le hablas.

Iris usa IA generativa en vivo: el modelo que elijas crea cada respuesta en el momento, a partir de tus mensajes, y puede equivocarse. Si ves contenido inapropiado, puedes reportarlo a [correo de soporte pendiente].

Iris puede usar herramientas para hacer tareas en tu equipo, dentro de los permisos que configures. También puedes ampliarla, siempre por decisión tuya:
- con habilidades y conectores para el asistente, que buscas e instalas desde el catálogo integrado y que publican terceros;
- con apps para su escritorio, desde un archivo .zip o una carpeta que elijas. Algunas apps incluyen código Python que se ejecuta con tus permisos de Windows. Antes de instalar una app, Iris te muestra lo que trae y te pide confirmación.

Nada se enciende sin tu permiso: el micrófono, la cámara y la captura de pantalla tienen permisos separados. Conectar tu cuenta de Google es opcional y solo da acceso de lectura a Calendario y Gmail.

Personaliza su apariencia (también con tu propio avatar VRM o PMX), su voz, su personalidad, los temas y el escritorio, con apps, accesos y widgets.

Iris OS está en beta: algunas funciones pueden cambiar.

## Funciones del producto

Partner Center admite hasta 20, de 200 caracteres como máximo.

- Chat y conversación por voz con un avatar 3D animado.
- Vida propia: lee, trabaja y descansa en su habitación, y lo deja para atenderte.
- Funciona con proveedores de IA compatibles o con modelos locales.
- Permisos separados para el micrófono, la cámara y la captura de pantalla.
- Tu propio avatar VRM o PMX, además de voces, personalidad y temas.
- Escritorio con apps y widgets; Calendario y Gmail de Google en modo lectura, opcional.
- Automatizaciones con resultados guardados en una bandeja.
- [Solo si Companion va dentro del paquete:] Ventana flotante para tener a Iris sobre tus otras aplicaciones.

## Términos de búsqueda

asistente IA · avatar 3D · chat por voz · asistente de escritorio · compañera virtual · VRM · IA local

## Requisitos adicionales del sistema

[Borrador del 2026-10-05. Sin cifras en un equipo modesto: no publicarlo hasta la medición de la fase 2 del [plan](../../docs/iris_plan_auditoria_2026-11-17.md).

Única evidencia por ahora: el usuario la usó en una VM de VirtualBox con 8 GB de RAM, 4 núcleos y 128 MB de vídeo virtual. Funcionaba, aunque no muy fluida. Esa vídeo virtual probablemente dibuja WebGL por software, así que es una prueba de que arranca y funciona, no de fluidez.]

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10 22H2, 64 bits (el manifiesto pide 10.0.19045) | Windows 11, 64 bits |
| Memoria | 8 GB | 12 GB o más |
| Gráfica | Integrada con DirectX 11 (Intel UHD 630, AMD Vega 8) | Dedicada de 4 GB (GTX 1650, RX 6500 XT) |
| Disco | 3 GB libres | 5 GB en SSD |
| Red | Internet para proveedores remotos y la voz en línea | Banda ancha |

## Notas para la certificación

Este texto va en Partner Center → Opciones de envío → Notas para la certificación. Es la parte técnica, donde corresponde nombrar el motor.

- **`runFullTrust`:** Iris incluye su propio motor de agente de código abierto (Hermes Agent, de Nous Research, licencia MIT) y dos intérpretes de Python privados dentro del paquete. `runFullTrust` permite que el lanzador inicie esos procesos locales. Por defecto escuchan solo en 127.0.0.1: Core en el puerto 8765 y el motor en el 8642. El acceso desde la red local es opcional, lo activa el usuario y exige emparejar el dispositivo.
- **Código dinámico (10.2.2):**
  - El asistente usa herramientas y ejecuta las automatizaciones que configura el usuario, dentro de los permisos que este define.
  - Las habilidades y los conectores MCP se instalan desde el catálogo integrado, por acción del usuario.
  - Antes de instalar una app externa (un .zip o una carpeta que elige el usuario), Iris muestra qué contiene y pide confirmación. Si incluye un componente Python, ese código puede ejecutarse con los permisos de Windows del usuario.
  - Todo esto está descrito en la ficha.
- **Actualizaciones:** [Incluir solo cuando esté comprobado en el MSIX final: que esta edición se actualiza únicamente por la Store, sin el canal propio de la edición de GitHub. Hoy está pendiente; ver el [README](README.md).]
- **Cómo probarla:** [Pendiente.]
  - Dar una credencial exclusiva para pruebas, de un proveedor que permita ese uso, con cupo limitado y sin acceso a las cuentas personales.
  - Escribir los pasos y la credencial solo en estas notas privadas, nunca en la ficha pública.
  - Antes de enviarla, seguir esos mismos pasos en un Windows limpio hasta recibir la primera respuesta.
  - Sin un proveedor configurado, el revisor solo verá la bienvenida y el asistente. La voz en línea de prueba no necesita clave.
- **IA generativa (11.16):** declarada en el envío. Los reportes llegan a [correo de soporte pendiente].

## En el borrador

- **Descripciones de Store.** Pegar el texto de arriba en español y guardarlo. Reservar las capturas para el MSIX final:
  - el avatar y el chat;
  - la habitación;
  - el escritorio con widgets;
  - la configuración de permisos;
  - Companion, si está integrado en ese paquete.

  Usar PNG de al menos 1366 × 768 para escritorio. [Requisitos de capturas](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/screenshots-and-images)
- **Correo de soporte: es lo primero que hay que resolver.** Decisión del usuario; el 2026-11-17 decidió dejarlo para más adelante, así que los marcadores siguen en la ficha. Hace falta antes de enviarla a certificación. Sirve a la vez para reportar contenido (11.16), como contacto de privacidad y como contacto de soporte, y permite terminar esos textos sin esperar a compilar.
  - Hay que revisarlo y responder: Microsoft pide atender los reportes.
  - Los Issues públicos de GitHub no sirven como vía principal, porque alguien podría publicar sin querer parte de una conversación privada.
  - Cambio de producto pendiente: un enlace «Reportar esta respuesta» dentro de Iris, que deje elegir qué fragmento compartir.
- **Precios y disponibilidad.** Ficha gratuita, como corresponde a una beta pública. Confirmar los mercados al planear el lanzamiento.
- **Propiedades.** Elegir la categoría entre las opciones de Partner Center.
  - La política de privacidad necesita una URL pública y permanente, por ejemplo `https://iris-os.cloud/privacidad`. Comprobado el 2026-11-17: hoy esa ruta da 404.
  - La ventana de Privacidad de la portada explica qué viene apagado, pero una política tiene que decir más:
    - qué se guarda en el PC;
    - qué se envía al proveedor de IA y al servicio de voz;
    - cómo controla el usuario esos datos;
    - a qué correo escribir.

    La base está en `docs/guides/privacy-security.md`. [Política 10.5](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies#105-personal-information)
  - El contacto de soporte es el mismo correo.
- **Clasificación por edades.** Completar el cuestionario de IARC con las funciones del paquete final y sus servicios conectados. La clasificación la decide el cuestionario.
- **Paquetes.** Subir el candidato cuando estén hechos estos pasos, descritos en [la guía del prototipo](README.md):
  - integrar Companion;
  - adaptar las actualizaciones al canal de la Store;
  - probar el MSIX firmado con un certificado de prueba en un Windows limpio;
  - pasar el Windows App Certification Kit.
- **Opciones de envío.** Declarar el uso de IA generativa y pegar las notas para la certificación. [Política de IA generativa](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies#1116-live-generative-ai-content)

La certificación empieza cuando se pulsa **Enviar para certificación**. [Guía del borrador MSIX](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/create-app-submission)
