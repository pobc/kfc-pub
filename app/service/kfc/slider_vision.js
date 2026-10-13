// Adapted from geetest-vision-validator/src/vision.cjs; runs inside Chrome.
(() => {
'use strict';
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}


function assertRgbaLayer(layer, width, height, name) {
  if (!layer || layer.length !== width * height * 4) {
    throw new Error(`${name}图层尺寸不正确`);
  }
}

function findOpaqueBounds(rgba, width, height, alphaThreshold = 16) {
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  let pixels = 0;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (rgba[(y * width + x) * 4 + 3] < alphaThreshold) continue;
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
      pixels += 1;
    }
  }

  if (maxX < minX || maxY < minY) {
    throw new Error('拼图块图层中没有找到非透明像素');
  }
  return {
    x: minX,
    y: minY,
    width: maxX - minX + 1,
    height: maxY - minY + 1,
    pixels,
  };
}

function buildDifferenceMask(gapped, full, width, height, threshold) {
  const mask = new Uint8Array(width * height);
  const strengths = new Uint16Array(width * height);
  for (let index = 0; index < width * height; index += 1) {
    const offset = index * 4;
    const strength = Math.max(
      Math.abs(gapped[offset] - full[offset]),
      Math.abs(gapped[offset + 1] - full[offset + 1]),
      Math.abs(gapped[offset + 2] - full[offset + 2]),
    );
    strengths[index] = strength;
    if (strength >= threshold) mask[index] = 1;
  }
  return { mask, strengths };
}

function findDifferenceComponents(mask, strengths, width, height) {
  const visited = new Uint8Array(mask.length);
  const components = [];
  const queue = new Int32Array(mask.length);

  for (let seed = 0; seed < mask.length; seed += 1) {
    if (!mask[seed] || visited[seed]) continue;
    let head = 0;
    let tail = 0;
    queue[tail++] = seed;
    visited[seed] = 1;
    let minX = width;
    let minY = height;
    let maxX = -1;
    let maxY = -1;
    let pixels = 0;
    let strength = 0;

    while (head < tail) {
      const index = queue[head++];
      const x = index % width;
      const y = Math.floor(index / width);
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
      pixels += 1;
      strength += strengths[index];

      for (let yy = Math.max(0, y - 1); yy <= Math.min(height - 1, y + 1); yy += 1) {
        for (let xx = Math.max(0, x - 1); xx <= Math.min(width - 1, x + 1); xx += 1) {
          const next = yy * width + xx;
          if (!mask[next] || visited[next]) continue;
          visited[next] = 1;
          queue[tail++] = next;
        }
      }
    }

    components.push({
      x: minX,
      y: minY,
      width: maxX - minX + 1,
      height: maxY - minY + 1,
      pixels,
      strength,
    });
  }
  return components;
}

function matchSlicePixels(full, slice, width, height, piece, estimatedDistance, options = {}) {
  const radius = options.photometricRadius
    ?? Math.max(7, Math.min(11, Math.ceil(piece.width * 0.18)));
  const start = clamp(Math.floor(estimatedDistance - radius), 0, width - 1);
  const end = clamp(Math.ceil(estimatedDistance + radius), start, width - 1);
  const alphaThreshold = options.matchAlphaThreshold ?? 240;
  const maximumPenalty = options.maximumColorPenalty ?? 180;
  const candidates = [];

  for (let distance = start; distance <= end; distance += 1) {
    let penalty = 0;
    let matchingPixels = 0;
    let comparedPixels = 0;
    for (let y = piece.y; y < piece.y + piece.height; y += 1) {
      for (let x = piece.x; x < piece.x + piece.width; x += 1) {
        const targetX = x + distance;
        if (targetX < 0 || targetX >= width) continue;
        const sourceOffset = (y * width + x) * 4;
        if (slice[sourceOffset + 3] < alphaThreshold) continue;
        const targetOffset = (y * width + targetX) * 4;
        const colorDifference =
          Math.abs(slice[sourceOffset] - full[targetOffset])
          + Math.abs(slice[sourceOffset + 1] - full[targetOffset + 1])
          + Math.abs(slice[sourceOffset + 2] - full[targetOffset + 2]);
        penalty += Math.min(maximumPenalty, colorDifference);
        if (colorDifference < 24) matchingPixels += 1;
        comparedPixels += 1;
      }
    }
    if (comparedPixels > 0) {
      candidates.push({
        distance,
        penalty: penalty / comparedPixels,
        matchingPixels,
        comparedPixels,
      });
    }
  }

  candidates.sort((a, b) => a.penalty - b.penalty || b.matchingPixels - a.matchingPixels);
  if (candidates.length === 0) throw new Error('拼图块与完整背景无法进行像素对齐');
  return { best: candidates[0], candidates };
}

