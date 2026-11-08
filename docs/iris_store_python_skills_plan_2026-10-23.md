# Ampliación comunitaria de Iris Store: Python, acciones y skills

Fecha: **2026-10-23**. Estado: **implementado en fuente, autorizado por el usuario; candidato nuevo y QA instalada pendientes**.
El contrato SDK 2 se describe en la [guía de apps externas](guides/community-apps-python.md).
La implementación y su evidencia están en el informe del SDK 2 (informe interno no distribuido).
Contrato y recorridos del SDK comprobados. La suite general conserva dos fallos
intermitentes de rename de carpetas de Hermes; pasan aislados y quedan registrados
para cerrar antes de aprobar otro candidato.
No está incluido en los instaladores QA14; la aceptación de un candidato nuevo y
las comprobaciones instaladas permanecen pendientes. Las secciones siguientes
conservan el alcance original y sus criterios, no declaran por sí solas su aceptación.

## Objetivo acordado

Permitir que la comunidad distribuya una app con su interfaz, widgets, lógica Python
y, cuando la necesite, una skill para Hermes en un mismo paquete. Mejorar la skill
de contribución y las plantillas para que una persona pueda crear, probar, instalar,
actualizar y retirar ese paquete sin modificar el código fuente de Iris.

El usuario autorizó iniciar esta ampliación sobre la fuente del siguiente ciclo.
La prueba del candidato QA14 sigue pendiente y no cambia los EXE ya entregados.

## Punto de partida anterior al SDK 2

| Pieza | Situación al redactar el plan |
| --- | --- |
| Store | Instala `app.json` y `ui/`, incluidos widgets. Rechaza `backend/`, `skill/`, servicios y permisos. |
| Almacén | Guarda los paquetes de interfaz en `apps/app-packages/packages.sqlite3` del perfil, como registros y blobs; no extrae carpetas del ZIP. |
| Python del kit | `backend/service.py` se integra mediante revisión. `core/app_services.py` lo ejecuta dentro de Core solo si figura en `REVIEWED_APP_SERVICES`, hoy vacío. |
| Acciones | `core/app_actions.py` y la skill general `iris-os` ya permiten describir, validar y ejecutar operaciones registradas. Una skill propia no es obligatoria para cada app. |
| Skills administradas | `core/iris_skill_lifecycle.py` sincroniza la skill incluida `iris-os`; no es un instalador de skills de apps externas. |
| Ejemplo Player | Une ventana, widget y acciones de Hermes, pero usa una integración incluida con Iris, no un backend externo instalable. |

Fuentes: [Store](guides/iris-store.md), [gestor de paquetes](../core/app_packages.py),
[servicios](../core/app_services.py), [acciones](../core/app_actions.py),
[sincronización de la skill incluida](../core/iris_skill_lifecycle.py) y
[Player](../apps/player/README.md).

## Contrato implementado en fuente

1. **Una unidad de instalación.** La ficha declara componentes opcionales y versiones
   compatibles del SDK: interfaz, widgets, backend, acciones y skill. Versionar y
   validar el contrato en Python y JavaScript antes de documentar campos concretos.
   Las apps actuales de solo interfaz deben seguir funcionando.
2. **Python fuera de Core.** Las apps externas usan un proceso administrado por Iris,
   con arranque, parada, límites de petición y errores identificados por app. No
   basta con quitar el rechazo de `backend/` ni con añadir sus ids a la allowlist de
   servicios revisados que se importan dentro de Core.
3. **Runtime y dependencias separados.** Usar un Python compatible administrado por
   Iris, sin exigir instalaciones globales ni alterar los paquetes de Core o Hermes.
   La primera entrega puede limitarse a biblioteca estándar y un SDK acotado. Para
   admitir librerías adicionales hacen falta entornos propios, versiones y hashes
   fijados, compatibilidad con el Python/Windows empaquetado y recuperación ante una
   descarga fallida. No ejecutar instalaciones al abrir la app ni hooks arbitrarios
   del paquete durante su inspección.
4. **Instalación explícita.** Antes de instalar, mostrar qué Python se ejecutará,
   qué skill se añadirá, descargas necesarias y efectos de la actualización. Una
   app no se vuelve oficial ni revisada porque su ficha lo afirme. Conservar el
   modelo de confianza explícito de la Store: un proceso y un entorno separados
   mejoran estabilidad y dependencias, pero no son un sandbox del sistema operativo.
5. **Acciones descubribles.** Exponer las operaciones mediante el registro y sus
   validadores, resultados y confirmaciones. La skill general puede resolver apps
   sencillas; una skill adicional aporta instrucciones de uso y flujos específicos.
   Una instrucción de la skill no concede permisos ni sustituye una confirmación.
