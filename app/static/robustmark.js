/* A second, tougher watermark layer: a short ID that survives cropping and
 * moderate downscaling.
 *
 * The block-DCT mark in stego.js carries a whole sentence but is tied to an
 * 8x8 grid anchored at pixel (0,0), so a crop misaligns it and a resize
 * destroys the frequencies it lives in. This one trades capacity for
 * robustness: 48 bits instead of hundreds of characters, but it comes back
 * after edits that wipe the other one out.
 *
 * How it survives a crop: the pattern is TILED. One 128x128 tile repeats
 * across the whole image, so any crop still contains many complete copies.
 * Detection folds the entire image down onto a single tile - every repeat
 * adds together, which reinforces the mark while the picture underneath,
 * being unrelated to the tile period, averages towards flat. With a couple of
 * hundred tiles that is a large gain for free. A crop then shows up only as
 * an unknown offset, and folding a periodic signal at the wrong offset just
 * returns the tile circularly shifted - so a known sync pattern recovers it
 * with one cross-correlation.
 *
 * How it survives a resize: a downscale by s turns the tile period into
 * 128*s, so detection tries a range of periods and keeps whichever produces
 * the sharpest sync peak. The carrier itself is band-limited to 4-24 cycles
 * per tile - high enough to stay invisible, low enough that halving the
 * image keeps it above the new Nyquist limit.
 *
 * What it will not survive: rotation (that needs Fourier-Mellin embedding),
 * or a downscale far past 2x, where the carrier frequencies stop existing in
 * the file at all and no decoder can bring them back.
 *
 * No DOM here, so it can be tested outside a browser.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.SUPLOAD = root.SUPLOAD || {}).robustmark = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var TILE = 128;
  var ID_BITS = 48;
  var CRC_BITS = 16;
  var TOTAL_BITS = ID_BITS + CRC_BITS;   // 64 carriers, plus one for sync
  var BAND_LOW = 4;                      // cycles per tile
  var BAND_HIGH = 24;
  var PATTERN_SEED = 0x51ead17e;

  // --- small complex FFT (radix-2, power of two only) ----------------------

  function fft1d(re, im, n, inverse) {
    for (var i = 1, j = 0; i < n; i++) {
      var bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        var tr = re[i]; re[i] = re[j]; re[j] = tr;
        var ti = im[i]; im[i] = im[j]; im[j] = ti;
      }
    }
    for (var len = 2; len <= n; len <<= 1) {
      var ang = (inverse ? 2 : -2) * Math.PI / len;
      var wr = Math.cos(ang), wi = Math.sin(ang);
      for (var start = 0; start < n; start += len) {
        var cr = 1, ci = 0;
        for (var k = 0; k < len / 2; k++) {
          var a = start + k, b = a + len / 2;
          var xr = re[b] * cr - im[b] * ci;
          var xi = re[b] * ci + im[b] * cr;
          re[b] = re[a] - xr; im[b] = im[a] - xi;
          re[a] += xr; im[a] += xi;
          var ncr = cr * wr - ci * wi;
          ci = cr * wi + ci * wr; cr = ncr;
        }
      }
    }
    if (inverse) for (var m = 0; m < n; m++) { re[m] /= n; im[m] /= n; }
  }

  function fft2d(re, im, n, inverse) {
    var rowR = new Float64Array(n), rowI = new Float64Array(n);
    var y, x;
    for (y = 0; y < n; y++) {
      for (x = 0; x < n; x++) { rowR[x] = re[y * n + x]; rowI[x] = im[y * n + x]; }
      fft1d(rowR, rowI, n, inverse);
      for (x = 0; x < n; x++) { re[y * n + x] = rowR[x]; im[y * n + x] = rowI[x]; }
    }
    for (x = 0; x < n; x++) {
      for (y = 0; y < n; y++) { rowR[y] = re[y * n + x]; rowI[y] = im[y * n + x]; }
      fft1d(rowR, rowI, n, inverse);
      for (y = 0; y < n; y++) { re[y * n + x] = rowR[y]; im[y * n + x] = rowI[y]; }
    }
  }

  // --- carriers ------------------------------------------------------------

  function rng(seed) {
    var s = seed >>> 0 || 1;
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5;  s >>>= 0;
      return s / 4294967296;
    };
  }

  /* One carrier per bit: white noise with everything outside the usable
   * frequency band removed. Band-limiting is what buys both invisibility
   * (nothing slow enough to read as a blotch) and resize survival (nothing
   * fine enough to fall off the end when the image is halved). */
  function makeCarrier(index) {
    var n = TILE, i;
    var re = new Float64Array(n * n), im = new Float64Array(n * n);
    var rand = rng(PATTERN_SEED + index * 2654435761);
    for (i = 0; i < n * n; i++) re[i] = rand() * 2 - 1;

    fft2d(re, im, n, false);
    for (var v = 0; v < n; v++) {
      var fy = v > n / 2 ? v - n : v;
      for (var u = 0; u < n; u++) {
        var fx = u > n / 2 ? u - n : u;
        var f = Math.sqrt(fx * fx + fy * fy);
        if (f < BAND_LOW || f > BAND_HIGH) { re[v * n + u] = 0; im[v * n + u] = 0; }
      }
    }
    fft2d(re, im, n, true);

    var out = new Float32Array(n * n), mean = 0;
    for (i = 0; i < n * n; i++) mean += re[i];
    mean /= n * n;
    var energy = 0;
    for (i = 0; i < n * n; i++) { out[i] = re[i] - mean; energy += out[i] * out[i]; }
    var rms = Math.sqrt(energy / (n * n)) || 1;
    for (i = 0; i < n * n; i++) out[i] /= rms;
    return out;
  }

  var carriers = null;
  function getCarriers() {
    if (!carriers) {
      carriers = [];
      for (var i = 0; i <= TOTAL_BITS; i++) carriers.push(makeCarrier(i));
    }
    return carriers;
  }
  var SYNC = TOTAL_BITS; // the last carrier is always added with +1

  // --- payload -------------------------------------------------------------

  function crc16(bits) {
    var bytes = [];
    for (var i = 0; i < bits.length; i += 8) {
      var b = 0;
      for (var k = 0; k < 8; k++) b = (b << 1) | (bits[i + k] || 0);
      bytes.push(b);
    }
    var crc = 0xffff;
    for (var j = 0; j < bytes.length; j++) {
      crc ^= bytes[j] << 8;
      for (var m = 0; m < 8; m++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
    return crc;
  }

  function idToBits(hex) {
    if (!/^[0-9a-f]{12}$/i.test(hex)) throw new Error("ID must be 12 hex characters.");
    var bits = new Uint8Array(TOTAL_BITS);
    for (var i = 0; i < ID_BITS; i++) {
      var nibble = parseInt(hex[Math.floor(i / 4)], 16);
      bits[i] = (nibble >> (3 - (i % 4))) & 1;
    }
    var crc = crc16(bits.subarray(0, ID_BITS));
    for (var c = 0; c < CRC_BITS; c++) bits[ID_BITS + c] = (crc >> (CRC_BITS - 1 - c)) & 1;
    return bits;
  }

  function bitsToId(bits) {
    var hex = "";
    for (var i = 0; i < ID_BITS; i += 4) {
      var nibble = 0;
      for (var k = 0; k < 4; k++) nibble = (nibble << 1) | bits[i + k];
      hex += nibble.toString(16);
    }
    var crc = 0;
    for (var c = 0; c < CRC_BITS; c++) crc = (crc << 1) | bits[ID_BITS + c];
    return crc === crc16(bits.subarray(0, ID_BITS)) ? hex : null;
  }

  function randomId() {
    var hex = "";
    var bytes = new Uint8Array(6);
    if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (var i = 0; i < 6; i++) bytes[i] = Math.floor(Math.random() * 256);
    for (var j = 0; j < 6; j++) hex += ("0" + bytes[j].toString(16)).slice(-2);
    return hex;
  }

  // --- luma ----------------------------------------------------------------

  function luma(data, width, height) {
    var y = new Float32Array(width * height);
    for (var i = 0, p = 0; i < y.length; i++, p += 4) {
      y[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
    }
    return y;
  }

  function boxBlur(src, width, height, radius) {
    var tmp = new Float32Array(src.length), out = new Float32Array(src.length);
    var r = Math.max(1, Math.round(radius)), span = r * 2 + 1, x, y, i, sum;
    for (y = 0; y < height; y++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += src[y * width + Math.min(width - 1, Math.max(0, i))];
      for (x = 0; x < width; x++) {
        tmp[y * width + x] = sum / span;
        sum += src[y * width + Math.min(width - 1, x + r + 1)] - src[y * width + Math.max(0, x - r)];
      }
    }
    for (x = 0; x < width; x++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += tmp[Math.min(height - 1, Math.max(0, i)) * width + x];
      for (y = 0; y < height; y++) {
        out[y * width + x] = sum / span;
        sum += tmp[Math.min(height - 1, y + r + 1) * width + x] - tmp[Math.max(0, y - r) * width + x];
      }
    }
    return out;
  }

  // --- embed ---------------------------------------------------------------

  function embed(image, idHex, options) {
    options = options || {};
    // 1.6 measured: the quietest setting (45.9 dB) that still survives the
    // compound attacks - crop then JPEG, downscale then JPEG. 1.2 is prettier
    // but drops the mark on both of those; above 1.6 buys nothing and only
    // costs visibility.
    var strength = options.strength === undefined ? 1.6 : options.strength;
    var width = image.width, height = image.height, data = image.data;
    if (width < TILE || height < TILE) throw new Error("Image is too small to carry a robust mark.");

    var bits = idToBits(idHex);
    var c = getCarriers();

    var tile = new Float32Array(TILE * TILE);
    for (var b = 0; b < TOTAL_BITS; b++) {
      var sign = bits[b] ? 1 : -1;
      var carrier = c[b];
      for (var i = 0; i < tile.length; i++) tile[i] += sign * carrier[i];
    }
    for (i = 0; i < tile.length; i++) tile[i] += c[SYNC][i] * 1.5; // sync rides louder

    var rms = 0;
    for (i = 0; i < tile.length; i++) rms += tile[i] * tile[i];
    rms = Math.sqrt(rms / tile.length) || 1;
    for (i = 0; i < tile.length; i++) tile[i] /= rms;

    // Busy areas hide more than flat ones, so push harder where detail is.
    var y0 = luma(data, width, height);
    var smooth = boxBlur(y0, width, height, 3);

    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        var idx = y * width + x;
        var activity = Math.abs(y0[idx] - smooth[idx]);
        var gain = strength * (0.7 + Math.min(1.8, activity / 9));
        var delta = tile[(y % TILE) * TILE + (x % TILE)] * gain;
        var p = idx * 4;
        for (var ch = 0; ch < 3; ch++) {
          var v = data[p + ch] + delta;
          data[p + ch] = v < 0 ? 0 : v > 255 ? 255 : v;
        }
      }
    }
    return image;
  }

  // --- detect --------------------------------------------------------------

  /* Fold the image onto one tile of the given period. Every repeat of the
   * pattern lands on the same cell and adds; the picture underneath does not
   * share that period, so it averages towards flat. */
  function fold(high, width, height, period) {
    var acc = new Float64Array(period * period);
    var count = new Float64Array(period * period);
    for (var y = 0; y < height; y++) {
      var row = (y % period) * period;
      for (var x = 0; x < width; x++) {
        var cell = row + (x % period);
        acc[cell] += high[y * width + x];
        count[cell]++;
      }
    }
    for (var i = 0; i < acc.length; i++) if (count[i]) acc[i] /= count[i];
    return acc;
  }

  function resampleTo(src, from, to) {
    var out = new Float64Array(to * to);
    for (var y = 0; y < to; y++) {
      var sy = (y + 0.5) * from / to - 0.5;
      var y0 = Math.floor(sy), ty = sy - y0;
      var ya = ((y0 % from) + from) % from, yb = ((y0 + 1) % from + from) % from;
      for (var x = 0; x < to; x++) {
        var sx = (x + 0.5) * from / to - 0.5;
        var x0 = Math.floor(sx), tx = sx - x0;
        var xa = ((x0 % from) + from) % from, xb = ((x0 + 1) % from + from) % from;
        var top = src[ya * from + xa] * (1 - tx) + src[ya * from + xb] * tx;
        var bot = src[yb * from + xa] * (1 - tx) + src[yb * from + xb] * tx;
        out[y * to + x] = top * (1 - ty) + bot * ty;
      }
    }
    return out;
  }

  /* Circular cross-correlation via FFT. A crop shifts the folded tile
   * cyclically, and the peak of this surface says by how much. */
  function correlate(acc, carrier) {
    var n = TILE, i;
    var ar = new Float64Array(n * n), ai = new Float64Array(n * n);
    var br = new Float64Array(n * n), bi = new Float64Array(n * n);
    for (i = 0; i < n * n; i++) { ar[i] = acc[i]; br[i] = carrier[i]; }
    fft2d(ar, ai, n, false);
    fft2d(br, bi, n, false);
    for (i = 0; i < n * n; i++) {
      var re = ar[i] * br[i] + ai[i] * bi[i];   // conjugate of b
      var im = ai[i] * br[i] - ar[i] * bi[i];
      ar[i] = re; ai[i] = im;
    }
    fft2d(ar, ai, n, true);

    var best = -Infinity, at = 0, sum = 0, sumSq = 0;
    for (i = 0; i < n * n; i++) {
      var v = ar[i];
      sum += v; sumSq += v * v;
      if (v > best) { best = v; at = i; }
    }
    var mean = sum / (n * n);
    var sd = Math.sqrt(Math.max(1e-12, sumSq / (n * n) - mean * mean));
    return { peak: best, score: (best - mean) / sd, dx: at % n, dy: Math.floor(at / n) };
  }

  function shiftTile(acc, dx, dy) {
    var n = TILE, out = new Float64Array(n * n);
    for (var y = 0; y < n; y++) {
      for (var x = 0; x < n; x++) {
        out[y * n + x] = acc[(((y + dy) % n) + n) % n * n + ((((x + dx) % n) + n) % n)];
      }
    }
    return out;
  }

  function extract(image, options) {
    options = options || {};
    if (image.width < TILE / 2 || image.height < TILE / 2) return null;

    /* Detection folds the whole image once per candidate period, so cost
     * grows with area - a 12MP photo across 120 periods would take most of a
     * minute. It does not need the whole photo: the pattern repeats, so a
     * window a few hundred tiles wide carries the same signal. Taking a
     * centred window bounds detection time no matter how large the input,
     * and costs nothing, because surviving a crop is the entire point. */
    var cap = options.detectCap || 1536;
    var width = Math.min(image.width, cap);
    var height = Math.min(image.height, cap);
    var offX = (image.width - width) >> 1;
    var offY = (image.height - height) >> 1;

    var view = image.data;
    if (width !== image.width || height !== image.height) {
      view = new Uint8ClampedArray(width * height * 4);
      for (var vy = 0; vy < height; vy++) {
        var src = ((vy + offY) * image.width + offX) * 4;
        view.set(image.data.subarray(src, src + width * 4), vy * width * 4);
      }
    }

    var y0 = luma(view, width, height);

    // Strip the picture, keep the band the carrier lives in.
    var low = boxBlur(y0, width, height, Math.round(TILE / 6));
    var high = new Float32Array(y0.length);
    for (var i = 0; i < y0.length; i++) high[i] = y0[i] - low[i];

    // The tile period scales with the image, so search for it.
    var minPeriod = options.minPeriod || 48;
    var maxPeriod = options.maxPeriod || 168;
    maxPeriod = Math.min(maxPeriod, Math.min(width, height));
    var c = getCarriers();

    var best = null;
    for (var period = minPeriod; period <= maxPeriod; period++) {
      var acc = fold(high, width, height, period);
      var norm = period === TILE ? acc : resampleTo(acc, period, TILE);
      var hit = correlate(norm, c[SYNC]);
      if (!best || hit.score > best.score) {
        best = { score: hit.score, period: period, dx: hit.dx, dy: hit.dy, acc: norm };
      }
    }
    if (!best || best.score < (options.minScore || 4)) return null;

    var aligned = shiftTile(best.acc, best.dx, best.dy);
    var bits = new Uint8Array(TOTAL_BITS);
    var magnitudes = [];
    for (var b = 0; b < TOTAL_BITS; b++) {
      var carrier = c[b], dot = 0;
      for (var k = 0; k < TILE * TILE; k++) dot += aligned[k] * carrier[k];
      bits[b] = dot > 0 ? 1 : 0;
      magnitudes.push(Math.abs(dot));
    }

    var id = bitsToId(bits);
    if (!id) return null;

    magnitudes.sort(function (p, q) { return p - q; });
    return {
      id: id,
      sync: best.score,
      period: best.period,
      scale: best.period / TILE,
      margin: magnitudes[0] / (magnitudes[magnitudes.length >> 1] || 1),
    };
  }

  return {
    embed: embed,
    extract: extract,
    randomId: randomId,
    TILE: TILE,
    ID_BITS: ID_BITS,
    _internal: { makeCarrier: makeCarrier, fft2d: fft2d, idToBits: idToBits, bitsToId: bitsToId },
  };
});
