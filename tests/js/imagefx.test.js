const assert = require("assert");
const fx = require(__dirname + "/../../app/static/imagefx.js");

let passed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); passed++; }
  catch (e) { console.log("  FAIL " + name + "\n       " + e.message); process.exitCode = 1; }
}

function canvas(w, h, seed) {
  let s = seed || 7;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const p = (y * w + x) * 4;
      const base = 100 + 50 * Math.sin(x / 17) + 30 * Math.cos(y / 13);
      const grain = (rnd() - 0.5) * 10;
      data[p] = base + grain; data[p + 1] = base * 0.9 + grain;
      data[p + 2] = base * 0.8 + grain; data[p + 3] = 255;
    }
  }
  return { data, width: w, height: h };
}

// A "secret": dense high-frequency structure, like small print on a document.
// Both variants have the SAME proportion of ink, so any surviving difference
// is genuinely leaked structure rather than a difference in overall darkness.
function writeSecret(img, x0, y0, x1, y1, variant) {
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const p = (y * img.width + x) * 4;
      const on = variant === 0
        ? ((x >> 1) + (y >> 2)) % 3 === 0
        : ((y >> 1) + (x >> 2)) % 3 === 0;
      const v = on ? 20 : 235;
      img.data[p] = v; img.data[p + 1] = v; img.data[p + 2] = v;
    }
  }
}

// Deliberately different ink density, to measure the one thing concealment
// cannot hide: roughly how dark the area was.
function writeSecretDenser(img, x0, y0, x1, y1) {
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const p = (y * img.width + x) * 4;
      const v = ((x >> 1) + (y >> 1)) % 8 === 0 ? 20 : 235;
      img.data[p] = v; img.data[p + 1] = v; img.data[p + 2] = v;
    }
  }
}

function regionDiff(a, b, x0, y0, x1, y1, width) {
  let total = 0, n = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const p = (y * width + x) * 4;
      for (let c = 0; c < 3; c++) { total += Math.abs(a.data[p + c] - b.data[p + c]); n++; }
    }
  }
  return total / n;
}

const BOX = { x0: 60, y0: 50, x1: 200, y1: 120 };

console.log("imagefx:");

test("destroys the concealed content, it is not merely hidden", () => {
  // Two images differing ONLY in the secret. If concealment is genuinely
  // destructive, the outputs must be near-identical - the secret cannot have
  // survived in any recoverable form.
  const a = canvas(280, 180), b = canvas(280, 180);
  writeSecret(a, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 0);
  writeSecret(b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 1);
  const before = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);

  const opts = { style: "defocus", strength: 9, seed: 99, grain: 3 };
  const mask = fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 });
  fx.conceal(a, mask, opts);
  fx.conceal(b, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), opts);
  const after = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);

  console.log(`       difference ${before.toFixed(1)} -> ${after.toFixed(2)} ` +
              `(${(100 * after / before).toFixed(1)}% remains)`);
  assert.ok(after < before * 0.02, `${after} is not small enough vs ${before}`);
});

test("a small area is destroyed as thoroughly as a large one", () => {
  // The case that matters: people conceal a name or a number, not half a page.
  const SMALL = { x0: 100, y0: 80, x1: 140, y1: 100 };
  const a = canvas(280, 180), b = canvas(280, 180);
  writeSecret(a, SMALL.x0, SMALL.y0, SMALL.x1, SMALL.y1, 0);
  writeSecret(b, SMALL.x0, SMALL.y0, SMALL.x1, SMALL.y1, 1);
  const before = regionDiff(a, b, SMALL.x0, SMALL.y0, SMALL.x1, SMALL.y1, 280);
  const opts = { style: "defocus", strength: 9, seed: 11, grain: 3 };
  fx.conceal(a, fx.buildMask(280, 180, [{ type: "rect", ...SMALL }], { seed: 5 }), opts);
  fx.conceal(b, fx.buildMask(280, 180, [{ type: "rect", ...SMALL }], { seed: 5 }), opts);
  const after = regionDiff(a, b, SMALL.x0, SMALL.y0, SMALL.x1, SMALL.y1, 280);
  console.log(`       small area ${before.toFixed(1)} -> ${after.toFixed(2)} ` +
              `(${(100 * after / before).toFixed(1)}% remains)`);
  assert.ok(after < before * 0.02, `small areas leak: ${(100*after/before).toFixed(1)}% remains`);
});

