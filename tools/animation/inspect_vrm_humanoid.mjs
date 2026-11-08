import fs from 'node:fs';
import path from 'node:path';

const CORE_BONES = [
  'hips',
  'spine',
  'chest',
  'upperChest',
  'neck',
  'head',
  'leftShoulder',
  'leftUpperArm',
  'leftLowerArm',
  'leftHand',
  'rightShoulder',
  'rightUpperArm',
  'rightLowerArm',
  'rightHand',
  'leftUpperLeg',
  'leftLowerLeg',
  'leftFoot',
  'leftToes',
  'rightUpperLeg',
  'rightLowerLeg',
  'rightFoot',
  'rightToes',
];

function parseGlbJson(filePath) {
  const data = fs.readFileSync(filePath);
  const magic = data.toString('utf8', 0, 4);
  if (magic !== 'glTF') {
    throw new Error(`Not a binary glTF/VRM file: ${filePath}`);
  }

  let offset = 12;
  while (offset + 8 <= data.length) {
    const chunkLength = data.readUInt32LE(offset);
    const chunkType = data.readUInt32LE(offset + 4);
    offset += 8;

    // JSON chunk type, little-endian ASCII "JSON".
    if (chunkType === 0x4e4f534a) {
      const text = data.toString('utf8', offset, offset + chunkLength).trim();
      return JSON.parse(text);
    }
    offset += chunkLength;
  }

  throw new Error(`No JSON chunk found in ${filePath}`);
}

function buildParents(nodes) {
  const parents = new Array(nodes.length).fill(null);
  nodes.forEach((node, parentIndex) => {
    for (const childIndex of node.children || []) {
      parents[childIndex] = parentIndex;
    }
  });
  return parents;
}

function summarizeNode(nodes, parents, nodeIndex) {
  if (typeof nodeIndex !== 'number' || !nodes[nodeIndex]) {
    return null;
  }
  const node = nodes[nodeIndex];
  const parentIndex = parents[nodeIndex];
  return {
    index: nodeIndex,
    name: node.name || '',
    parent: typeof parentIndex === 'number'
      ? { index: parentIndex, name: nodes[parentIndex]?.name || '' }
      : null,
    children: (node.children || []).slice(0, 12).map((childIndex) => ({
      index: childIndex,
      name: nodes[childIndex]?.name || '',
    })),
    translation: node.translation || null,
    rotation: node.rotation || null,
    scale: node.scale || null,
  };
}

function buildHumanoidMap(json) {
  const nodes = json.nodes || [];
  const parents = buildParents(nodes);
  const map = {};
  let version = 'unknown';

  const vrm0HumanBones = json.extensions?.VRM?.humanoid?.humanBones;
  if (Array.isArray(vrm0HumanBones)) {
    version = 'VRM0';
    for (const humanBone of vrm0HumanBones) {
      map[humanBone.bone] = summarizeNode(nodes, parents, humanBone.node);
    }
  }

  const vrm1HumanBones = json.extensions?.VRMC_vrm?.humanoid?.humanBones;
  if (vrm1HumanBones && typeof vrm1HumanBones === 'object') {
    version = 'VRM1';
    for (const [boneName, spec] of Object.entries(vrm1HumanBones)) {
      map[boneName] = summarizeNode(nodes, parents, spec.node);
    }
  }

  return { version, map };
}

function skinJointSummary(json) {
  const nodes = json.nodes || [];
  return (json.skins || []).map((skin, index) => ({
    index,
    name: skin.name || '',
    jointCount: skin.joints?.length || 0,
    jointsSample: (skin.joints || []).slice(0, 80).map((nodeIndex) => ({
      index: nodeIndex,
      name: nodes[nodeIndex]?.name || '',
    })),
  }));
}

function inspect(filePath) {
  const json = parseGlbJson(filePath);
  const { version, map } = buildHumanoidMap(json);
  const nodes = json.nodes || [];

  return {
    source: filePath,
    vrmVersion: version,
    nodeCount: nodes.length,
    skinSummary: skinJointSummary(json),
    coreHumanoid: Object.fromEntries(
      CORE_BONES.map((boneName) => [boneName, map[boneName] || null]),
    ),
    humanoidBoneNames: Object.fromEntries(
      Object.entries(map).map(([boneName, node]) => [boneName, node?.name || null]),
    ),
    leftRightSanity: {
      leftArmChain: [
        map.leftShoulder?.name,
        map.leftUpperArm?.name,
        map.leftLowerArm?.name,
        map.leftHand?.name,
      ],
      rightArmChain: [
        map.rightShoulder?.name,
        map.rightUpperArm?.name,
        map.rightLowerArm?.name,
        map.rightHand?.name,
      ],
      leftLegChain: [
        map.leftUpperLeg?.name,
        map.leftLowerLeg?.name,
        map.leftFoot?.name,
        map.leftToes?.name,
      ],
      rightLegChain: [
        map.rightUpperLeg?.name,
        map.rightLowerLeg?.name,
        map.rightFoot?.name,
        map.rightToes?.name,
      ],
    },
  };
}

const files = process.argv.slice(2);
if (!files.length) {
  console.error('Usage: node tools/animation/inspect_vrm_humanoid.mjs <avatar.vrm> [...]');
  process.exit(1);
}

const outDir = path.resolve('tools', 'blender', 'output');
fs.mkdirSync(outDir, { recursive: true });

for (const file of files) {
  const report = inspect(file);
  const outPath = path.join(outDir, `${path.basename(file, path.extname(file))}_vrm_humanoid_report.json`);
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2), 'utf8');
  console.log(`Wrote ${outPath}`);
  console.log(JSON.stringify({
    source: report.source,
    vrmVersion: report.vrmVersion,
    leftRightSanity: report.leftRightSanity,
  }, null, 2));
}
