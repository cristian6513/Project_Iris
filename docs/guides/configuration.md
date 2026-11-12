# Configuración

[← Índice de documentación](../README.md)

El wizard es la vía recomendada para configurar IRIS y Hermes. Esta guía
explica qué controla cada capa y cómo evitar configuraciones contradictorias.

Revisado el 2026-10-16 para la fuente vigente. La disponibilidad por instalador
se distingue en [Estado actual](../estado-actual.md).

## Fuentes de configuración

| Capa | Responsabilidad |
| --- | --- |
| IRIS | Tema, identidad, personalidad, voz, memoria y preferencias de las superficies. |
| Hermes | Proveedor, modelo, OAuth, API keys, Gateway, skills y capacidades del agente. |
| `.env` | Overrides de desarrollo o despliegue; no es el flujo recomendado para usuarios finales. |

IRIS persiste su configuración en `~/.iris/config.json`. El runtime resuelve
la ubicación administrada de Hermes según la plataforma; no copies rutas de
otro equipo. [`.env.example`](../../.env.example) documenta variables admitidas
sin incluir secretos.

`IRIS_HOME` puede seleccionar otro perfil. En el paquete Windows, los datos
mutables quedan separados de las carpetas versionadas del programa. Las cuentas
Google tienen su propio almacén de Iris y no pertenecen al OAuth de proveedores
de modelos administrado por Hermes.

## Proveedor y autenticación

### API key

Selecciona el proveedor, introduce la clave y verifica la conexión antes de
continuar. El modelo debe pertenecer al proveedor o endpoint guardado. Un
nombre de modelo válido no corrige una URL o credencial equivocada.

### OAuth

OAuth utiliza la sesión y el adaptador de Hermes. IRIS no debe convertir esa
sesión en una API key ni configurar silenciosamente otro proveedor. Si Hermes
no confirma la sesión, el wizard debe mostrar el estado pendiente y ofrecer
reintento o diagnóstico.

### Ollama y LM Studio

IRIS inspecciona endpoints locales y sus modelos disponibles. La detección no
implica que el servicio arrancará con IRIS ni que el modelo quepa en RAM/VRAM.
Configura el autoarranque en la aplicación correspondiente si lo necesitas.

### Catálogo de modelos

El catálogo general combina datos versionados con la fuente oficial admitida por
Hermes. En la fuente posterior a QA13, primero eliges proveedor y acceso: con OAuth los modelos se cargan al terminar la autorización; con API key, pulsa **Cargar modelos**. Consultar no guarda la clave ni cambia el proveedor principal. La interfaz distingue una lista del servicio de una lista pública o de referencia. Salvo en
Nous Portal, existe una entrada manual para modelos compatibles que aún no
aparecen en el catálogo, pero debe verificarse antes de guardarla.

En **Nous Portal**, conecta tu cuenta y consulta sus modelos. La lista posterior al acceso incluye los admitidos por tu plan; no hay una cantidad fija de modelos. Iniciar sesión y consultar el acceso son comprobaciones distintas. Si falla la consulta de cuenta, Iris hace un reintento acotado y distingue entre sesión no disponible, acceso sin confirmar y precios no disponibles. Puedes volver a pulsar **Cargar modelos** sin reiniciar la configuración.
Si la cuenta o su plan no se pudieron comprobar, la selección queda bloqueada.
Pulsa **Actualizar modelos** para reintentar. **Actualizar catálogo** renueva la información general de proveedores. No se sustituye un modelo
guardado por uno de pago automáticamente.

**OpenRouter** y **OpenCode Zen** son proveedores distintos. OpenRouter consulta la lista para la cuenta, incluidos los gratuitos compatibles que devuelva el servicio. Los gratuitos de Zen son exclusivos de la aplicación OpenCode: desde Iris/Hermes necesitas créditos y un modelo de pago de Zen, o una suscripción Go con uno de sus modelos incluidos. La lista pública no valida la clave ni la cuota. Véase [Acceso a proveedores](provider-login.md).

