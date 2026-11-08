# Copias limpias para publicar Iris

Esta carpeta organiza **tres repositorios independientes**:

- [Project iris](<Project iris/README.md>): código fuente completo del producto, pruebas, documentación y página web.
- [Aplicaciones Iris](<Aplicaciones Iris/README.md>): guías, skill de contribución, plantillas, Contador con Python/skill y Player como referencia integrada.
- [Transiciones iris](<Transiciones iris/README.md>): formato, validador y tres ejemplos libres en CSS y JSON.

Project Alice sigue siendo el lugar de trabajo. Estas carpetas son copias preparadas para revisión y publicación; no incluyen su `.git`, perfiles, credenciales, instaladores, modelos descargados ni evidencia privada de QA. Los informes internos de auditoría permanecen en Alice.

## Pasar cambios de Alice a estas copias

Desde Project Alice, con Python 3.11 o posterior:

```text
python scripts/export_repositories.py --destination "../Project Iris"
python scripts/export_repositories.py --destination "../Project Iris" --apply
python scripts/export_repositories.py --destination "../Project Iris" --verify
```

El primer comando solo muestra el plan. El segundo copia los archivos declarados y el tercero comprueba hashes. La herramienta no hace commits, no crea remotos y no sube nada a GitHub.

Si una copia de publicación tiene una edición manual, se detiene antes de escribir: primero integra esa mejora en Alice. Las contribuciones recibidas en GitHub también deben volver a Alice antes del siguiente traspaso.

Cada repo lleva `SOURCE.json` con la huella del contenido exportado y el commit base de Alice. Un árbol con cambios sin commit se identifica como tal; no se presenta como una reproducción exacta de ese commit ni del último instalador.

Las versiones anteriores de archivos sustituidos o retirados se guardan en `.iris-publication/backups/`, fuera de los tres repos. El registro de propiedad de los archivos está también allí. No borres ese registro mientras uses la sincronización.

Los ZIP de aplicaciones comunitarias y las transiciones para donadores no se incorporan automáticamente. Los instaladores terminados se publican como entregas descargables, después de la QA, no como archivos del código fuente.
