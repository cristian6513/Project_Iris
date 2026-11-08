---
name: iris-app-contador
description: Consulta el contador local de la app Contador y suma una cantidad cuando la persona lo pide.
license: MIT
---

# Contador

Usa esta skill cuando la persona se refiera al valor de la app Contador de Iris OS.

Consulta el catálogo de acciones mediante la skill general `iris-os`. La app es
`contador`; ofrece `status` con `{}` e `increment` con `{"amount": entero}`.
`amount` admite de 1 a 1000. Usa el adaptador `app_action.py` de `iris-os` para
pedir la acción registrada; no ejecutes el backend como archivo ni modifiques sus datos.

Para consultar, pide `status` y comunica el valor confirmado en `data.count`.
Para sumar, usa la cantidad que pidió la persona; si no está clara, pregúntala.
Core aplica la política y las confirmaciones. Esta skill no concede permisos.

Si la operación se deniega o la app no está disponible, explica ese resultado.
Si tarda demasiado, puede haberse realizado: consulta el estado antes de sugerir
otro intento. No repitas una suma automáticamente. No necesitas abrir la ventana
ni el widget para consultar el valor desde Hermes.