function detectSliderGeometry(gapped, full, slice, width, height, options = {}) {
  assertRgbaLayer(gapped, width, height, '缺口背景');
  assertRgbaLayer(full, width, height, '完整背景');
  assertRgbaLayer(slice, width, height, '拼图块');

  const piece = findOpaqueBounds(slice, width, height, options.alphaThreshold ?? 16);
  const threshold = options.differenceThreshold ?? 24;
  const { mask, strengths } = buildDifferenceMask(gapped, full, width, height, threshold);
  const minimumX = options.minimumX ?? piece.x + Math.max(24, Math.floor(piece.width * 0.7));
  const minimumPixels = options.minimumPixels ?? Math.max(30, Math.floor(piece.pixels * 0.08));
  const candidates = findDifferenceComponents(mask, strengths, width, height)
    .filter((item) => item.x >= minimumX && item.pixels >= minimumPixels)
    .filter((item) => item.width >= piece.width * 0.45 && item.width <= piece.width * 1.45)
    .filter((item) => item.height >= piece.height * 0.45 && item.height <= piece.height * 1.75)
    .map((item) => {
      const widthRatio = Math.min(item.width, piece.width) / Math.max(item.width, piece.width);
      const heightRatio = Math.min(item.height, piece.height) / Math.max(item.height, piece.height);
      return { ...item, score: item.strength * widthRatio * heightRatio };
    })
    .sort((a, b) => b.score - a.score);

  if (candidates.length === 0) {
    throw new Error('完整背景与缺口背景的差值中没有找到可靠缺口');
  }

  const gap = candidates[0];
  const estimatedDistance = gap.x - piece.x;
  const borderInset = clamp(gap.y - piece.y, 0, 10);
  const geometricDistance = estimatedDistance - borderInset;
  const photometric = matchSlicePixels(
    full,
    slice,
    width,
    height,
    piece,
    geometricDistance,
    options,
  );
  const separatedRunnerUp = photometric.candidates.find(
    (item) => Math.abs(item.distance - photometric.best.distance) > 2,
  );
  const photometricConfidence = separatedRunnerUp
    ? separatedRunnerUp.penalty / Math.max(1, photometric.best.penalty)
    : 10;
  const unusuallyWideGap = gap.width >= piece.width * 1.2;
  const photometricHasEnoughPixels = photometric.best.matchingPixels >= piece.pixels * 0.15;
  const photometricIsReliable = (
    photometricConfidence >= 1.08
      && Math.abs(photometric.best.distance - geometricDistance) <= 6
  ) || (
    unusuallyWideGap
      && photometricHasEnoughPixels
      && Math.abs(photometric.best.distance - estimatedDistance) <= 20
  );
  return {
    piece,
    gap,
    distance: photometricIsReliable ? photometric.best.distance : geometricDistance,
    estimatedDistance,
    geometricDistance,
    confidence: photometricConfidence,
    distanceSource: photometricIsReliable ? 'photometric' : 'geometry',
    photometricMatch: photometric.best,
    threshold,
    candidates: candidates.slice(0, 10),
    differenceMask: mask,
  };
}


