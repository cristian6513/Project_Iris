# Iris Store: instalar apps desde un archivo

[← Índice](../README.md) · [Estado actual](../estado-actual.md) · [Contribuir aplicaciones](contributing-apps.md)

Revisión: 2026-10-28. **Store y SDK 2 base incluidos desde QA15, también en QA16 y QA17.** La presencia en
el candidato no sustituye su QA instalada ni valida automáticamente una app externa.
Informe técnico (informe interno no distribuido).

Los avisos de apps y «Avisar a Iris» descritos abajo pertenecen a la ampliación del
28 de octubre, incluida en el candidato QA18. Sus pruebas del runtime aislado
pasan; falta la QA instalada con WebView2 y modelo real. Los EXE QA15–QA17
conservan su contenido anterior.

Iris Store instala apps de IRIS OS que te pasan como **archivo .zip** o como **carpeta**.
Es la primera versión, la sencilla: no hay catálogo en internet ni cuentas. Eliges el
archivo, Iris lo revisa, te muestra lo que trae y lo instala solo si dices que sí.

## Crear una app con Iris

La skill incluida «Crear apps de Iris» permite pedir una ventana o un widget
personal desde el chat local. Iris también puede preparar un borrador si
detecta que le falta una herramienta reutilizable para tu petición.

Iris prepara la app en el perfil y te entrega la ruta del ZIP validado.
Elige ese archivo en Iris Store para revisar lo que trae y confirmar la
instalación. Si después le pides un cambio, puede actualizar el borrador de
la misma app y darte un ZIP con la versión nueva.

El constructor puede incluir un backend Python para guardar datos compartidos
entre la ventana, los widgets e Iris, acciones para Hermes y una skill propia.
También puede usar los avisos y las demás funciones del
[SDK 2](community-apps-python.md). La comprobación del ZIP verifica su formato.
La Store muestra las observaciones sobre el código antes de instalar.

## Instalar una app

1. Abre **Aplicaciones → Iris Store**.
2. Pulsa **Elegir archivo .zip** o **Elegir carpeta**, o arrastra el .zip o la carpeta
   al recuadro.
3. Iris comprueba que la app tenga el formato correcto, con las mismas reglas que usan
   las apps incluidas. Si algo no cuadra, te dice qué y no instala nada.
4. Revisa la tarjeta:
   - nombre, versión, descripción, tamaño y widgets que incluye;
   - **Lo que hace su código**: una revisión automática que busca si se conecta a
     internet, si llama a funciones internas de Iris, si ejecuta código creado en el
     momento, si inserta HTML sin filtrar, si guarda datos o si tiene código difícil de
     revisar. Es orientativa: no garantiza que la app sea segura;
   - los archivos que no se instalan (documentación y pruebas).
5. Pulsa **Instalar**. Iris te lo pregunta una vez más con su diálogo.
6. Listo, sin recargar nada: la app aparece al momento en **Aplicaciones**, la Store
   te ofrece **Abrir** y sus widgets ya están en **Personalizar → Widgets**.

Si el escritorio no pudiera actualizarse (por ejemplo, porque Core no respondió), la
app queda instalada y la Store te ofrece **Reintentar**; las demás apps siguen como
estaban. **Recargar el escritorio** queda como último recurso.

### Antes de instalar: el aviso importante

En esta versión, **una app instalada tiene el mismo acceso que Iris**: puede leer tu
correo y tus conversaciones o pedirle cosas a Hermes. Instala solo apps de personas en
las que confíes. Si la descargaste de internet, revisa antes su código o pídele a una IA
que lo revise. Ejecutar Python en un proceso propio no es un sandbox de Windows.

## Actualizar o reinstalar

Instalar otra vez una app con el mismo identificador la **reemplaza entera**: la tarjeta
dice qué versión reemplaza o que se reinstala la misma. Si otra ventana la cambió
mientras la revisabas, Iris te pide elegir el archivo de nuevo; nunca mezcla versiones.

Si la app está abierta (su ventana o alguno de sus widgets), Iris te avisa antes: **se
reiniciará con la nueva versión** y perderás lo que no hayas guardado en ella. Solo se
reinicia esa app: su ventana sigue abierta con la versión nueva y sus widgets se vuelven
a cargar. El resto del escritorio no se toca.

## Desinstalar

En **Iris Store → Instaladas desde archivo → Desinstalar**, o desde **Administrar
aplicaciones**, donde la app aparece como «Instalada desde archivo». Iris pregunta antes.
Al momento se cierran sus ventanas y se quitan la app, sus estilos, sus widgets y su
acceso del inicio. Las apps incluidas con Iris no se desinstalan.

