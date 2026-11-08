# Plantilla: app de solo interfaz

Una app mínima de IRIS OS: una ventana con una lista, búsqueda y detalle, que usa **datos de ejemplo**. Sirve para empezar una app que no necesita Python ni la IA.

## Crear tu app a partir de esta plantilla

1. Copia la carpeta con el nombre de tu app: `apps/_plantilla` → `apps/notas`. El nombre de la carpeta y el `id` de `app.json` deben coincidir: minúsculas, números y guiones.
2. Reemplaza `mi-app` por el id de tu app en `app.json`, `ui/main.js` (la constante `CSS`) y `ui/style.css`. También puedes reemplazarlo en toda la carpeta. Las pruebas no dependen del nombre: buscan cada elemento por su `data-part`.
3. En `app.json` cambia `name`, `description` e `icon`. El icono es un nombre de [`face/js/os_icons.js`](../../face/js/os_icons.js).
4. En `ui/main.js` cambia los datos de ejemplo por los de tu app. Crea cada elemento con `el(etiqueta, parte)`: recibe la clase `<id>-<parte>`, la que usa `style.css`, y el `data-part` que buscan las pruebas.
5. Genera el catálogo:

   ```bash
   node scripts/build_app_catalog.mjs
   ```

   El script valida la ficha y copia `ui/` a `face/apps/<id>/`. No edites esa copia, porque se regenera.
6. Prueba la app:

   ```bash
   node --test apps/notas/tests/*.test.mjs
   ```

   Ábrela también desde el lanzador del escritorio, con ratón y teclado.

## Qué contiene

```text
app.json          ficha de la app (solo datos)
ui/main.js        createApp(context): mount, refresh, setVisibility, navigate, dispose
ui/style.css      estilos, todos bajo .mi-app
tests/            pruebas de la app
```

**Contrato de la interfaz.**
- **Cuándo se descarga:** IRIS OS descarga `ui/main.js` la primera vez que se abre la app y llama a `createApp(context)` una sola vez.
- **Cerrar no destruye:** cerrar o minimizar la ventana no destruye la app; solo cambia su visibilidad (`setVisibility`).
- **`dispose()`:** debe liberar lo que la app creó (timers, listeners) y vaciar su vista.

Más detalle en [`apps/README.md`](../README.md).

**Reglas que la plantilla ya cumple:**
- los textos van con `textContent`, nunca con `innerHTML`;
- los estilos solo afectan a la app;
- los colores salen del tema activo;
- el foco es visible y las animaciones respetan «reducir movimiento»;
- el reloj de la ventana solo corre mientras se ve.

Si tu app necesita leer datos con Python, parte de [`_plantilla-servicio`](../_plantilla-servicio/README.md).
