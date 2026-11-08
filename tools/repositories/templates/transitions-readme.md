# Crear transiciones para Iris OS

Guía, formato y ejemplos de transiciones de entrada del escritorio y de apertura/restauración de ventanas. Son animaciones CSS limitadas y validadas por Iris; no son plugins JavaScript, poses del avatar ni paquetes de efectos de otros escritorios.

- [Guía completa](docs/guides/theme-transitions.md).
- [Esquema JSON v1](docs/schemas/iris-transition-v1.schema.json).
- [Ejemplos CSS y JSON](examples/transitions/README.md): Entrada suave, Zoom sereno y Giro sutil.

## Comprobar y convertir

Solo necesitas Node.js; no hay paquetes npm que instalar.

```text
node scripts/transition_tool.mjs check examples/transitions/entrada-suave.css
node scripts/transition_tool.mjs check examples/transitions/entrada-suave.json
node scripts/transition_tool.mjs convert mi-entrada.css mi-entrada.json --name "Mi entrada" --author "Tu nombre" --license "MIT"
```

La herramienta utiliza el mismo lector que Theme Studio y no sobrescribe un JSON existente. Comprueba el archivo final y luego impórtalo en **Theme Studio → Transiciones → Añadir transición**. Prueba con movimiento reducido y un tema alternativo.

Los tres ejemplos incluidos tienen su aviso MIT dentro de los archivos. Las transiciones destinadas a donadores se mantienen aparte; este kit no las publica ni las incorpora automáticamente.

`SOURCE.json` identifica el corte del lector y los ejemplos. Los cambios se mantienen en el proyecto de desarrollo y se exportan a este repositorio. No cambies sus límites para hacer pasar un efecto: adapta el efecto o propón una ampliación del formato.
