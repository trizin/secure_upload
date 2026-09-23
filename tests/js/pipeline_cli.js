// Mirrors what the page does on save: conceal the marked areas, then embed
// the hidden mark over the finished picture.
const fs = require("fs");
const stego = require(__dirname + "/../../app/static/stego.js");
const fx = require(__dirname + "/../../app/static/imagefx.js");

const [, , rawPath, wStr, hStr, spec, message, outPath] = process.argv;
const width = parseInt(wStr, 10), height = parseInt(hStr, 10);
const data = new Uint8ClampedArray(fs.readFileSync(rawPath));
const image = { data, width, height };
const opts = JSON.parse(spec); // {x0,y0,x1,y1,style,strength,soften}

const mask = fx.buildMask(width, height,
  [{ type: "rect", x0: opts.x0, y0: opts.y0, x1: opts.x1, y1: opts.y1 }],
  { feather: opts.feather || 14, seed: 1000 });
fx.conceal(image, mask, { style: opts.style, strength: opts.strength, seed: 500, angle: 12 });
if (opts.soften) fx.softenAll(image, opts.soften, 99);
if (message) stego.embed(image, message, { strength: opts.stegoStrength || 16 });

fs.writeFileSync(outPath, Buffer.from(data.buffer, data.byteOffset, data.length));
console.log(JSON.stringify({ ok: true }));
