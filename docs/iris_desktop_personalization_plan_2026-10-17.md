# Cajón de aplicaciones y personalización del inicio

Fecha: 2026-10-17. Propuesta solicitada por el usuario durante la preparación de
F14–F16, revisada con su decisión posterior y la propuesta compartida de Claude.
**Cajón/inicio/visibilidad implementados en fuente y exportación web; falta el
candidato Windows y su QA.** La personalización precede a la compilación conjunta
acordada. Véanse el informe (informe interno no distribuido) y la
[guía actual](guides/desktop-home.md). La gestión de paquetes externos sigue siendo
diseño y requiere su entrega propia, descrita abajo.

La implementación usa `HomePreferences`/`DesktopHome`, la app F09 Aplicaciones y
GET/PUT `/api/desktop/home`. Accesos, iconos ocultos y orden se guardan en la clave
`home` del almacén F12, sin modificar sus preferencias de widgets. El primer
alcance permitió Anterior/Siguiente sobre una cuadrícula adaptable. La mejora
del 2026-10-18 añade arrastre desde Mover, menús por clic derecho/pulsación
prolongada, edición con Listo y galería de widgets con previews declarativas;
no posiciones libres. Informe de interacción (informe interno no distribuido).
Los accesos iniciales son Theme Studio, Cámara, Player y
Configuración. No hay un desinstalador externo ni nuevas conexiones de servicio.

## Decisiones vigentes

- No habrá opción de desactivar aplicaciones, tampoco Correo o Calendario.
  Ocultar sus iconos conserva sus funciones y los consentimientos existentes.
- No se añade «Restablecer inicio». Quitar todos los accesos es una configuración
  válida: se recuperan individualmente desde Apps o Ajustes, sin borrar el diseño.
- Las apps incluidas con Iris están protegidas frente a desinstalación desde este
  administrador. Solo podrá desinstalarse un paquete externo administrado por Iris.
- La procedencia y la propiedad las resuelve Core; una bandera en `app.json`
  no convierte una app en oficial ni autoriza a borrar su carpeta.

Estas decisiones sustituyen la propuesta anterior de desactivar funcionalmente
Correo/Calendario. No cambian los controles específicos de Google, los permisos
de IA o los ajustes de recordatorios que ya tiene cada función.

## Experiencia solicitada

- **Cajón de aplicaciones:** catálogo de apps instaladas y visibles, búsqueda,
  navegación por teclado y apertura en las ventanas actuales. Botón Apps siempre
  alcanzable y atajo dentro de Iris. El atajo exacto se define sin interceptar
  globalmente la tecla Windows ni sustituir silenciosamente la paleta existente.
- **Inicio personalizable:** los accesos predeterminados se pueden quitar y
  reordenar; cada persona añade los suyos desde el cajón. Quitar un acceso no
  desinstala ni desactiva la aplicación. El catálogo sigue siendo la fuente común.
- **Menú contextual de una app:** «Agregar a inicio»; «Agregar widget» solo si
  el manifiesto declara un widget compatible; «Ocultar del cajón» y acceso al
  administrador. «Desinstalar» solo aparece para un paquete externo instalado
  y registrado por Iris cuyo ciclo de retirada esté soportado por Core.
  El mismo menú se ofrece por teclado y mediante un botón visible para pantalla
  táctil. No debe depender exclusivamente del clic derecho.
- **Menú de un acceso del inicio:** abrir, quitar de inicio y opciones propias
  de esa app. Una misma app no produce accesos duplicados al pulsar varias veces.
- **Widgets junto a los accesos:** conservar estado y ciclo de vida de F12.
  La zona central del inicio combina accesos y widgets en una distribución
  adaptable; no se limita al rail lateral actual. Guardar orden/posición por perfil
  y ofrecer reorganización por teclado, sin invadir el dock o las ventanas.
  Añadir desde una app usa su definición en `app.json`, sin otro catálogo manual.
  Quitar el acceso no elimina su widget; quitar el widget no desactiva la app.
- **Administrador de aplicaciones:** muestra las apps, su procedencia y su
  visibilidad; permite volver a mostrar las ocultas. Siempre accesible desde
  Ajustes, incluso sin accesos en inicio. No ofrece desactivación ni un
  restablecimiento que sustituya el diseño del usuario.
- **Apps integradas/Hermes generales:** por decisión del usuario se ocultan sus
  iconos del cajón sin bloquear el uso por otras rutas o sus funciones. No borrar
  archivos, credenciales o datos. No llamar «Desactivar» a esa acción visual.
- **Correo y Calendario:** ocultar tiene la misma semántica visual que en las
  otras apps. No pausa sincronización, clasificación consentida, preparación
  de reuniones o recordatorios; tampoco activa ninguno de esos servicios.
  Desconectar cuentas y cambiar permisos siguen sus flujos específicos.
- **Apps externas/futura Iris Store:** desinstalar exige el gestor de paquetes y sus
  registros de propiedad. Actualmente no existe instalación externa de Store;
  no se ofrece una acción que finja desinstalar ni se borran carpetas por su nombre.
  La futura acción se determina por el origen verificado por Core.

