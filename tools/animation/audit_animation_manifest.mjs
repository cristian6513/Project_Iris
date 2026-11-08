import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const ANIM_DIR = path.join(ROOT, 'face', 'animations');
const MANIFEST = path.join(ANIM_DIR, 'manifest.json');

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function parseGlbJson(filePath) {
  const data = fs.readFileSync(filePath);
  if (data.toString('utf8', 0, 4) !== 'glTF') return null;
  let offset = 12;
  while (offset + 8 <= data.length) {
    const chunkLength = data.readUInt32LE(offset);
    const chunkType = data.readUInt32LE(offset + 4);
    offset += 8;
    if (chunkType === 0x4e4f534a) {
      return JSON.parse(data.toString('utf8', offset, offset + chunkLength).trim());
    }
    offset += chunkLength;
  }
  return null;
}

function inspectClip(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  const stat = fs.statSync(filePath);
  const base = { exists: true, bytes: stat.size, ext };

  if (ext === '.glb' || ext === '.gltf') {
    const json = ext === '.glb'
      ? parseGlbJson(filePath)
      : readJson(filePath);
    const animations = json?.animations || [];
    const nodes = json?.nodes || [];
    const first = animations[0] || null;
    const channels = first?.channels || [];
    const rotationChannels = channels.filter((c) => c.target?.path === 'rotation');
    const humanoidNames = rotationChannels
      .map((c) => nodes[c.target.node]?.name || '')
      .filter(Boolean)
      .filter((name) => /hip|spine|chest|neck|head|shoulder|arm|elbow|wrist|leg|knee|foot|toe/i.test(name));
    return {
      ...base,
      animations: animations.length,
      firstAnimation: first?.name || null,
      channels: channels.length,
      rotationChannels: rotationChannels.length,
      humanoidRotationSample: humanoidNames.slice(0, 32),
    };
  }

  return base;
}

const manifest = readJson(MANIFEST);
const clips = manifest.clips || {};
const poseMap = manifest.pose_map || {};
const report = {
  generatedAt: new Date().toISOString(),
  manifest: MANIFEST,
  clips: {},
  poseMap: {},
  missingClips: [],
};

for (const [clipName, filename] of Object.entries(clips)) {
  const filePath = path.join(ANIM_DIR, filename);
  if (!fs.existsSync(filePath)) {
    report.clips[clipName] = { exists: false, filename };
    report.missingClips.push(clipName);
    continue;
  }
  report.clips[clipName] = { filename, ...inspectClip(filePath) };
}

for (const [poseName, clipName] of Object.entries(poseMap)) {
  report.poseMap[poseName] = {
    clipName,
    clipExists: Boolean(report.clips[clipName]?.exists),
    filename: report.clips[clipName]?.filename || null,
  };
}

const outDir = path.join(ROOT, 'tools', 'blender', 'output');
fs.mkdirSync(outDir, { recursive: true });
const outPath = path.join(outDir, 'animation_manifest_audit.json');
fs.writeFileSync(outPath, JSON.stringify(report, null, 2), 'utf8');

console.log(`Wrote ${outPath}`);
console.log(JSON.stringify({
  missingClips: report.missingClips,
  pcSit: report.clips.pc_sit_down_v2,
  pcIdle: report.clips.pc_seated_idle_v2,
  poseMap: Object.fromEntries(
    Object.entries(report.poseMap).filter(([name]) => /pc|sit|lie|look|wave|think/i.test(name)),
  ),
}, null, 2));