### Principal y proveedores adicionales

En el código posterior a `qa11`, **Guardar proveedor** conserva el principal y
pliega el formulario en una tarjeta de estado. **Cambiar proveedor** permite
reemplazarlo; **Agregar otro proveedor** guarda su clave o sesión sin cambiar
el principal. Los endpoints propios y servidores locales solo se configuran
como principal. Verificar una credencial adicional tampoco debe reemplazarlo.

Los modelos de los proveedores autenticados se eligen en el
[selector de cada conversación](models.md). Cambiar el predeterminado global
de Hermes es una acción distinta y pide confirmación.

Mensajería es opcional. El bloque del Gateway aparece después y comprueba
credenciales, programa requerido y servidor local antes de arrancar. Guardar
preferencias sin un proveedor listo no se presenta como una IA lista para usar.

### Cuentas de Google

En **Conexiones** puedes importar o pegar el JSON de un cliente OAuth de
escritorio propio. Se valida y se protege por usuario de Windows/perfil; después
autorizas cada cuenta en Google. Este archivo no es una API key del proveedor de
IA. Cambiarlo exige desconectar cuentas y terminar trabajos pendientes. Véase
[Conexiones](google-connections.md); la configuración personal está incluida
en QA16 y su QA instalada se registra por candidato.

## Identidad y personalidad

Puedes configurar:

- nombre visible;
- nombre del perfil;
- plantilla de personalidad;
- varios rasgos compatibles;
- instrucciones personalizadas;
- backstory opcional.

Una plantilla es un punto de partida, no una biografía impuesta. Si el usuario
deja el backstory vacío, IRIS no inventa uno. Cuando se guarda una identidad,
IRIS sincroniza los contratos compatibles del workspace de Hermes sin
sobrescribir un `USER.md` que el usuario ya haya escrito.

La historia oficial corta de IRIS funciona como opción predeterminada, pero
puede editarse o eliminarse.

## Voz

El proveedor de voz se elige de forma independiente al proveedor del modelo:

| Motor | Tipo | Consideraciones |
| --- | --- | --- |
| Edge TTS | Remoto ligero | Necesita internet; no requiere GPU local. |
| Kokoro | Local opcional | Ligero frente a stacks mayores; requiere instalar su extra aprobado (≈ 222 MB + modelo de ≈ 330 MB al primer uso). Voces en español. |
| ElevenLabs | API opcional | Requiere cuenta y clave; clonación sujeta a consentimiento y términos. |
| MiniMax | API opcional | Clave propia de voz; no utiliza el OAuth del proveedor de IA. Clonación según cuenta. |
| Fish Audio | API opcional | Clave propia, búsqueda de voces por idioma con muestra gratis y clonación privada en Fish. `s2.1-pro-free` seleccionado inicialmente para pruebas sujetas a límites; modelos de pago identificados. Síntesis y búsqueda probadas con una cuenta real el 2026-10-21; clonación real pendiente. |
| Fish Speech | Servidor propio | Iris conecta con el servidor que administres; no es la API alojada de Fish Audio. |
| Coqui / NeuTTS | Local en desarrollo | Sin extra en el paquete Windows: dependen de paquetes sin wheel (docopt, llama-cpp-python) e Iris no compila en el equipo del usuario. |
| HF Qwen3-TTS | Local opcional | Extra aprobado grande (≈ 435 MB + pesos al primer uso); el paquete actual usa CPU. Experimental por su demora. |

Los extras locales se instalan desde el paso de voz del asistente con versiones exactas aprobadas por Iris (`core/extras_data/`), cada uno en su propio entorno dentro del perfil (`<IRIS_HOME>/extras/<extra>`). El motor se ejecuta en un proceso aparte que solo ve ese entorno; el runtime incluido nunca se modifica. Cada extra se puede cancelar, reparar o quitar; al quitarlo, los modelos descargados se conservan salvo que se pida borrarlos.

