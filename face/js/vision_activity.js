/** Boolean activity hints only: no keystrokes, titles, audio or input contents. */
export function buildVisionActivity({ documentLike, status, quiet = null } = {}) {
    const visibility = documentLike?.visibilityState;
    const visible = visibility === 'visible' ? true : visibility === 'hidden' ? false : null;
    const element = documentLike?.activeElement;
    // Conservative: a focused editable field is enough to defer. This does not
    // detect typing in another application or read the field's contents.
    const typing = !element ? null : Boolean(
        element.isContentEditable
        || element.tagName === 'TEXTAREA'
        || (element.tagName === 'INPUT' && ![
            'button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'color', 'file', 'hidden',
        ].includes(element.type)),
    );
    const known = ['idle', 'ready', 'listening', 'thinking', 'speaking', 'tool'].includes(status);
    return {
        visible,
        typing,
        speaking: known ? status === 'speaking' : null,
        listening: known ? status === 'listening' : null,
        quiet: typeof quiet === 'boolean' ? quiet : null,
        // Future explicit activity modes. Missing evidence must not imply idle.
        focus: null,
        gaming: null,
        presenting: null,
        meeting: null,
    };
}
