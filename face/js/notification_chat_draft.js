/** Explicit local draft hand-off. Never sends, submits or merges with user content. */
export function prepareNotificationChatDraft(panel, draft, openChat) {
    const input = panel?.chatInput;
    if (!input || typeof openChat !== 'function') throw Error('El chat no está disponible.');
    if (draft?.sent !== false || draft?.contains_observation_data !== false
        || !/^[a-f0-9]{32}$/.test(draft?.notification_id || '')
        || typeof draft?.text !== 'string' || !draft.text.trim()
        || draft.text.length > 600 || draft.text.trimStart().startsWith('/')) {
        throw Error('El borrador recibido no es válido.');
    }
    if (input.value || panel._attachments?.length || (panel._pendingAttachLoads || 0) > 0) {
        throw Error('Ya tienes un mensaje o adjuntos en el chat. Consérvalos y termina ese borrador antes de preparar otro.');
    }
    // Opening may fail; in that case keep the original composer untouched.
    openChat();
    input.value = draft.text;
    panel._autoResizeTextarea?.();
    input.dispatchEvent?.(new Event('input', { bubbles: true }));
    input.focus?.();
    return true;
}
