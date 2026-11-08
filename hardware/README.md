# IRIS Hardware — Physical Control Layer

> Integración Arduino/ESP32 para servomotores, sensores y actuadores.

## Estado

Estructura inicial para una implementación futura. El control físico permanece apagado por defecto.

## Componentes previstos

- Arduino: servomotores, indicadores LED y sensores.
- ESP32: puente Bluetooth Low Energy, control Wi-Fi y cámara.

## Protocolo

```json
{
  "comando_hardware": {
    "dispositivo": "arduino_01",
    "accion": "servo",
    "pin": 9,
    "angulo": 45
  }
}
```

La documentación de cableado se añadirá cuando el módulo deje de ser experimental.
