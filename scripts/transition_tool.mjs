#!/usr/bin/env node
/** Contributor helper; uses exactly the same reader as Theme Studio. */
import { open, writeFile } from 'node:fs/promises';
import { parseTransitionFile, exportTransition, TRANSITION_LIMITS } from '../face/js/os_transition_format.js';

try {
    const [command, input, output, ...args] = process.argv.slice(2);
    if (!['check', 'convert'].includes(command) || !input || command === 'convert' && !output) {
        throw new Error('Uso: node scripts/transition_tool.mjs check ARCHIVO\n     node scripts/transition_tool.mjs convert ENTRADA SALIDA.json [--effect NOMBRE] [--name NOMBRE] [--author AUTOR] [--license LICENCIA]');
    }
    const options = {};
    if (command === 'check' && (output || args.length)) throw new Error('check solo recibe un archivo.');
    for (let i = 0; i < args.length; i += 2) {
        const flag = args[i]?.replace(/^--/, '');
        if (!['effect', 'name', 'author', 'license'].includes(flag) || args[i + 1] === undefined || Object.hasOwn(options, flag)) throw new Error('Opción no válida o repetida.');
        options[flag] = args[i + 1];
    }
    const handle = await open(input, 'r');
    let bytes;
    try {
        const buffer = Buffer.alloc(TRANSITION_LIMITS.bytes + 1);
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
        if (bytesRead > TRANSITION_LIMITS.bytes) throw new Error('El archivo supera 64 KiB.');
        bytes = buffer.subarray(0, bytesRead);
    } finally { await handle.close(); }
    const parsed = parseTransitionFile(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (command === 'check') {
        for (const item of parsed.transitions) console.log(`${item.name}: ${item.keyframes.length} fotogramas, ${item.duration} ms · compatible`);
    } else {
        const item = options.effect ? parsed.transitions.find(item => item.name === options.effect) : parsed.transitions.length === 1 ? parsed.transitions[0] : null;
        if (!item) throw new Error('Elige una animación existente con --effect NOMBRE.');
        const { effect, ...metadata } = options;
        await writeFile(output, exportTransition({ ...item, ...metadata }), { flag: 'wx' });
        console.log(`Paquete creado: ${output}`);
    }
} catch (error) {
    console.error(error.message); process.exitCode = 1;
}