## Texto del menú y gestión de cuentas

La diferencia entre ocultar y detener debe explicarse antes de elegir, sin
depender de un tooltip ni de que el usuario conozca el funcionamiento interno.
Para Correo y Calendario, mostrar una acción de dos líneas:

- **Ocultar del cajón**
  «La sincronización y los avisos que tengas activados continuarán».

La aclaración permanece visible en el menú de ratón, teclado y pantalla táctil,
y forma parte de su descripción accesible. No afirmar «seguirá sincronizando»
sin matiz: puede no haber una cuenta conectada o estar el servicio sin activar.
En las demás apps, la descripción es «Solo oculta el icono; la app sigue funcionando».
Ocultar es reversible y no añade otra confirmación.

El administrador repite la explicación y ofrece **«Gestionar cuentas en Conexiones»**
para Correo/Calendario, con el texto «Para detener la sincronización de una cuenta,
desconéctala en Conexiones». Ese acceso solo navega: no desconecta ni revoca nada.
La desconexión conserva su confirmación y explica qué servicios de esa cuenta
afecta; no prometer que borra datos locales o apaga toda la app. Los controles
propios de recordatorios y permisos de IA siguen disponibles por separado.

## Inicio y recuperación sin restablecimiento

Quitar de inicio afecta solo a ese acceso; ocultar del cajón solo a su icono.
No cierra ventanas ni elimina widgets, cuentas, permisos, datos o funciones.
Los demás puntos de entrada conservan su comportamiento. El administrador
explica ese alcance y permite volver a mostrar una app.

Los accesos de fábrica se usan solo mientras no exista un inicio guardado.
Un inicio vacío guardado no se interpreta como falta de preferencias. Una
actualización, un error de lectura o una respuesta de guardado atrasada no deben
restaurar accesos ni sobrescribir la personalización. Preservar el último estado
válido y ofrecer recuperación del error sin reinicializar silenciosamente.
La migración conserva los widgets F12 y las preferencias de cada perfil.

## Base comprobada y cambios previstos

- `face/js/os_app_manifest.js`: valida manifiestos y widgets; no acredita
  procedencia o propiedad. Mantener el contrato versionado y no convertir
  una declaración de manifiesto en permiso para ejecutar o borrar.
- `face/js/os_app_registry.js` y `hermes_activity.js`: catálogo y apertura común.
  La visibilidad se aplica al cajón, sin duplicar el catálogo ni bloquear funciones.
- `core/desktop_widgets.py`: conserva el layout F12 y añade preferencias de inicio
  separadas y versionadas, con revisiones por perfil. Los campos de un home guardado
  o enviado son obligatorios; un documento incompleto no recibe valores de fábrica.
- `face/js/os_widgets.js`: reutilizar lifecycle/visibilidad y los tokens de Iris.
  El administrador ya está registrado como app propia del kit F09 y tiene entrada
  permanente de recuperación en Ajustes.
- `face/js/os_app_host.js`: ya tiene `dispose` para la interfaz y protege frente
  a cargas tardías. No equivale a detener un backend Python.
- `core/app_actions.py`: permite retirar claves de acciones concretas.
  `core/app_services.py` registra routers/acciones revisados; su lista
  `REVIEWED_APP_SERVICES` está vacía. No hay aún un gestor completo para detener
  servicios, desmontar rutas y retirar un paquete externo. Los servicios
  revisados de F09 corren dentro de Core; no habilitar Python de terceros con esa vía.

## Base prevista para paquetes externos

Preparar estos contratos evita rehacer el escritorio cuando llegue la Store.
No significa habilitar instalación externa ni ejecutar paquetes hoy.

### Procedencia, carpetas y registro

- **Apps incluidas:** Core resuelve su procedencia y protección contra el catálogo
  revisado y el inventario de la versión distribuida. Ni `official: true` ni
  el nombre de una carpeta son prueba. Un inventario con hashes comprueba bytes;
  la confianza en su origen depende de cómo se distribuye y verifica ese inventario.
- **Código externo:** reservar una raíz propia fuera de los archivos de la
  versión de Iris; propuesta `<IRIS_HOME>/app-packages/<id>/<installation-id>/`.
  No reutilizar `apps/` del repositorio ni `<IRIS_HOME>/apps/`: este último ya
  contiene datos, por ejemplo `apps/calendar/preparations.sqlite3`.
  La ruta final se fija al implementar el gestor, con migración si procede.
- **Datos:** reutilizar el contrato F09 `<IRIS_HOME>/app-data/<id>` para apps
  externas nuevas, sin mover los almacenes actuales de las apps incluidas.
  Reservar IDs integrados y rechazar colisiones; código y datos tienen propietarios
  y destinos separados, y sobreviven a una actualización de Iris según su contrato.
