# Redes sociales de Project IRIS (IRIS OS)

Kit para abrir y configurar Instagram, TikTok, YouTube y X con la misma identidad. Las
imágenes salen de las plantillas de `brand/` con las fuentes y colores de la web:

```bash
node pagina-iris/scripts/render-images.mjs --redes
```

Esta carpeta no se publica con la web (`build.mjs` solo copia lo que está en `PUBLISH`).

## La ilustración

Los banners de YouTube y X usan de fondo `brand/iris-arte.webp`: IRIS en su escritorio, sin
texto, generada con ChatGPT (1672×941; para YouTube se amplía, y el texto, que se dibuja
encima, queda nítido).

- **Derechos:** según los términos de OpenAI vigentes desde el 1 de enero de 2026, eres el
  propietario del resultado y puedes usarlo comercialmente. No es exclusivo, porque otros
  pueden obtener imágenes parecidas, y puede no estar protegido por derechos de autor. Lo
  que protege la marca es el nombre y el logo, no la ilustración.
- **Cambiarla:** sustituye `brand/iris-arte.webp` por otra sin texto, con la cara de IRIS a
  media altura y a la derecha, y vuelve a ejecutar el comando de arriba. El logo y el eslogan
  se colocan solos dentro de las zonas seguras.

## Antes de nada: el nombre

Decidido el 2026-10-07: **el canal y las cuentas se llaman Project IRIS; el producto es IRIS OS.**
Ya existen varios asistentes de IA llamados «IRIS», incluido uno llamado «Íris» que también usa
Hermes Agent, y «Project Iris» fue además el nombre en clave de unas gafas de realidad aumentada
de Google. Para que ni las personas ni los buscadores confundan el proyecto con otra cosa:

- **Nombre visible:** «Project IRIS» en las cuatro redes. El producto se nombra siempre como
  **IRIS OS**, nunca solo «IRIS», en las bios, los títulos y las descripciones. Por eso las bios
  de abajo empiezan por «IRIS OS:».
- **El mismo usuario en las cuatro redes: `cristian_irisos`** (decidido el 2026-10-07). Une tu
  nombre con el del producto, así se ve que detrás hay una persona. Funciona en las cuatro redes:
  solo letras y guion bajo, y 15 caracteres, el máximo de X. YouTube y X lo muestran con
  mayúsculas (`@Cristian_IrisOS`); Instagram y TikTok, en minúsculas. En YouTube ya está puesto.
- **Menciona «Hermes Agent»:** es lo que mejor lo distingue.

## Cuentas y seguridad

- Un correo propio para las cuatro, mejor del dominio cuando lo compres (por ejemplo
  `hola@iris-os.cloud`) que uno personal.
- Activa la verificación en dos pasos en todas, con una app de autenticación, no SMS.
- Guarda los códigos de recuperación en tu gestor de contraseñas.

## Perfil por red

| | Instagram | TikTok | YouTube | X |
|---|---|---|---|---|
| Nombre visible | Project IRIS | Project IRIS | Project IRIS | Project IRIS |
| Foto de perfil | `perfil-logo.png` | `perfil-logo.png` | `perfil-logo.png` | `perfil-logo.png` |
| Portada | — | — | `youtube-banner.jpg` | `x-cabecera.jpg` |
| Límite de la bio | 150 | 80 | 1000 (descripción) | 160 |
| Tipo de cuenta | Profesional → Creador | Personal (ver nota) | Canal de marca | Normal |

`perfil-iris.png` es una alternativa con el retrato de IRIS. Uses la que uses, que sea **la
misma en las cuatro**: es lo que la gente reconoce al cambiar de red.

**TikTok:** las cuentas de empresa solo pueden usar la biblioteca de música comercial; con
una cuenta personal conservas los sonidos en tendencia, que ayudan mucho al alcance.

### Instagram (bio: 137 de 150)

```
IRIS OS: asistente de IA con voz, memoria y avatar 3D 🤖
Tu propio escritorio de IA, impulsado por Hermes Agent.
Próximamente en Windows 👇
```

