# Animaciones corporales de Iris

> Revisión documental: 2026-11-13. Disponibilidad y candidato exacto:
> [Estado actual](../estado-actual.md). Los informes fechados conservan el alcance
> de su propia prueba; no acreditan automáticamente todos los VRM o ejecutables.

## Biblioteca distribuida

**QA24 contiene 95 clips de fábrica**. Incluye gestos originales de Cristian,
gestos adaptados de movimientos de Tripo AI, variantes de habla y caminata,
acciones de sala y reposo boca abajo. Las variantes se eligen al reproducir
la acción. Si reemplazas un gesto de fábrica, Iris usa tu clip y deja de
mezclarlo con las variantes y expresiones sincronizadas de fábrica.
[Licencias y créditos](licenses.md).

El catálogo de fuente está en face/animations/manifest.json. El empaquetado usa
los inventarios explícitos scripts/release_assets.json y
core/factory_animation_inventory.py, con hashes. Los dos recursos antiguos
sit y walk_in_place pueden seguir en la fuente
para pruebas/compatibilidad (los borradores pc_sit_down_v2 y pc_seated_idle_v2 se
retiraron el 2026-11-11), pero **no forman parte de los clips distribuidos** ni
se ofrecen al agente como gestos.

Los gestos del menú **Poses** se filtran por los recursos disponibles. Las
secuencias de muebles se usan desde **Explorar → Acciones** o las
[rutinas de la habitación](room.md#vida-propia); no se convierten en gestos de
conversación genéricos. Las bibliotecas del perfil pueden ampliar o sustituir
recursos conforme al contrato de animaciones, sin añadir archivos de terceros
al inventario de fábrica por un comodín.

## Pose base y estados de voz

- idle es el reposo de base del VRM; idle_casual, idle_lazy y stretch añaden
  variaciones ambientales según el contexto.
- listen entra al confirmar una captura manual o activar realmente la captura
  de Wake. Se reproduce una vez y conserva el último fotograma hasta terminar;
  tener Wake esperando no la activa.
- think acompaña el procesamiento. El habla alterna tres versiones entre
  respuestas, con transiciones suaves.
- Un gesto de respuesta puede terminar antes de continuar la animación de habla.
- listen y talk se reservan al estado de voz, aunque se puedan previsualizar.
- Las acciones sentadas conservan el asiento: los gestos de cabeza compatibles
  pueden superponerse, pero se omite un gesto completo que rompería la postura.

Companion no tiene los muebles. Sustituye visitas por acciones de pie y carga
los pequeños modelos de libro y tablet cuando existen. Consulta sus
[alternativas y preferencias](desktop-companion.md#vida-propia-en-companion).

## Expresiones faciales por pose

El manifiesto declara perfiles faciales y body_only_clips para los 59 clips
corporales. El cuerpo conserva su propiedad de los huesos, mientras ojos,
visemas y emoción se coordinan aparte. Un clip externo con rostro propio puede
conservar su control facial: no se trata como corporal solo por su nombre.

En idle, idle_casual, idle_lazy y stretch, **moments** permite variar el acento a
lo largo del clip. Cada momento usa segundos from/to y puede definir emoción,
intensidad y relajación de párpados. Al hablar o recibir una emoción del agente
el acento de reposo se retira más deprisa; sigue siendo una transición gradual.

~~~json
{
  "emotion": "neutral",
  "strength": 0.35,
  "moments": [
    {"from": 2.6, "to": 6, "emotion": "feliz", "strength": 0.24}
  ]
}
~~~

- emotion elige un acento temporal; null no añade un preset.
- strength se limita a 0,7.
- eyes.blinkInterval y blinkDuration regulan el parpadeo natural.
- lidClosure puede llegar a 0,3; no impide un parpadeo completo.
- gaze: natural conserva la mirada; no mueve el torso ni cambia la cámara.
- mouth.open se limita a 0,22 en silencio. El estado y el audio de habla tienen
  prioridad inmediata sobre esa apertura añadida.

El VRM debe tener expresiones vinculadas a geometría. **IRIS MToon V12**, usado
por QA22, sí tiene vínculos para parpadeo, visemas y emociones. Los problemas de
las copias antiguas IRIS_VRMA_corregida no describen este modelo actual. Otros
VRM necesitan sus propias expresiones y pueden reaccionar de otra manera.

## Velocidad y transiciones

La reproducción de los fotogramas es **1×**. El mezclador utiliza la duración
real del clip; las duraciones de respaldo de poses procedurales no recortan un
VRMA a tres segundos. Separar claves ralentiza un movimiento; extender solo la
última clave añade espera.

La entrada y el retorno mezclan poses. Desde QA22, detener un clip también reduce
la instantánea gradualmente sin necesitar una animación entrante: no debe
mantenerla congelada y soltarla de golpe al terminar el plazo. La prueba
permanente comprueba el punto medio y el máximo cambio entre fotogramas.

## Recursos y comprobación

Los 18 gestos revisados corrigen los codos de señalar y pensar y conservan los
brazos originales de estirarse, que aún pasan ante la cara aproximadamente un
segundo. Las secuencias de sala necesitan puntos de asiento, manos y cámara
compatibles con los modelos; una malla nueva o un VRM de proporciones distintas
requiere comprobación visual.

Consulta el resumen de QA24 (informe interno no distribuido)
para conocer su alcance. Como referencias históricas, consulta la revisión de gestos (informe interno no distribuido), su
cierre de transición (informe interno no distribuido) y
rutinas de sala y Companion (informe interno no distribuido).
La comprobación de QA22 incluyó seis clips de gesto o reposo por modelo y
las secuencias de sala con IRIS y Cantarella. La cobertura visual depende
del avatar y del candidato.

Las galerías anteriores de cuatro modelos y scripts como verify_pose_faces.mjs
son evidencia histórica o herramientas de diagnóstico de su corte. Para añadir
recursos, comprueba los inventarios y el contrato web actuales. Recargar Face
no sustituye un Companion nativo ya instalado: los cambios de su código o
recursos necesitan el paquete correspondiente.

[← Documentación](../README.md)