- **Registro de instalación de Core:** esquema versionado con ID de instalación,
  ID/versión de app, procedencia/fuente, digest e inventario del paquete, fecha,
  compatibilidad y estado del ciclo. Core deriva las rutas dentro de sus raíces;
  ni la petición ni el manifiesto eligen una ruta que luego pueda borrarse.
  El registro se confirma tras validar la instalación y debe permitir recuperación
  de una operación incompleta. Prueba gestión/propiedad, no que el código sea seguro.
- No descubrir apps ejecutables escaneando carpetas. Solo un instalador soportado
  puede dar de alta un paquete tras sus comprobaciones. La tienda futura llama a
  esa API de Core; no escribe directamente el registro ni modifica el catálogo integrado.

### Retirada completa de una app administrada

1. Resolver el registro, verificar que la app es externa, está administrada por
   Iris y no ocupa un ID o destino protegido. Revalidar propiedad/rutas durante
   la operación; nunca borrar una carpeta solo por coincidir con el ID.
2. Confirmar nombre y versión. **Conservar datos por defecto**; borrar sus datos
   exige una elección explícita y no alcanza a cuentas, credenciales o servicios
   compartidos de Google/Hermes.
3. Registrar la retirada pendiente y dejar de admitir nuevas acciones y trabajos
   de esa instalación. Es una barrera temporal del proceso de desinstalación,
   no una nueva opción de desactivar aplicaciones.
4. Cerrar sus ventanas, recoger sus widgets/accesos y retirar sus acciones/rutas.
   Detener solo servicios/trabajos de los que el gestor tenga propiedad y esperar
   su cierre. F09 aporta piezas, pero falta ese contrato coordinado de lifecycle.
   No ejecutar un `uninstall.py` arbitrario del paquete ni matar procesos por nombre.
5. Retirar solo archivos del paquete verificado y sus recursos administrados.
   Comprobar enlaces/junctions, hardlinks, sustituciones de ruta, archivos bloqueados
   y carreras antes de tocar el destino. No recorrer enlaces hacia otras carpetas
   ni modificar contenido compartido. La limpieza opcional de datos tiene su
   propia validación de propiedad; no basta con reutilizar la ruta del código.
6. Guardar el resultado real y recuperar interrupciones. Si un servicio no se
   detiene o quedan archivos bloqueados, no anunciar éxito ni perder el registro
   necesario para reintentar. La limpieza debe poder repetirse sin dañar otras apps.

### Alcance de la Store posterior

El gestor puede probarse con paquetes y registros sintéticos en perfiles
desechables, sin tienda ni ejecución de Python externo. El escritorio consume
su API de elegibilidad y estado; no inventa una desinstalación antes de tenerla.
Si no hay apps externas administradas, no aparece «Desinstalar».

Instalación, permisos, distribución/firma, compatibilidad, aislamiento y
actualización/rollback de código externo siguen siendo una entrega independiente.
Un proceso aparte, una firma o un manifiesto por sí solos no crean un sandbox.
El registro y la retirada deben prepararse con esa evolución, sin habilitar
prematuramente el cargador de terceros.

## Validación prevista

- Escritorio: persistencia/reinicio/actualización, perfiles independientes,
  migración F12, inicio vacío conservado y recuperación individual desde Ajustes;
  doble clic/guardados concurrentes, duplicados y errores sin pérdida de layout.
- Visibilidad: una app oculta sigue funcionando; ocultar Correo/Calendario no
  cambia permisos ni sus tareas. No confundir ocultar el icono con cerrar la
  ventana, que conserva su lifecycle visual actual.
- Claridad: aclaración visible y accesible antes de ocultar, también por teclado
  y toque; comprobar casos sin cuenta/con servicio apagado para no anunciar una
  sincronización inexistente. El acceso a Conexiones solo navega y no cambia cuentas.
- Interfaz: app sin widget, manifiesto ausente/incompatible, teclado, Escape,
  toque, menú contextual, reorganización accesible, tema alternativo y móvil.
- Gestor futuro: paquete sintético con registro válido; marca falsa de oficial;
  carpeta sin registro; ID integrado, colisión, rutas externas/enlazadas o cambiadas;
  retirada con ventanas/widgets/acciones/trabajos activos, datos conservados o
  borrados explícitamente, archivos bloqueados, interrupción y reintento.
- Probar que no se borran paquetes integrados, recursos ajenos ni datos compartidos;
  tampoco se detienen servicios de otra app. Los controles sintéticos no acreditan
  seguridad de ejecución de terceros ni sustituyen la futura QA de Store.

Fuentes: petición del usuario de esta sesión, plan A08 (informe interno no distribuido),
[diseño de apps/widgets](iris_apps_widgets_plan.md),
F12 (informe interno no distribuido) y
revisión de carpetas F09 (informe interno no distribuido).
Las rutas de código anteriores se inspeccionaron el 2026-10-17; el informe distingue
lo implementado de los contratos externos pendientes. Los registros fechados conservan sus
propuestas históricas; las decisiones actuales de esta entrega están arriba.
