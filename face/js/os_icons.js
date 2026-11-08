/* IRIS OS line icons (24×24, stroke only, currentColor), shared by the desktop
 * (hermes_activity.js), the IRIS dialogs (iris_dialog.js) and the setup wizard, which gets
 * them through /shared/os_icons.js. One set, so the same mark looks the same everywhere. */

export const OS_ICON_PATHS = Object.freeze({
    core: '<path d="M12 3l7 4v10l-7 4-7-4V7l7-4z"/><path d="M12 8l3.5 2v4L12 16l-3.5-2v-4L12 8z"/><path d="M12 3v5m0 8v5"/>',
    desktop: '<path d="M4 5h16v10H4z"/><path d="M9 19h6m-3-4v4"/><path d="M7 8h4m-4 3h7"/>',
    activity: '<path d="M4 13h3l2-6 4 10 2-5h5"/><path d="M5 19h14"/><path d="M5 5h14"/>',
    missions: '<path d="M6 5h12v14H6z"/><path d="M9 9h6M9 13h6"/><path d="M9 17h3"/>',
    routines: '<path d="M12 7v5l3 2"/><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4 5v4h4"/>',
    automations: '<path d="M13 3 5.5 13H11l-1 8 7.5-10H12l1-8z"/>',
    doctor: '<path d="M7 4h10l3 3v13H4V4h3z"/><path d="M8 9h8M8 13h4"/><path d="M15 15l1.3 1.3L20 12.5"/>',
    tools: '<path d="M14.5 5.5l4 4-9 9H5.5v-4l9-9z"/><path d="M13 7l4 4"/><path d="M5 6h4M6 5v4"/>',
    camera: '<path d="M4 8h4l1.5-2h5L16 8h4v10H4z"/><circle cx="12" cy="13" r="3"/>',
    voice: '<path d="M12 4a3 3 0 0 0-3 3v4a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3z"/><path d="M5 10a7 7 0 0 0 14 0M12 17v4"/>',
    memory: '<path d="M7 7h10v10H7z"/><path d="M9 3v4m3-4v4m3-4v4M9 17v4m3-4v4m3-4v4M3 9h4m-4 3h4m-4 3h4m10-6h4m-4 3h4m-4 3h4"/>',
    skills: '<path d="M12 3l2.2 5 5.3.5-4 3.5 1.2 5.2L12 14.5 7.3 17.2 8.5 12l-4-3.5 5.3-.5L12 3z"/>',
    gateway: '<path d="M4 12h5m6 0h5"/><circle cx="12" cy="12" r="3"/><path d="M7 7a7 7 0 0 1 10 0M7 17a7 7 0 0 0 10 0"/>',
    settings: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/><path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.05.05a2.1 2.1 0 0 1-2.97 2.97l-.05-.05a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.08 1.65V21a2.1 2.1 0 0 1-4.2 0v-.08a1.8 1.8 0 0 0-1.08-1.65 1.8 1.8 0 0 0-1.98.36l-.05.05a2.1 2.1 0 1 1-2.97-2.97l.05-.05A1.8 1.8 0 0 0 4.6 15a1.8 1.8 0 0 0-1.65-1.08H3a2.1 2.1 0 0 1 0-4.2h.08A1.8 1.8 0 0 0 4.73 8.64a1.8 1.8 0 0 0-.36-1.98l-.05-.05a2.1 2.1 0 0 1 2.97-2.97l.05.05a1.8 1.8 0 0 0 1.98.36A1.8 1.8 0 0 0 10.4 2.4V2a2.1 2.1 0 0 1 4.2 0v.08a1.8 1.8 0 0 0 1.08 1.65 1.8 1.8 0 0 0 1.98-.36l.05-.05a2.1 2.1 0 0 1 2.97 2.97l-.05.05a1.8 1.8 0 0 0-.36 1.98 1.8 1.8 0 0 0 1.65 1.08H22a2.1 2.1 0 0 1 0 4.2h-.08A1.8 1.8 0 0 0 19.4 15z"/>',
    agents: '<path d="M6 8.5h6l2-3h4v10h-6l-2 3H6z"/><path d="M9 12h6"/><path d="M4 5v14M20 5v14"/><circle cx="12" cy="12" r="1.5"/>',
    palette: '<path d="M12 3a9 9 0 0 0 0 18h1.4a2.1 2.1 0 0 0 1.5-3.58 1.2 1.2 0 0 1 .85-2.05H17a4 4 0 0 0 4-4C21 6.75 16.97 3 12 3z"/><circle cx="7.8" cy="10.1" r=".8"/><circle cx="10.2" cy="7.4" r=".8"/><circle cx="13.5" cy="7.3" r=".8"/><circle cx="16.2" cy="9.8" r=".8"/>',
    apps: '<rect x="4" y="4" width="6" height="6" rx="1.4"/><rect x="14" y="4" width="6" height="6" rx="1.4"/><rect x="4" y="14" width="6" height="6" rx="1.4"/><rect x="14" y="14" width="6" height="6" rx="1.4"/>',
    marketplace: '<path d="M5 8h14l-1 12H6L5 8z"/><path d="M9 9V6a3 3 0 0 1 6 0v3"/><path d="M8 13h8M10 16h4"/>',
    player: '<path d="M8 18V6l10-2v12"/><circle cx="6" cy="18" r="2.5"/><circle cx="16" cy="16" r="2.5"/><path d="M8 9l10-2"/>',
    continuity: '<circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="7" r="2.5"/><circle cx="12" cy="17" r="2.5"/><path d="M9.2 8.1l2 6.3m3.6 0l2-6.3M9.5 7h5"/>',
    permissions: '<path d="M12 3l7 3v5c0 4.8-2.8 8.2-7 10-4.2-1.8-7-5.2-7-10V6l7-3z"/><path d="M9 12l2 2 4-5"/>',
    notifications: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
    power: '<path d="M12 3v8"/><path d="M7.5 6.5a7 7 0 1 0 9 0"/>',
    models: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
    calendar: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 17h2"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
    // Actions used by the dialogs.
    update: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>',
    download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    notes: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    warning: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    // The same marks as the Face shell buttons and the Command Center (index.html, panel.js).
    brain: '<path d="M8 5.5h8l2.5 2.5v8L16 18.5H8L5.5 16V8L8 5.5Z"/><path d="M9 9h6v6H9z"/><path d="M12 2.8v2.7M12 18.5v2.7M2.8 12h2.7M18.5 12h2.7"/>',
    vision: '<path d="M3 12s3.4-6 9-6 9 6 9 6-3.4 6-9 6-9-6-9-6Z"/><circle cx="12" cy="12" r="2.7"/>',
    chat: '<path d="M5 6h14v9H9l-4 3V6Z"/><path d="M8 10h8M8 13h5"/>',
    mind: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M8 14.5c-1.5-1.1-2.4-2.7-2.4-4.5A6.4 6.4 0 0 1 12 3.8a6.4 6.4 0 0 1 6.4 6.2c0 1.8-.9 3.4-2.4 4.5-.7.5-1 1.1-1 1.9H9c0-.8-.3-1.4-1-1.9Z"/>',
    companion: '<rect x="4" y="4" width="16" height="13" rx="2.5"/><path d="M8.5 20h7M12 17v3"/><circle cx="12" cy="10.5" r="2.2"/>',
    devices: '<rect x="4" y="5" width="16" height="10" rx="2"/><path d="M9 19h6M12 15v4"/>',
    cube: '<path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z"/><path d="M12 12 4.5 7.8M12 12l7.5-4.2M12 12v8.5"/>',
    database: '<ellipse cx="12" cy="6.5" rx="6.5" ry="3"/><path d="M5.5 6.5v5c0 1.7 2.9 3 6.5 3s6.5-1.3 6.5-3v-5"/><path d="M5.5 11.5v5c0 1.7 2.9 3 6.5 3s6.5-1.3 6.5-3v-5"/>',
    // Setup wizard: credentials, voice, files and states.
    key: '<circle cx="7.5" cy="14.5" r="3.5"/><path d="M10 12l9-9"/><path d="M16 6l2.5 2.5M13.5 8.5l2 2"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><path d="M12 14.5v2.5"/>',
    speaker: '<path d="M4.5 9.5H8l4.5-4v13L8 14.5H4.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    ear: '<path d="M7 9a5 5 0 0 1 10 0c0 3-2.5 4-3 6.5a3.5 3.5 0 0 1-6.6 1.3"/><path d="M10 9.5a2 2 0 0 1 4 0c0 1.2-1 1.8-1.5 2.6"/>',
    keyboard: '<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M7 13.5h.01M17 13.5h.01M9.5 13.5h5"/>',
    search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.2 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.2-3.5-8.5s1.1-6.1 3.5-8.5z"/>',
    cloud: '<path d="M7.5 18.5H17a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7 9.6a4.45 4.45 0 0 0 .5 8.9z"/>',
    home: '<path d="M4 11.5 12 4.5l8 7"/><path d="M6 10v9.5h12V10"/><path d="M10 19.5v-5h4v5"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    upload: '<path d="M12 17V5"/><path d="m7 10 5-5 5 5"/><path d="M5 20h14"/>',
    identity: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="11" r="2.3"/><path d="M5.8 16.5a3.6 3.6 0 0 1 6.4 0M14 10h4M14 13.5h3"/>',
    avatar: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/><path d="M12 2.5v1.5M18 5l-1 1M6 5l1 1"/>',
    sparkle: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 16l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    flask: '<path d="M9.5 3.5h5M10.5 3.5v5.5L5.5 18a1.8 1.8 0 0 0 1.6 2.5h9.8a1.8 1.8 0 0 0 1.6-2.5l-5-9V3.5"/><path d="M7.5 15h9"/>',
    edit: '<path d="M5 19l1-4L15.5 5.5a2.1 2.1 0 0 1 3 3L9 18z"/><path d="M13.5 7.5l3 3"/>',
    dna: '<path d="M7 3.5c0 4 10 5 10 8.5s-10 4.5-10 8.5"/><path d="M17 3.5c0 4-10 5-10 8.5s10 4.5 10 8.5"/><path d="M8.5 7h7M8.5 17h7"/>',
    launch: '<path d="M13.5 4.5c2.8-1 5-1 6 0s1 3.2 0 6l-6 6-6.5-6.5z"/><path d="M7 10l-3 .5 2.5-3.5h4M14 17l-.5 3 3.5-2.5v-4"/><circle cx="15" cy="9" r="1.4"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    skip: '<path d="M6 6l8 6-8 6z"/><path d="M17.5 6v12"/>',
    block: '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
    swap: '<path d="M5 9h14l-3.5-3.5M19 15H5l3.5 3.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    success: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.4 2.4 4.6-5"/>',
    error: '<circle cx="12" cy="12" r="8.5"/><path d="M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6"/>',
    dot: '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
});

// Older names kept by callers: the IRIS mark is «core», the gear «settings», and so on.
const ALIASES = Object.freeze({ iris: 'core', gear: 'settings', shield: 'permissions', bolt: 'automations' });

export function osIconSvg(name = 'core', { className = '', strokeWidth = 1.7 } = {}) {
    const key = OS_ICON_PATHS[name] ? name : ALIASES[name];
    const body = OS_ICON_PATHS[key] || OS_ICON_PATHS.core;
    const cls = className ? ` class="${className}"` : '';
    return `<svg${cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${strokeWidth}"`
        + ` stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}
