# Animaciones corporales de Iris

> Disponibilidad por versión/candidato: [Estado actual](../estado-actual.md).
> Los resultados fechados de esta guía corresponden a aquella comprobación,
> no a una validación nueva de todos los ejecutables.

La biblioteca de Cristian contiene 18 VRMA originales. Las copias utilizadas
por Face y Companion están en `face/animations/iris_*.vrma`; los originales de
Descargas no se modifican. `face/animations/manifest.json` relaciona sus nombres
originales con los identificadores del catálogo.

## Pose base y estados de voz

- `idle` usa `pose_predeterminada`: se aplica al cargar cada VRM y sustituye los
  ángulos fijos que antes bajaban brazos, codos y manos. Una exportación de un
  solo fotograma es válida; internamente se sostiene sin movimiento.
- `idle_casual`, `idle_lazy` y `stretch` son gestos ambientales ocasionales.
- `listen` entra al detectar la palabra de activación o comenzar una grabación
  manual confirmada. Se reproduce una vez y conserva el último fotograma hasta
  terminar la captura. Tener Wake armado esperando la palabra no activa la pose.
- `think` acompaña la transcripción/procesamiento y `talk` el habla. Se repiten
  con transiciones suaves. Un gesto de respuesta puede terminar antes de
  continuar la animación de conversación.
- `listen` y `talk` se reservan al estado real de voz; no se ofrecen al modelo
  como gestos independientes. Sí se pueden previsualizar en el menú de poses.

Las acciones de habitación que aún no tienen reemplazo (sentarse, caminar y
usar el escritorio) conservan sus recursos anteriores. Los clips `sit`,
`walk_in_place`, `pc_sit_down_v2` y `pc_seated_idle_v2` están reservados a
la habitación: no se ofrecen al agente como gestos y Core descarta su selección
en `animacion_virtual`. Los 16 gestos originales de conversación siguen
disponibles.

## Expresiones faciales por pose

Los 18 perfiles de `facial_profiles` están activos en Face y Companion. Saludos, confirmación y celebración añaden una sonrisa suave; pensar, descansar y estirarse relajan los párpados; enojo y sorpresa tienen sus propios acentos. Escuchar y hablar conservan la emoción de la conversación. Los cambios entran y salen gradualmente, sin sustituir la emoción que llega del chat.

```json
"think": {
    "emotion": "pensativa",
    "strength": 0.45,
    "eyes": {
        "gaze": "natural",
        "blinkInterval": 5.5,
        "blinkDuration": 0.36,
        "lidClosure": 0.08
    },
    "mouth": null
}
```

- `emotion`: acento temporal; `null` conserva la emoción de la conversación.
- `strength`: intensidad de mezcla, limitada a 0.7.
- `blinkInterval`: intervalo aproximado en segundos, con variación natural.
- `blinkDuration`: duración del cierre/apertura, fijada al comenzar cada parpadeo.
- `lidClosure`: relajación leve de párpados (máximo 0.18); permite cerrar completamente al parpadear.
- `gaze: natural`: permite mirar con los ojos durante la pose. No modifica cabeza, cuello ni torso.
- `mouth.open`: apertura leve en silencio (máximo 0.22). El audio y el estado de habla tienen prioridad.

`body_only_clips` declara los 18 archivos sin animación facial. Su reproducción bloquea los movimientos corporales secundarios que podrían producir saltos, pero permite ojos y expresiones. Las animaciones externas que sí traen rostro mantienen el control facial. No se modifica el VRMA ni se reactiva el espejo de cámara.

Los modelos necesitan formas faciales vinculadas a sus expresiones. Carlotta, Cantarella y Carthetia permiten parpadeo visible. Las copias de Iris revisadas (incluida `IRIS_VRMA_corregida.vrm`) tienen nombres de expresiones sin formas vinculadas; el código no puede cerrar sus párpados hasta corregir esa exportación.

## Velocidad de reproducción

Las poses se reproducen a **1×** (`setEffectiveTimeScale(1)` y reloj del mezclador a 1). Los 18 archivos instalados coinciden byte por byte con los originales de Descargas. Los tiempos `duration: 3000` del catálogo son valores de respaldo para poses procedurales; los VRMA terminan según su tiempo real, sin acortarse a tres segundos. Las transiciones mezclan poses y suavizan el regreso, sin acelerar fotogramas.

La pose base de un solo fotograma se mantiene fija; escuchar conserva el último fotograma hasta terminar la captura, mientras pensar y hablar repiten su ciclo.

Si un movimiento resulta demasiado rápido, separar sus fotogramas clave en la línea de tiempo aumenta su duración. Extender únicamente el fotograma final de exportación añade espera, pero no ralentiza el movimiento ya existente.

## Verificación

La mejora facial se verifica con `scripts/verify_pose_faces.mjs` (18 perfiles, Face y Companion, cuatro modelos). Mide el parpadeo sobre la geometría, los relojes de reproducción a 1× y la prioridad del audio. Informes y capturas: `artifacts/pose-faces/`. El archivo `durations.json` compara los originales con las copias instaladas.

Se verificaron Iris VRM 1 y los tres VRM 0 de Descargas: Cantarella, Carlotta y
Carthetia. La galería y los informes están en `artifacts/animation-integration/`.
Se probaron finales, repeticiones, interrupciones y el flujo de voz del Companion
compilado mediante un Core simulado; esta prueba no graba el micrófono real.

Para cargar los cambios, reiniciar Core y recargar Face. El ejecutable compilado
está en `companion/src-tauri/target/release/iris-desktop-companion.exe`; no se
instala automáticamente sobre la versión que ya esté abierta o instalada.
