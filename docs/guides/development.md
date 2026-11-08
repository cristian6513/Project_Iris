# Desarrollo y publicación

[← Índice de documentación](../README.md)

Esta guía cubre el entorno reproducible, las pruebas y la higiene mínima antes
de publicar IRIS OS.

Revisión: 2026-10-16. Antes de elegir una entrega, lee el
[estado actual](../estado-actual.md) y el plan coordinador. Los comandos de build
o publicación son procedimientos, no tareas ejecutadas por leer esta guía.

## Preparar el entorno

```bash
uv sync --extra dev
```

Extras opcionales de desarrollo (motores dentro del venv del checkout):

```bash
uv sync --extra kokoro
uv sync --extra coqui
uv sync --extra neutts
uv sync --extra hardware
```

No instales `all` salvo que realmente vayas a probar todas las integraciones:
varias dependen de hardware, Python o librerías nativas concretas.

Los botones de extras del Wizard no usan estos grupos: instalan los locks
aprobados de `core/extras_data/` en entornos aislados del perfil
(`<IRIS_HOME>/extras/`) y la voz se ejecuta en un proceso aparte. Para
regenerar un lock aprobado, en Windows x64 con CPython 3.11:

```bash
uv run --no-sync python scripts/lock_extras.py --extra kokoro
```

## Ejecutar el proyecto

```bash
uv run python -m wizard.app
uv run python -m core.agent
```

Después de una instalación editable también están disponibles `iris` y el alias
legacy `alice`.

## Pruebas

### Preparar los recursos antes de la suite Python

Las pruebas de distribución necesitan una exportación web verificada. La raíz
indicada por `IRIS_WEB_ASSETS_ROOT` debe contener `face/` y `wizard/ui/`. No es
la carpeta `face/` del checkout ni una exportación de otra revisión. El fixture
`tests/test_distribution_builds.py` usa esa raíz para construir fuera del repo.

Cuando cambian manifiestos o módulos de las apps, primero comprueba su catálogo:

```bash
node scripts/build_app_catalog.mjs --check
```

Para generar una exportación nueva, sigue [Recursos web locales](local-web-build.md).
`--verify-contract` debe coincidir con el inventario aprobado. Si el código
cambió después del último contrato, detente a identificar la diferencia: no
regeneres el contrato a ciegas ni cuentes esos errores como pruebas aprobadas.
La integración F09.6 debe reunir fuente, catálogo, contrato y exportación.

Con una exportación ya verificada, en PowerShell:

```powershell
$env:IRIS_WEB_ASSETS_ROOT = (Resolve-Path artifacts/mi-exportacion-verificada).Path
uv run --no-sync pytest -q
Remove-Item Env:IRIS_WEB_ASSETS_ROOT
```

Suite Python en un entorno donde esa variable ya está configurada:

```bash
uv run --no-sync pytest -q
```

Suite JavaScript:

```bash
node --test tests/js/*.test.mjs apps/*/tests/*.test.mjs
```

Companion:

```bash
npm --prefix companion ci
npm --prefix companion run build
cargo test --manifest-path companion/src-tauri/Cargo.toml
```

### Integración continua

`.github/workflows/ci.yml` define compuertas en cada cambio a
`main`, pull request y lanzamiento manual:

- Python 3.11 sobre Windows, que es la plataforma principal actual;
- Python 3.13 sobre Linux, para comprobar el extremo más reciente declarado;
- pruebas JavaScript y build web del Companion con Node 22;
- pruebas nativas del Companion con la versión Rust fijada en
  `rust-toolchain.toml`, y pruebas nativas de Desktop;
- inspección del contenido distribuible para impedir que bytecode, `.env`,
  workspaces 3D o paquetes de animación locales entren por accidente;
- construcción de `sdist` y wheel en Linux.

El workflow usa permisos de solo lectura, lockfiles y acciones fijadas por
commit. No recibe credenciales, no conecta un proveedor y no activa cámara,
micrófono, GPU ni servicios externos. Por tanto, pasar CI es una compuerta de
código reproducible, no reemplaza el smoke test físico de una release.
La tarea JS actual de CI ejecuta `tests/js/*.test.mjs`; para comprobar también
los módulos nuevos de apps se usa el comando ampliado de esta guía. No atribuyas
a CI resultados de suites que su workflow aún no invoca.
El instalador Companion solo puede aparecer dentro del canal de release
dedicado y debe coincidir con el SHA-256 de su manifiesto.

Una release necesita además pruebas manuales de OAuth, proveedor real,
micrófono, cámara, wake word, GPU, acceso LAN e instalador del Companion.

## Organización del código

```text
core/          Runtime y servicios
wizard/        Instalación y configuración
face/          Interfaz y avatar
companion/     Aplicación Tauri
personality/   Plantillas
scripts/       Utilidades y validaciones
tests/         Pruebas automatizadas
docs/          Guías y registros técnicos
apps/          Manifiestos, módulos y plantillas de apps propias
desktop/       Ventana Windows, runtime launcher e instalador versionado
tools/skills/  Fuentes de skills compartidos para colaboradores
```

