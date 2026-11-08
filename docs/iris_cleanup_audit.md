# IRIS OS Cleanup Audit

> **Referencia histórica/de diseño.** Puede describir rutas, versiones o
> funciones del corte original. Para uso actual, consulta [Estado actual](estado-actual.md)
> y las [guías](README.md); no se altera aquí la evidencia histórica.

Fecha: 2026-08-13

## Decisiones de esta pasada

- Se renombraron los tres artefactos Blender históricos sin borrar su contenido.
- Se eliminó `alice_robot.egg-info/`, metadato generado y regenerable que duplicaba `iris_os.egg-info/`.
- Se limpiaron textos visibles y comentarios desactualizados de IRIS cuando eran claramente marca vieja.
- Se mantuvieron alias tecnicos legacy cuando romperlos podria afectar configuraciones existentes.

## Alias legacy que siguen siendo intencionales

- Los imports históricos de configuración/agente/memoria se conservan como aliases.
- Las variables `ALICE_*` tienen menor prioridad que las nuevas `IRIS_*`.
- Las rutas antiguas del lifecycle permanecen ocultas del esquema junto a `/api/iris/...`.
- El comando `alice` continúa como alias del comando canónico `iris`.

El almacenamiento nuevo es `~/.iris`; al iniciar se migra el directorio
anterior. Los IDs persistidos de tema y personalidad también se normalizan a
`iris` al cargarlos.

## Assets candidatos a revisar

Estos archivos no parecen estar referenciados por runtime directo, o son artefactos de trabajo:

- `IRIS-room-hotspots-desk.png`
- `IRIS-room-hotspots-platform.png`
- `IRIS-room-lighting-polish.png`
- `IRIS-room-phase1.png`
- `tools/blender/output/iris_clean_rig_preview.glb` si sigue sin usarse en runtime
- `tools/blender/output/iris_animation_workbench.blend` si se decide no conservar la fuente Blender
- `tools/blender/output/iris_animation_workbench.blend1` si no se necesita el backup automático
- `tools/blender/output/animation_manifest_audit.json`

Antes de borrarlos conviene confirmar si alguno se usa para documentacion, demo, backup de Blender o empaquetado local.

## Archivos GLB/GLTF que no conviene borrar aun

Estos si estan conectados al sistema de animaciones o a `manifest.json`:

- `face/animations/pc_focus_idle.glb`
- `face/animations/pc_seated_idle.glb`
- `face/animations/pc_sit_down.glb`
- `face/animations/walk_in_place.glb`
- `face/animations/_draft_pc_sit_down_v2.glb`
- `face/animations/_draft_pc_seated_idle_v2.glb`

Aunque `_draft_*` suena temporal, hoy se usa para las poses de PC. Mejor renombrarlos en una migracion especifica:

- `_draft_pc_sit_down_v2.glb` -> `pc_sit_down_v2.glb`
- `_draft_pc_seated_idle_v2.glb` -> `pc_seated_idle_v2.glb`

Tambien hay `.gltf` equivalentes que parecen copias de trabajo/export. No estan en `manifest.json`, pero pueden ser utiles para inspeccion manual.

## Siguiente limpieza recomendada

1. Evitar que metadatos de empaquetado antiguos reaparezcan fuera del build temporal.
2. Decidir si los archivos fuente de Blender renombrados se conservan como archivo.
3. Retirar aliases legacy únicamente en una futura versión mayor documentada.

## Verificación de cierre

- Suite Python: `316 passed, 1 skipped` (la regresión de coexistencia `.IRIS`/`.iris` solo aplica a filesystems case-sensitive).
- Suite JavaScript: `19 passed`.
- Suite Rust del Desktop Companion: `6 passed`.
- Build Vite del frontend nativo: correcto y regenerado con los fuentes actuales.
- Los únicos usos restantes del nombre anterior en runtime son lectores o aliases explícitos de migración, no textos visibles ni valores nuevos.

