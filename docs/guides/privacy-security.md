# Privacidad y seguridad

[← Índice de documentación](../README.md)

IRIS combina un agente, navegador, voz, cámara y servicios locales. Cada una de
esas capas tiene permisos y límites distintos. La configuración segura es
local y opt-in por defecto.

Revisado contra la fuente el 2026-10-17. **Local primero no significa que toda
la IA funcione sin red.** Los destinos dependen de los proveedores de chat,
visión y voz. Véase [Estado actual](../estado-actual.md) para los candidatos.

## Principios

1. Micrófono, cámara, wake word y acceso LAN requieren una acción visible.
2. Guardar una preferencia no equivale a conceder el permiso.
3. Las claves nunca deben aparecer completas en diagnósticos o logs.
4. Una superficie recibe solo las capacidades que necesita.
5. Las funciones opcionales deben fallar de forma explícita y recuperable.

## Micrófono y wake word

La detección de wake word pertenece a Hermes y ocurre localmente cuando el
motor está disponible. IRIS no mantiene un segundo detector paralelo.

- escucha apagada inicialmente;
- botón/estado visible mientras está armada;
- comprobación de motor, dispositivo, STT y TTS;
- pausa durante la respuesta para evitar autoactivación;
- nueva sesión u orden solo después de detectar la frase;
- chat escrito siempre disponible como alternativa.

No debe transmitirse audio a un proveedor o WebSocket simplemente por abrir la
interfaz.

## Cámara y percepción

La cámara requiere permiso del navegador o de WebView2 y la capacidad local
correspondiente. IRIS usa una concesión por superficie para evitar capturas
simultáneas. El modo Espejo y el mocap facial del usuario se retiraron de Face y
Companion: la percepción no copia tu cara al avatar.

Hay cuatro autorizaciones distintas:

- `camera.capture`: abrir el dispositivo.
- `vision.context`: incorporar al contexto señales derivadas autorizadas.
- `vision.snapshot`: tomar una foto para una petición, mostrando el proveedor
  de destino y pidiendo confirmación por defecto. Recordar ese destino no
  concede permiso para otro proveedor.
