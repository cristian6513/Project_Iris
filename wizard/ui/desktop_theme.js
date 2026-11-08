import { OS_THEME_DEFAULT, OS_THEME_PRESETS } from '/desktop/theme-contract.js';

const names = { iris: 'Cyber', rose: 'Sakura', verdant: 'Hacker', sunrise: 'Classic' };
const backgrounds = { city: 'iris-os-bg-v1.png', sakura: 'theme-sakura.png', nebula: 'theme-sakura.png',
    hacker: 'theme-hacker.png', grid: 'theme-hacker.png', classic: 'theme-classic.png', sunrise: 'theme-classic.png' };
const numberFields = { cardOpacity: [30, 95], blurIntensity: [0, 40], backgroundDim: [0, 95],
    accentGlow: [0, 100], borderGlow: [0, 100], neonIntensity: [0, 100] };
const flags = ['reducedMotion', 'animatedGlow', 'transparentCards', 'compactMode'];
let currentPreset = 'iris';
let currentTheme = { ...OS_THEME_DEFAULT };
let lastRemote = '';
let pendingTheme;
let saveTimer;
let saving = false;
let wizardPreview = false;

export function setThemeView(view) {
    wizardPreview = view === 'setup';
    if (!wizardPreview) lastRemote = '';
}

export function sanitizeTheme(input, preset = 'iris') {
    const defaults = { ...OS_THEME_DEFAULT, ...(OS_THEME_PRESETS[preset] || {}) };
    const theme = {};
    for (const key of ['accent', 'accent2', 'readyColor']) {
        theme[key] = /^#[0-9a-f]{6}$/i.test(input?.[key]) ? input[key] : defaults[key];
    }
    theme.background = Object.hasOwn(backgrounds, input?.background) ? input.background : defaults.background;
    theme.profile = ['iris', 'alice', 'sakura', 'halo', 'glass'].includes(input?.profile) ? input.profile : defaults.profile;
    for (const [key, [min, max]] of Object.entries(numberFields)) {
        const value = Number(input?.[key] ?? defaults[key]);
        theme[key] = Number.isFinite(value) ? Math.round(Math.min(max, Math.max(min, value))) : Number(defaults[key]);
    }
    for (const key of flags) theme[key] = typeof input?.[key] === 'boolean' ? input[key] : defaults[key];
    return theme;
}

function renderTheme(theme, preset) {
    const root = document.documentElement;
    root.style.setProperty('--iris-os-accent', theme.accent);
    root.style.setProperty('--iris-os-accent-2', theme.accent2);
    root.style.setProperty('--iris-ready-color', theme.readyColor);
    root.style.setProperty('--iris-os-bg-image', `url("/theme-assets/${backgrounds[theme.background]}")`);
    root.style.setProperty('--iris-background-dim', theme.backgroundDim / 100);
    root.style.setProperty('--iris-card-opacity', theme.cardOpacity / 100);
    root.style.setProperty('--iris-blur-intensity', `${theme.blurIntensity}px`);
    root.style.setProperty('--iris-accent-glow', theme.accentGlow / 100);
    root.style.setProperty('--iris-neon-intensity', theme.neonIntensity / 100);
    root.style.setProperty('--iris-border-glow', theme.borderGlow / 100);
    for (const key of flags) root.dataset[key] = String(theme[key]);
    root.style.setProperty('--iris-profile-bg-image', 'url("/theme-assets/iris-profile-icon-v1.png")');
    document.getElementById('edition').textContent = `${names[preset] || 'Cyber'} Edition`;
    currentTheme = theme;
}

export function applyThemeSnapshot(snapshot) {
    if (!snapshot || saving || pendingTheme || wizardPreview) return;
    const serialized = JSON.stringify(snapshot);
    if (serialized === lastRemote) return;
    lastRemote = serialized;
    currentPreset = Object.hasOwn(names, snapshot.preset) ? snapshot.preset : 'iris';
    renderTheme(sanitizeTheme(snapshot.theme || OS_THEME_PRESETS[currentPreset], currentPreset), currentPreset);
}

async function flushTheme() {
    if (saving || !pendingTheme) return;
    const theme = pendingTheme;
    pendingTheme = undefined;
    saving = true;
    try {
        await fetch('/desktop/theme', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(theme) });
    } catch { /* Live colors remain applied; the next change can retry persistence. */ }
    finally { saving = false; if (pendingTheme) void flushTheme(); }
}

// Called only after the parent verified the sending frame's origin and identity.
export function receiveFaceTheme(input) {
    const theme = sanitizeTheme(input, currentPreset);
    renderTheme(theme, currentPreset);
    pendingTheme = theme;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushTheme, 200);
}

export function receiveWizardPreset(preset) {
    if (!Object.hasOwn(names, preset)) return;
    currentPreset = preset;
    lastRemote = '';
    renderTheme(sanitizeTheme(OS_THEME_PRESETS[preset], preset), preset);
}

renderTheme(sanitizeTheme(currentTheme), currentPreset);
