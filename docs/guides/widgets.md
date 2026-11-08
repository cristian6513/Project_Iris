# Widgets de IRIS OS

Revisión documental: 2026-10-28. **F12 base está incluido en QA16 y QA17.** La presencia
en el paquete no acredita automáticamente la QA instalada o táctil. Consulta el
[estado actual](../estado-actual.md) y el informe F12 (informe interno no distribuido).

`askIris` y los avisos de apps (`notify`, `cancelNotice`, `scheduledNotices`) pertenecen
a la ampliación del 28 de octubre, incluida en el candidato QA18, con pruebas del
runtime aislado aprobadas y QA instalada pendiente. No está en QA16/QA17.
La [guía SDK 2](community-apps-python.md) incluye sus
ejemplos y la separación entre operaciones de interfaz y operaciones para Iris.

## Personalizar tu escritorio

En el escritorio actual de fuente, pulsa **Personalizar → Widgets** o usa
**Aplicaciones → clic derecho o pulsación prolongada en Calendario → Agregar widget**.
La galería muestra vistas previas con datos de ejemplo; no monta el widget real
ni activa cuentas, música o permisos. **Añadir Calendario** guarda una instancia.
Puedes añadir hasta cuatro
instancias y quitarlas. **Editar inicio** permite ordenarlas junto a los accesos
con **Anterior/Siguiente**. Escape cierra el editor de widgets y devuelve el foco
al botón. El orden y los filtros se guardan en tu perfil, separados de las posiciones
de las ventanas. Otro perfil conserva sus propias preferencias. La disposición
lateral F12 anterior se conserva en el almacén para compatibilidad; el nuevo
inicio usa una cuadrícula compartida. Véase [Inicio y aplicaciones](desktop-home.md).

El catálogo de fuente contiene Calendario e **IRIS Player**. En Player, el widget
muestra la pista, el progreso y anterior/reproducir/pausar/siguiente del mismo
reproductor de su ventana y su skill. No crea otro audio ni autoriza carpetas:
para elegir una, abre Player. Ocultarlo o quitarlo no corta la música. Véase el
[ejemplo de app local para la comunidad](../../apps/player/README.md).

Cada instancia de Calendario puede mostrar todos los calendarios visibles o uno
concreto desde **Elegir calendarios**. Respeta también los que ocultaste en la app.
El widget compacto muestra hoy y hasta tres eventos pendientes o en curso del
periodo guardado; la app completa conserva el mes y las demás vistas.
Pulsa un evento para abrir su fecha, cuenta y detalle en la
misma ventana Calendario, o **Abrir Calendario** para consultar la vista completa.
Cerrar esa ventana conserva el widget. En espacios pequeños hay desplazamiento
dentro de la zona de widgets, por encima del dock.

Añadirlo solo lee los datos locales existentes. Conectar una cuenta y actualizar
desde Google siguen siendo acciones de [Conexiones](google-connections.md) y
[Calendario](calendar.md). El widget no activa OAuth, sincronización periódica,
recordatorios, IA, cámara ni voz. Sus datos incluyen la fecha de la última lectura.
Un error, permiso revocado o vista parcial no se presentan como una agenda vacía.

App y widget comparten el servicio de vistas: peticiones iguales se unen y hay
un solo ciclo de refresco por periodo visible. Al ocultarlos se pausa ese refresco;
los recordatorios ya autorizados siguen perteneciendo a Core. Si una app deja de
estar disponible, su widget se identifica como no disponible y puedes quitarlo.
Una configuración dañada o de un esquema desconocido no se reemplaza por defecto.

## Declarar un widget en tu app

La declaración va en **el mismo `apps/<id>/app.json`**, como campo opcional. Una
app sin widgets no cambia. Ejemplo real de [`calendar/app.json`](../../apps/calendar/app.json):

```json
"widgets": [
  {
    "id": "calendar.month",
    "name": "Calendario",
    "entry": "widget.js",
    "style": "widget.css",
    "size": "compact",
    "configVersion": 1,
    "preview": {
      "layout": "agenda",
      "title": "Tu día, de un vistazo",
      "subtitle": "Hoy · agenda de ejemplo",
      "items": [{ "label": "09:00", "detail": "Preparar el proyecto" }]
    }
  }
]
```

Es un fragmento para añadir al objeto de la ficha, no un manifiesto completo.
El identificador comienza por el de la app y un punto. `entry` y `style` nombran
archivos de su propia carpeta `ui/`, sin carpetas, URL o rutas externas. `style`
es opcional. La primera versión admite tamaño `compact`, configuración versión
1 y hasta cuatro declaraciones por app. No habilita código externo o permisos.

### Vista previa declarativa

`preview` es opcional. Sin ella, la galería dibuja una tarjeta genérica con el
nombre del widget. La dibuja el shell con sus colores y tipografía; no se importa
`entry`, ni se llama a `createWidget`, ni se presta un servicio para ilustrarla.