Cuenta profesional de tipo Creador (te da estadísticas sin perder funciones). Categoría:
la más cercana a software o producto digital; los nombres exactos cambian según el idioma.

### TikTok (bio: 77 de 80)

```
IRIS OS: IA con voz, memoria y avatar 3D para tu PC 🤖 Próximamente en Windows
```

### X (bio: 155 de 160; X cuenta cada emoji como 2 y aun así cabe)

```
Soy Cristian 🇨🇴 y creo IRIS OS: una IA con voz, memoria y avatar 3D, impulsada por Hermes Agent. Aquí te enseño IA de forma fácil y te cuento cómo avanza 👇
```

- **Nombre:** `Cristian · Project IRIS`. En X conviene que se vea la persona.
- **Ubicación:** Colombia.
- **Sitio web:** el canal de YouTube hasta que la web esté publicada; es a donde apunta el 👇.
- **Fecha de nacimiento:** visible solo para ti.
- **Post fijado:** el teaser, cuando lo publiques.

### YouTube

- Canal **Project IRIS**, con el identificador `@Cristian_IrisOS` ya puesto.
- Banner `youtube-banner.jpg`: el texto está dentro de la zona de 1546×423 que YouTube
  muestra en móvil, escritorio y TV.
- «Contenido creado para niños»: **No**.
- Enlaces del canal: la web y las otras tres redes.
- Descripción:

```
Project IRIS es el canal de IRIS OS, un asistente de IA con voz, memoria, avatar 3D y un escritorio propio, impulsado por Hermes Agent. Hermes hace el trabajo del agente; IRIS le da cara, voz, memoria y un lugar donde convivir con él.

Aquí verás a IRIS en acción y aprenderás inteligencia artificial de forma fácil, sin tecnicismos. Y para quien quiera ir más allá, habrá contenido avanzado para los miembros del canal.

Privacidad primero: el micrófono y la cámara vienen apagados, y nada se enciende sin ti.

IRIS OS llegará pronto a Windows.
```

## El enlace de la bio

El estado de publicación debe comprobarse con el despliegue real; tener el
dominio en `site.config.json` no demuestra que la página esté publicada. Si aún
no está accesible, deja el enlace vacío o apunta al canal de YouTube. Cuando se
verifique `https://iris-os.cloud/`, puede usarse en las cuatro redes. Esta guía
no cambia las bios ni publica la página por sí misma.

## Cuando tengas las cuentas

Pon las URLs de los perfiles en `pagina-iris/site.config.json`:

```json
"social": {
    "instagram": "https://www.instagram.com/cristian_irisos/",
    "tiktok": "https://www.tiktok.com/@cristian_irisos",
    "youtube": "https://www.youtube.com/@Cristian_IrisOS",
    "x": "https://x.com/Cristian_IrisOS"
}
```

El build entonces:

- añade la sección «Síguenos» con los enlaces en la ventana Comunidad;
- los pone como `sameAs` en los datos estructurados, para que los buscadores sepan que esas
  cuentas (Project IRIS) son de IRIS OS;
- añade `twitter:site` para que las tarjetas de X citen la cuenta.

Rechaza una URL que no sea de un perfil de esa red (por ejemplo, con `?igsh=` copiado del
móvil), para no publicar un enlace equivocado. Recuerda también cambiar la tarjeta «Seguir el
desarrollo» de Comunidad, que dice «Próximamente», cuando el canal tenga vídeos.

## Qué publicar

La línea de los canales (decidida el 2026-10-07): **mostrar a IRIS y enseñar IA de forma fácil,
sin tecnicismos**. Lo avanzado irá aparte, para los miembros de pago del canal de YouTube.

**Gratis, en las cuatro redes.** Es lo que atrae público y genera confianza:

