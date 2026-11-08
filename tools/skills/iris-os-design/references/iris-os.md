# Apps y superficies de Iris OS

Referencia de integración basada en el código del 2026-09-29. Las rutas son relativas al checkout de Iris. Consultar [sistema visual](visual-system.md) y después el módulo más cercano al cambio solicitado.

## Localizar el patrón existente

| Necesidad | Fuente útil |
| --- | --- |
| App, vista, ventana, launcher y dock | `face/js/hermes_activity.js`, `face/css/styles.css` |
| Ventana estrecha y móvil | `face/css/iris_os_responsive.css` |
| Estados/orígenes de agentes | `face/js/async_agents_state.js`, `face/css/async_agents.css` |
| Rutinas, selección e historial | `face/js/routines_state.js`, `_renderRoutines()` del shell |
| Bandeja y acciones de avisos | `face/js/notification_center.js`, `face/css/notifications.css` |
| Permisos/capacidades y mutaciones | `face/js/capability_center.js` |
| App modular y resumen | `face/js/iris_player.js`, `face/css/iris_player.css` |
| Perfiles/continuidad | `face/js/continuity_center.js`, `face/js/continuity_state.js` |
| Navegación por comandos | `face/js/command_palette.js`, `face/css/command_palette.css` |
| Tema compartido Desktop/Wizard | `wizard/ui/desktop_theme.js`, `wizard/ui/desktop_theme.css` |
| Companion transparente | `face/companion/companion.js`, `face/companion/companion.css` |

## Añadir o ampliar una app del escritorio

El shell define `OS_WINDOW_APPS`, `osIcon()` y las vistas `data-workbench-view`. `_openOsApp()` abre la app; `_openWindow()` monta la **misma vista** en `.os-window-body` y gestiona restauración. No clonar el DOM ni construir un administrador paralelo de ventanas.

Para una app nueva real, revisar conjuntamente:

- Importación y construcción de su módulo/root; seguir el patrón `new App({ root, onSummary, ... })` del vecino pertinente.
- Entrada de `OS_WINDOW_APPS`, título/icono/acento y geometría inicial.
- Catálogo `osIcon()`, vista `data-workbench-view`, accesos de launcher/dashboard y comandos si corresponden.
- Allowlists de `_setTab()`, dispatch de `_loadWindowApp()` y `_loadActiveTab()`; el registro actual es manual, no una API pública exportada.
- Visibilidad efectiva, carga inicial, resumen, eventos y cleanup.

Ejemplo de acceso a una app **ya existente**:

```html
<button type="button" data-os-open="notifications" data-accent="cyan">
  <strong>Notification Center</strong>
  <span>Bandeja local</span>
</button>
```

El shell enlaza `data-os-open` a su apertura; el icono se inserta mediante su catálogo. Cambiar el ID del ejemplo no registra otra app.

Reutilizar el marco `os-window`, titlebar, minimizar/anclar/maximizar/cerrar, foco y Escape. Una app normal no requiere overlay bloqueante de modal. Los tamaños iniciales existentes (780×560, 920×620 o 1120×680 según app) se limitan al espacio disponible; no son anchos mínimos universales.

Conservar layout y llaves/versiones persistidas. Un ajuste visual no debe borrar la posición, selección, filtros o preferencias del usuario.

## Componentes, estados y actualizaciones

Usar variables existentes y encapsular estilos bajo el root de la app. Leer la cascada efectiva antes de copiar una regla antigua. Los nombres GlassPanel/StatusPill/etc. aparecen en comentarios CSS; no asumir por ello que existe una biblioteca JS exportada de componentes.

Mostrar carga, datos, vacío útil y error recuperable como estados diferentes. No convertir una petición fallida en «No hay tareas». Un POST aceptado no significa trabajo completado; Gateway detenido, función no disponible, historial guardado y resultado entregado tienen comportamientos diferentes.

Botones dependen de capacidades reales y estado de mutación. Al cambiar de selección, abortar solicitudes anteriores mediante el patrón existente y comprobar el ID/origen vigente antes de publicar éxito o error. Coalescer lecturas compatibles y conservar contenido estable mientras se actualiza.

Polling visual solo cuando la app está efectivamente visible: documento visible, shell abierto, ventana montada, no minimizada y vista correcta. Limpiar timers/listeners al ocultar/cerrar. Un motor durable de automatizaciones pertenece a Core; no implementarlo como timer de su ventana.

Preservar foco, campos del usuario y detalles abiertos durante refrescos. No reemplazar DOM enfocado si el contenido no cambió. Tratar resultados/remotos como texto: `textContent` o helpers de escape existentes. El JSON interno no es copy; no ejecutar acciones antiguas del avatar al presentar historial.

## Responsive, teclado y movimiento