test("overall darkness survives by design, and styles differ in how much", () => {
  // Honest bound on what concealment cannot do. Averaging preserves roughly
  // how bright the area was - that is what makes it look like the same scene
  // out of focus rather than a patch pasted on. It should be a whisper.
  const a = canvas(280, 180), b = canvas(280, 180);
  writeSecret(a, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 0);
  writeSecretDenser(b, BOX.x0, BOX.y0, BOX.x1, BOX.y1);
  const before = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);
  const opts = { style: "defocus", strength: 9, seed: 21, grain: 3 };
  fx.conceal(a, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), opts);
  fx.conceal(b, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), opts);
  const after = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);
  console.log(`       defocus keeps ${(100 * after / before).toFixed(0)}% of the brightness difference`);

  // Structure is gone (proved above); what remains is roughly how dark the
  // area was. That is unavoidable for anything meant to look like natural
  // defocus - real out-of-focus blur preserves local average brightness. The
  // styles that push towards white or black discard even that, at the cost of
  // only being plausible where a highlight or shadow makes sense.
  const c = canvas(280, 180), d = canvas(280, 180);
  writeSecret(c, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 0);
  writeSecretDenser(d, BOX.x0, BOX.y0, BOX.x1, BOX.y1);
  const glareOpts = { style: "glare", strength: 12, seed: 21, grain: 3 };
  fx.conceal(c, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), glareOpts);
  fx.conceal(d, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), glareOpts);
  const glareAfter = regionDiff(c, d, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);
  console.log(`       glare   keeps ${(100 * glareAfter / before).toFixed(0)}%`);

  assert.ok(after < before * 0.5, "defocus should still roughly halve it");
  assert.ok(glareAfter < after, "glare should leak less brightness than defocus");
});

test("holds up across every style", () => {
  fx.styles.forEach((style) => {
    const a = canvas(280, 180), b = canvas(280, 180);
    writeSecret(a, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 0);
    writeSecret(b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 1);
    const before = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);
    const opts = { style, strength: 9, seed: 3, grain: 3, angle: 15 };
    fx.conceal(a, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), opts);
    fx.conceal(b, fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 }), opts);
    const after = regionDiff(a, b, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 280);
    assert.ok(after < before * 0.05, `${style}: ${(100*after/before).toFixed(1)}% of the secret remains`);
  });
});

test("keeps grain, so the patch is not suspiciously clean", () => {
  const img = canvas(280, 180);
  writeSecret(img, BOX.x0, BOX.y0, BOX.x1, BOX.y1, 0);
  const mask = fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 });
  fx.conceal(img, mask, { style: "defocus", strength: 9, grain: 4, seed: 1 });

  let energy = 0, n = 0;
  for (let y = BOX.y0 + 15; y < BOX.y1 - 15; y++) {
    for (let x = BOX.x0 + 15; x < BOX.x1 - 15; x++) {
      const p = (y * 280 + x) * 4;
      const d = img.data[p] - (img.data[p - 4] + img.data[p + 4]) / 2;
      energy += d * d; n++;
    }
  }
  const rms = Math.sqrt(energy / n);
  console.log(`       residual grain ${rms.toFixed(2)} (a plain blur would be ~0)`);
  assert.ok(rms > 1.0, "concealed area has no grain, which is a giveaway");
});

test("edges are soft and irregular, not a tell-tale rectangle", () => {
  const mask = fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 });
  let partial = 0;
  for (let i = 0; i < mask.length; i++) if (mask[i] > 0.02 && mask[i] < 0.98) partial++;
  assert.ok(partial > 3000, "boundary is too abrupt: only " + partial + " transitional pixels");

  // Walk down each column and find where the mask crosses half strength.
  // For a machine-drawn rectangle that boundary sits at the same y every
  // time; a natural-looking edge has to wander.
  const crossings = [];
  for (let x = BOX.x0; x < BOX.x1; x += 4) {
    for (let y = 0; y < 179; y++) {
      if (mask[y * 280 + x] >= 0.5) { crossings.push(y); break; }
    }
  }
  const spread = Math.max(...crossings) - Math.min(...crossings);
  console.log(`       top edge wanders over ${spread}px`);
  assert.ok(spread >= 3, "edge is a straight line, it would look machine-made");
});

test("changes nothing outside the mask", () => {
  const img = canvas(280, 180), copy = canvas(280, 180);
  const mask = fx.buildMask(280, 180, [{ type: "rect", ...BOX }], { seed: 5 });
  const bounds = fx.maskBounds(mask, 280, 180, 0);
  fx.conceal(img, mask, { style: "defocus", strength: 8, grain: 3, seed: 2 });

  let touched = 0;
  for (let y = 0; y < 180; y++) {
    for (let x = 0; x < 280; x++) {
      const inside = x >= bounds.x0 && x <= bounds.x1 && y >= bounds.y0 && y <= bounds.y1;
      if (inside) continue;
      const p = (y * 280 + x) * 4;
      for (let c = 0; c < 3; c++) if (img.data[p + c] !== copy.data[p + c]) touched++;
    }
  }
  assert.strictEqual(touched, 0, touched + " pixels changed outside the mask");
  // The soft falloff does reach past the drawn rectangle - that is the point.
  console.log(`       drawn ${BOX.x1 - BOX.x0}x${BOX.y1 - BOX.y0}, ` +
              `affected ${bounds.x1 - bounds.x0}x${bounds.y1 - bounds.y0}`);
});

test("measures real grain from the image", () => {
  const quiet = canvas(200, 200, 3);
  for (let i = 0; i < quiet.data.length; i += 4) {
    quiet.data[i] = quiet.data[i + 1] = quiet.data[i + 2] = 128;
  }
  assert.ok(fx.measureGrain(quiet, null) < 1.5, "flat image should read as low grain");
  assert.ok(fx.measureGrain(canvas(200, 200, 9), null) > 1.5, "grainy image should read higher");
});

console.log("  " + passed + " passed");