function makeRng(seed = Date.now()) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function buildMouseWander(width, height, options = {}) {
  const rng = makeRng(options.seed);
  const margin = options.margin ?? 36;
  const segments = options.segments ?? 6 + Math.floor(rng() * 4);
  const start = {
    x: options.startX ?? margin + rng() * (width * 0.22),
    y: options.startY ?? margin + rng() * (height * 0.25),
  };
  const targets = [];
  for (let i = 0; i < segments - 1; i += 1) {
    targets.push({
      x: margin + rng() * (width - margin * 2),
      y: margin + rng() * (height - margin * 2),
    });
  }
  targets.push({
    x: options.endX ?? margin + rng() * (width - margin * 2),
    y: options.endY ?? margin + rng() * (height - margin * 2),
  });

  const points = [{ x: start.x, y: start.y, delayMs: 40 + Math.round(rng() * 80) }];
  let current = start;
  for (let segmentIndex = 0; segmentIndex < targets.length; segmentIndex += 1) {
    const target = targets[segmentIndex];
    const dx = target.x - current.x;
    const dy = target.y - current.y;
    const length = Math.max(1, Math.hypot(dx, dy));
    const normalX = -dy / length;
    const normalY = dx / length;
    const bend = (rng() - 0.5) * Math.min(150, length * 0.42);
    const control1 = {
      x: current.x + dx * 0.3 + normalX * bend,
      y: current.y + dy * 0.3 + normalY * bend,
    };
    const control2 = {
      x: current.x + dx * 0.72 - normalX * bend * 0.45,
      y: current.y + dy * 0.72 - normalY * bend * 0.45,
    };
    const steps = 9 + Math.floor(rng() * 8);

    for (let step = 1; step <= steps; step += 1) {
      const t = step / steps;
      const mt = 1 - t;
      let x = mt ** 3 * current.x
        + 3 * mt ** 2 * t * control1.x
        + 3 * mt * t ** 2 * control2.x
        + t ** 3 * target.x;
      let y = mt ** 3 * current.y
        + 3 * mt ** 2 * t * control1.y
        + 3 * mt * t ** 2 * control2.y
        + t ** 3 * target.y;
      if (step !== steps) {
        x += (rng() - 0.5) * 1.3;
        y += (rng() - 0.5) * 1.3;
      }
      points.push({
        x: clamp(x, margin, width - margin),
        y: clamp(y, margin, height - margin),
        delayMs: 13 + Math.round(rng() * 20) + (step === steps ? 45 + Math.round(rng() * 120) : 0),
      });
    }
    current = target;
  }

  const last = points.at(-1);
  last.x = targets.at(-1).x;
  last.y = targets.at(-1).y;
  return points;
}

function buildPreClickRoutine(width, height, options = {}) {
  const rng = makeRng(options.seed);
  const margin = options.margin ?? 36;
  const behaviorLevel = clamp(Math.floor(options.behaviorLevel ?? 0), 0, 4);
  const endX = clamp(
    options.endX ?? margin + rng() * (width - margin * 2),
    margin,
    width - margin,
  );
  const endY = clamp(
    options.endY ?? margin + rng() * (height - margin * 2),
    margin,
    height - margin,
  );
  // 点击前只从按钮附近自然靠近；全页面随机游走会形成与真实操作不符的长轨迹。
  const startX = clamp(
    options.startX ?? endX + 42 + rng() * (54 + behaviorLevel * 8),
    margin,
    width - margin,
  );
  const startY = clamp(
    options.startY ?? endY - 28 + rng() * 56,
    margin,
    height - margin,
  );
  const dx = endX - startX;
  const dy = endY - startY;
  const length = Math.max(1, Math.hypot(dx, dy));
  const normalX = -dy / length;
  const normalY = dx / length;
  const bend = (rng() - 0.5) * Math.min(18, length * 0.18);
  const control1 = {
    x: startX + dx * 0.34 + normalX * bend,
    y: startY + dy * 0.34 + normalY * bend,
  };
  const control2 = {
    x: startX + dx * 0.76 - normalX * bend * 0.35,
    y: startY + dy * 0.76 - normalY * bend * 0.35,
  };
  const steps = options.segments ?? 14 + Math.floor(rng() * 7) + behaviorLevel;
  const points = [];
  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps;
    const mt = 1 - t;
    const x = mt ** 3 * startX
      + 3 * mt ** 2 * t * control1.x
      + 3 * mt * t ** 2 * control2.x
      + t ** 3 * endX;
    const y = mt ** 3 * startY
      + 3 * mt ** 2 * t * control1.y
      + 3 * mt * t ** 2 * control2.y
      + t ** 3 * endY;
    points.push({
      x: step === steps ? endX : x + (rng() - 0.5) * 0.45,
      y: step === steps ? endY : y + (rng() - 0.5) * 0.45,
      delayMs: 10 + Math.round(rng() * 15)
        + (step === 0 ? 20 + Math.round(rng() * 35) : 0)
        + (step === steps ? 90 + Math.round(rng() * 100) : 0),
    });
  }
  return points;
}

