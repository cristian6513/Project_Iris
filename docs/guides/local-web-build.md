# Recursos web locales de Iris

A08.3 prepara Face, el preview web de Companion y el Wizard con módulos,
fuentes, QR y Vision locales. Las conexiones a proveedores remotos conservan
sus requisitos de red y autenticación. El runtime privado y el instalador
Windows tienen entregas propias en el plan A08.

Revisión documental: 2026-10-26. QA16 ya contiene una exportación verificada de
su fuente congelada. El checkout tiene cambios posteriores, incluido este manual.
Al retomar, usar la exportación y el contrato de la misma revisión que se vaya
a construir; una carpeta anterior no incorpora cambios posteriores. Una nueva
exportación web no reconstruye el instalador entregado. Véase
[Estado actual](../estado-actual.md).

## Preparación desde el checkout

Se necesita la versión de Node de `.node-version` y las dependencias exactas
de `companion/package-lock.json`. El modelo MediaPipe versionado está incluido
en `companion/vendor/mediapipe`; el builder comprueba sus bytes y licencia.
La instalación inicial de paquetes npm requiere red o una caché preparada.

Si cambian `apps/`, primero genera y revisa su catálogo/exportación para Face:

```powershell
node scripts/build_app_catalog.mjs
node scripts/build_app_catalog.mjs --check
```

El mismo paso genera los imports y estilos de `widgets` declarados en cada
`app.json`, además de `core/widget_catalog.py`. No editar esas salidas a mano
ni mantener otra lista de widgets al preparar el candidato. Véase
[Widgets](widgets.md).

Ese paso no construye el instalador ni actualiza por sí solo el contrato web.

```powershell
npm --prefix companion ci --ignore-scripts
node scripts/build_web.mjs --output artifacts/local-web-new --verify-contract scripts/release_web_assets.json
```

El destino debe ser nuevo. El comando conserva los builds de preview y rechaza
enlaces, rutas ambiguas, recursos faltantes, versiones incorrectas y un
inventario diferente del aprobado. Face usa `/face/`; el Wizard tiene sus
propias fuentes bajo `/ui/` y conserva sus scripts clásicos.

Si un cambio autorizado modifica un recurso público, crear otro candidato
con `--output` y revisar sus diferencias. `--contract scripts/release_web_assets.json`
registra explícitamente el nuevo inventario de confianza para esa revisión;
no usarlo para ocultar fallos de verificación. Ejecutar las pruebas relacionadas,
la reconstrucción de paquetes y el smoke offline antes de aceptar el candidato.

## Wheel y sdist

Desde el checkout, señalar el staging aprobado al construir:

```powershell
$env:IRIS_WEB_ASSETS_ROOT = (Resolve-Path artifacts/local-web-new).Path
uv build
uv run --no-sync python scripts/verify_distribution.py dist
Remove-Item Env:IRIS_WEB_ASSETS_ROOT
```

Los hooks leen únicamente los recursos del staging validado. El sdist guarda
los mismos bytes preparados y permite reconstruir la wheel sin Node/npm;
el entorno Python de build debe disponer de sus dependencias. El verificador
contrasta hashes, tamaños, rutas, licencias y ausencia de recursos privados
con los contratos del checkout. A04 sigue controlando las poses de fábrica.

Para las pruebas Python de distribución se usa también `IRIS_WEB_ASSETS_ROOT`
antes de ejecutar pytest, no solamente antes de `uv build`. Su valor debe ser
la raíz que contiene `face/` y `wizard/ui/`. Una exportación vieja puede fallar
contra un contrato nuevo; corregir el entorno no convierte un inventario
realmente desactualizado en válido. Véase [Desarrollo](development.md#pruebas).

`scripts/build_release.py` prepara el staging web, los paquetes Python y el
instalador Companion. El instalador integral de Iris tiene una entrada distinta:
[COMPILAR.cmd y su receta de compilación personal](build-from-source.md), que
también prepara runtime y staging, compila el launcher y ejecuta NSIS. El smoke
de recursos web por sí solo no demuestra que un instalador funcione en una
máquina limpia.

## Verificación y recuperación

Los tests están en `tests/test_distribution_*.py`, `tests/test_static_assets.py`,
`tests/test_http_read_security.py` y las suites JS de web, QR y Vision.
`scripts/verify_local_web_server.py` y `scripts/verify_local_web.mjs` prueban un
candidato copiado a un directorio con espacios y Unicode, con APIs sintéticas,
red externa bloqueada y vídeo sintético. Guardan peticiones, hashes y capturas;
no conectan cuentas ni prueban cámara física o Tauri instalado.

Un fallo deja el candidato incompleto en su propio destino para inspección.
Preparar otro destino nuevo; la preview y los perfiles existentes permanecen
disponibles. Recuperar el contrato previo junto con sus recursos aprobados si
se descarta una revisión; no mezclar contratos y bytes de versiones distintas.
