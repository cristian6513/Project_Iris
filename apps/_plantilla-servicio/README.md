# Plantilla: app con servicio Python de lectura

Una app de IRIS OS cuya interfaz lee datos de **su propio servicio Python**. Úsala cuando la app necesita datos guardados, archivos o procesos que el navegador no puede hacer solo. Si no los necesitas, parte de [`_plantilla`](../_plantilla/README.md).

## Qué contiene

```text
app.json                   ficha de la app (solo datos)
ui/main.js                 interfaz: lee GET /api/apps/<id>/items; handleIntent
ui/style.css               estilos, todos bajo .mi-servicio
backend/service.py         create_router(context) y operations(context)
backend/sample_items.json  datos de ejemplo que trae la app
tests/main.test.mjs        pruebas de la interfaz (con un servicio simulado)
tests/test_service.py      pruebas del servicio
```

**El servicio:**
- `create_router(context)` devuelve un `APIRouter` de FastAPI.
- Core lo publica en `/api/apps/<id>/`, con las mismas protecciones que el resto de su API.
- `context.data_dir` es la carpeta de la app dentro del perfil (`<perfil>/app-data/<id>`). Core la elige y la crea; una petición nunca indica una ruta.
- La plantilla solo lee: devuelve los datos de ejemplo o, si existe, el `items.json` de esa carpeta.

**La interfaz:**
- Una lectura nueva siempre gana: una respuesta vieja que llega tarde se descarta.
- Al retirar la app se cancela la lectura en curso.
- Si el servicio no está activo, lo dice en lugar de mostrar una lista vacía.

## Acciones para Iris

`backend/service.py` también define `operations(context)`, con dos acciones de ejemplo:

| Acción | Efecto | Dónde corre | Qué hace |
| --- | --- | --- | --- |
| `list_items` | `read` | Core | Devuelve los elementos con la **misma función** (`load_items`) que usa `GET /items`, así que la interfaz y Hermes nunca discrepan. |
| `show_item` | `local` | Interfaz de la app | Llega a `handleIntent` de `ui/main.js`, que marca el elemento en pantalla y responde con lo que realmente hizo. |

**Confirmación y permisos:**
- Core valida los argumentos de cada acción. Por ejemplo, `show_item` exige `{"item": "<id>"}`.
- Pide confirmación a la persona en IRIS OS con el texto de su `label`. Si la acción corre en Core, la confirmación se pide antes de ejecutarla.
- Las dos usan la capacidad `apps.actions`.

Hermes las encuentra en el catálogo de la skill `iris-os`.

## Cuándo se activa el servicio

**Core solo monta el servicio de una app revisada.** Añadir su `id` a `REVIEWED_APP_SERVICES` en [`core/app_services.py`](../../core/app_services.py) es parte de la revisión del pull request. Hasta entonces, la interfaz muestra «El servicio de esta app no está activo en Iris».

El servicio corre dentro de Core, con los mismos permisos que Iris. Por eso:
- usa solo la biblioteca estándar y los paquetes que Iris ya incluye: una app nunca instala dependencias;
- no ejecutes comandos ni rutas que vengan de una petición;
- valida y limita todo lo que devuelves.

## Pasos

1. Copia la carpeta: `apps/_plantilla-servicio` → `apps/lector`. El `id` de `app.json` debe coincidir con el nombre de la carpeta.
2. Reemplaza `mi-servicio` por el id de tu app en `app.json`, `ui/main.js` (la constante `CSS`) y `ui/style.css`. También puedes reemplazarlo en toda la carpeta. Ni el servicio ni las pruebas dependen del nombre: las pruebas leen el id de `app.json` y buscan cada elemento por su `data-part`.
3. Cambia `name`, `description` e `icon` en `app.json`.
4. Genera el catálogo con `node scripts/build_app_catalog.mjs`. Solo `ui/` llega a `face/apps/<id>/`; `backend/` y `tests/` se quedan en la carpeta de la app.
5. Prueba:

   ```bash
   node --test apps/lector/tests/*.test.mjs
   ```

   ```bash
   .venv/Scripts/python.exe -m pytest apps/lector/tests
   ```
