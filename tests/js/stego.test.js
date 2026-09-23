// Pure-JS tests: no image codec involved, just the algorithm.
const assert = require("assert");
const stego = require(__dirname + "/../../app/static/stego.js");

function photo(width, height, seed) {
  // Something with structure and grain, so blocks are not all flat.
  let s = seed || 1;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const p = (y * width + x) * 4;
      const base = 90 + 60 * Math.sin(x / 23) * Math.cos(y / 31) + 40 * (x / width);
      const grain = (rnd() - 0.5) * 18;
      data[p] = base + grain;
      data[p + 1] = base * 0.95 + grain;
      data[p + 2] = base * 0.85 + grain;
      data[p + 3] = 255;
    }
  }
  return { data, width, height };
}

function psnr(a, b) {
  let mse = 0;
  for (let i = 0; i < a.length; i++) {
    if ((i & 3) === 3) continue;
    const d = a[i] - b[i];
    mse += d * d;
  }
  mse /= (a.length * 3) / 4;
  return 10 * Math.log10((255 * 255) / mse);
}

let passed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); passed++; }
  catch (e) { console.log("  FAIL " + name + "\n       " + e.message); process.exitCode = 1; }
}

console.log("stego:");

test("round-trips a message", () => {
  const img = photo(640, 480);
  stego.embed(img, "sent to berkay 2026-09-22");
  const out = stego.extract(img);
  assert.strictEqual(out.text, "sent to berkay 2026-09-22");
  assert.ok(out.confidence > 0.95, "confidence " + out.confidence);
});

test("round-trips unicode", () => {
  const img = photo(640, 480);
  stego.embed(img, "tatil fotoğrafı — ünlü çığ ŞİÖ");
  assert.strictEqual(stego.extract(img).text, "tatil fotoğrafı — ünlü çığ ŞİÖ");
});

test("stays invisible (PSNR above 40 dB)", () => {
  const img = photo(640, 480);
  const before = img.data.slice();
  stego.embed(img, "watermark");
  const q = psnr(before, img.data);
  assert.ok(q > 40, "PSNR was only " + q.toFixed(1) + " dB");
  console.log("       PSNR " + q.toFixed(1) + " dB");
});

test("reports nothing on a clean image", () => {
  assert.strictEqual(stego.extract(photo(640, 480)), null);
});

test("reports nothing rather than garbage when damaged", () => {
  const img = photo(640, 480);
  stego.embed(img, "secret");
  // Scribble over two thirds of it.
  for (let i = 0; i < img.data.length; i++) {
    if (i % 12 < 8) img.data[i] = (i * 7) & 255;
  }
  const out = stego.extract(img);
  assert.ok(out === null || out.text !== "secret" ? out === null : true,
    "should fail closed, got " + JSON.stringify(out));
});

test("survives a redacted region wiping part of the image", () => {
  const img = photo(800, 600);
  stego.embed(img, "only for acme bank");
  // Flatten a 200x120 area, as a redaction would.
  for (let y = 100; y < 220; y++) {
    for (let x = 150; x < 350; x++) {
      const p = (y * 800 + x) * 4;
      img.data[p] = img.data[p + 1] = img.data[p + 2] = 128;
    }
  }
  assert.strictEqual(stego.extract(img).text, "only for acme bank");
});

test("refuses a message too big for the image", () => {
  assert.throws(() => stego.embed(photo(200, 150), "x".repeat(5000)), /too/i);
});

test("capacity scales with area", () => {
  assert.ok(stego.capacityBytes(1920, 1080) > stego.capacityBytes(640, 480));
});

console.log("  " + passed + " passed");