Evita ampliar archivos centrales ya grandes si una responsabilidad puede vivir
en un módulo probado y reutilizable.

## Política del repositorio

Se versionan:

- código y documentación;
- lockfiles;
- manifiestos;
- licencias y avisos de terceros;
- recursos que la UI carga en ejecución;
- el modelo de MediaPipe requerido por el Companion.

No se versionan:

- `.env` o credenciales;
- configuración local de agentes;
- entornos y cachés;
- `node_modules`, `target`, `dist` o instaladores;
- workspace `assets/`, Blender y renders de QA;
- avatares de usuario;
- paquetes VRMA de terceros;
- modelos descargados.

Si un recurso artístico debe formar parte del producto, muévelo conscientemente
a `face/public/assets/`, optimízalo, documenta su origen/licencia y comprueba
que una referencia de runtime lo utiliza.

## Antes de publicar cambios

Antes de cada push verifica el árbol, la rama, el remoto y el upstream; no
deduzcas un estado limpio o una publicación a partir de una guía anterior:

```bash
git rev-parse --show-toplevel
git status
git remote -v
git branch -vv
```

No uses `push --force` por rutina ni publiques archivos ignorados solo para que
una demo funcione. Si cambia la visibilidad del remoto, confirma antes la
licencia, los assets redistribuibles y que el historial no contenga secretos.

## Checklist de pull request

- [ ] Cambio enfocado y comprensible.
- [ ] Pruebas nuevas o actualizadas.
- [ ] Suites relevantes aprobadas.
- [ ] Sin secretos, rutas personales o binarios generados.
- [ ] Documentación pública actualizada.
- [ ] Documento maestro actualizado si cambió arquitectura o producto.
- [ ] Riesgos manuales anotados.

## Checklist de release

- [ ] Versión única y coherente.
- [ ] Matriz de Hermes revisada.
- [ ] Instalación limpia y reparación verificadas.
- [ ] Catálogo de proveedores actualizado.
- [ ] OAuth/API key probados sin exponer credenciales.
- [ ] Voz, cámara y LAN probados con consentimiento.
- [ ] Companion firmado o marcado claramente como no firmado.
- [ ] Artefactos y hashes generados desde un árbol limpio.
- [ ] Licencias de dependencias y recursos revisadas.

### Orden de integración de un candidato

1. Cerrar los cambios previstos y las revisiones de esa fuente. Las pruebas de
   mutación deben usar copias aisladas y no solaparse con una exportación/build
   de los archivos que modifican.
2. Generar/revisar el catálogo de apps y una exportación web nueva. Contrastar
   el inventario y dejar identificado el contrato usado.
3. Ejecutar las suites afectadas y las generales requeridas, con su entorno de
   distribución correcto. Conservar fallos y omisiones en el informe.
4. Preparar runtimes, staging, instaladores y sus verificaciones según
   [Desktop](../../desktop/README.md). El motor empaquetado se toma de
   `scripts/runtime_sources.json`; no se actualiza con un `pip install` suelto.
5. Verificar payload, hashes, procedencia y firma declarada; después hacer la
   QA del EXE exacto. Un build válido no demuestra que haya pasado Windows limpio.

No se considera un candidato estable si su exportación mezcla revisiones. Los
intentos anteriores se conservan identificados y no se borran como parte de una
actualización documental. Los commits y la publicación se rigen por la
autorización de la tarea; esta lista no los ejecuta ni los autoriza por sí misma.

### Sustitución revisada de Edge-TTS en Hermes

El runtime de Hermes 0.21.6/revisión `818c13be1dc4fd28987e1e881a9408224afd4535`
usa Edge-TTS 7.3.1 en lugar del 7.2.7 fijado upstream. El código, pyproject y
uv.lock de Hermes conservan sus hashes originales; solo cambia el bloque
correspondiente del lock Windows de distribución.

`scripts/runtime_dependency_overrides.py` valida la declaración exacta de
`scripts/runtime_sources.json`: runtime, revisión, Python, versiones y hashes.
También reconstruye la huella del lock anterior para comprobar que no cambió
otra dependencia. Constructor, inventario y verificador privado aplican la
misma política. La excepción queda registrada en el pin y en la salida JSON
del grafo; no desactiva la validación de requisitos transitivos. Una revisión
de Hermes, Python u otra versión de Edge requiere revisar esta excepción.

La prueba debe usar la herramienta TTS de Hermes y su Python privado, generar
un archivo MP3 y Ogg/Opus decodificables y no depender de enviar mensajes a
una cuenta real. Ver el cierre de QA13 (informe interno no distribuido).

## Registros de diseño

El [documento maestro](../iris_os_master.md) conserva decisiones, backlog,
riesgos y evidencia. Los planes especializados sirven como historial técnico,
no como promesa automática de que todo esté disponible.