Con la ampliación de avisos, desinstalar también cancela los recordatorios futuros
de esa app. Los avisos que ya llegaron siguen legibles en la Bandeja.

## Controlar los avisos y los mensajes de una app

En **Administrar aplicaciones** puedes elegir por app «Mostrar y guardar en la
Bandeja», «Solo en la Bandeja» o «Desactivados». Un recordatorio puede llegar aunque
la ventana de la app esté cerrada, porque lo conserva Core. La tarjeta depende de
que Iris esté disponible y de tus preferencias; no abre por sí sola una conversación.

Si una app tiene el botón **Avisar a Iris**, necesitas pulsarlo tú. Iris muestra
la app, el mensaje y, al abrir **Ver los datos de la app**, todos los datos que se
enviarán. **Cancelar** no envía nada. «Enviar siempre desde» esa app omite la
vista previa en los siguientes envíos, pero cada uno sigue necesitando una acción
tuya; puedes revocarlo en Administrar aplicaciones. Es información de la app para
la conversación actual, no un permiso nuevo para actuar en tu equipo.

## Qué puede traer una app y qué no

Una app de la Store incluye `app.json` y la carpeta `ui/` (módulos `.js`, una hoja
`.css`, más la de cada widget, imágenes `.svg`, `.png` o `.webp` y datos `.json`).
Con SDK 2 puede añadir `backend/` y `skill/` bajo el contrato `extension`.
Consulta la [guía de contribución](contributing-apps.md) y el
[Contador instalable](../../examples/apps/contador/README.md).

**Disponibilidad:** QA14 instala interfaz y widgets (SDK 1). QA15 y QA16 incluyen
[Python, acciones y skills con SDK 2](community-apps-python.md). La tabla describe
ese contrato; el estado de QA del candidato exacto está en la [matriz](../estado-actual.md).

| Admite | No admite en esta versión |
| --- | --- |
| Interfaz en ventana y hasta cuatro widgets | Servicios importados dentro de Core, servidores propios o hooks de instalación |
| SDK 2: backend Python en proceso propio, acciones declaradas y skill opcional | Dependencias descargadas, ejecutables, entornos Python o scripts dentro de la skill |
| Hasta 2 MB de interfaz, 512 KB por archivo, 400 archivos en el paquete | Permisos (`requestedCapabilities`) ni servicios (`services`) |
| Un .zip normal (Deflate) o una carpeta | .zip cifrados, enlaces, rutas con «..» o nombres repetidos |
| Ids propios | El id de una app incluida con Iris (`calendar`, `store`…) |

`README`, `LICENSE` y la carpeta `tests/` pueden venir en el paquete: no se instalan.
Las carpetas ocultas (`.git`) y los restos de macOS (`__MACOSX`) se ignoran.

La inspección del SDK 2 muestra los componentes y operaciones antes de confirmar.
Desinstalar conserva los datos por defecto; para eliminarlos marca **Eliminar
también sus datos al desinstalar**. Una skill ajena o editada se conserva con aviso.
Consulta el [contrato y ejemplo completo](community-apps-python.md).

## Dónde se guarda

Cada app instalada vive en tu perfil de Iris, dentro de un almacén propio
(`apps/app-packages/packages.sqlite3`), separada de los archivos de Iris. El backend
se materializa por instalación en una caché privada y comprobada antes de ejecutarse;
sus datos están en una carpeta separada y la skill tiene propietario registrado en
el perfil de Hermes. Desinstalar retira solo esos componentes administrados. Otro
perfil tiene sus propias apps. El almacén nuevo usa formato 2: QA14 lo rechaza sin
modificarlo; usa perfiles desechables para esta ampliación mientras validas la QA.

## Para quien crea apps

- Prueba tu app con `node --test apps/<id>/tests/*.test.mjs` y empaquétala como .zip de
  su carpeta, por ejemplo `mi-app/app.json` + `mi-app/ui/…`.
- La interfaz usa las reglas del catálogo (`scripts/build_app_catalog.mjs`). Los
  servicios integrados y los permisos siguen requiriendo revisión; las extensiones
  externas del SDK 2 usan su contrato propio y se validan con la herramienta de paquetes.
- Para apps externas con SDK 2, usa `scripts/app_package_tool.py check CARPETA`
  y `pack CARPETA SALIDA.zip`; no las incluyas en el catálogo predeterminado de `apps/`.
- Los módulos se sirven desde `/api/app-packages/files/<instalación>/…`; los imports
  relativos entre tus archivos (`./timer.js`) funcionan igual que en `face/apps/<id>/`.