function classifyVerificationResult(messages) {
  const normalizedMessages = Array.isArray(messages)
    ? messages.map((message) => String(message).trim()).filter(Boolean)
    : [String(messages ?? '').trim()].filter(Boolean);
  const message = normalizedMessages.join(' | ');

  if (/forbidden/i.test(message)) {
    return {
      kind: 'behavior-forbidden',
      message,
      messages: normalizedMessages,
      puzzlePassed: true,
      verificationPassed: false,
      retryable: true,
    };
  }
  if (/验证通过|验证成功|二次校验均成功/.test(message)) {
    return {
      kind: 'success',
      message,
      messages: normalizedMessages,
      puzzlePassed: true,
      verificationPassed: true,
      retryable: false,
    };
  }
  if (/请正确拼合图像/.test(message)) {
    return {
      kind: 'alignment-failure',
      message,
      messages: normalizedMessages,
      puzzlePassed: false,
      verificationPassed: false,
      retryable: true,
    };
  }
  if (/请重试|再试|重试/.test(message)) {
    return {
      kind: 'retry-requested',
      message,
      messages: normalizedMessages,
      puzzlePassed: null,
      verificationPassed: false,
      retryable: true,
    };
  }
  return {
    kind: 'unknown',
    message,
    messages: normalizedMessages,
    puzzlePassed: null,
    verificationPassed: null,
    retryable: false,
  };
}

function buildTrajectory(startX, startY, distance, options = {}) {
  if (!Number.isFinite(distance) || distance <= 0) {
    throw new Error('滑块距离必须是正数');
  }
  const rng = makeRng(options.seed);
  const behaviorLevel = clamp(Math.floor(options.behaviorLevel ?? 0), 0, 4);
  const steps = Math.max(
    16,
    Math.floor(options.steps ?? clamp(25 + distance * 0.16 + rng() * 9 + behaviorLevel, 28, 54)),
  );
  const overshoot = Math.max(0, options.overshoot ?? 0.8 + rng() * 1.8);
  const curve = options.curve ?? (rng() - 0.5) * 3.6;
  const endDrift = (rng() - 0.5) * 1.4;
  const outwardDistance = distance + overshoot;
  const points = [];
  let previousX = startX;
  let tremorY = 0;
  const pauseIndex = Math.floor(steps * (0.56 + rng() * 0.22));

  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    // 最小加加速度曲线在起点和终点速度均为零，比固定分段模板更接近一次连续拖动。
    const progress = 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5;
    const xNoise = i === 0 || i === steps ? 0 : (rng() - 0.5) * 0.36;
    let x = startX + outwardDistance * progress + xNoise;
    if (i > 0 && i < steps) {
      x = clamp(x, previousX + 0.02, startX + outwardDistance);
    }
    if (i === steps) x = startX + outwardDistance;
    previousX = x;

    tremorY = clamp(tremorY * 0.72 + (rng() - 0.5) * 0.34, -0.55, 0.55);
    const y = startY
      + Math.sin(Math.PI * t) * curve
      + endDrift * progress
      + tremorY;
    let delayMs = 9 + Math.round(rng() * 13);
    if (i === 0) delayMs += 35 + Math.round(rng() * 55);
    if (i === pauseIndex) delayMs += 28 + Math.round(rng() * (38 + behaviorLevel * 6));
    if (i >= steps - 2) delayMs += 18 + Math.round(rng() * 32);
    points.push({ x, y, delayMs });
  }

  const correctionCount = 2 + Math.floor(rng() * 2);
  for (let index = 1; index <= correctionCount; index += 1) {
    const progress = index / (correctionCount + 1);
    points.push({
      x: startX + distance + overshoot * (1 - progress),
      y: startY + endDrift * (1 - progress),
      delayMs: 28 + Math.round(rng() * 38),
    });
  }
  points.push({
    x: startX + distance,
    y: startY,
    delayMs: 80 + Math.round(rng() * 75),
  });
  return points;
}

