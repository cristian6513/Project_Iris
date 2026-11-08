/** Bring the existing conversation forward. Opening chat never sends or starts devices. */
export function openChatSurface({ activityCanvas, panel } = {}) {
    activityCanvas?.close?.();
    panel?.openChat?.();
}