| Campo de `preview` | Contrato |
| --- | --- |
| `layout` | `agenda`, `player` o `summary`. Son composiciones pasivas de Iris, no nombres de código o plantillas externas. |
| `title` | Texto obligatorio, hasta 60 caracteres. |
| `subtitle` | Texto opcional, hasta 80 caracteres. |
| `items` | Lista obligatoria de 0 a 3 ejemplos; cada uno tiene `label` (hasta 60 caracteres) y `detail` opcional (hasta 60). |

El validador rechaza campos desconocidos y caracteres de control. No hay campos
para imágenes, HTML, CSS, scripts, URL o permisos. Los textos se muestran como
texto, aunque contengan signos de HTML. Usa datos ficticios y deja claro que son
ejemplos: no incluyas correos, agendas, pistas ni nombres personales reales.
Player declara `layout: "player"`, con una pista ficticia y controles dibujados
sin acciones. `summary` sirve para otras apps de la comunidad. La elección de
layout no concede acceso al reproductor o al calendario.

```text
apps/mi-app/
  app.json
  ui/main.js
  ui/style.css
  ui/widget.js
  ui/widget.css
  tests/widget.test.mjs
```

En una app nueva usa `ui.entry`. Un adaptador existente también puede declarar
widgets en su propia carpeta `ui/`, como Player: el build verifica esa carpeta
sin exigirle una entrada de ventana nueva. El módulo exporta `createWidget(context)`. El host entrega un `root`
propio de cada instancia, su `config`, `openApp(id, payload)` y el callback
`configure(calendars)` del filtro de Calendario, y `askIris({ text, data })`, el
mismo «Avisar a Iris» de las ventanas. Los widgets de una app instalada desde
Iris Store reciben además `notify`, `cancelNotice` y `scheduledNotices`; son los
avisos de su app, descritos en [apps/README.md](../../apps/README.md). No entrega
el shell completo.
`services` está vacío salvo una inyección revisada y explícita del shell para
un widget concreto. Hoy solo `player.transport` recibe `services.player`; ni
la ficha ni las preferencias pueden concederlo. Su API interna y sus límites
están en el README de Player; no es un permiso genérico para futuros plugins.
También entrega `signal`, abortado al retirar o fallar esa instancia; úsalo para
cancelar sus peticiones y bindings. Los métodos del controlador aplican su estado
de forma síncrona; el trabajo asíncrono debe manejar sus errores y cancelación.

| Controlador del widget | Uso |
| --- | --- |
| `setVisibility(visible)` | Booleano: iniciar o pausar el trabajo visual según visibilidad efectiva. No usa el objeto de visibilidad de las apps. |
| `configure(config)` | Aplicar las preferencias confirmadas de esa instancia; no conceder permisos. |
| `dispose()` | Soltar consumidores, listeners y peticiones propias y vaciar su root. Obligatorio e idempotente. |

Los datos y servicios de dominio deben compartirse con la app; no copiar tokens,
crear otro cliente Google o iniciar una segunda sincronización desde el widget.
Los trabajos durables se mantienen en Core. El filtro `calendars` pertenece al
primer widget; configuraciones nuevas requieren ampliar y revisar su contrato,
no guardar credenciales en las preferencias del escritorio.

Un punto de partida sin lecturas ni trabajo de fondo para `ui/widget.js`:

```js
export function createWidget({ root, openApp }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'mi-app-widget-open';
    button.textContent = 'Abrir Mi app';
    button.addEventListener('click', () => openApp('mi-app'));
    root.append(button);
    return {
        setVisibility(visible) {},
        configure(config) {},
        dispose() { button.remove(); },
    };
}
```

Sustituye `mi-app` por el id real y declara por ejemplo `mi-app.summary` en su
ficha. Añade las lecturas y su cancelación cuando la app las necesite; el ejemplo
solo abre la ventana ya registrada.

Genera el catálogo con:

```bash
node scripts/build_app_catalog.mjs
node scripts/build_app_catalog.mjs --check
```

El generador produce el registro, imports y estilos de widgets para Face y los
metadatos para Core desde ese JSON. No hay que añadir una lista manual en cada
superficie. No edites `face/apps/`, `face/js/os_app_catalog.js` ni
`core/widget_catalog.py`. Una referencia inexistente, un duplicado o un módulo
sin `createWidget()` detienen la generación. La inclusión sigue pasando por
revisión del código y el build de Iris; no es una instalación libre de plugins.

Referencia funcional: [`widget.js`](../../apps/calendar/ui/widget.js),
[`widget.css`](../../apps/calendar/ui/widget.css) y
[pruebas](../../apps/calendar/tests/widget.test.mjs). Sigue también la
[guía de contribución](contributing-apps.md): tema, teclado, tamaños, carga,
vacío, error, respuesta tardía y retirada deben tener un comportamiento real.

[← Documentación](../README.md)
