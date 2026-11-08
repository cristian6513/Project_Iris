# Ideas diferidas: cuenta Iris, créditos y Android

Registro de conversación: **2026-10-26**. Estas ideas no son funciones
implementadas, una oferta comercial ni tareas que deban iniciarse automáticamente.
El [estado vigente](estado-actual.md) sigue distinguiendo fuente y candidato.

## Cuenta Iris y servicio de suscripción

El usuario plantea, a futuro, iniciar sesión en la web, contratar un plan y
elegir IA/voz; al entrar en Iris, recuperar esas preferencias para simplificar
la configuración inicial. Después aclaró que primero hay que estudiar
proveedores, créditos incluidos y consumo según modelo, un trabajo separado.

Quedan por decidir proveedores, costes reales de entrada/salida y voz, cuotas,
equivalencia de créditos, precios, pagos y arquitectura del servicio. No se ha
elegido una plataforma de facturación ni implementado un login de cuenta Iris.
Tampoco se ha decidido hacer obligatoria una cuenta o retirar el uso actual con
proveedores propios/locales. Conservar esa opción fue una recomendación, no una
decisión de producto cerrada.

La propuesta técnica contempla custodiar las claves del servicio en servidor y
usar sesiones por usuario; todavía requiere diseño y revisión. No supone
reutilizar suscripciones personales OAuth de otros proveedores. El login Google
de Conexiones y los accesos IA actuales conservan sus flujos y consentimientos;
una futura cuenta Iris no concedería por sí sola Calendario, Correo o micrófono.

## Android

El usuario decidió esperar a la beta Android del motor antes de iniciar una
adaptación de Iris. La distribución integral comprobada sigue siendo Windows
x64. No hay una app Android de Iris acreditada por este documento, ni se ha
elegido entre cliente conectado al PC y ejecución independiente en el teléfono.

Cuando se retome, verificar el estado oficial del motor y revisar runtime,
almacenamiento, permisos, ejecución en segundo plano y compatibilidad de apps
SDK 2. La disponibilidad de una interfaz web no demuestra que Python, audio o
los servicios nativos funcionen igual en Android.
