# Tu inicio y el cajón de aplicaciones

[← Índice](../README.md) · [Estado actual](../estado-actual.md)

Revisión documental: 2026-10-26. Inicio, cajón, widgets y arrastre directo están
incluidos en QA16. La evidencia de desarrollo no sustituye la QA instalada ni
las pruebas táctiles reales del candidato. Informe de Inicio (informe interno no distribuido)
y arrastre e iconos del tamaño del dock (informe interno no distribuido).
Las apps instaladas desde un
archivo con [Iris Store](iris-store.md) aparecen en el cajón como las demás.

## Abrir y buscar apps

**Chat** en el dock abre la conversación existente con Iris, muestra su sección
y enfoca el campo para escribir. Conserva el borrador y las ventanas del escritorio;
no envía un mensaje ni activa el micrófono. Cierra el panel de chat para regresar
a la habitación y usa **OS** para volver al escritorio. También está disponible
desde el dock del Panel.

Pulsa **Aplicaciones** en el dock. Escribe en **Buscar aplicaciones**: la búsqueda admite
nombres y descripciones, sin distinguir mayúsculas o tildes; prioriza los nombres.
Enter abre el primer resultado y Flecha abajo lleva el foco a la lista. Escape
cierra el cajón. **Alt+A**, mientras el foco esté dentro de Iris y no estés
escribiendo en un campo, abre el cajón. La tecla Windows y Ctrl+K conservan su función.

Un clic abre la app. **Clic derecho**, **mantener pulsado** en una pantalla táctil
o **Mayús+F10/tecla de menú** abren sus opciones, sin botones de tres puntos.
Mover el dedo cancela la pulsación prolongada para permitir el desplazamiento;
soltar después de abrir el menú no lanza la app. Flechas, Inicio/Fin y Escape
permiten recorrer/cerrar ese menú; no hace falta ratón.

Sobre un espacio vacío de Inicio, el mismo gesto ofrece **Widgets**, **Editar inicio**
y **Administrar aplicaciones**, con los iconos de Iris. El botón **Personalizar**
y **Ajustes → Personalizar inicio** ofrecen una alternativa visible.

## Agregar y quitar accesos

En el menú de una app de **Aplicaciones → Agregar a inicio** añades un acceso sin abrir la app. No se duplica
si lo vuelves a intentar. Un perfil nuevo empieza con Theme Studio, Cámara,
IRIS Player y Ajustes; los cuatro se pueden quitar. La mejora de la cabecera no
cambia estos valores ni reorganiza perfiles existentes.

Desde el acceso, **menú → Quitar de inicio** retira solo ese acceso. La app permanece
en el cajón, sus ventanas no se cierran y sus widgets o funciones no cambian.
**Inicio** recupera el escritorio; **Panel** conserva la vista de actividad.
Ambos tienen su propia indicación activa en la navegación y en el dock.

Puedes dejar el inicio vacío. Se respeta al reiniciar o actualizar: no hay un
botón que restablezca y borre tu organización. Vuelve a añadir lo que quieras
individualmente desde Aplicaciones o **Ajustes → Administrar aplicaciones**.

## Cabecera y Panel

La cabecera compacta muestra el estado de Iris en español, la hora y los botones
de Ajustes y salir. Distingue la conexión sin comprobar, el fallo local y el
trabajo confirmado. «En espera» no asegura que haya un proveedor configurado.
«Escuchando» aparece al grabar; tener el detector de frase de activación armado
no se presenta como una grabación de tu petición. Mostrar el estado no activa
micrófono, cámara ni tareas.

El Panel conserva los detalles de herramientas y servicios. Su contenido tiene
desplazamiento propio por encima del dock: **Inicio**, **Aplicaciones** y **Panel**
siguen accesibles. Los nombres de las apps son los mismos en el cajón, las
ventanas y las tarjetas. **Tareas programadas** son tareas por horario;
**Automatizaciones** son las reglas por inactividad de Iris.

Si los accesos y widgets ocupan más altura, desplázate dentro del Inicio. También
puedes enfocar **Accesos y widgets del inicio** con Tab y usar Ctrl+Inicio/Ctrl+Fin o las
flechas para recorrerlo, manteniendo el dock fuera de esa área.

## Combinar accesos y widgets

