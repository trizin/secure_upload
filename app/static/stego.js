/* Robust invisible watermarking.
 *
 * Bits are carried in the relationship between two mid-frequency DCT
 * coefficients of each 8x8 luma block - the classic Koch-Zhao construction.
 * That band is chosen deliberately: low frequencies are where visible
 * artifacts would show, high frequencies are what JPEG throws away first.
 * Mid-band survives re-compression while staying invisible.
 *
 * What this survives: saving again, JPEG re-compression (down to roughly
 * quality 50), mild colour and brightness adjustment.
 *
 * What it does NOT survive: cropping, resizing, rotation, screenshots at a
 * different resolution, or anyone who knows the scheme and wants it gone.
 * Nothing invisible embedded in pixels survives all of that. Treat it as
 * "I can prove this copy was the one I sent you", not as armour.
 *
 * No DOM here on purpose, so it can be tested outside a browser.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.SUPLOAD = root.SUPLOAD || {}).stego = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var N = 8;
  var MAGIC = 0xa517;
  var HEADER_BITS = 16 + 12 + 16;   // magic, payload length, CRC
  var HEADER_REPEATS = 7;           // the header must survive local damage
  var MAX_BYTES = 4095;             // 12-bit length field

  // The pair of coefficients that carry each bit, chosen by measurement rather
  // than by eye. Two things matter: the pair must sit symmetrically across the
  // diagonal so JPEG quantises both with a near-identical step (19 and 17 in
  // the standard luma table), and that step must be small enough that the
  // embedding margin outlives it. Pairs further out - (3,4)/(4,3), steps 51
  // and 56 - are quantised so coarsely that compression alone flips their
  // ordering, losing the mark by quality 85. This pair holds to quality 55.
  var C1 = 1 * N + 3;
  var C2 = 3 * N + 1;

  var COS = new Float32Array(N * N);
  for (var x = 0; x < N; x++) {
    for (var u = 0; u < N; u++) {
      COS[x * N + u] = Math.cos(((2 * x + 1) * u * Math.PI) / 16);
    }
  }
  var CU = new Float32Array(N);
  for (var i = 0; i < N; i++) CU[i] = i === 0 ? Math.SQRT1_2 : 1;

  function dct8(src, so, ss, dst, dof, ds) {
    for (var u = 0; u < N; u++) {
      var sum = 0;
      for (var x = 0; x < N; x++) sum += src[so + x * ss] * COS[x * N + u];
      dst[dof + u * ds] = 0.5 * CU[u] * sum;
    }
  }

  function idct8(src, so, ss, dst, dof, ds) {
    for (var x = 0; x < N; x++) {
      var sum = 0;
      for (var u = 0; u < N; u++) sum += 0.5 * CU[u] * src[so + u * ss] * COS[x * N + u];
      dst[dof + x * ds] = sum;
    }
  }

  var tmp = new Float32Array(N * N);

  function forwardDCT(block) {
    for (var r = 0; r < N; r++) dct8(block, r * N, 1, tmp, r * N, 1);
    for (var c = 0; c < N; c++) dct8(tmp, c, N, block, c, N);
  }

  function inverseDCT(block) {
    for (var c = 0; c < N; c++) idct8(block, c, N, tmp, c, N);
    for (var r = 0; r < N; r++) idct8(tmp, r * N, 1, block, r * N, 1);
  }

  // --- bit plumbing --------------------------------------------------------

  function crc16(bytes) {
    var crc = 0xffff;
    for (var i = 0; i < bytes.length; i++) {
      crc ^= bytes[i] << 8;
      for (var b = 0; b < 8; b++) {
        crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
      }
    }
    return crc;
  }

  function pushBits(out, value, count) {
    for (var i = count - 1; i >= 0; i--) out.push((value >> i) & 1);
  }

  function buildBitstream(bytes) {
    var header = [];
    pushBits(header, MAGIC, 16);
    pushBits(header, bytes.length, 12);
    pushBits(header, crc16(bytes), 16);

    var payload = [];
    for (var i = 0; i < bytes.length; i++) pushBits(payload, bytes[i], 8);
    return { header: header, payload: payload };
  }

  // Deterministic shuffle. Each bit's copies land all over the image rather
  // than in one corner, so redacting a region cannot take out a whole copy -
  // and in particular cannot take out the header.
  function blockOrder(count) {
    var order = new Int32Array(count);
    for (var i = 0; i < count; i++) order[i] = i;
    var state = 0x9e3779b9 ^ count;
    for (var j = count - 1; j > 0; j--) {
      state ^= state << 13; state >>>= 0;
      state ^= state >>> 17;
      state ^= state << 5;  state >>>= 0;
      var k = state % (j + 1);
      var t = order[j]; order[j] = order[k]; order[k] = t;
    }
    return order;
  }

  // --- colour --------------------------------------------------------------

  function toLuma(data, width, height) {
    var y = new Float32Array(width * height);
    for (var i = 0, p = 0; i < y.length; i++, p += 4) {
      y[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
    }
    return y;
  }

  // Apply the luma change as an equal RGB offset. Chroma is left untouched,
  // which keeps colours exactly as they were.
  function applyLuma(data, original, updated) {
    for (var i = 0, p = 0; i < updated.length; i++, p += 4) {
      var d = updated[i] - original[i];
      if (d === 0) continue;
      data[p] = clamp(data[p] + d);
      data[p + 1] = clamp(data[p + 1] + d);
      data[p + 2] = clamp(data[p + 2] + d);
    }
  }

  function clamp(v) { return v < 0 ? 0 : v > 255 ? 255 : Math.round(v); }

  // --- capacity ------------------------------------------------------------

  function gridOf(width, height) {
    return { cols: Math.floor(width / N), rows: Math.floor(height / N) };
  }

  function capacityBytes(width, height) {
    var g = gridOf(width, height);
    var blocks = g.cols * g.rows;
    var usable = blocks - HEADER_BITS * HEADER_REPEATS;
    if (usable <= 0) return 0;
    return Math.min(MAX_BYTES, Math.floor(usable / 8 / 3)); // room for 3 copies
  }

  // --- embed ---------------------------------------------------------------

  function embed(image, message, options) {
    options = options || {};
    var strength = options.strength || 16;
    var width = image.width, height = image.height, data = image.data;

    var bytes = new TextEncoder().encode(message);
    if (bytes.length === 0) throw new Error("Nothing to embed.");
    if (bytes.length > MAX_BYTES) throw new Error("Message is too long.");

    var g = gridOf(width, height);
    var blocks = g.cols * g.rows;
    var stream = buildBitstream(bytes);
    var need = HEADER_BITS * HEADER_REPEATS + stream.payload.length;
    if (blocks < need) {
      throw new Error(
        "Image is too small for this message. It can hold about " +
        capacityBytes(width, height) + " characters."
      );
    }

    var bits = new Uint8Array(blocks);
    var used = new Uint8Array(blocks);
    var idx = 0, i;
    for (var rep = 0; rep < HEADER_REPEATS; rep++) {
      for (i = 0; i < stream.header.length; i++) {
        bits[idx] = stream.header[i]; used[idx] = 1; idx++;
      }
    }
    // Fill everything that is left with repeats of the payload, so extraction
    // can take a majority vote instead of trusting one copy.
    var pi = 0;
    while (idx < blocks) {
      bits[idx] = stream.payload[pi % stream.payload.length];
      used[idx] = 1;
      idx++; pi++;
    }

    var order = blockOrder(blocks);
    var luma = toLuma(data, width, height);
    var original = luma.slice();
    var block = new Float32Array(N * N);

    for (var slot = 0; slot < blocks; slot++) {
      if (!used[slot]) continue;
      var bIndex = order[slot];
      var bx = (bIndex % g.cols) * N;
      var by = Math.floor(bIndex / g.cols) * N;

      for (var r = 0; r < N; r++) {
        for (var c = 0; c < N; c++) block[r * N + c] = luma[(by + r) * width + bx + c];
      }
      forwardDCT(block);

      // Busier blocks hide a bigger margin, so push harder where it cannot be
      // seen. Extraction only compares the two coefficients, so it never needs
      // to know what margin was used.
      var energy = 0;
      for (i = 1; i < N * N; i++) energy += Math.abs(block[i]);
      var delta = strength * (1 + Math.min(1.5, energy / 2200));

      var a = block[C1], b = block[C2];
      var mid = (a + b) / 2;
      if (bits[slot]) {
        if (a - b < delta) { block[C1] = mid + delta / 2; block[C2] = mid - delta / 2; }
      } else {
        if (b - a < delta) { block[C2] = mid + delta / 2; block[C1] = mid - delta / 2; }
      }

      inverseDCT(block);
      for (r = 0; r < N; r++) {
        for (c = 0; c < N; c++) luma[(by + r) * width + bx + c] = block[r * N + c];
      }
    }

    applyLuma(data, original, luma);
    return image;
  }

  // --- extract -------------------------------------------------------------

  function readBits(image) {
    var width = image.width, height = image.height;
    var g = gridOf(width, height);
    var blocks = g.cols * g.rows;
    var luma = toLuma(image.data, width, height);
    var order = blockOrder(blocks);
    var block = new Float32Array(N * N);
    var bits = new Uint8Array(blocks);
    var margins = new Float32Array(blocks);

    for (var slot = 0; slot < blocks; slot++) {
      var bIndex = order[slot];
      var bx = (bIndex % g.cols) * N;
      var by = Math.floor(bIndex / g.cols) * N;
      for (var r = 0; r < N; r++) {
        for (var c = 0; c < N; c++) block[r * N + c] = luma[(by + r) * width + bx + c];
      }
      forwardDCT(block);
      var diff = block[C1] - block[C2];
      bits[slot] = diff > 0 ? 1 : 0;
      margins[slot] = Math.abs(diff);
    }
    return { bits: bits, margins: margins, blocks: blocks };
  }

  function majority(bits, positions) {
    var ones = 0;
    for (var i = 0; i < positions.length; i++) ones += bits[positions[i]];
    return { bit: ones * 2 >= positions.length ? 1 : 0, agree: Math.max(ones, positions.length - ones) / positions.length };
  }

  function bitsToInt(bits, from, count) {
    var v = 0;
    for (var i = 0; i < count; i++) v = (v << 1) | bits[from + i];
    return v;
  }

  function extract(image) {
    var read = readBits(image);
    var bits = read.bits, blocks = read.blocks;
    if (blocks < HEADER_BITS * HEADER_REPEATS) return null;

    // Header first: majority across its repeats.
    var header = new Uint8Array(HEADER_BITS);
    var agreeSum = 0;
    for (var i = 0; i < HEADER_BITS; i++) {
      var positions = [];
      for (var rep = 0; rep < HEADER_REPEATS; rep++) positions.push(rep * HEADER_BITS + i);
      var m = majority(bits, positions);
      header[i] = m.bit;
      agreeSum += m.agree;
    }

    if (bitsToInt(header, 0, 16) !== MAGIC) return null;
    var length = bitsToInt(header, 16, 12);
    var crc = bitsToInt(header, 28, 16);
    if (length === 0 || length > MAX_BYTES) return null;

    var payloadBits = length * 8;
    var start = HEADER_BITS * HEADER_REPEATS;
    var available = blocks - start;
    if (available < payloadBits) return null;

    var voted = new Uint8Array(payloadBits);
    var payloadAgree = 0;
    for (i = 0; i < payloadBits; i++) {
      var pos = [];
      for (var k = start + i; k < blocks; k += payloadBits) pos.push(k);
      var pm = majority(bits, pos);
      voted[i] = pm.bit;
      payloadAgree += pm.agree;
    }

    var bytes = new Uint8Array(length);
    for (i = 0; i < length; i++) bytes[i] = bitsToInt(voted, i * 8, 8);
    if (crc16(bytes) !== crc) return null;

    var text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (e) {
      return null;
    }
    return {
      text: text,
      confidence: (agreeSum / HEADER_BITS) * 0.3 + (payloadAgree / payloadBits) * 0.7,
      copies: Math.floor(available / payloadBits),
    };
  }

  return {
    embed: embed,
    extract: extract,
    capacityBytes: capacityBytes,
    _internal: { crc16: crc16, forwardDCT: forwardDCT, inverseDCT: inverseDCT, blockOrder: blockOrder },
  };
});
