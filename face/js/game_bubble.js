/** Reserve space above the renderer instead of covering the avatar's face. */
export function layoutGameBubble(doc = globalThis.document) {
    const bubble = doc.getElementById('speech-bubble');
    const visible = doc.body.dataset.gameSession === 'active' && bubble && !bubble.hidden;
    doc.body.dataset.gameSpeech = visible ? 'visible' : 'hidden';
    if (!visible) return;
    const root = doc.documentElement;
    const chip = doc.getElementById('game-mode-indicator');
    const top = Math.max(80, (chip?.getBoundingClientRect().bottom || 110) + 10);
    root.style.setProperty('--game-bubble-top', `${Math.round(top)}px`);
    const height = doc.defaultView?.innerHeight || 620;
    root.style.setProperty('--game-text-height', `${Math.min(160, Math.max(54, height - top - 240))}px`);
    root.style.setProperty('--game-avatar-top', `${Math.round(bubble.getBoundingClientRect().bottom + 10)}px`);
}