Comprobar ancho de ventana además del viewport: una app acoplada puede ser estrecha en monitor grande. `iris_os_responsive.css` usa contenedores y una regla de 560px para contenido de ventana; a 920px y 640px adapta rails, launcher y dock.

Reutilizar esa adaptación o encapsular una regla nueva cuando el contenido lo requiera. Verificar scroll interno, titlebar alcanzable, dock sin solapamiento, zoom, texto largo y un estado de error. No ocultar la única acción útil por reducir ancho.

Mantener nombre accesible, foco visible y recorrido de teclado en los controles. Respetar flags de Theme Studio, reducción de movimiento y fondo personalizado; probar un tema distinto del predeterminado. Un color de acento no reemplaza una etiqueta de estado.

## Límites entre superficies

### Widgets y futuras apps

El usuario planteó widgets personalizables en home, primero Calendario, y un kit de apps para una futura Iris Store. Leer `docs/iris_apps_widgets_plan.md` cuando la tarea toque esa ampliación. Es una propuesta pendiente; el registro actual todavía es manual y el rail derecho no es un host de widgets.

Widget y app son vistas distintas del mismo servicio/estado. Abrir un evento desde el widget reutiliza la ventana; cerrar esa ventana no elimina el widget. Respetar su propio ancho de contenedor y consumir la visibilidad del widget sin fingir que la app está abierta. Los recordatorios durables permanecen en Core. No copiar en el widget toda la composición de un mockup de ventana grande.

El kit común se prepara primero para apps integradas del proyecto. Un manifiesto futuro no concede permisos ni habilita imports remotos; paquetes de terceros/Store requieren una entrega propia. Verificar en código qué partes del registro están implementadas antes de diseñar contra una API supuesta.

### Adaptación por superficie

| Superficie | Qué conservar |
| --- | --- |
| Face / Iris OS | Ventanas, dock, launcher, preferencias y ciclo de vida del shell. |
| Iris Desktop | Host del producto y arranque claro; comparte tema a través del contrato servido como `/desktop/theme-contract.js`. No crear otro sistema de temas por pantalla. |
| Wizard | Lenguaje de configuración/pasos; variables `--installer-accent`, `--installer-accent-2`, `--installer-ready` adaptan la paleta. No sustituirlo por escritorio flotante. |
| Companion | Avatar protagonista, fondo transparente, controles compactos y contraste sobre fondos del escritorio. Usa `--companion-*` y un subconjunto compatible del snapshot `irisOsTheme.v1`/`visual_theme`; actualmente no importa `os_theme.js` directamente. |

Companion tiene adaptación propia a 350px y fuentes locales. No copiar el shell entero ni afirmar que comparte automáticamente todos sus tokens. Un cambio de tema debe revisarse en su adaptador si lo afecta.

## Contratos que el diseño no debe cambiar por accidente

- Notification Center actual acepta `iris.notification/v1` con origen Vision y entrega silenciosa: recibir un aviso no abre ventana, inicia chat ni voz. Reutilizar controles no habilita Gmail/cron dentro de ese contrato.
- Async Agents distingue manual/chat, incluso con IDs de texto iguales. La vista chat no hereda creación/parada de agentes manuales. Conservar identidad compuesta y capacidades.
- Una app no activa cámara, micrófono, TTS, login ni herramientas por construirse o renderizarse. Mantener controles y permisos explícitos del flujo.
- `.hermes/skills/iris-os/SKILL.md` es un skill operativo de Player; no define el diseño. Reutilizar su contrato/API si la tarea incluye control del Player, sin cambiarlo para un ajuste visual.
- Gmail, Calendario y automatizaciones por presencia están acordados en `docs/iris_automations_google_plan.md`, pero todavía no implementados en esta base. Verificar el estado actual antes de presentar controles como funcionales. Su integración requiere servicios y bandeja general propios del plan.

## Verificación relacionada

Seleccionar por comportamiento cambiado; estos son puntos de entrada, no una orden de ejecutar todos siempre:

| Cambio | Pruebas existentes útiles |
| --- | --- |
| Registro, carga y ventanas | `tests/js/os_app_lifecycle.test.mjs` |
| Agentes/orígenes | `tests/js/async_agents_panel.test.mjs`, `async_agents_state.test.mjs` |
| Avisos/acciones | `tests/js/notification_center.test.mjs`, `notification_actions.test.mjs` |
| Tema/host Desktop | `tests/js/desktop_theme.test.mjs`, `desktop_host.test.mjs` |
| Continuidad/perfiles | `tests/js/continuity_center.test.mjs`, `continuity_state.test.mjs` |
| Rutinas | `tests/js/routines_state.test.mjs` |

Añadir regresiones de comportamiento si se modifica integración. Pruebas Python/API solo cuando cambie su contrato o comportamiento. La revisión visual sigue necesaria para densidad, contraste, tamaños y foco; no declarar pruebas físicas de cámara/WebView2 por pasar tests de JS.
