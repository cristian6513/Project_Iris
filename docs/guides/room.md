# Habitación 3D

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

La habitación es la escena visual de IRIS OS. Esta guía describe el corte
**QA22 (2026-11-06)**: muebles texturizados, acciones del avatar, Vida propia y
controles visuales. Los informes fechados mantienen sus mediciones originales.

## Qué incluye

Los controles de la habitación y el **Centro
de control** se muestran en español: **Visión**, **Chat**, **Estado de Iris**,
**Configuración rápida**, **Conexión con Hermes** y **Conectar dispositivos**.
Los estilos Cyber, Sakura, Hacker y Classic conservan sus nombres.

La cabecera del Centro de control incluye
**Donaciones**, que abre la página de [Ko-fi de Iris](https://ko-fi.com/cristian_irisos)
en el navegador únicamente al pulsarla. No añade un pago dentro de Iris.

Al abrir el escritorio, los controles de la habitación quedan fuera de la
navegación con Tab y del árbol de accesibilidad. Al salir vuelven a su estado
anterior; los módulos que estaban cerrados siguen cerrados. El botón **Chat**
del dock abre la conversación existente, conserva el borrador y enfoca el cuadro
de texto. Entrar o salir del escritorio no inicia cámara, micrófono ni un envío.
La comprobación de fuente/exportación y los límites están en el
informe de idioma y accesibilidad (informe interno no distribuido).

- Muebles modelados y texturizados para escritorio, silla, sofá, cama y estantería.
- Libro y tablet que el avatar puede coger, usar y guardar.
- Materiales, luz de relleno y acentos adaptados a Classic, Cyber, Hacker y Sakura.
- Vistas de **Explorar** para encuadrar cada zona y controles de acciones visuales.

Abre la inspección de la habitación y selecciona una de esas vistas. Al cambiar
el tamaño de ventana se conserva el encuadre automático; si mueves la cámara
manualmente, el siguiente cambio de tamaño respeta esa decisión. La preferencia
del sistema de movimiento reducido evita la transición animada entre vistas.

Al salir del Inspector se cancela el desplazamiento y el temporizador de la
acción visual, y el avatar vuelve al centro. Se limpia la pose y se recupera la
altura anterior a sentarse, evitando que un retorno pendiente lo desplace otra
vez. Esto no detiene la respuesta hablada ni cancela trabajo de Hermes.

La escena reutiliza materiales y texturas y evita sombras dinámicas. Cambiar
el tema conserva las pantallas y los mapas compartidos. Las medidas, los puntos
de interacción y las animaciones se ajustaron a los muebles nuevos; cambiar
una malla exige revisar de nuevo asientos, manos, recorridos y cámaras.

## Acciones del avatar

Abre **Explorar → Acciones** para volver al centro, leer o usar la tablet de
pie, sentarte en la silla/sofá/cama, usar el PC, leer en un asiento, acostarte,
mirar por la ventana o ir al librero. **Usar PC** incluye la silla y las manos
sobre teclado/mouse. **Ventana** apoya la mano en el cristal y cambia el encuadre.
Los controles solo ejecutan acciones que tengan recursos compatibles disponibles.

Son acciones visuales del avatar: no conceden control de Windows ni hacen que
Iris lea un documento o una pantalla. La cámara, las capturas y las herramientas
siguen sus permisos separados. Una orden manual tiene prioridad sobre una rutina.

## Vida propia

En **Centro de control → Configuración rápida → Avatar → Vida propia** elige
**Apagada**, **Tranquila** (inicial) o **Activa**. La elección de la habitación
se guarda por navegador y origen; es independiente de la preferencia de Companion.

| Tiempo sin interacción | Tranquila | Activa |
| --- | --- | --- |
| Lectura o tablet cerca del centro | 1 minuto | 25 segundos |
| Visita al sofá, cama, silla o ventana | 4 minutos | 1 minuto y medio |
| Descanso acostada | 15 minutos | 10 minutos |

Son umbrales de decisión, no alarmas exactas. Las órdenes y la conversación van
por delante. No empieza rutinas con movimiento reducido, la sala/ventana oculta,
Explorar abierto o mientras escucha, piensa o habla. Abrir el micrófono evita
que empiece otra actividad; no equivale por sí solo a una frase enviada.

Al hablarle guarda el libro o la tablet. Puede responder sentada; si está
acostada se incorpora. Si caminaba por iniciativa propia o estaba mirando la
ventana de espaldas, vuelve al centro. En un asiento admite gestos de cabeza y
omite los de cuerpo completo que la sacarían de la pose. Una tarea real con
herramientas de más de unos 20 segundos puede llevarla al PC a teclear; es una
señal visual, no una tarea nueva. Detener o cerrar el turno cancela esa intención.

### Teletransporte

Cuando una visita fue iniciativa de Iris, al comenzar una conversación puede
volver al centro con un efecto de unos 1,2 s. La decisión se toma una vez tras
al menos cinco minutos sin interacción, con un máximo de un salto cada quince
minutos. Los valores actuales son 15 % en Tranquila y 40 % en Activa.

No se usa si la enviaste tú, va caminando, está en el centro, la sala está
oculta, está en Explorar, hay movimiento reducido, Vida propia está Apagada o
la calidad es Ahorro. No es un efecto garantizado en cada conversación. Una
orden nueva o el comienzo del habla lo cancela para que no siga desapareciendo
mientras responde.

Diseño y comprobaciones de rutinas (informe interno no distribuido) ·
Recursos empaquetados y límites de QA22 (informe interno no distribuido).

## Acercar el avatar

**Centro de control → Configuración rápida → Avatar**
incluye **Acercar el avatar**. Mueve la barra de **Normal** a **Más cerca** para ver
mejor a Iris o tu VRM, sin entrar en Explorar. El cambio es inmediato; la cámara
mantiene su posición y aumenta el encuadre hasta 2,5 veces. **Restablecer vista**
vuelve al tamaño normal. También puedes usar las flechas del teclado sobre la barra.

Explorar conserva sus controles libres y suspende este acercamiento. Al salir,
la vista de conversación recupera el valor elegido. Se guarda por navegador y
origen en `IRIS.roomAvatarZoom.v1`; no es una preferencia sincronizada entre
perfiles, otros dispositivos o el Companion. Si el navegador no permite guardarlo,
el control funciona durante la sesión y lo avisa. No usa la webcam ni captura imágenes.

Comprobación fechada: acercamiento del avatar (informe interno no distribuido).

## Ánimo y voz

El indicador de ánimo cambia al empezar la respuesta,
sin esperar a que Iris termine de hablar. La boca conserva la sincronización con
el audio; al terminar se aplica la expresión emocional y vuelve a neutral tras
unos 3,5 segundos. Si llega otra respuesta, el temporizador anterior se cancela.

Face y Companion comparten ese ciclo. Al desconectarse de Core liberan el audio
y el movimiento de habla, sin dejar una emoción pendiente para la siguiente conexión.
No se añaden animaciones ni se modifica la selección de voz o de proveedor.

## Paisaje de la ventana

Cada tema de habitación tiene un paisaje local:
**Cyber**, ciudad nocturna azul y magenta; **Sakura**, ciudad al atardecer con
cerezos; **Hacker**, ciudad nocturna en verde; **Classic**, ciudad y río de día.
Se cambia desde los botones de la habitación o su selector en Configuración
rápida. La elección del tema del escritorio es independiente.

El paisaje conserva la ventana con profundidad y su máscara: solo aparece en
el hueco, incluso al entrar en Explorar. Se adapta a la proporción del plano del
paisaje sin deformar la imagen. Durante la carga se conserva el último paisaje
válido; si falta un archivo, se usa una ciudad procedural con los colores del
tema elegido. Cambiar rápidamente de tema descarta las cargas anteriores.

Solo se mantiene una textura de paisaje activa. No se precargan las cuatro ni
se añaden luces o mallas por tema. Las imágenes generadas tienen aproximadamente
1.834 × 858 píxeles nativos: esta entrega no las convierte en imágenes 4K.

## La habitación y el Player

La segunda pasada reutiliza la tablet y los indicadores de los altavoces del
escritorio. Reproduce una canción desde **IRIS OS → Player** y vuelve a la
habitación: la vista **Desk** permite acercarte a la tablet. Es una pantalla de
estado, no un segundo reproductor; los controles siguen en la app Player.

- Muestra título, artista, espera, carga, reproducción, pausa o error de audio.
- Solo anuncia reproducción tras el evento `playing` del elemento de audio;
  solicitar `play` no equivale a haber empezado a sonar.
- Indica silencio si el volumen está a cero o el elemento está silenciado.
- Los indicadores tienen una pulsación tenue únicamente durante reproducción;
  no simulan un análisis de frecuencias. El movimiento reducido, la calidad baja
  y una habitación/pestaña oculta desactivan esa pulsación.
- El resumen pertenece al Player de esa misma página. No sincroniza otras
  pestañas, el Companion ni otros dispositivos; no envía música o metadatos a
  Hermes y no abre otro canal de audio.

La pantalla usa una sola textura reutilizable, redibujada únicamente cuando
cambia el estado o el tema, no en cada frame. No añade geometría o luces.

## Ambientes manuales de luz

En **Habitación / Explorar → Ambiente de luz**, o en **Configuración rápida → Visual
y Audio**, puedes elegir:

- **Original:** conserva la iluminación de referencia del tema.
- **Trabajo:** refuerza el escritorio y reduce la luz de las zonas de descanso.
- **Descanso:** destaca sofá y cama con el escritorio más tenue.
- **Noche:** atenúa la iluminación ambiental sin apagar las pantallas.

La elección se guarda por navegador y origen en `IRIS.roomAtmosphere.v1`.
Sin preferencia válida se usa Original. Si el navegador bloquea el guardado,
el cambio funciona durante esa sesión y el control lo avisa. Los dos selectores
de la misma página permanecen sincronizados; no se sincronizan otras pestañas
en vivo ni se modifica la configuración de Hermes.

El ambiente y el tema son independientes: se conservan los colores del tema y
el ambiente seleccionado al cambiar entre Classic, Cyber, Hacker y Sakura. Las
transiciones duran unos 650 ms, o son inmediatas con movimiento reducido.
Las acciones del PC y la ventana conservan su intensidad base, sobre la que se
aplica el ambiente; volver a Original no cancela la acción actual. Los cambios
de estado de Hermes en el monitor también pasan por ese controlador: no saltan
a iluminación plena cuando está seleccionado Noche.

El ambiente ajusta las luces existentes y el brillo de las superficies de cada
zona. Conserva la exposición de la cámara y la iluminación dedicada al avatar;
las pantallas mantienen su propio brillo. Noche no es un ciclo día/noche:
no usa horarios, ubicación, clima, estado de Vision, modos de silencio o audio.

En la fuente posterior a QA19, el sofá y la cama incorporan relleno neutro y un
contorno tenue del color del tema, visible en ángulos rasantes. Ayuda a distinguir
el cuero negro y la ropa de cama del suelo sin colorear toda su superficie. El
contorno también se atenúa con el ambiente de su zona, incluido Noche, y cambia
de color al cambiar de tema. No añade mallas, luces ni pases de renderizado.

El panel de inspección conserva desplazamiento vertical también en ventanas
de escritorio de poca altura.

## Monitor de actividad: estabilidad y coste

La revisión del 8 de septiembre de 2026 corrige la propiedad de su textura:
cambiar el tema ya no sustituye ni libera la pantalla viva de Hermes. Se libera
solo el mapa decorativo que deja de usarse, una vez. El material usa el mismo
mapa para iluminar el contenido y evitar que un brillo uniforme tape el texto.

La animación decorativa del monitor tiene un límite de 12 redibujados/s, 4 en
calidad baja y 2 en espera. Los eventos de estado no esperan a ese intervalo.
Con movimiento reducido no hay animación periódica; con Room o la pestaña
ocultos se difiere el dibujo y al volver se muestra el estado más reciente.
Esto no pausa Hermes, Player, la voz ni sus notificaciones, y no añade timers.

En el escenario aislado de actividad estable, 600 frames a 60 Hz producen 120
redibujados en lugar de 600: **80 % menos redibujado del monitor**, no 80 % menos
consumo global ni una medición de FPS. La verificación también cambia 32 veces
de tema y comprueba que se conserva la textura viva, sin liberarla.

## Brisa y audio ambiente

En **Configuración rápida → Visual y Audio**, **Brisa (cabello y ropa)** aplica
viento suave a las cadenas físicas compatibles del VRM. Recuerda la elección
por navegador y se pausa si el sistema pide movimiento reducido. Un modelo sin
físicas de cabello o ropa no gana esas físicas automáticamente. No necesita
cámara ni modifica la cara o el cuerpo principal.

El audio ambiente es otro control independiente: no se activa por encender la
brisa o cambiar la luz. Estos cambios están incluidos en QA16; la QA instalada
debe comprobar el comportamiento real junto con los controles visuales.

## Verificación

Pruebas pequeñas, sin navegador:

```sh
node --test tests/js/room_details.test.mjs
node --test tests/js/player_playback_observer.test.mjs tests/js/room_media_display.test.mjs tests/js/player_audio_defaults.test.mjs
node --test tests/js/room_atmosphere.test.mjs tests/js/room_atmosphere_controls.test.mjs
node --test tests/js/room_runtime_regressions.test.mjs
node --test tests/js/room_activity_performance.test.mjs
```

La revisión visual opcional necesita las dependencias de `companion` instaladas,
Playwright y un navegador compatible (Edge en Windows; Chromium en otros
sistemas). No descarga ni instala estas dependencias automáticamente:

```sh
node scripts/verify_room_visual.mjs
```

Si Playwright no se resuelve desde el proyecto, acepta
`--playwright-module /ruta/al/modulo/playwright`. El script sirve únicamente la
escena de prueba por loopback, bloquea peticiones externas y guarda capturas en
`.room-qa.tmp/`, ignorada por Git. No arranca Core, Hermes, micrófono o cámara.
También ejercita el Player real con un WAV sintético de silencio servido por
loopback y la salida de Chromium silenciada: inicio explícito, pausa, error de
decodificación, limpieza de fuente y preferencia de movimiento reducido.
Los ambientes se prueban con los controles reales de inspección, transiciones,
acciones del PC/ventana, persistencia tras recargar y ventanas de 1200 × 540 y
390 × 844. La carga diferida del selector de Configuración rápida, almacenamiento
bloqueado y sincronización entre controles tienen regresiones unitarias.
La revisión de cierre añade cuatro regresiones de iluminación, cancelación,
altura y limpieza del movimiento. El navegador ejecuta los métodos reales de
Room/Idle con un avatar sintético: salir durante el trayecto, limpiar una pose
sentada y avanzar frames sin volver al destino anterior, conservando la pausa
del sistema de movimiento durante una respuesta hablada. No usa una cuenta de
Hermes ni reproduce una conversación real para esa comprobación.

La revisión histórica del acabado de septiembre comprobó los cuatro temas y
encuadres estrechos con una escena aislada. Para los recursos que hoy contiene
el instalador y las pruebas recientes, consulta el informe QA22 (informe interno no distribuido);
los conteos de aquella escena no validan por sí solos el candidato actual.

## Límites y próximos pasos

QA22 incluye estas acciones y sus animaciones, y Companion 0.1.8 incorpora las
alternativas de pie. La comprobación automática de recursos empaquetados usa
IRIS y Cantarella en Edge, con fotogramas adelantados en muchos casos; no
sustituye voz real, WebView2 instalado ni tiempos normales de Vida propia.

Las proporciones, los pesos y las físicas de cada VRM pueden producir cruces
con ropa o muebles. Un modelo con falda muy larga puede sobresalir del barrido
de teletransporte. Cambiar el modelo de una cama o silla requiere revisar sus
puntos de interacción. El rendimiento en el equipo mínimo sigue pendiente.

La integración con Player continúa siendo visual y local; clima, ciclo día/noche
automático y decoración adicional son mejoras futuras. Los FPS y la calidad
se administran por separado en [Rendimiento](configuration.md#rendimiento-del-avatar-y-la-habitación).

[← Documentación](../README.md)