La activación por voz pertenece a Hermes. IRIS expone **Hey Hermes** o
**Hey + nombre del perfil** cuando el motor instalado lo permite. La escucha:

- está apagada inicialmente;
- requiere activación visible;
- no se arma solo por guardar la configuración;
- debe detenerse o pausarse al hablar IRIS para evitar autoactivaciones.

Se configura desde Ajustes de IRIS OS, fuera del recorrido inicial simplificado.
Guardar el asistente envía `hermes_wake_mode="keep"`: no sustituye una frase
personalizada ni cambia la preferencia de escucha de una instalación existente.
Las claves de ElevenLabs/MiniMax/Fish Audio y sus identificadores de voz están junto al
motor en Voz; la clonación y la prueba reutilizan esa misma credencial. Dejar
la clave vacía conserva la guardada, sin mostrarla en la página.

En Fish Audio, elige el idioma y pulsa **Buscar voces**. El nombre es opcional, y la
búsqueda trae 20 voces por página, las más populares primero; **Ver más voces** trae la
página siguiente. Cada voz muestra su idioma y sus ♥ en Fish. **Solo mis voces** consulta
las de tu cuenta. Al buscar, Iris comprueba la clave y avisa si no es válida. También
puedes pegar un identificador del catálogo.

Elegir una voz no reproduce nada:
- **Escuchar muestra de Fish · gratis** reproduce el audio que Fish publicó con esa voz.
  No gasta cuota, pero la frase es la de Fish. Iris lo trae desde Fish; la página no se
  conecta a Fish.
- **Escuchar voz seleccionada** genera tu frase con Fish y gasta cuota.

La clonación de Iris admite una muestra de 10–60 s y la confirmación de autorización; si
la voz sale robótica, prueba con 30–60 s. La transcripción es opcional: si la dejas vacía,
Fish transcribe la muestra. Iris envía el WAV a Fish y crea una voz privada, y el audio no
se publica desde Iris.

Sin una API key puedes recorrer los campos, pero no consultar el catálogo,
clonar ni generar una muestra. La integración se comprueba con respuestas
simuladas; eso no verifica calidad, demora ni permisos efectivos de una cuenta.

### Guardar y cambiar la voz

En Voz, **Guardar voz · nombre del motor** guarda el proveedor seleccionado, su voz y sus opciones. No guarda los cambios pendientes de IA, identidad o avatar, ni genera audio. Una clave vacía conserva la guardada. En QA22, el mensaje confirma que **Iris la usará en su próxima respuesta**, sin cerrar la ventana ni detener Core. El resumen final también sigue guardando la configuración completa.

La acción principal queda en una barra visible al pie del paso de voz al desplazarte. Elegir una voz del catálogo Fish también marca los
cambios pendientes. Explorar o escuchar muestras no guarda automáticamente una
elección; el botón permite confirmar cuándo quieres conservarla.

La muestra gratuita de Fish y **Escuchar voz seleccionada** comparten controles de reproducir, pausar y posición con los colores de Iris, accesibles con teclado. La prueba de una frase propia sigue siendo una síntesis con el proveedor y puede consumir cuota.

Core puede seguir funcionando aunque cierres la ventana: por eso cerrar y abrir
Iris no era suficiente para aplicar una voz en candidatos anteriores. QA22
comprueba la selección guardada antes de cada frase y solo reconstruye el motor
cuando cambió la configuración de voz.

El formulario no devuelve tu clave guardada. Ver el campo vacío no indica que se
haya borrado; déjalo vacío para conservarla. Si Fish rechaza una clave nueva al
guardar la tarjeta, Iris muestra el motivo y no sustituye la configuración anterior.

La primera respuesta con una voz local nueva puede tardar mientras carga el
modelo. Se libera el anterior antes de cargar el siguiente. Si pulsas Detener
durante esa carga, la próxima respuesta puede intentar la voz guardada. Si la
voz nueva no arranca o no produce audio, aparece un aviso; no vuelve a la voz
anterior en silencio. Una configuración de voz ilegible o inválida conserva la
voz que estaba funcionando.

