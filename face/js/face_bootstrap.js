import { publishDesktopFaceState } from './desktop_host.js?v=3';

// Observe failures while fetching, parsing or evaluating the room's module graph,
// even when the code that normally starts the app never gets to run.
void import('./app.js?v=432').then(({ startIrisFace }) => startIrisFace()).catch(error => {
    console.error('IRIS init failed:', error);
    publishDesktopFaceState('error');
    const loading = document.getElementById('loading-screen');
    if (loading) {
        const message = document.createElement('p');
        message.style.color = '#ef4444';
        message.textContent = `Error: ${error?.message || error || 'fallo de inicio desconocido'}`;
        loading.replaceChildren(message);
    }
});
