// Test harness: lets Python drive embed/extract on raw RGBA buffers so the
// watermark can be run through a genuine JPEG encode/decode cycle.
const fs = require("fs");
const stego = require(__dirname + "/../../app/static/stego.js");

const [, , cmd, rawPath, wStr, hStr, ...rest] = process.argv;
const width = parseInt(wStr, 10), height = parseInt(hStr, 10);
const data = new Uint8ClampedArray(fs.readFileSync(rawPath));
const image = { data, width, height };

if (cmd === "embed") {
  const [message, outPath, strength] = rest;
  stego.embed(image, message, strength ? { strength: parseFloat(strength) } : {});
  fs.writeFileSync(outPath, Buffer.from(data.buffer, data.byteOffset, data.length));
  console.log(JSON.stringify({ ok: true }));
} else if (cmd === "extract") {
  console.log(JSON.stringify(stego.extract(image) || { text: null }));
} else if (cmd === "capacity") {
  console.log(JSON.stringify({ bytes: stego.capacityBytes(width, height) }));
} else {
  console.error("unknown command"); process.exit(2);
}
