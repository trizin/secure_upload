(function () {
  "use strict";

  // Copy buttons appear on both the upload page and the download landing
  // page, so this is wired up before anything upload-specific.
  function wireCopyButtons() {
    var buttons = document.querySelectorAll("button[data-copy]");
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener("click", function () {
        var target = document.getElementById(btn.getAttribute("data-copy"));
        if (!target) return;
        target.select();
        var confirm = function () {
          var original = btn.textContent;
          btn.textContent = "Copied";
          setTimeout(function () { btn.textContent = original; }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(target.value).then(confirm, function () {});
        } else {
          try { document.execCommand("copy"); confirm(); } catch (e) { /* selection is the fallback */ }
        }
      });
    });
  }

  wireCopyButtons();

  var drop = document.getElementById("drop");
  var input = document.getElementById("file");
  if (!drop || !input) return;  // not the upload page

  var statusBox = document.getElementById("status");
  var statusText = document.getElementById("statustext");
  var fill = document.getElementById("fill");
  var result = document.getElementById("result");
  var errorBox = document.getElementById("error");
  var form = document.getElementById("form");

  function show(el) {
    [form, statusBox, result, errorBox].forEach(function (n) {
      if (n) n.hidden = n !== el;
    });
  }

  function fail(message) {
    document.getElementById("errortext").textContent = message;
    show(errorBox);
  }

  function succeed(data) {
    var link = document.getElementById("link");
    link.value = data.url;
    document.getElementById("filelink").value = data.file_url;
    document.getElementById("delete").href = data.delete_url;
    var when = new Date(data.expires_at * 1000);
    document.getElementById("resultmeta").textContent =
      data.name + " · deleted " + when.toLocaleString();
    show(result);
    link.focus();
    link.select();
  }

  function upload(file) {
    if (!file) return;
    show(statusBox);
    fill.style.width = "0%";
    statusText.textContent = "Uploading…";

    var body = new FormData();
    body.append("file", file, file.name);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");

    xhr.upload.addEventListener("progress", function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round((e.loaded / e.total) * 100);
      fill.style.width = pct + "%";
      if (pct >= 100) statusText.textContent = "Stripping metadata…";
    });

    xhr.addEventListener("load", function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status === 200 && data && data.url) succeed(data);
      else fail((data && data.error) || "Upload failed (" + xhr.status + ").");
    });
    xhr.addEventListener("error", function () { fail("Connection lost during upload."); });
    xhr.send(body);
  }

  input.addEventListener("change", function () { upload(input.files[0]); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  ["dragenter", "dragover"].forEach(function (name) {
    drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (name) {
    drop.addEventListener(name, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
  });
})();
