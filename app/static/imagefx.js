/* Concealment that reads as a camera artifact.
 *
 * The order of operations is the whole point. A blur that merely smooths
 * pixels can sometimes be undone - deconvolution against a known kernel, or
 * for clean pixelation, brute-forcing candidate renderings until one matches
 * the block averages. So detail is destroyed FIRST, by collapsing the region
 * to a handful of samples and expanding it again, which is not invertible.
 * Only then is a plausible-looking defect synthesised on top of the ruins.
 *
 * The second half of the job is looking natural. Two things give a software
 * blur away to the eye: the boundary is too regular, and the blurred patch is
 * cleaner than everything around it - real sensor grain is absent exactly
 * where the smoothing happened. So edges are warped with noise, and grain is
 * measured from the untouched part of the image and put back afterwards.
 *
 * No DOM here, so it can be tested outside a browser.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.SUPLOAD = root.SUPLOAD || {}).imagefx = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function rng(seed) {
    var s = seed >>> 0 || 1;
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5;  s >>>= 0;
      return s / 4294967296;
    };
  }

  // --- value noise, for irregular mask edges -------------------------------

  function valueNoise(width, height, cell, seed) {
    var cols = Math.ceil(width / cell) + 2;
    var rows = Math.ceil(height / cell) + 2;
    var rand = rng(seed);
    var lattice = new Float32Array(cols * rows);
    for (var i = 0; i < lattice.length; i++) lattice[i] = rand();

    var out = new Float32Array(width * height);
    for (var y = 0; y < height; y++) {
      var gy = y / cell, y0 = Math.floor(gy), fy = gy - y0;
      var sy = fy * fy * (3 - 2 * fy);
      for (var x = 0; x < width; x++) {
        var gx = x / cell, x0 = Math.floor(gx), fx = gx - x0;
        var sx = fx * fx * (3 - 2 * fx);
        var a = lattice[y0 * cols + x0], b = lattice[y0 * cols + x0 + 1];
        var c = lattice[(y0 + 1) * cols + x0], d = lattice[(y0 + 1) * cols + x0 + 1];
        out[y * width + x] = (a + (b - a) * sx) + ((c + (d - c) * sx) - (a + (b - a) * sx)) * sy;
      }
    }
    return out;
  }

  // --- mask building -------------------------------------------------------

  function blurMask(mask, width, height, radius) {
    if (radius < 1) return mask;
    var tmp = new Float32Array(mask.length);
    var r = Math.round(radius), span = r * 2 + 1, x, y, i, sum;
    for (y = 0; y < height; y++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += mask[y * width + Math.min(width - 1, Math.max(0, i))];
      for (x = 0; x < width; x++) {
        tmp[y * width + x] = sum / span;
        var add = mask[y * width + Math.min(width - 1, x + r + 1)];
        var drop = mask[y * width + Math.max(0, x - r)];
        sum += add - drop;
      }
    }
    for (x = 0; x < width; x++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += tmp[Math.min(height - 1, Math.max(0, i)) * width + x];
      for (y = 0; y < height; y++) {
        mask[y * width + x] = sum / span;
        var add2 = tmp[Math.min(height - 1, y + r + 1) * width + x];
        var drop2 = tmp[Math.max(0, y - r) * width + x];
        sum += add2 - drop2;
      }
    }
    return mask;
  }

  /* Turn painted shapes into a soft, irregular mask.
   *
   * Two things have to be true at once, and they pull against each other.
   * The interior must be completely opaque - if feathering eats inward, a
   * small area never reaches full strength and the original shows straight
   * through, which is worse than useless because it looks concealed. And the
   * boundary must not be a clean geometric edge, because a perfect rectangle
   * of blur is the clearest possible sign that something was covered up.
   *
   * So the drawn shape is grown by the feather distance before rasterising,
   * putting the whole falloff OUTSIDE what the user painted, and the edge is
   * made irregular by varying the cut-off threshold with smooth noise rather
   * than by dimming pixels. Varying the threshold moves where the edge sits;
   * dimming would have made the middle see-through.
   */
  function buildMask(width, height, shapes, options) {
    options = options || {};
    var seed = options.seed || 1234;
    var feather = options.feather === undefined ? 14 : options.feather;
    var wobble = options.wobble === undefined ? 0.35 : options.wobble;
    // The edge is found by cutting a blurred ramp at a threshold that varies
    // with noise. How far the boundary can wander is set by how wide that ramp
    // is, so it is deliberately blurred wider than the final softness - cut
    // first, soften afterwards. Blurring to the final width and then cutting
    // leaves an edge that is still essentially straight.
    var spread = Math.max(4, Math.round(feather * 2));
    var grow = Math.ceil(spread + feather * 1.5);
    var mask = new Float32Array(width * height);
    var x, y, i;

    shapes.forEach(function (shape) {
      if (shape.type === "rect") {
        var x0 = Math.max(0, Math.min(shape.x0, shape.x1) - grow) | 0;
        var x1 = Math.min(width, Math.max(shape.x0, shape.x1) + grow) | 0;
        var y0 = Math.max(0, Math.min(shape.y0, shape.y1) - grow) | 0;
        var y1 = Math.min(height, Math.max(shape.y0, shape.y1) + grow) | 0;
        for (y = y0; y < y1; y++) {
          for (x = x0; x < x1; x++) mask[y * width + x] = 1;
        }
      } else if (shape.type === "brush") {
        var r = shape.radius + grow, r2 = r * r;
        var bx0 = Math.max(0, (shape.x - r) | 0), bx1 = Math.min(width, (shape.x + r + 1) | 0);
        var by0 = Math.max(0, (shape.y - r) | 0), by1 = Math.min(height, (shape.y + r + 1) | 0);
        for (var yy = by0; yy < by1; yy++) {
          for (var xx = bx0; xx < bx1; xx++) {
            var dx = xx - shape.x, dy = yy - shape.y;
            if (dx * dx + dy * dy <= r2) mask[yy * width + xx] = 1;
          }
        }
      }
    });

    if (wobble > 0) {
      blurMask(mask, width, height, spread);
      var noise = valueNoise(width, height, Math.max(8, options.noiseCell || 34), seed);
      for (i = 0; i < mask.length; i++) {
        var m = mask[i];
        if (m <= 0) continue;
        var cut = 0.15 + noise[i] * wobble * 1.4;    // where the edge falls
        var v = (m - cut) / (1 - cut);               // interior (m = 1) stays 1
        mask[i] = v <= 0 ? 0 : v >= 1 ? 1 : v;
      }
    }
    blurMask(mask, width, height, feather);
    return mask;
  }

  function maskBounds(mask, width, height, pad) {
    var x0 = width, y0 = height, x1 = 0, y1 = 0, found = false;
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        if (mask[y * width + x] > 0.004) {
          found = true;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
    }
    if (!found) return null;
    pad = pad || 0;
    return {
      x0: Math.max(0, x0 - pad), y0: Math.max(0, y0 - pad),
      x1: Math.min(width - 1, x1 + pad), y1: Math.min(height - 1, y1 + pad),
    };
  }

  // --- region buffers ------------------------------------------------------

  function readRegion(image, b) {
    var w = b.x1 - b.x0 + 1, h = b.y1 - b.y0 + 1;
    var out = new Float32Array(w * h * 3);
    for (var y = 0; y < h; y++) {
      var srow = ((b.y0 + y) * image.width + b.x0) * 4, drow = y * w * 3;
      for (var x = 0; x < w; x++) {
        out[drow + x * 3] = image.data[srow + x * 4];
        out[drow + x * 3 + 1] = image.data[srow + x * 4 + 1];
        out[drow + x * 3 + 2] = image.data[srow + x * 4 + 2];
      }
    }
    return { data: out, width: w, height: h };
  }

  /* Irreversible step. Collapsing to a few samples and expanding again throws
   * the information away rather than rearranging it, which is what stops the
   * result being recoverable by deconvolution or by brute-forcing candidates
   * against block averages. */
  function destroyDetail(region, samples) {
    var w = region.width, h = region.height, src = region.data;
    var cols = Math.max(1, Math.min(w, samples));
    var rows = Math.max(1, Math.round(cols * (h / w)) || 1);
    var small = new Float32Array(cols * rows * 3);
    var count = new Float32Array(cols * rows);

    var x, y, c;
    for (y = 0; y < h; y++) {
      var ry = Math.min(rows - 1, (y * rows / h) | 0);
      for (x = 0; x < w; x++) {
        var rx = Math.min(cols - 1, (x * cols / w) | 0);
        var di = (ry * cols + rx) * 3, si = (y * w + x) * 3;
        small[di] += src[si]; small[di + 1] += src[si + 1]; small[di + 2] += src[si + 2];
        count[ry * cols + rx]++;
      }
    }
    for (var i = 0; i < cols * rows; i++) {
      var n = count[i] || 1;
      small[i * 3] /= n; small[i * 3 + 1] /= n; small[i * 3 + 2] /= n;
    }

    for (y = 0; y < h; y++) {
      var fy = Math.max(0, Math.min(rows - 1, (y + 0.5) * rows / h - 0.5));
      var y0 = Math.floor(fy), y1 = Math.min(rows - 1, y0 + 1), ty = fy - y0;
      for (x = 0; x < w; x++) {
        var fx = Math.max(0, Math.min(cols - 1, (x + 0.5) * cols / w - 0.5));
        var x0 = Math.floor(fx), x1 = Math.min(cols - 1, x0 + 1), tx = fx - x0;
        for (c = 0; c < 3; c++) {
          var a = small[(y0 * cols + x0) * 3 + c], b2 = small[(y0 * cols + x1) * 3 + c];
          var d = small[(y1 * cols + x0) * 3 + c], e = small[(y1 * cols + x1) * 3 + c];
          var top = a + (b2 - a) * tx, bot = d + (e - d) * tx;
          src[(y * w + x) * 3 + c] = top + (bot - top) * ty;
        }
      }
    }
    return region;
  }

  /* Out-of-focus light forms a disk, not a bell curve - which is why real
   * bokeh has hard-edged circles of confusion and a Gaussian blur does not
   * look like a camera missing focus. */
  function diskBlur(region, radius) {
    if (radius < 1) return region;
    var w = region.width, h = region.height, src = region.data;
    var out = new Float32Array(src.length);
    var offsets = [];
    var r = Math.round(radius), r2 = radius * radius;
    for (var dy = -r; dy <= r; dy++) {
      for (var dx = -r; dx <= r; dx++) {
        if (dx * dx + dy * dy <= r2) offsets.push(dy * w + dx, dx, dy);
      }
    }
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        var sr = 0, sg = 0, sb = 0, n = 0;
        for (var i = 0; i < offsets.length; i += 3) {
          var nx = x + offsets[i + 1], ny = y + offsets[i + 2];
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          var si = (ny * w + nx) * 3;
          sr += src[si]; sg += src[si + 1]; sb += src[si + 2]; n++;
        }
        var di = (y * w + x) * 3;
        out[di] = sr / n; out[di + 1] = sg / n; out[di + 2] = sb / n;
      }
    }
    region.data = out;
    return region;
  }

  function motionBlur(region, length, angleDeg) {
    if (length < 2) return region;
    var w = region.width, h = region.height, src = region.data;
    var out = new Float32Array(src.length);
    var a = (angleDeg * Math.PI) / 180;
    var dx = Math.cos(a), dy = Math.sin(a);
    var steps = Math.max(2, Math.round(length));
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        var sr = 0, sg = 0, sb = 0, n = 0;
        for (var s = -steps / 2; s <= steps / 2; s++) {
          var nx = Math.round(x + dx * s), ny = Math.round(y + dy * s);
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          var si = (ny * w + nx) * 3;
          sr += src[si]; sg += src[si + 1]; sb += src[si + 2]; n++;
        }
        var di = (y * w + x) * 3;
        out[di] = sr / n; out[di + 1] = sg / n; out[di + 2] = sb / n;
      }
    }
    region.data = out;
    return region;
  }

  function glare(region, amount, seed) {
    var w = region.width, h = region.height, d = region.data;
    var cx = w / 2, cy = h / 2, maxR = Math.sqrt(cx * cx + cy * cy);
    var rand = rng(seed);
    var ox = (rand() - 0.5) * w * 0.3, oy = (rand() - 0.5) * h * 0.3;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        var dx = x - cx - ox, dy = y - cy - oy;
        var t = 1 - Math.min(1, Math.sqrt(dx * dx + dy * dy) / maxR);
        var k = Math.pow(t, 1.5) * amount;
        var i = (y * w + x) * 3;
        d[i] += (255 - d[i]) * k;
        d[i + 1] += (250 - d[i + 1]) * k;
        d[i + 2] += (235 - d[i + 2]) * k * 0.9;  // slightly warm, as real flare is
      }
    }
    return region;
  }

  function lowLight(region, amount) {
    var d = region.data;
    for (var i = 0; i < d.length; i += 3) {
      for (var c = 0; c < 3; c++) {
        var v = d[i + c];
        d[i + c] = v * (1 - amount * 0.45) + 26 * amount;  // lifted, flattened blacks
      }
    }
    return region;
  }

  // --- grain ---------------------------------------------------------------

  /* Measure the sensor noise the camera actually produced, sampling only
   * pixels the mask does not cover. Putting this back is what stops the
   * concealed patch being conspicuously cleaner than its surroundings. */
  function measureGrain(image, mask) {
    var w = image.width, h = image.height, data = image.data;
    var total = 0, n = 0;
    for (var y = 1; y < h - 1; y += 3) {
      for (var x = 1; x < w - 1; x += 3) {
        if (mask && mask[y * w + x] > 0.02) continue;
        var p = (y * w + x) * 4;
        for (var c = 0; c < 3; c++) {
          var centre = data[p + c];
          var avg = (data[p - 4 + c] + data[p + 4 + c] +
                     data[p - w * 4 + c] + data[p + w * 4 + c]) / 4;
          var diff = centre - avg;
          total += diff * diff; n++;
        }
      }
    }
    if (!n) return 2;
    return Math.min(14, Math.max(0.8, Math.sqrt(total / n) * 0.8));
  }

  function addGrain(region, sigma, chromaBoost, seed) {
    var d = region.data, rand = rng(seed);
    chromaBoost = chromaBoost || 1;
    for (var i = 0; i < d.length; i += 3) {
      var luma = (rand() + rand() + rand() - 1.5) * sigma;
      for (var c = 0; c < 3; c++) {
        var chroma = (rand() - 0.5) * sigma * 0.6 * chromaBoost;
        d[i + c] += luma + chroma;
      }
    }
    return region;
  }

  // --- the pipeline --------------------------------------------------------

  /* How few samples the region collapses to. This must be an absolute count,
   * not a fraction of the area: an early version used a fixed number of
   * columns, which meant a small redaction kept one sample every few pixels
   * and left the content partly legible, while a large one was destroyed
   * properly. Small areas are exactly where people put the things that matter
   * - a name, a number, a face - so the floor is what counts. */
  function sampleCount(region, strength) {
    var byStrength = Math.max(2, Math.round(7 - strength * 0.35));
    var byArea = Math.max(2, Math.floor(region.width / 8));
    return Math.min(byStrength, byArea);
  }

  var STYLES = {
    defocus: function (region, strength, opts) {
      destroyDetail(region, sampleCount(region, strength));
      diskBlur(region, Math.max(2, Math.round(strength * 0.9)));
      return region;
    },
    motion: function (region, strength, opts) {
      destroyDetail(region, sampleCount(region, strength));
      motionBlur(region, strength * 2.2, opts.angle);
      diskBlur(region, Math.max(1, strength * 0.25));
      return region;
    },
    glare: function (region, strength, opts) {
      destroyDetail(region, sampleCount(region, strength));
      diskBlur(region, Math.max(2, strength * 0.6));
      glare(region, Math.min(0.92, 0.3 + strength * 0.05), opts.seed);
      return region;
    },
    lowlight: function (region, strength, opts) {
      destroyDetail(region, sampleCount(region, strength));
      diskBlur(region, Math.max(2, strength * 0.5));
      lowLight(region, Math.min(1, 0.35 + strength * 0.05));
      return region;
    },
  };

  function conceal(image, mask, options) {
    options = options || {};
    var style = STYLES[options.style] ? options.style : "defocus";
    var strength = options.strength === undefined ? 8 : options.strength;
    var seed = options.seed || 4321;
    var angle = options.angle === undefined ? 12 : options.angle;

    var bounds = maskBounds(mask, image.width, image.height, Math.ceil(strength * 3));
    if (!bounds) return image;

    var grain = options.grain === undefined ? measureGrain(image, mask) : options.grain;
    var region = readRegion(image, bounds);

    STYLES[style](region, strength, { angle: angle, seed: seed });

    // Grain last: a smoothed patch with no grain in a grainy photo is the
    // giveaway, both to the eye and to anyone checking noise statistics.
    addGrain(region, grain, style === "lowlight" ? 2.2 : 1, seed + 7);

    var w = region.width;
    for (var y = bounds.y0; y <= bounds.y1; y++) {
      for (var x = bounds.x0; x <= bounds.x1; x++) {
        var m = mask[y * image.width + x];
        if (m <= 0) continue;
        var si = ((y - bounds.y0) * w + (x - bounds.x0)) * 3;
        var di = (y * image.width + x) * 4;
        for (var c = 0; c < 3; c++) {
          var v = image.data[di + c] * (1 - m) + region.data[si + c] * m;
          image.data[di + c] = v < 0 ? 0 : v > 255 ? 255 : v;
        }
      }
    }
    return image;
  }

  /* Optional finishing pass over the whole frame. One soft patch in an
   * otherwise crisp photo still draws the eye, so a little overall softness
   * and grain makes the concealed areas stop being the odd one out. */
  function softenAll(image, amount, seed) {
    if (amount <= 0) return image;
    var b = { x0: 0, y0: 0, x1: image.width - 1, y1: image.height - 1 };
    var region = readRegion(image, b);
    diskBlur(region, Math.max(1, amount * 1.6));
    addGrain(region, amount * 1.1, 1, seed || 99);
    var blend = Math.min(0.85, amount * 0.22);
    for (var i = 0, p = 0; i < region.data.length; i += 3, p += 4) {
      for (var c = 0; c < 3; c++) {
        var v = image.data[p + c] * (1 - blend) + region.data[i + c] * blend;
        image.data[p + c] = v < 0 ? 0 : v > 255 ? 255 : v;
      }
    }
    return image;
  }

  return {
    buildMask: buildMask,
    maskBounds: maskBounds,
    conceal: conceal,
    softenAll: softenAll,
    measureGrain: measureGrain,
    styles: Object.keys(STYLES),
    _internal: { valueNoise: valueNoise, destroyDetail: destroyDetail, readRegion: readRegion },
  };
});