6. **Skills con propietario.** Registrar instalación, app, versión, archivos y hashes
   de cada skill en el perfil Hermes correcto. Evitar colisiones con `iris-os`,
   skills ajenas y las de otra app. No reescribir SOUL.md ni otras instrucciones
   globales para insertar el contenido del paquete. Si un archivo administrado fue
   modificado por la persona, informar el conflicto y conservarlo sin sobrescribirlo.
7. **Archivos y datos separados.** Mantener el almacén actual. Si el ejecutor necesita
   materializar Python o scripts, hacerlo en una ubicación privada y versionada,
   derivada del registro de instalación, con hashes y rutas comprobados. Los datos
   de usuario quedan aparte; nunca tratar `apps/` del perfil como una carpeta de
   paquetes que se pueda borrar en bloque.
8. **Ciclo completo.** Instalar todos los componentes o conservar el estado anterior;
   actualizar con reinicio explícito de la app afectada; retirar sus operaciones,
   proceso, skill administrada, widgets, estilos y accesos al desinstalar. Aplicar
   la elección de conservar o eliminar sus datos solo a los datos que le pertenecen.
   Resolver operaciones y conversaciones en curso sin ejecutar respuestas de una
   versión retirada ni repetir efectos externos por un timeout.

Los conectores de cuentas siguen siendo un contrato aparte: una app no incluye
claves ni tokens en el ZIP, la ficha o las instrucciones de Hermes. La ampliación
debe coordinarse con el [plan del SDK de conexiones](iris_connections_sdk_plan_2026-10-17.md),
sin convertir esta entrega en una integración de Notion u otro proveedor real.

## Orden de trabajo

1. Cerrar la QA actual y agrupar las correcciones que correspondan al siguiente candidato.
2. Definir el contrato versionado, el propietario de cada componente y la recuperación
   de instalaciones/actualizaciones interrumpidas. Revisar el lanzamiento con los
   Python privados que realmente se empaquetan.
3. Implementar paquete y ejecutor Python con biblioteca estándar, acciones y lifecycle.
4. Implementar la skill opcional administrada, descubrimiento por Hermes y conflictos
   de versiones/perfil; conservar el funcionamiento manual con Hermes apagado.
5. Probar una app externa completa; luego actualizar la guía, la skill de contribución
   y las plantillas contra la API ejecutada. Evaluar dependencias adicionales como
   ampliación explícita, sin presentarlas como ya admitidas.
6. Exportar y compilar el conjunto en un candidato nuevo, con QA instalada.

## Qué debe enseñar la skill de contribución

La [skill de contribución](../tools/skills/iris-app-contributor/SKILL.md) ya se amplió
contra la API ejecutada y el ejemplo Contador:

- Elegir entre solo interfaz, interfaz con widgets y app con Python/acciones/skill.
- Generar ficha, plantilla, README y pruebas que se puedan renombrar sin romperse.
- Definir qué hace la app al cerrar, minimizar o quitar su widget; compartir el
  estado entre ventana, widget y las acciones de Hermes.
- Pedir solo los recursos necesarios y describir errores, cancelación y efectos
  inciertos. Distinguir acciones registradas de instrucciones en una skill.
- Probar y empaquetar con un comando documentado que compruebe el contrato real.
- Instalar desde la Store en un perfil desechable, actualizar y desinstalar sin
  editar Iris; explicar qué dependencias están soportadas y cuáles todavía no.

## Evidencia necesaria para darla por terminada

Usar una app de ejemplo sin cuentas externas: ventana, widget, un cálculo o
temporizador persistido por Python y acciones consultables desde Hermes. Debe
demostrar una skill opcional con un flujo propio, además del camino sin skill propia.

- Validación de manifiesto/paquete equivalente en build y Store, compatibilidad
  con los paquetes actuales y rechazo de rutas, colisiones y dependencias no soportadas.
- Instalar, consultar desde ventana/widget/Hermes, cancelar una acción y comprobar
  un proceso que falla o tarda demasiado sin bloquear Core.
- Actualizar abierta, desinstalar con guardados o acciones en curso y reinstalar;
  no reaparecen acciones, widgets o estilos de la versión anterior.
- Interrumpir instalación/actualización y recuperar la versión previa sin dejar
  skill huérfana ni cambiar dependencias compartidas.
- Dos perfiles independientes, una skill ajena con el mismo nombre y una skill
  administrada modificada manualmente: no se sobrescriben ni se borran datos ajenos.
- Probar con Hermes apagado y en conversación real dentro del candidato Windows,
  fuera del checkout y con los Python privados. Los tests del SDK por sí solos
  no acreditan esa integración instalada.

La guía distingue el SDK 2 de la fuente nueva del SDK 1 incluido en QA14, que solo
instala interfaz y widgets. Los criterios de conversación real y candidato Windows
siguen pendientes; las comprobaciones de fuente y los Python privados se detallan
en el informe. No hay catálogo remoto, sandbox de código hostil ni un SDK de
dependencias generales en esta entrega.