Alcance de la validación de QA22 (informe interno no distribuido):
los controles de carga/cancelación fueron sintéticos; falta la prueba instalada
con voz y proveedor reales.

## Tema y superficies

El tema elegido en el wizard se comparte con IRIS OS. El Desktop Companion
consume el mismo estado visual cuando está disponible, pero su instalación y
autoarranque son independientes del Gateway de Hermes.

## Rendimiento del avatar y la habitación

En fuente posterior a QA16, **Iris OS → Ajustes → Rendimiento** ofrece **30 FPS**,
**60 FPS** y **Sin límite**. El valor inicial es **60 FPS**; pulsa **Guardar
rendimiento** para aplicarlo a la habitación y Companion abiertos y conservarlo
para el siguiente inicio del perfil. No hace falta recargar el escritorio.

En fuente posterior a QA19, la misma tarjeta añade **Calidad gráfica**:

- **Ahorro:** reduce la resolución 3D, desactiva el suavizado por muestras y el
  brillo de posprocesado de la habitación. El texto y los controles siguen nítidos.
- **Equilibrado:** valor inicial, con suavizado moderado y una resolución limitada
  en pantallas de alta densidad.
- **Alta calidad:** aumenta el suavizado y admite más resolución en esas pantallas;
  puede usar más GPU.

Calidad y FPS se eligen por separado. **Guardar rendimiento** aplica ambos a las
vistas abiertas y los conserva por perfil, sin recargar Iris ni su avatar. La
etiqueta de la tarjeta muestra los valores confirmados por Core; editar los
selectores o fallar al guardar no cambia esa etiqueta. El ajuste también se
conserva al reiniciar. Consulta las mediciones y límites (informe interno no distribuido).

Además, **Ahorro** limita las texturas compatibles del avatar a un máximo de 1024 px por lado
**al cargar el modelo**, manteniendo su proporción y sin modificar el VRM
original. Cambiar la calidad no recarga automáticamente el avatar que ya está
en pantalla: vuelve a cargarlo para aplicar ese límite. El renderizado y los
FPS sí cambian al guardar, sin esa recarga.

30 FPS puede reducir la carga de CPU/GPU; sin límite permite que Iris renderice
al ritmo que admita la pantalla. Son máximos, no una garantía de rendimiento:
el avatar, la habitación y el equipo pueden rendir por debajo. El ajuste no cambia
la velocidad de la voz ni el análisis de cámara. Las vistas ocultas dejan de
renderizar; la conversación y el audio mantienen sus propios ciclos.
El limitador admite 2 ms de variación al llegar un fotograma para evitar descartes
en pantallas cercanas a 60 Hz; ese margen no se acumula como permiso para dibujar
por encima del presupuesto.

## Variables de entorno

Utiliza `.env` principalmente en desarrollo, contenedores o automatización
controlada:

```bash
cp .env.example .env
```

Nunca confirmes `.env` en Git. Evita poner claves directamente en comandos,
capturas, logs o archivos de prueba.

## Cambiar una configuración existente

1. Abre de nuevo el wizard.
2. Despliega **Cambiar una configuración** y elige la sección necesaria.
3. Modifica sus opciones y continúa al resumen para guardarlas.
4. Si se trata de un problema del motor, usa **Diagnóstico y reparación**;
   abrirlo solo consulta estado. Aplicar una activación/reinicio requiere su botón.
5. Los cambios de voz TTS guardados se aplican a la siguiente respuesta en QA22.
   Reinicia un servicio solo cuando el ajuste o el diagnóstico lo indique, por
   ejemplo al actualizar su código o cambiar una opción de arranque.

## Siguientes lecturas

- [Instalación y primeros pasos](getting-started.md)
- [Privacidad y seguridad](privacy-security.md)
- [Historia e inspiración](project-history.md)
