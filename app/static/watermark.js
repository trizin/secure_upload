/* Watermark & conceal - all of it in the browser.
 *
 * Nothing here contacts the network except the explicit "Upload to supload"
 * button. That is deliberate: this page exists to handle passports, bank
 * letters and the like, and sending those to a server to be processed would
 * defeat the point of the site.
 *
 * Editing happens against a downscaled copy so dragging stays responsive;
 * the full-resolution image is only processed once, on save.
 */
(function () {
  "use strict";

  var stego = window.SUPLOAD.stego;
  var robust = window.SUPLOAD.robustmark;
  var fx = window.SUPLOAD.imagefx;

  var PREVIEW_MAX = 1100;

  var state = {
    source: null,       // full-resolution ImageData
    preview: null,      // downscaled ImageData used while editing
    scale: 1,           // preview / source
    shapes: [],         // in SOURCE coordinates
    markArea: null,     // visible mark confined to this rect, also SOURCE coords
    tool: "rect",
    drag: null,
    name: "image",
  };

  var $ = function (id) { return document.getElementById(id); };
  var canvas = $("wmcanvas");
  var ctx = canvas.getContext("2d", { willReadFrequently: true });
  var work = document.createElement("canvas");
  var workCtx = work.getContext("2d", { willReadFrequently: true });

  // --- loading -------------------------------------------------------------

  function loadFile(file) {
    if (!file || !/^image\//.test(file.type)) {
      note("That is not an image.");
      return;
    }
    state.name = (file.name || "image").replace(/\.[^.]+$/, "");
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      adopt(img);
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      note("That image could not be opened.");
    };
    img.src = url;
  }

  function adopt(img) {
    work.width = img.naturalWidth;
    work.height = img.naturalHeight;
    workCtx.drawImage(img, 0, 0);
    state.source = workCtx.getImageData(0, 0, work.width, work.height);

    var longest = Math.max(work.width, work.height);
    state.scale = longest > PREVIEW_MAX ? PREVIEW_MAX / longest : 1;
    var pw = Math.max(1, Math.round(work.width * state.scale));
    var ph = Math.max(1, Math.round(work.height * state.scale));

    var tmp = document.createElement("canvas");
    tmp.width = pw; tmp.height = ph;
    var tctx = tmp.getContext("2d");
    tctx.imageSmoothingQuality = "high";
    tctx.drawImage(img, 0, 0, pw, ph);
    state.preview = tctx.getImageData(0, 0, pw, ph);

    state.shapes = [];
    state.markArea = null;
    updateAreaLabel();
    $("wmdrop").hidden = true;
    $("wmstage").hidden = false;
    $("wminfo").textContent =
      work.width + " x " + work.height + " px" +
      (state.scale < 1 ? " (preview scaled to fit; saving uses full resolution)" : "");
    updateCapacity();
    render();
  }

  // --- the pipeline --------------------------------------------------------

  function autoFeather(width, height) {
    var manual = +$("wmFeather").value;
    if (manual > 0) return manual;
    return Math.max(5, Math.min(26, Math.round(Math.min(width, height) * 0.012)));
  }

  function groupShapes() {
    // One mask per distinct look, so different areas can be concealed
    // differently without each one paying for its own blur pass.
    var groups = {};
    state.shapes.forEach(function (s) {
      var key = s.style + "|" + s.strength;
      (groups[key] = groups[key] || { style: s.style, strength: s.strength, items: [] })
        .items.push(s);
    });
    return Object.keys(groups).map(function (k) { return groups[k]; });
  }

  function scaleShape(s, k) {
    if (s.type === "rect") {
      return { type: "rect", x0: s.x0 * k, y0: s.y0 * k, x1: s.x1 * k, y1: s.y1 * k };
    }
    return { type: "brush", x: s.x * k, y: s.y * k, radius: Math.max(1, s.radius * k) };
  }

  function applyConceal(image, k) {
    var feather = autoFeather(image.width, image.height);
    var angle = 12;
    groupShapes().forEach(function (group, i) {
      var mask = fx.buildMask(image.width, image.height,
        group.items.map(function (s) { return scaleShape(s, k); }),
        { feather: feather, seed: 1000 + i * 37, wobble: 0.35 });
      fx.conceal(image, mask, {
        style: group.style,
        strength: group.strength * Math.max(0.35, k),
        seed: 500 + i * 91,
        angle: angle,
      });
    });
  }

  function drawVisibleMark(c, width, height, k) {
    if (!$("wmVisible").checked) return;
    var text = $("wmText").value.trim();
    if (!text) return;

    var size = (+$("wmSize").value / 100) * Math.min(width, height);
    var gap = +$("wmGap").value / 10;
    var angle = (+$("wmAngle").value * Math.PI) / 180;
    var dark = $("wmDark").checked;

    c.save();

    /* Confining the mark is a clip, but the tiling is also re-centred on the
     * area. Clipping a tiling centred on the whole image would, for a small
     * box, show whatever fragment of a word happened to fall inside it. */
    var centreX = width / 2, centreY = height / 2;
    var reach = Math.hypot(width, height);
    var area = state.markArea;
    if (area) {
      var ax0 = Math.max(0, Math.min(area.x0, area.x1) * k);
      var ax1 = Math.min(width, Math.max(area.x0, area.x1) * k);
      var ay0 = Math.max(0, Math.min(area.y0, area.y1) * k);
      var ay1 = Math.min(height, Math.max(area.y0, area.y1) * k);
      if (ax1 - ax0 < 2 || ay1 - ay0 < 2) { c.restore(); return; }
      c.beginPath();
      c.rect(ax0, ay0, ax1 - ax0, ay1 - ay0);
      c.clip();
      centreX = (ax0 + ax1) / 2;
      centreY = (ay0 + ay1) / 2;
      // Only cover the clipped box, not the whole canvas - otherwise a small
      // area still costs thousands of invisible draw calls.
      reach = Math.hypot(ax1 - ax0, ay1 - ay0);
    }

    c.globalAlpha = +$("wmOpacity").value / 100;
    c.font = "600 " + size.toFixed(1) + "px ui-sans-serif, system-ui, sans-serif";
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillStyle = dark ? "#000" : "#fff";
    // A faint outline in the opposite colour keeps the mark readable over
    // both dark and light parts of a photo.
    c.strokeStyle = dark ? "rgba(255,255,255,.35)" : "rgba(0,0,0,.35)";
    c.lineWidth = Math.max(1, size * 0.045);

    var stepX = c.measureText(text).width * gap * 0.5 + size;
    var stepY = size * gap;

    c.translate(centreX, centreY);
    c.rotate(angle);
    for (var y = -reach; y <= reach; y += stepY) {
      var offset = (Math.round(y / stepY) % 2) * stepX * 0.5;
      for (var x = -reach; x <= reach; x += stepX) {
        c.strokeText(text, x + offset, y);
        c.fillText(text, x + offset, y);
      }
    }
    c.restore();
  }

  function compose(base, k, forExport) {
    var image = {
      data: new Uint8ClampedArray(base.data),
      width: base.width,
      height: base.height,
    };

    applyConceal(image, k);

    var soften = +$("wmSoften").value;
    if (soften > 0) fx.softenAll(image, soften * Math.max(0.35, k), 99);

    var out = forExport ? work : canvas;
    out.width = image.width;
    out.height = image.height;
    var c = out.getContext("2d", { willReadFrequently: true });
    c.putImageData(new ImageData(image.data, image.width, image.height), 0, 0);

    drawVisibleMark(c, image.width, image.height, k);

    // Both hidden layers go on at the end, over the finished picture -
    // anything applied afterwards would disturb them. The robust ID is added
    // first and the message second, which is the order proven to leave both
    // readable.
    if (forExport && $("wmRobust").checked) {
      var ident = $("wmRobustId").value.trim().toLowerCase();
      if (/^[0-9a-f]{12}$/.test(ident)) {
        var rid = c.getImageData(0, 0, image.width, image.height);
        try {
          robust.embed(rid, ident);
          c.putImageData(rid, 0, 0);
        } catch (e) {
          note(e.message);
        }
      }
    }

    if (forExport && $("wmStego").checked) {
      var message = $("wmStegoText").value.trim();
      if (message) {
        var id = c.getImageData(0, 0, image.width, image.height);
        try {
          stego.embed(id, message, { strength: +$("wmStrength").value });
          c.putImageData(id, 0, 0);
        } catch (e) {
          note(e.message);
          return out;
        }
      }
    }
    return out;
  }

  var pending = null;
  function render() {
    if (!state.preview) return;
    if (pending) cancelAnimationFrame(pending);
    pending = requestAnimationFrame(function () {
      pending = null;
      compose(state.preview, state.scale, false);
      drawOverlay();
    });
  }

  // While dragging, show the outline only - recomposing on every pointer
  // move would make a large image crawl.
  function drawOverlay() {
    var c = canvas.getContext("2d");
    var k = state.scale;

    // The committed mark area stays outlined, so it is clear the visible mark
    // is confined rather than mysteriously missing elsewhere.
    if (state.markArea && !(state.drag && state.drag.tool === "mark")) {
      var a = state.markArea;
      c.save();
      c.strokeStyle = "rgba(120,220,170,.9)";
      c.lineWidth = 1.5;
      c.setLineDash([2, 5]);
      c.strokeRect(Math.min(a.x0, a.x1) * k, Math.min(a.y0, a.y1) * k,
        Math.abs(a.x1 - a.x0) * k, Math.abs(a.y1 - a.y0) * k);
      c.restore();
    }

    if (!state.drag) return;
    c.save();
    c.strokeStyle = "rgba(255,255,255,.95)";
    c.lineWidth = 1.5;
    c.setLineDash([5, 4]);
    var d = state.drag;
    if (d.type === "rect") {
      c.strokeRect(Math.min(d.x0, d.x1) * k, Math.min(d.y0, d.y1) * k,
        Math.abs(d.x1 - d.x0) * k, Math.abs(d.y1 - d.y0) * k);
    }
    c.restore();
  }

  // --- interaction ---------------------------------------------------------

  function toImageCoords(event) {
    var rect = canvas.getBoundingClientRect();
    var x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    var y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    return { x: x / state.scale, y: y / state.scale };
  }

  function currentLook() {
    return { style: $("wmStyle").value, strength: +$("wmFx").value };
  }

  canvas.addEventListener("pointerdown", function (e) {
    if (!state.preview) return;
    canvas.setPointerCapture(e.pointerId);
    var p = toImageCoords(e);
    if (state.tool === "rect" || state.tool === "mark") {
      state.drag = { type: "rect", tool: state.tool, x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    } else {
      state.drag = { type: "brush", last: null };
      brushAt(p);
    }
  });

  canvas.addEventListener("pointermove", function (e) {
    if (!state.drag) return;
    var p = toImageCoords(e);
    if (state.drag.type === "rect") {
      state.drag.x1 = p.x; state.drag.y1 = p.y;
      compose(state.preview, state.scale, false);
      drawOverlay();
    } else {
      brushAt(p);
    }
  });

  function brushAt(p) {
    var radius = +$("wmBrush").value / 2 / state.scale;
    var last = state.drag.last;
    if (last && Math.hypot(p.x - last.x, p.y - last.y) < radius * 0.4) return;
    state.drag.last = p;
    var look = currentLook();
    state.shapes.push({
      type: "brush", x: p.x, y: p.y, radius: radius,
      style: look.style, strength: look.strength,
    });
    render();
  }

  canvas.addEventListener("pointerup", function () {
    if (!state.drag) return;
    if (state.drag.type === "rect") {
      var d = state.drag;
      var bigEnough = Math.abs(d.x1 - d.x0) > 3 && Math.abs(d.y1 - d.y0) > 3;
      if (bigEnough && d.tool === "mark") {
        state.markArea = { x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1 };
        updateAreaLabel();
      } else if (bigEnough) {
        var look = currentLook();
        state.shapes.push({
          type: "rect", x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1,
          style: look.style, strength: look.strength,
        });
      }
    }
    state.drag = null;
    render();
  });

  // --- saving --------------------------------------------------------------

  function exportBlob(callback) {
    note("Working at full resolution…");
    setTimeout(function () {
      var out = compose(state.source, 1, true);
      var type = $("wmFormat").value;
      var quality = +$("wmQuality").value / 100;
      out.toBlob(function (blob) {
        note(blob ? "" : "Could not encode the image.");
        if (blob) callback(blob);
      }, type, type === "image/jpeg" ? quality : undefined);
    }, 20);
  }

  function note(message) { $("wmSaveNote").textContent = message; }

  $("wmSave").addEventListener("click", function () {
    if (!state.source) return;
    exportBlob(function (blob) {
      var ext = $("wmFormat").value === "image/png" ? "png" : "jpg";
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = state.name + "-marked." + ext;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      note("Saved.");
    });
  });

  // --- reading a hidden mark ----------------------------------------------

  $("wmCheck").addEventListener("change", function (e) {
    var file = e.target.files[0];
    if (!file) return;
    var out = $("wmCheckOut");
    out.textContent = "Reading…";
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      var c = document.createElement("canvas");
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      var cc = c.getContext("2d", { willReadFrequently: true });
      cc.drawImage(img, 0, 0);
      var pixels = cc.getImageData(0, 0, c.width, c.height);
      var lines = [];

      var found = stego.extract(pixels);
      if (found) {
        lines.push('Message: "' + found.text + '" (' +
          Math.round(found.confidence * 100) + "% agreement across " +
          found.copies + " copies)");
      }

      var ident = robust.extract(pixels);
      if (ident) {
        var scaled = Math.abs(ident.scale - 1) > 0.03
          ? " - this copy was resized to about " + Math.round(ident.scale * 100) + "%"
          : "";
        lines.push("Robust ID: " + ident.id + scaled);
      }

      out.textContent = lines.length
        ? lines.join("  |  ")
        : "No hidden mark found. It may never have had one, or it was cropped, " +
          "resized or compressed too hard to survive.";
    };
    img.onerror = function () { URL.revokeObjectURL(url); out.textContent = "Could not read that image."; };
    img.src = url;
  });

  // --- wiring --------------------------------------------------------------

  function updateAreaLabel() {
    var a = state.markArea;
    $("wmAreaOut").textContent = a
      ? "Visible mark confined to " + Math.round(Math.abs(a.x1 - a.x0)) + " x " +
        Math.round(Math.abs(a.y1 - a.y0)) + " px."
      : "Covering the whole image.";
  }

  $("wmMarkAll").addEventListener("click", function () {
    state.markArea = null;
    updateAreaLabel();
    render();
  });

  $("wmNewId").addEventListener("click", function () {
    $("wmRobustId").value = robust.randomId();
  });
  $("wmRobustId").value = robust.randomId();

  function updateCapacity() {
    if (!state.source) return;
    var bytes = stego.capacityBytes(state.source.width, state.source.height);
    $("wmCapacity").textContent = bytes
      ? "Room for about " + bytes + " characters at this size."
      : "This image is too small to hold a hidden mark.";
  }

  var LIVE = {
    wmSize: function (v) { return v + "%"; },
    wmOpacity: function (v) { return v + "%"; },
    wmAngle: function (v) { return v + "°"; },
    wmGap: function (v) { return (v / 10).toFixed(1) + "×"; },
    wmStrength: function (v) { return v; },
    wmFx: function (v) { return v; },
    wmBrush: function (v) { return v; },
    wmQuality: function (v) { return v; },
    wmFeather: function (v) { return v === "0" ? "auto" : v; },
    wmSoften: function (v) { return v === "0" ? "off" : v; },
  };
  Object.keys(LIVE).forEach(function (id) {
    var input = $(id), output = $(id + "Out");
    var sync = function () {
      if (output) output.textContent = LIVE[id](input.value);
      render();
    };
    input.addEventListener("input", sync);
    sync();
  });

  ["wmVisible", "wmText", "wmDark", "wmStyle", "wmStego", "wmStegoText",
   "wmRobust", "wmRobustId"].forEach(function (id) {
    $(id).addEventListener("input", render);
    $(id).addEventListener("change", render);
  });

  $("wmFormat").addEventListener("change", function () {
    $("wmQualityRow").hidden = $("wmFormat").value !== "image/jpeg";
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-tool]"), function (btn) {
    btn.addEventListener("click", function () {
      state.tool = btn.getAttribute("data-tool");
      Array.prototype.forEach.call(document.querySelectorAll("[data-tool]"), function (b) {
        b.classList.toggle("is-on", b === btn);
      });
      $("wmBrushRow").hidden = state.tool !== "brush";
    });
  });

  $("wmUndo").addEventListener("click", function () {
    // A brush stroke is many dabs; drop the whole run, not one dot.
    var last = state.shapes[state.shapes.length - 1];
    if (!last) return;
    if (last.type === "rect") state.shapes.pop();
    else while (state.shapes.length && state.shapes[state.shapes.length - 1].type === "brush") state.shapes.pop();
    render();
  });

  $("wmClear").addEventListener("click", function () { state.shapes = []; render(); });

  var drop = $("wmdrop"), fileInput = $("wmfile");
  fileInput.addEventListener("change", function () { loadFile(fileInput.files[0]); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  ["dragenter", "dragover"].forEach(function (n) {
    drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (n) {
    drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) loadFile(e.dataTransfer.files[0]);
  });
  document.addEventListener("paste", function (e) {
    var items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) { loadFile(items[i].getAsFile()); return; }
    }
  });
})();
