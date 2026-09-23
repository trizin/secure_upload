const fs = require("fs");
const rm = require(__dirname + "/../../app/static/robustmark.js");

const [, , cmd, rawPath, wStr, hStr, ...rest] = process.argv;
const width = parseInt(wStr, 10), height = parseInt(hStr, 10);
const data = new Uint8ClampedArray(fs.readFileSync(rawPath));
const image = { data, width, height };

if (cmd === "embed") {
  const [id, outPath, strength] = rest;
  rm.embed(image, id, strength ? { strength: parseFloat(strength) } : {});
  fs.writeFileSync(outPath, Buffer.from(data.buffer, data.byteOffset, data.length));
  console.log(JSON.stringify({ ok: true }));
} else if (cmd === "extract") {
  console.log(JSON.stringify(rm.extract(image) || { id: null }));
} else {
  console.error("unknown command"); process.exit(2);
}
