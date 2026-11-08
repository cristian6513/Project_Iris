# Transcodificador KTX2 de IRIS

Face y Companion empaquetan estos dos archivos mediante `scripts/basis_assets.mjs`.
El cargador de Three.js 0.170.0 usa su API de Basis. La compilación evita que
Embind genere JavaScript en el worker, de modo que Companion puede mantener
su política de seguridad con `wasm-unsafe-eval` y sin `unsafe-eval`.

- Basis Universal `v1_50_0_2`, revisión `b76a431c6a39c07fe8bb2edf1cbf44781150ed6b`.
- Emscripten `4.0.23`, SDK `c0bb220cb6e6f4e0fabb6f6db9efd53390ef5e56`.
- [Licencias y hashes](../../third-party/BasisUniversal-Apache-2.0.txt).

Para reconstruir el par, activa esa versión de Emscripten en la terminal y
ejecuta desde `webgl/transcoder` del repositorio de Basis Universal. El comando
aplica las opciones del CMake de esa versión y fija la configuración de
Embind y la API del módulo que necesita Three.js.

```sh
emcc ../../transcoder/basisu_transcoder.cpp basis_wrappers.cpp ../../zstd/zstddeclib.c \
  -I../../transcoder -DNDEBUG -DBASISD_SUPPORT_UASTC_HDR=1 -DBASISD_SUPPORT_UASTC=1 \
  -DBASISD_SUPPORT_BC7=1 -DBASISD_SUPPORT_ATC=0 -DBASISD_SUPPORT_ASTC_HIGHER_OPAQUE_QUALITY=0 \
  -DBASISD_SUPPORT_PVRTC2=0 -DBASISD_SUPPORT_FXT1=0 -DBASISD_SUPPORT_ETC2_EAC_RG11=0 \
  -DBASISU_SUPPORT_ENCODING=0 -DBASISD_ENABLE_DEBUG_FLAGS=1 \
  -DBASISD_SUPPORT_KTX2=1 -DBASISD_SUPPORT_KTX2_ZSTD=1 \
  -O3 -fno-strict-aliasing --bind -sALLOW_MEMORY_GROWTH=1 -sASSERTIONS=0 \
  -sMALLOC=emmalloc -sMODULARIZE=1 -sEXPORT_NAME=BASIS -sDYNAMIC_EXECUTION=0 \
  -sEXPORTED_RUNTIME_METHODS=HEAP8 \
  -sINCOMING_MODULE_JS_API=wasmBinary,onRuntimeInitialized,locateFile,print,printErr,onAbort \
  -o basis_transcoder.js
```

Al actualizar el par, comprueba los hashes y la carga sin conexión de un VRM
KTX2 en el Companion empaquetado con su CSP activa. Comprueba también un VRM
PNG y el límite de texturas en Ahorro.