- **IRIS en acción:** respondiendo con voz, el avatar reaccionando, los temas, el Companion.
- **IA fácil:** qué es un agente, qué es un modelo local, por qué tu IA «recuerda», cómo
  hablarle mejor. Un concepto por vídeo y explicado con IRIS como ejemplo.
- **Privacidad:** enseñar que la cámara y el micrófono están apagados hasta que tú los activas.
- **Lo básico de IRIS:** instalarla, elegir proveedor, poner tu propio avatar VRM. Esto no
  debe quedar detrás del pago: quien no consigue instalarla no llega a ser miembro.

**Avanzado, para miembros de YouTube.** Es para quien quiere ir más allá:

- crear habilidades y herramientas propias (MCP) para IRIS y Hermes;
- modelos locales a fondo (Ollama, LM Studio): cuál elegir según tu equipo;
- automatizaciones y rutinas;
- cómo está construida IRIS por dentro.

Así un vídeo gratuito puede terminar con «la versión completa, para miembros».

**Cuándo se pueden activar las membresías.** YouTube exige estar en el Programa de Socios con
al menos 500 suscriptores, 3000 horas de visualización en 12 meses (o 3 millones de vistas de
Shorts en 90 días), tener 18 años o más y no tener el canal marcado como «para niños». YouTube se
queda con el 30 % de cada membresía. Comprueba los requisitos exactos en YouTube Studio →
Ganar dinero, porque cambian. Hasta llegar ahí, lo avanzado puede ir en Ko-fi o Patreon y
pasar a YouTube cuando se activen las membresías.

**Mientras tanto, en X: Original Content Rewards.** El 7 de septiembre de 2026 sustituyó al
reparto de ingresos anterior. Paga por el contenido propio. Requisitos (consultados el 2026-10-07
en la ayuda de X):

- suscripción **Premium, Premium+ o Premium Business** (Basic no vale);
- **500 seguidores verificados**, es decir, que paguen Premium;
- **500 000 impresiones** en el feed de inicio de cuentas verificadas en los últimos 90 días, sin
  contar las respuestas;
- 18 años o más y vivir en un país del programa. Colombia está;
- solicitarlo en Creator Studio y que lo aprueben.

Cobro: cada dos semanas por Stripe, con un mínimo de 30 USD. Solo cuentan las impresiones de
usuarios Premium.

Lo que cuenta como original y lo que hay que evitar:

- **Cuenta:** tus explicaciones, tus vídeos y tus opiniones. Subir a X tus propios vídeos de
  TikTok o YouTube está permitido, porque eres el autor.
- **No cuenta:** lo copiado, lo apenas modificado, los recopilatorios, ni lo hecho o publicado
  de forma automatizada. Las piezas de «IA fácil» con tu voz y tu explicación son claramente
  originales; una imagen generada por IA sin nada tuyo encima es más dudosa.
- **Prohibido:** pedir repetidamente likes, seguidores o reposts. Hacer una pregunta para abrir
  conversación no es lo mismo, pero evita los «dale like y sígueme».

**Formatos.** El mismo vídeo vertical sirve para Reels, TikTok y Shorts (1080×1920, con el texto
lejos de los bordes, donde cada app pone sus botones). En X funcionan mejor los hilos con un clip
corto. En YouTube, además de los Shorts, conviene tener un vídeo largo de presentación y uno de
instalación.

## Tamaños de referencia

| Red | Foto de perfil | Portada | Vídeo vertical |
|---|---|---|---|
| Instagram | 320×320 mínimo | — | 1080×1920 |
| TikTok | 200×200 mínimo | — | 1080×1920 |
| YouTube | 800×800 | 2560×1440, zona segura 1546×423 | Shorts 1080×1920 |
| X | 400×400 | 1500×500 | 1080×1920 o 1920×1080 |

Fuentes, consultadas el 2026-10-07: [Buffer](https://buffer.com/resources/social-media-image-sizes/),
[Iconosquare](https://www.iconosquare.com/blog/social-media-image-sizes),
[X header, Neal Schaffer](https://nealschaffer.com/twitter-banner-size/).