- `vision.ai_request` («Fotos que Iris decide pedir»): Iris pide la foto en un
  turno local con un código de un solo uso. «Preguntar» muestra el aviso de Iris
  OS; «Permitir» la toma sin aviso solo con la cámara ya encendida, y el chat lo
  indica. Su equivalente de pantalla es `screen.ai_request`. Véase
  [Cámara](camera.md#cuando-iris-decide-mirar).

Las señales de presencia/movimiento se analizan en la superficie local. Una foto
a petición puede salir al proveedor elegido. La imagen validada se conserva en
memoria para ese turno; el destino y los permisos deben comprobarse antes de
capturar. Abrir la cámara no autoriza enviar imágenes automáticamente.

## Inactividad de Windows y resúmenes hablados

F08a observa únicamente marcas de tiempo de entrada en tu sesión de Windows,
desde Core y después de activar la opción. No registra teclas, texto, títulos
de otras apps ni imágenes. Las interrupciones de observación se consideran
desconocidas; no se usa el tiempo de suspensión como ausencia.

F08b necesita activar la voz globalmente y marcar cada tarea. La síntesis usa
el motor de voz configurado, que puede enviar el resumen a un servicio externo.
No invoca otro modelo de chat ni abre sensores. Solo una superficie local
actualizada recibe los bytes; no hay reproducción de reserva en los altavoces
del servidor. Los recibos distinguen reproducción completa, fallo e incertidumbre.
Un intento ambiguo no se repite automáticamente al reiniciar.

El silencio de Iris tiene control explícito. La consulta de notificaciones de
Windows es una señal adicional, no una garantía de detectar todo Focus/No
molestar moderno. Estas funciones están incluidas en QA16;
su [guía](automations.md) separa los permisos y sus límites.

## Proveedores remotos

Cuando utilizas un proveedor remoto, el contenido enviado sale del equipo.
Revisa:

- términos y política de retención;
- región y cuenta seleccionadas;
- uso de conversaciones para entrenamiento;
- tratamiento de audio, imágenes y archivos;
- permisos de herramientas.

IRIS no puede garantizar las prácticas de un tercero.

La [transcripción en directo F16](manual-voice.md#transcripción-en-directo-f16)
solicita al STT de Hermes texto provisional mientras hablas. Con STT remoto el
audio puede salir del equipo antes de enviar el texto final al chat. Cancelar no
recupera ese audio ni su cuota. La opción se guarda por perfil de Hermes y también
afecta a Wake; no abre el micrófono al activarla. Iris no archiva los parciales en
el historial y solo admite el resultado final como orden.

## OAuth y API keys

El OAuth de proveedores de IA lo administra Hermes. Las API keys deben guardarse
en la configuración prevista, no en Git ni en el historial de la terminal.

Las cuentas Google de **Conexiones** son una integración distinta: el cliente
OAuth personal y sus tokens quedan protegidos con DPAPI para el usuario de
Windows y el perfil de Iris. No se importan las credenciales de la skill Google
Workspace de Hermes. La API no entrega el JSON privado ni tokens a la interfaz;
cambiar de cliente requiere desconectar las cuentas y terminar trabajos activos.
La protección no promete portabilidad a otro usuario/equipo ni impide que código
malicioso ejecutado con tu misma identidad de Windows acceda a tus datos.

Conectar Google o actualizar su caché no envía datos a una IA. Desconectar localmente
y revocar permisos en Google son acciones distintas. Véase [Conexiones](google-connections.md).

La [clasificación de Correo F14](mail.md#relevancia-con-ia-f14) y la
[preparación de reuniones F15](calendar.md#preparar-una-reunión-con-ia-f15)
tienen autorizaciones independientes para enviar contenido a un modelo.
F15 exige revisar las fuentes y el destino por petición; no incorpora correo,
adjuntos ni asistentes automáticamente. Los resultados y fuentes autorizadas se
conservan en el perfil local en SQLite, sin afirmar cifrado de ese contenido.
Descartar una entrada de Bandeja no equivale a borrarla. Cancelar impide admitir
notas tardías, pero no recupera datos ya enviados ni cuota ya consumida.

Activar una regla de [Automatizaciones](automations.md) sí puede ejecutar una
tarea en Hermes y consumir cuota. El permiso es independiente de observar
presencia; mostrar un aviso no equivale a ejecutar una herramienta ni leerlo.

Antes de compartir un diagnóstico:

- comprueba URLs con parámetros;
- elimina nombres de usuario de rutas;
- revisa encabezados y variables;
- evita capturar pantallas que muestren claves.

## Red local

Wizard y Core se enlazan a loopback por defecto. Habilitar LAN permite que
otros dispositivos alcancen la superficie publicada y cambia el modelo de
amenaza.

- utiliza el emparejamiento previsto;
- no publiques los puertos directamente en internet;
- usa TLS para cámara/micrófono en navegadores remotos;
- protege la red Wi-Fi;
- desactiva LAN cuando no se necesite.

Los certificados locales pueden ser autofirmados. Verifica el origen antes de
aceptarlos en otro dispositivo.

## Desktop Companion

El Companion es opt-in, siempre visible cuando está activo y separado del
autoarranque del Gateway. La aplicación:

- no debe capturar micrófono por una restricción de cámara;
- debe mostrar estados de escucha y error;
- debe validar los artefactos antes de reparar/instalar;
- no debe aceptar páginas o comandos de orígenes arbitrarios.

## Datos locales

`~/.iris/` puede contener configuración, memoria, certificados y estado. El
workspace de Hermes puede contener sesiones, perfiles y credenciales. Realiza
copias de seguridad cifradas si esos datos son importantes.

El `.gitignore` excluye estados locales comunes, pero no reemplaza una revisión
antes de cada commit.

## Checklist antes de habilitar funciones sensibles

- [ ] Entiendo qué proceso usará el dispositivo.
- [ ] Veo un indicador claro de que está activo.
- [ ] Puedo desactivarlo sin cerrar todo IRIS.
- [ ] Sé si los datos permanecen locales o salen a un proveedor.
- [ ] Probé el flujo de error y revocación.
- [ ] No hay credenciales en logs, capturas o Git.

## Riesgos abiertos

La validación automatizada no reproduce todos los navegadores, micrófonos,
cámaras, drivers, GPUs o proveedores. Antes de una release se necesita QA
manual con hardware y cuentas reales. El threat model remoto completo continúa
como tarea de producto en el [documento maestro](../iris_os_master.md).
