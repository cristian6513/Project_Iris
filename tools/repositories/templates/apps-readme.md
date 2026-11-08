# Crear aplicaciones para Iris OS

Guías y ejemplos del SDK de aplicaciones. Puedes crear una ventana, añadirle un widget y, con **SDK 2**, incluir Python y una skill opcional que ayude a Iris a usar sus acciones.

## Empieza aquí

1. Lee [Apps y su ficha](apps/README.md).
2. Para una app instalable con Python y skill, sigue la [guía SDK 2](docs/guides/community-apps-python.md) y copia [Contador](examples/apps/contador/README.md).
3. Para widgets, consulta [su guía](docs/guides/widgets.md).
4. Entrega la carpeta o un ZIP y pruébalo en Iris Store, con un perfil desechable.

Si trabajas con un asistente, dale [la skill de contribución](tools/skills/iris-app-contributor/SKILL.md). Es una guía para desarrollar; no es la skill operativa que se instala en Hermes junto a una app.

## Audiencias, avisos y comunicación entre apps

La ampliación del 28 de octubre permite reservar operaciones a la interfaz o a Iris
con `audience`, programar avisos persistentes con `notify` o `iris_notices`, enviar
información a Iris tras una acción de la persona con `askIris` y abrir otra app con
datos mediante `openApp`. La [guía SDK 2](docs/guides/community-apps-python.md)
incluye ejemplos y límites.

Estas funciones están aprobadas en fuente posterior a QA17; no están incluidas en
los instaladores QA15–QA17, que sí traen el SDK 2 base. Comprueba el corte de Iris
que usas para probar la app: `sdkVersion: 2` no identifica por sí solo esa ampliación.

## Ejemplos y plantillas

| Carpeta | Qué enseña | Distribución |
| --- | --- | --- |
| `examples/apps/contador` | Python estándar, estado compartido, ventana, widget y skill | Ejemplo externo instalable por la Store |
| `apps/_plantilla` | Ventana e interfaz básica | Plantilla: cambia los identificadores antes de usarla |
| `apps/_plantilla-servicio` | Servicio integrado mediante revisión | No equivale al backend externo del SDK 2 |
| `apps/player` | App y widget de música sin cuentas externas | Referencia de una app oficial integrada; no se instala como paquete externo |

Player depende del producto principal. Sus fuentes completas y servicio están en el repositorio principal; aquí se conserva su ejemplo de app y widget. Los ZIP experimentales de terceros no se añaden a estos ejemplos automáticamente.

## Validación oficial

Las copias de `core/` y `face/js/` son referencias del contrato, no un runtime autónomo de Iris. Para el chequeo oficial necesitas también el código principal y un Python con sus dependencias de desarrollo.

```text
python herramientas/validar_app.py check examples/apps/contador --iris-source "../Project iris"
python herramientas/validar_app.py pack carpeta-de-mi-app mi-app.zip --iris-source "../Project iris"
```

El ayudante llama al `app_package_tool.py` del producto; no reimplementa sus reglas, no ejecuta la app y no instala dependencias. `--python` permite elegir el intérprete de tu entorno de Iris. Si la carpeta principal está junto a este kit con el nombre anterior, se detecta automáticamente.

Pasar `check` no basta para aceptar una app con Python: prueba también el worker real y su ciclo de vida dentro de Iris. Si no puedes hacerlo, indica esa parte como pendiente. Las reglas y límites vigentes están en la guía SDK 2.

## Mantener el kit

`SOURCE.json` identifica el corte de las fuentes. Las guías y referencias provienen del producto; los enlaces a material de otro repositorio se muestran como rutas para consultarlo allí. La URL pública de cada repositorio se acuerda al publicarlo.

Los cambios de este kit vuelven primero al proyecto de desarrollo y se exportan de nuevo. Conserva la licencia y atribución de los recursos de cada ejemplo. Este kit no anuncia un instalador público ni convierte una prueba de fuente en QA instalada.
