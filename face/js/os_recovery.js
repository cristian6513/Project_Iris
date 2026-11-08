/** Human first, diagnostic details on demand. Never turns a failed read into empty data. */
export function homeRecovery(error) {
    if (error?.status === 409) return { message: 'El inicio cambió en otra ventana. Se intentó recuperar su versión actual; revisa el orden antes de volver a editar.', detail: String(error.message || ''), action: 'Volver a cargar' };
    if (error?.status === 422) return { message: 'No se pudo guardar ese cambio. Tu inicio anterior se conserva.', detail: String(error.message || ''), action: 'Volver a cargar' };
    return { message: 'No pude leer o guardar tu inicio. Comprueba que Iris siga abierta y vuelve a cargarlo; tus preferencias no se restablecieron.', detail: String(error?.message || 'Lectura no disponible'), action: 'Volver a cargar' };
}
