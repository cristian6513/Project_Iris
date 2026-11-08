/* The emoji the setup wizard writes inside <wz-icon> and the IRIS OS icon each one stands
 * for (wizard_icons.js draws them). Only <wz-icon> is read: the wizard never turns other text
 * into icons, so what the person writes or a provider sends stays exactly as it is. */

// emoji → [icon, tone]; the tone colours states (ok, warn, err, busy, idle).
const TABLE = {
    '✅': ['success', 'ok'], '✓': ['check', 'ok'], '🟢': ['dot', 'ok'],
    '❌': ['error', 'err'], '✖': ['error', 'err'], '✕': ['close', ''], '⛔': ['block', 'err'],
    '🚫': ['block', 'err'], '🛑': ['stop', 'err'], '💥': ['warning', 'err'],
    '⚠': ['warning', 'warn'], '🟡': ['dot', 'warn'], '🧹': ['warning', 'warn'],
    '⏳': ['clock', 'busy'], '⚪': ['dot', 'idle'], 'ℹ': ['info', ''],
    '🧠': ['brain', ''], '⚡': ['automations', ''], '🔧': ['tools', ''], '🛠': ['tools', ''],
    '🧩': ['tools', ''], '🧰': ['tools', ''], '⚙': ['settings', ''], '🔑': ['key', ''],
    '🔐': ['lock', ''], '🔒': ['lock', ''], '👁': ['vision', ''], '📎': ['link', ''], '🔗': ['link', ''],
    '🌐': ['globe', ''], '🧪': ['flask', ''], '✏': ['edit', ''], '📦': ['cube', ''],
    '📥': ['download', ''], '⬇': ['download', ''], '⬆': ['upload', ''], '🚀': ['launch', ''],
    '🔄': ['update', ''], '🔁': ['update', ''], '♻': ['update', ''], '🩺': ['doctor', ''],
    '🔍': ['search', ''], '🔎': ['search', ''], '🔊': ['speaker', ''], '🎤': ['voice', ''],
    '🎙': ['voice', ''], '🧬': ['dna', ''], '📁': ['folder', ''], '📂': ['folder', ''],
    '☁': ['cloud', ''], '🏠': ['home', ''], '📝': ['notes', ''], '📋': ['notes', ''],
    '🪪': ['identity', ''], '🎭': ['avatar', ''], '⭐': ['skills', ''], '🖥': ['devices', ''],
    '👋': ['ear', ''], '👂': ['ear', ''], '✨': ['sparkle', ''], '✦': ['sparkle', ''],
    '🆕': ['sparkle', ''], '⌨': ['keyboard', ''], '⚔': ['permissions', ''], '💡': ['mind', ''],
    '⏹': ['stop', ''], '⏭': ['skip', ''], '🤖': ['core', ''], '🗑': ['trash', ''],
    '↔': ['swap', ''], '🎨': ['palette', ''], '📱': ['chat', ''], '🔌': ['gateway', ''],
    '➕': ['plus', ''], '💰': ['key', ''],
};

export const WIZARD_ICONS = new Map(Object.entries(TABLE));

/** The icon for a token («⚠️» or «⚠»), or null when it is not one of the wizard's. */
export function iconForToken(token) {
    return WIZARD_ICONS.get(String(token ?? '').replace(/\uFE0F/g, '').trim()) || null;
}