Las apps que declaran un widget compatible ofrecen **Agregar widget: …** en su
menú. Abre su vista previa antes de añadirlo. También puedes usar
**Personalizar → Widgets** para explorar toda la galería: nombre, tamaño y una
ilustración con datos de ejemplo. Las vistas previas no cargan el widget real,
no consultan cuentas ni reproducen audio. **Añadir** guarda la instancia y **Listo**
o Escape cierra la galería. Están Calendario e
[IRIS Player](iris-player.md); se conservan el límite de cuatro instancias y los
filtros F12 de Calendario. El widget Player controla el mismo audio de su app y
su skill; añadirlo no inicia música y quitarlo no la corta.

Calendario muestra hoy y hasta tres eventos pendientes o en curso, con el periodo
de la copia local indicado al pie. Un evento abre su cuenta y fecha en Calendario;
**Abrir Calendario** lleva a la app completa, que conserva el mes y las demás vistas.
El filtro guardado permanece en **Elegir calendarios**; añadir el widget no conecta
Google ni ejecuta sincronización remota o IA.

## Ordenar arrastrando

Los accesos y los widgets se ordenan como en un móvil o en el escritorio de Windows,
sin entrar en ningún modo:

- **Ratón:** pulsa sobre un icono o widget y muévelo. Un clic con un pequeño
  temblor sigue abriendo la app; el arrastre empieza a partir de unos pocos píxeles.
- **Pantalla táctil:** mantén pulsado (aparece su menú) y, sin levantar el dedo,
  muévelo. Deslizar sin mantener antes sigue desplazando el inicio.

Mientras arrastras, los demás se apartan y el hueco marca dónde quedará. Al soltar
se guarda ese orden, también si sueltas fuera de los iconos; soltar sobre el icono no
lo abre. **Escape**, ocultar el escritorio o un cambio guardado desde otra ventana
cancelan el movimiento y lo devuelven a su sitio. Si Core no confirma el guardado,
el inicio vuelve al orden guardado. Un clic en los botones de un widget funciona como
siempre; sus campos de texto, deslizadores y barras de desplazamiento nunca lo levantan.

**Personalizar → Editar inicio** ofrece además **Anterior/Siguiente** (‹ ›) y
**Quitar** (✕) en cada elemento, para ordenar con teclado. **Listo** devuelve la vista
normal; mientras editas se pausa el refresco visual del widget. Quitar un acceso
conserva su widget y quitar un widget conserva la app.

Los iconos del inicio tienen el tamaño y el aspecto de los del dock (112 px); un widget
ocupa tres de esas casillas. La distribución se adapta al espacio, con desplazamiento
por encima del dock; se guarda el orden, sin huecos ni coordenadas libres, así que
cambiar el tamaño de la ventana nunca deja iconos fuera. Los temas y el movimiento
reducido siguen las preferencias de Iris.

## Ocultar y recuperar iconos

**Ocultar del cajón** solo afecta al icono de Apps. No elimina el acceso del
inicio, no bloquea otras rutas y no detiene servicios. Para Correo/Calendario
el menú avisa: **«La sincronización y los avisos que tengas activados continuarán»**.

La app **Administrar aplicaciones**, también accesible desde **Ajustes → Administrar aplicaciones**,
permite mostrar de nuevo los iconos ocultos y agregar/quitar accesos. Ese acceso
de Ajustes sigue disponible aunque hayas ocultado todos los iconos.

En Correo y Calendario se ofrece **Gestionar cuentas en Conexiones**. Abrirlo
solo navega; desconectar una cuenta conserva su confirmación y explica su efecto.
Los permisos de IA, recordatorios y borrado de datos siguen siendo decisiones separadas.

## Guardado y errores

Las preferencias son por perfil y distintas del layout de ventanas y de las
configuraciones de widgets. Guardar en dos ventanas a la vez no permite que una
copia antigua sobrescriba la nueva: se muestra el conflicto y se recupera el estado
real, sin repetir el cambio automáticamente.

Un error de lectura o un formato futuro no restaura los accesos de fábrica.
Se explica cómo recuperarse con **Volver a cargar**, conservando el último estado
válido. El código o mensaje técnico queda en **Detalles del problema**.
No se anuncian cambios guardados antes de la confirmación del servidor.

Las apps incluidas con Iris no se pueden desinstalar desde este administrador.
Las que instalaste desde archivo aparecen como **Instalada desde archivo** y
ofrecen **Desinstalar en Iris Store**, con confirmación. La
[Store sencilla](iris-store.md), incluida en QA16, incorpora o retira apps
y widgets sin recargar normalmente el escritorio. Consulta el alcance de QA
en [Estado actual](../estado-actual.md). Ocultar un icono y desinstalar una app son acciones distintas.
