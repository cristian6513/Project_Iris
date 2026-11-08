# WebRTC Bridge — Plan de Implementación

> **Referencia histórica/de diseño.** Puede describir rutas, versiones o
> funciones del corte original. Para uso actual, consulta [Estado actual](estado-actual.md)
> y las [guías](README.md); no se altera aquí la evidencia histórica.

## Visión General

El teléfono se conecta via LAN y establece una conexión WebRTC P2P con el PC:
- **Teléfono → PC**: stream de cámara + micrófono (via WebRTC DataChannel)
- **PC → Teléfono**: canvas del avatar (video) + audio TTS (audio)

```
┌─────────────┐  WebSocket (señalización)  ┌─────────────┐
│   Teléfono  │◄──────────────────────────►│     PC      │
│  (remote)   │                            │   (host)    │
│             │  WebRTC P2D (medios)      │             │
│  cam/mic → ─┼────────────────────────────┼→ avatar     │
│  avatar ← ──┼────────────────────────────┼─ TTS audio  │
└─────────────┘                            └─────────────┘
```

## Componentes a Crear/Modificar

### 1. `face/js/webrtc_remote.js` (NUEVO)
Cliente WebRTC que corre en el teléfono. Captura cam/mic, recibe avatar video + TTS audio.

### 2. `face/js/webrtc_host.js` (NUEVO)
Cliente WebRTC que corre en el PC (dentro del tab del navegador). Recibe stream del teléfono y lo pasa al agente. Captura canvas y envía avatar video + TTS audio de vuelta.

### 3. `face/js/panel.js` (MODIFICAR)
- Agregar botones de "Modo Remoto" y "Modo Host"
- Invocar getUserMedia para capturar cam/mic en el teléfono
- Conectar webrtc_remote.js con el stream

### 4. `face/js/app.js` (MODIFICAR)
- Interceptar TTS audio para enviarlo por WebRTC
- Crear un MediaStream del canvas para enviar al peer

### 5. `websocket_server.py` (MODIFICAR)
- Agregar tipos de mensaje para WebRTC signaling:
  - `rtc.offer` / `rtc.answer` / `rtc.ice-candidate` / `rtc.init`

### 6. `agent.py` (MODIFICAR)
- Enviar TTS audio por WebRTC en lugar de (o además de) speakers locales

## API Endpoints WebSocket

| Mensaje | Dirección | Payload |
|---------|-----------|---------|
| `rtc.init` | tel→PC | `{role: "remote"\|"host", hasAudio: bool}` |
| `rtc.offer` | bidireccional | `{sdp: string, role: string}` |
| `rtc.answer` | bidireccional | `{sdp: string, role: string}` |
| `rtc.ice-candidate` | bidireccional | `{candidate: string, sdpMid: string, sdpMLineIndex: number}` |
| `rtc.stream_ready` | PC→tel | `{}` |
| `rtc.tts_audio` | PC→tel | `ArrayBuffer` (PCM 16kHz) |

## Flujo de Conexión

```
1. Teléfono conecta a /ws
2. Teléfono envía: {"type":"rtc.init", "payload":{"role":"remote"}}
3. PC recibe y crea RTCPeerConnection
4. PC envía: {"type":"rtc.offer", "payload":{"sdp": "...", "role":"host"}}
5. Teléfono recibe offer, setRemoteDescription, crea answer
6. Teléfono envía: {"type":"rtc.answer", "payload":{"sdp": "..."}}
7. PC recibe answer, setRemoteDescription
8. ICE candidates se intercambian
9. Conexión establecida (P2P)
10. Teléfono agrega cam/mic tracks al peer connection
11. PC recibe tracks → procesa con perception
12. PC envía canvas + TTS por peer connection
13. Teléfono recibe y reproduce
```

## Dependencias

```bash
# En el PC (servidor):
pip install aiortc

# En el navegador (ambos):
# No hay dependencias extra - WebRTC API es nativa del navegador
```

## Plan de Instalación

1. `pip install aiortc` en el PC
2. Copiar `webrtc_remote.js` a `face/js/`
3. Modificar `panel.js`, `app.js`, `websocket_server.py`, `agent.py`
4. Recargar IRIS
5. En el PC: activar "Modo Host" en el panel
6. En el teléfono: escanear QR, permisos de cam/mic
7. IRIS responde en el teléfono con avatar + TTS

## Notas Técnicas

- WebRTC en el navegador usa `RTCPeerConnection` API nativa
- Para TTS: el agente genera audio, lo convertimos a AudioTrack y enviamos por WebRTC
- El canvas se captura con `canvas.captureStream(30)` 
- Soporte: Chrome, Firefox, Edge (no Safari móvil por restricciones)