function compactMousePoints(points, options = {}) {
  if (!Array.isArray(points) || points.length === 0) return [];

  const maxPoints = Math.max(2, Math.floor(options.maxPoints ?? points.length));
  const delayScale = Math.max(0, Number(options.delayScale ?? 1));
  const minimumDelayMs = Math.max(0, Math.floor(options.minimumDelayMs ?? 1));
  const targetCount = Math.min(maxPoints, points.length);
  if (targetCount === points.length && delayScale === 1) {
    return points.map((point) => ({ ...point }));
  }

  const result = [];
  let previousIndex = -1;
  for (let index = 0; index < targetCount; index += 1) {
    const sourceIndex = Math.round(index * (points.length - 1) / (targetCount - 1));
    let accumulatedDelay = 0;
    for (let cursor = previousIndex + 1; cursor <= sourceIndex; cursor += 1) {
      accumulatedDelay += Math.max(0, Number(points[cursor].delayMs) || 0);
    }
    const source = points[sourceIndex];
    result.push({
      ...source,
      delayMs: Math.max(minimumDelayMs, Math.round(accumulatedDelay * delayScale)),
    });
    previousIndex = sourceIndex;
  }
  return result;
}


function readLayers() {
  const bg = [...document.querySelectorAll('.geetest_canvas_bg')]
    .find(canvas => canvas.getBoundingClientRect().width > 0);
  if (!bg) throw new Error('没有可见的极验画布');
  const root = bg.closest('.geetest_window') || bg.parentElement;
  const full = root.querySelector('.geetest_canvas_fullbg');
  const slice = root.querySelector('.geetest_canvas_slice');
  const canvases = [bg, full, slice];
  if (!canvases.every(c => c instanceof HTMLCanvasElement && c.width === bg.width && c.height === bg.height)) {
    throw new Error('极验画布未准备好或尺寸不一致');
  }
  const layers = canvases.map(c => c.getContext('2d').getImageData(0, 0, c.width, c.height).data);
  return { layers, width: bg.width, height: bg.height };
}

function signature() {
  const { layers, width, height } = readLayers();
  let hash = 2166136261;
  const pixels = layers[1];
  for (let offset = 0; offset < pixels.length; offset += 64) {
    hash = Math.imul(hash ^ pixels[offset], 16777619) >>> 0;
    hash = Math.imul(hash ^ pixels[offset + 1], 16777619) >>> 0;
    hash = Math.imul(hash ^ pixels[offset + 2], 16777619) >>> 0;
  }
  return `${width}:${height}:${hash}`;
}

function analyze() {
  const { layers, width, height } = readLayers();
  const geometry = detectSliderGeometry(...layers, width, height);
  const { differenceMask, ...result } = geometry;
  return { ...result, width, height, signature: signature() };
}

globalThis.kfcSliderVision = {
  analyze, signature, detectSliderGeometry, buildPreClickRoutine, buildTrajectory,
  classifyVerificationResult,
};
})();
