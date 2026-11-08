# Contador: app externa con Python, widget y skill

Ejemplo del **SDK 2 implementado en fuente después de QA14**. No está en los
instaladores QA14. No se añade al catálogo predeterminado del producto.

La ventana, el widget y las acciones de Hermes usan el mismo contador guardado
por `backend/main.py`. No requiere cuentas, claves, red ni dependencias externas.

```text
contador/
  app.json
  ui/main.js, widget.js, view.js, style.css
  backend/main.py
  skill/SKILL.md
  tests/test_counter.py
```

Desde la raíz de Iris, con el entorno de desarrollo preparado:

```text
.venv/Scripts/python.exe -m pytest examples/apps/contador/tests
.venv/Scripts/python.exe scripts/app_package_tool.py check examples/apps/contador
.venv/Scripts/python.exe scripts/app_package_tool.py pack examples/apps/contador artifacts/contador.zip
```

El último comando crea un ZIP nuevo; nunca sobrescribe uno existente. Impórtalo
desde Iris Store en un **perfil de pruebas** ejecutado con la fuente nueva. La
inspección no ejecuta Python. Al instalar se añade la skill; el proceso Python
arranca con la primera consulta de la ventana, el widget o Hermes.

Abre la app y añade su widget desde Inicio. Sumar en uno actualiza el valor del
otro; una lectura periódica solo corre mientras esa vista está visible. Pide a
Iris consultar el contador o sumar una cantidad. La skill general `iris-os`
descubre las dos operaciones incluso si se elimina la skill opcional del paquete
y se declara `extension.skill: false`. Las peticiones de Hermes usan la política
`apps.actions`; las confirmaciones las decide Core.

Al cerrar la ventana sus datos se conservan. Actualizar reinicia su worker y sus
vistas; desinstalar retira el proceso, las acciones y la skill administrada. Los
datos se conservan por defecto; la Store permite elegir eliminarlos.

Para crear otra app, copia esta carpeta fuera de `apps/` y sustituye el id
`contador` en la ficha, los ids del widget, las clases/eventos de la interfaz y
el nombre de la skill (`iris-app-<id>`). Cambia título, descripción y operaciones;
no edites Core, el shell ni su allowlist. Valida el resultado con `check`.

El contrato y sus límites están en la [guía de apps con Python](../../../docs/guides/community-apps-python.md).
