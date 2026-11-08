# Preparar los tres repositorios de Iris

Project Alice es el proyecto de desarrollo. Las copias limpias de publicación se
preparan en una carpeta hermana, Project Iris, con tres repositorios independientes:

| Carpeta | Contenido |
| --- | --- |
| `Project iris` | Código completo del producto, pruebas, documentación y web. |
| `Aplicaciones Iris` | Guías y skill de creación, plantillas, Contador instalable y Player como referencia integrada. |
| `Transiciones iris` | Contrato v1, lector oficial, herramienta y tres ejemplos libres. |

## Actualizar las copias

Desde la raíz de Alice, con Python 3.11 o posterior:

```text
python scripts/export_repositories.py --destination "../Project Iris"
python scripts/export_repositories.py --destination "../Project Iris" --apply
python scripts/export_repositories.py --destination "../Project Iris" --verify
```

El primer comando solo calcula el plan; `--apply` copia y verifica; `--verify`
comprueba que las copias coincidan con la fuente actual. La selección está en
`tools/repositories/exports.json`. Los kits reutilizan las fuentes del producto;
no mantienen otro validador con reglas distintas.

La exportación considera archivos versionados y archivos nuevos no ignorados
dentro de las carpetas declaradas. No copia el historial Git, runtimes, modelos,
perfiles, credenciales, capturas personales de QA ni informes internos. Un
detector acotado de credenciales detiene la copia si encuentra una coincidencia;
no sustituye la revisión del contenido que se vaya a publicar. Los avisos de
terceros, locks y código se conservan byte por byte. Se adaptan enlaces de la
documentación pública que apuntaban a material interno o a otro repositorio.

Cada repo contiene `SOURCE.json`: commit base, huella de fuentes, huella del
contenido y si había cambios sin commit. Estos datos no atribuyen la aceptación
de un instalador a la copia exportada. Un cambio posterior a QA16 sigue siendo
un cambio posterior a QA16, aunque tenga el mismo commit base.

## Proteger el trabajo

Antes de escribir, la herramienta rechaza cambios manuales y archivos no
administrados en las copias. Integra esas mejoras en Alice y repite el plan.
Lo mismo se aplica a contribuciones recibidas por GitHub: primero se incorporan
al origen y luego se exportan a los repositorios afectados.

Los archivos sustituidos o retirados se respaldan en
`Project Iris/.iris-publication/backups/`, fuera de los tres repositorios. El
registro de propiedad está en `.iris-publication/state.json`; consérvalo para
las siguientes actualizaciones. El `.git` de cada copia se mantiene aparte y
nunca se reemplaza por el de Alice.

La herramienta no crea repositorios remotos, no hace commits y no sube nada.
Al publicarlos, cada carpeta tendrá su propio `.git` y remoto. Los instaladores
y sus fuentes correspondientes se entregan como archivos de release tras su QA;
no se añaden al árbol de código. Los ZIP experimentales y las transiciones para
donadores se mantienen fuera hasta decidir expresamente su distribución.
