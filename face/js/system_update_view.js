const numbers = new Intl.NumberFormat('es-CO');
const megabytes = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 });

export function formatUpdateSize(bytes) {
    return `${megabytes.format(bytes / 1_000_000)} MB`;
}

const phases = Object.freeze({
    checking: 'Comprobando la versión',
    downloading: 'Descargando actualización',
    extracting: 'Preparando archivos',
    verifying: 'Verificando archivos',
    finalizing: 'Comprobando el arranque',
    activating: 'Reiniciando Iris',
    updating: 'Instalando actualización',
});

export function systemUpdateProgress(job, snapshot) {
    const phase = job?.phase || 'checking';
    const rollback = job?.operation === 'rollback';
    const target = job?.target || (rollback ? snapshot?.previous : snapshot?.available);
    const title = rollback && phase === 'verifying'
        ? 'Verificando la versión anterior'
        : phases[phase] || 'Preparando actualización';
    const { done, total } = job?.progress || {};
    const measured = Number.isFinite(total) && total > 0 && Number.isFinite(done) && done >= 0;
    const finished = measured ? Math.min(done, total) : null;
    let detail = '';
    if (measured && phase === 'downloading') {
        detail = `${formatUpdateSize(finished)} de ${formatUpdateSize(total)}`;
    } else if (measured && ['extracting', 'verifying'].includes(phase)) {
        detail = `${numbers.format(finished)} de ${numbers.format(total)} archivos`;
    } else if (phase === 'activating') {
        detail = 'Iris se cerrará y volverá a abrirse automáticamente.';
    } else if (target?.size > 0) {
        detail = `Descarga de ${formatUpdateSize(target.size)}`;
    }
    return { title, detail, percent: measured ? Math.floor((finished / total) * 100) : null };
}

/** Options for the shared confirmation dialog. */
export function systemUpdateDialog(snapshot = {}, rollback = false) {
    const current = (snapshot?.items || []).find((item) => item?.id === 'iris-os')?.version || '';
    const target = rollback ? snapshot?.previous : snapshot?.available;
    const version = target?.version || (rollback ? 'anterior' : 'nueva');
    const items = [];
    if (target?.hermes_version) items.push(`Iris ${target.version} incluye Hermes Agent ${target.hermes_version}.`);
    if (!rollback && target?.size > 0) items.push(`Descarga de ${formatUpdateSize(target.size)}.`);
    items.push('Tu perfil, tus conversaciones y tu configuración se conservan.');
    items.push('Iris se reiniciará al terminar de preparar la versión.');
    return {
        title: 'Actualización del sistema',
        icon: 'iris',
        message: `IRIS se cerrará y volverá a abrirse con la versión ${version}.`,
        question: '¿Deseas continuar?',
        versions: current || target?.version ? { from: current, to: target?.version } : null,
        details: {
            label: 'Ver cambios',
            text: rollback ? `Vuelves a la versión ${version} que tenías instalada.` : (target?.notes || 'Esta versión no trae notas.'),
            items,
        },
        confirmLabel: rollback ? `Volver a ${version}` : 'Confirmar actualización',
        confirmIcon: 'update',
    };
}
