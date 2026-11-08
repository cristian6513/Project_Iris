# Modelos de conversación y visión

[← Índice](../README.md) · [Estado y disponibilidad](../estado-actual.md)

## Modelo de esta conversación

En el chat, abre el botón de modelo situado sobre **Razonamiento**. También
puedes escribir `/model`. El selector permite buscar, actualizar el catálogo y
elegir **Predeterminado** o un modelo de un proveedor con credenciales disponibles.
Que aparezca en el catálogo no garantiza que tu cuenta tenga acceso a él.

La selección pertenece a esta conversación. Si Iris está respondiendo, el cambio
se aplica al siguiente mensaje. El botón distingue selección pendiente,
confirmada, fallida o detenida: elegir un modelo no prueba que ya haya respondido.
Un fallo se muestra con su motivo; puedes volver a Predeterminado o elegir otro.

El transporte de sesiones de F01 usa el modelo mostrado y no cambia
silenciosamente a un modelo de respaldo. **Detener** detiene el turno; no borra
la selección ni confirma una respuesta que no terminó.

## Predeterminado de Hermes y varios proveedores

Cambiar el predeterminado es una acción aparte y pide confirmación. Puede afectar
a otras conversaciones que usan ese valor, a los canales y a las rutinas de
Hermes. No equivale a cambiar solo el modelo de la conversación abierta.

En el wizard, **Guardar proveedor** pliega la sección al quedar configurada.
**Cambiar proveedor** modifica el principal; **Agregar otro proveedor** guarda
una credencial adicional sin reemplazarlo. Sus modelos pueden aparecer en el
selector. Los servidores locales y endpoints personalizados se configuran como
principal, no como otra credencial genérica. Véase [Configuración](configuration.md).

## Modelo de visión

Abre **IRIS OS → Modelos → Visión** para ver la ruta que utilizará una imagen.
La selección es independiente del modelo principal. Las opciones dependen de
los proveedores, credenciales y capacidades que confirme el Hermes conectado;
un endpoint configurado fuera de Iris puede requerir administrarse desde Hermes.

Las señales de presencia/movimiento de Vision Assistant se calculan localmente.
En cambio, una foto a petición puede usar un proveedor remoto. Antes de tomarla,
Iris muestra el destino y pide el permiso correspondiente, salvo que hayas
recordado ese destino de forma explícita. Permitir la cámara no autoriza enviar
fotos a cualquier proveedor. Véase [Cámara](camera.md).

## Límites de esta guía

Describe la fuente F01–F03. Su aceptación en un instalador se registra para cada
candidato, sin trasladar automáticamente una prueba de desarrollo a `qa11` u
otro EXE. Los modelos auxiliares de voz y la transcripción en directo conservan
sus entregas pendientes; no aparecen por cambiar solo el motor de Hermes.
