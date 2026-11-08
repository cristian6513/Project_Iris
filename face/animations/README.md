# VRM Animations (.vrma)

Drop `.vrma` files into this folder and they will be picked up automatically
by IRIS's idle system and scene-action system.

VRMA is Pixiv's standard animation format — **the same `.vrma` file works
on any VRM avatar**, because it encodes rotations against the standard
humanoid bone names rather than a specific skeleton.

---

## Expected filenames

IRIS looks for these specific names in `manifest.json`. You can edit the
manifest to map your own files. Missing files cause a graceful fallback
to the procedural (code-driven) poses — nothing breaks.

| Key           | Suggested file     | Used for                                 |
|---------------|--------------------|------------------------------------------|
| `idle`        | `idle.vrma`        | Neutral standing / breathing             |
| `idle_casual` | `idle_casual.vrma` | Alternate casual idle                    |
| `wave`        | `wave.vrma`        | Greeting / "saludar"                     |
| `think`       | `think.vrma`       | Thinking pose / "pensar"                 |
| `nod`         | `nod.vrma`         | Affirmative head nod / "asentir"         |
| `shake`       | `shake.vrma`       | Negative head shake / "negar"            |
| `look_around` | `look_around.vrma` | Curious looking around                   |
| `stretch`     | `stretch.vrma`     | Arms-up stretch                          |
| `hair_touch`  | `hair_touch.vrma`  | Touching hair / shy gesture              |
| `shy`         | `shy.vrma`         | Shy look-away                            |
| `sit`         | `sit.vrma`         | Walking + sitting / crouching at desk    |
| `lie`         | `lie.vrma`         | Yawning / tired stretch                  |
| `dance`       | `dance.vrma`       | Short dance (optional fun)               |

`idle_pool` may also contain expressive ambient loops. Keep
`return_idle_pool` limited to calm, neutral clips: IRIS uses that smaller
pool immediately after a gesture so a greeting, dance or pose does not flow
straight into an unrelated second action.

---

## Where to get free VRMA files

1. **[VRoid Hub Animations](https://hub.vroid.com/en/motions)** — Pixiv's
   official, most are free. Filter by "VRMA".

2. **[Booth.pm](https://booth.pm/)** — search `VRMA` or `.vrma`. Filter by
   price `0 JPY` for free.

3. **[three-vrm examples](https://github.com/pixiv/three-vrm/tree/dev/packages/three-vrm/examples)** —
   a few sample VRMA files in the examples folder.

4. **Mixamo + converter** — grab FBX animations from
   [Mixamo](https://www.mixamo.com/) and convert to VRMA with:
   - [VRM Converter for Mixamo](https://github.com/vrm-c/mixamo-converter)
   - [bUnofficial VRM Animation converter](https://github.com/saturday06/VRM_Addon_for_Blender)

---

## Verify an animation worked

Open the browser console after loading the page. You should see:

```
[VRMA] loaded 'wave' (1.25s) from /face/animations/wave.vrma
```

If it fails:

```
[VRMA] 'wave' not found at /face/animations/wave.vrma (will use procedural fallback)
```

— that just means the file isn't there. The avatar will use the built-in
bone-driven pose instead.

---

## Authoring your own

If you want to record original animations:

1. Open **Blender** with the
   [VRM Add-on for Blender](https://vrm-addon-for-blender.info/)
2. Import a VRM, pose keyframes on the humanoid bones
3. Export → VRM Animation (.vrma)

The resulting file plays on any other VRM too.

