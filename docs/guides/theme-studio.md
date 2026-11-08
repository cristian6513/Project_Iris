# Importar y compartir ajustes de Theme Studio

Revisión documental: 2026-10-26. Incluido en QA16; la QA instalada conserva el
alcance y los pendientes del informe del candidato (informe interno no distribuido).

Theme Studio adapta sus columnas al ancho de su propia ventana. Al restaurar o
acoplar una ventana estrecha, las tarjetas se apilan y los valores de los
deslizadores permanecen dentro de ellas. Maximizar recupera las columnas cuando
hay espacio; no hace falta maximizar para usar sus controles.

## Qué comparte el JSON

**Exportar tema** descarga un archivo JSON v1 con colores, opciones visuales y
la biblioteca de [transiciones](theme-transitions.md). Incluye la elección de fuentes incluidas (Outfit, Syne o Sistema). No incluye
los bytes del fondo, la foto o las fuentes propias, ni sus referencias locales.
Iris lo explica antes de descargar y puedes cancelar.

No incluye cuentas, claves, permisos, organización del Inicio ni widgets. Un paquete completo con imágenes o fuentes sería otra entrega.

## Importar un tema

1. En **Theme Studio**, pulsa **Importar tema** y selecciona un `.json`.
2. Iris valida todo antes de cambiar la pantalla. Admite el formato v1 exportado
   por Iris y el objeto antiguo de preferencias sin envoltorio. El límite es
   600 000 bytes. Un archivo vacío, incompatible o inválido conserva el tema actual.
3. Si el archivo antiguo contiene referencias a imágenes o fuentes propias, aparece
   **Importar sin archivos propios**. Cancelar conserva todo. Aceptar aplica solo sus ajustes y usa
   los fondos/perfiles incluidos elegidos por el archivo; nunca carga esas URLs.
4. Para personalizar las imágenes, usa **Buscar más fondos** o **Cambiar foto**
   y selecciona un archivo de tu equipo. No hay un campo para introducir servidores externos.

Las referencias a imágenes se omiten aunque parezcan rutas locales de Iris:
la imagen del autor no pertenece al perfil de quien importa. Así se evita que
un tema consulte servidores ajenos o muestre por accidente otro archivo local.

Los números pueden venir como texto, como en las exportaciones históricas, o
como números JSON. Se comprueban sus rangos y se conserva el cero. Los booleanos
deben ser `true` o `false`, no las cadenas `"true"` o `"false"`. Los IDs internos
de fondo/perfil no son los nombres visibles de las ediciones: no los renombres.

Un tema antiguo sin biblioteca conserva tus transiciones añadidas. Un tema con
biblioteca explícita importa esa biblioteca validada. Para compartir solo una
animación, usa **Exportar transición**.

## Si algo falla

Un error de lectura, validación o almacenamiento muestra el motivo y conserva
el tema anterior. Si cambias el tema, abres otra importación u ocultas Theme
Studio mientras lee el archivo, la respuesta anterior deja de aplicarse.
Las preferencias antiguas restauradas tampoco pueden introducir imágenes de
servidores externos: solo se mantienen los recursos de imagen administrados
por la API de subida del perfil actual.

El almacenamiento actual de Face conserva la elección del tema. Las fuentes
propias se guardan como recursos en Core, por perfil. Las imágenes y los temas
completos portables siguen en el [plan](../iris_theme_studio_extension_plan_2026-10-18.md).


## Elegir y añadir tipografía

En **Tipografía**, elige la fuente del texto y la de los títulos por separado.
Outfit y Syne se incluyen con Iris; **Sistema** usa la fuente de interfaz del
navegador. Se aplican a las apps, widgets, menús y diálogos de Iris OS. El
Command Center, el chat, el asistente de instalación y el Companion mantienen
sus elecciones actuales.

**Añadir fuente WOFF2** abre un archivo de tu equipo y lo decodifica para una
vista previa. Todavía no lo guarda ni lo aplica. **Guardar para texto** o
**Guardar para títulos** guarda los bytes en tu perfil y después la elección.
Puedes cancelar la vista previa sin cambiar el tema. Si solo falla el guardado
de la elección, el archivo queda en la biblioteca para elegirlo otra vez.

Se admiten hasta ocho archivos WOFF2 de 2 MB cada uno. Esta primera versión carga
el estilo que contiene cada archivo; no ofrece ejes de fuentes variables ni
promete pesos o caracteres que el autor no incluyó. TTF/OTF y paquetes con varias
variantes aún no se admiten. No instala tipografías en Windows ni busca archivos
en el equipo por su cuenta.

Una fuente ausente, dañada o que tarda demasiado deja una fuente incluida como
respaldo y un aviso con **Reintentar carga**. Cambiar de elección gana frente a
una carga anterior. **Fuentes propias → Quitar** pide confirmación; si estaba
seleccionada, primero guarda el respaldo y después quita solo ese recurso.
No restablece colores, Inicio, cuentas ni otros recursos.

La importación personal no concede derechos para redistribuir una fuente. El JSON
ligero no copia los archivos; un futuro paquete completo tendrá que conservar
su atribución y licencia. Implementación y pruebas (informe interno no distribuido).
