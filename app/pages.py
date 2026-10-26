"""HTML rendering.

Deliberately no template engine: three small pages, every interpolation passed
through html.escape, and all JavaScript in an external file so the page can be
served under a strict CSP with no 'unsafe-inline'.
"""

from __future__ import annotations

from html import escape

from . import config


def _layout(title: str, body: str, *, extra_head: str = "", wide: bool = False) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>{escape(title)}</title>
<link rel="stylesheet" href="/static/app.css">
{extra_head}
</head>
<body>
<main{' class="wide"' if wide else ''}>
<a class="brand" href="/">supload</a>
{body}
</main>
<footer>
  <span>Files are stripped of metadata and deleted after {config.RETENTION_SECONDS // 3600} hours.</span>
  <a href="/watermark">Watermark</a>
  <a href="/abuse">Report abuse</a>
</footer>
</body>
</html>"""


def upload_page(max_bytes: int, kinds: list[str], retention_hours: int) -> str:
    listed = ", ".join(sorted(kinds))
    body = f"""
<h1>Share a file, privately</h1>
<p class="lede">Metadata is stripped on arrival. The link is unguessable, and
everything is deleted after {retention_hours} hours.</p>

<form id="form">
  <label id="drop" for="file" tabindex="0">
    <strong>Choose a file</strong>
    <span>or drop it here &middot; up to {max_bytes // (1024 * 1024)} MB</span>
    <input type="file" id="file" name="file" hidden>
  </label>
</form>

<div id="status" hidden>
  <div class="bar"><div id="fill"></div></div>
  <p id="statustext">Uploading&hellip;</p>
</div>

<div id="result" hidden>
  <h2>Ready</h2>
  <label class="field">Share link
    <span class="copyrow"><input id="link" readonly><button type="button" data-copy="link">Copy</button></span>
  </label>
  <label class="field">Direct file link
    <span class="copyrow"><input id="filelink" readonly><button type="button" data-copy="filelink">Copy</button></span>
  </label>
  <p class="meta">The share link opens a page showing what the file is. The
  direct link is the file itself &mdash; use it for <span class="mono">curl</span>,
  scripts, or anywhere a plain URL is wanted.</p>
  <p class="meta" id="resultmeta"></p>
  <p class="meta">Keep this to delete it early: <a id="delete" href="#">revocation link</a></p>
  <p><a href="/">Upload another</a></p>
</div>

<div id="error" hidden><h2>Not uploaded</h2><p id="errortext"></p>
<p><a href="/">Try another file</a></p></div>

<details class="types">
  <summary>Which file types are accepted?</summary>
  <p>Only types with a real sanitizer behind them: <span class="mono">{escape(listed)}</span>.</p>
  <p>Archives, executables and unknown binaries are refused, because their
  metadata cannot be stripped reliably. HTML and SVG are refused because both
  can run JavaScript when opened.</p>
</details>
<script src="/static/app.js"></script>"""
    return _layout("supload", body)


def download_page(*, name: str, size: int, expires_in: str, url: str, token: str) -> str:
    body = f"""
<h1>{escape(name)}</h1>
<p class="lede">{_human_size(size)} &middot; deleted in {escape(expires_in)}</p>
<p><a class="button" href="{escape(url)}" rel="noopener">Download</a></p>
<label class="field">Direct link
  <span class="copyrow"><input id="direct" value="{escape(url)}" readonly><button type="button" data-copy="direct">Copy</button></span>
</label>
<p class="meta">This file had its metadata removed when it was uploaded.</p>
<p class="meta"><a href="/r/{escape(token)}">Report this file</a></p>
<script src="/static/app.js"></script>"""
    return _layout(name, body)


def message_page(title: str, message: str, *, status_note: str = "") -> str:
    note = f'<p class="meta">{escape(status_note)}</p>' if status_note else ""
    body = f"<h1>{escape(title)}</h1><p class=\"lede\">{escape(message)}</p>{note}"
    return _layout(title, body)


def abuse_page() -> str:
    body = f"""
<h1>Report abuse</h1>
<p class="lede">If a link hosted here points at malware, phishing or illegal
content, send it to <a href="mailto:{escape(config.ABUSE_EMAIL)}">{escape(config.ABUSE_EMAIL)}</a>
and it will be removed.</p>
<p class="meta">Include the full link. Every upload is deleted automatically
after {config.RETENTION_SECONDS // 3600} hours regardless.</p>"""
    return _layout("Report abuse", body)


def confirm_delete_page(name: str, action: str) -> str:
    body = f"""
<h1>Delete this file?</h1>
<p class="lede">{escape(name)}</p>
<form method="post" action="{escape(action)}">
  <button class="button danger" type="submit">Delete now</button>
</form>
<p class="meta">This cannot be undone.</p>"""
    return _layout("Delete file", body)


def _human_size(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} GB"


def report_page(name: str, action: str) -> str:
    body = f"""
<h1>Report this file?</h1>
<p class="lede">{escape(name)}</p>
<p class="meta">Flag this upload for review. For anything urgent, email
<a href="mailto:{escape(config.ABUSE_EMAIL)}">{escape(config.ABUSE_EMAIL)}</a>.</p>
<form method="post" action="{escape(action)}">
  <button class="button danger" type="submit">Report</button>
</form>"""
    return _layout("Report file", body)


def watermark_page() -> str:
    """Image watermarking and concealment. Everything runs in the browser -
    the image is never sent anywhere, which is the only honest way to offer
    this on a site people use to handle documents they care about."""
    body = """
<h1>Watermark &amp; conceal</h1>
<p class="lede">Stamp a visible mark across an image, hide an invisible one
inside it, and cover up anything you do not want to share. Nothing leaves your
device &mdash; this page is blocked from making network requests.</p>

<div id="wmapp">
  <label id="wmdrop" for="wmfile" tabindex="0">
    <strong>Choose an image</strong>
    <span>or drop it here, or paste from the clipboard</span>
    <input type="file" id="wmfile" accept="image/*" hidden>
  </label>

  <div id="wmstage" hidden>
    <div class="stage">
      <canvas id="wmcanvas"></canvas>
      <p class="meta" id="wminfo"></p>
    </div>

    <div class="panel">
      <section>
        <h2>Visible mark</h2>
        <label class="row"><input type="checkbox" id="wmVisible" checked> Show it</label>
        <label class="field">Text
          <input type="text" id="wmText" value="ONLY FOR VERIFICATION" maxlength="80">
        </label>
        <label class="field">Size <output id="wmSizeOut">4%</output>
          <input type="range" id="wmSize" min="2" max="12" step="0.5" value="4">
        </label>
        <label class="field">Opacity <output id="wmOpacityOut">28%</output>
          <input type="range" id="wmOpacity" min="5" max="90" value="28">
        </label>
        <label class="field">Angle <output id="wmAngleOut">-30&deg;</output>
          <input type="range" id="wmAngle" min="-90" max="90" value="-30">
        </label>
        <label class="field">Spacing <output id="wmGapOut">2.2&times;</output>
          <input type="range" id="wmGap" min="12" max="45" value="22">
        </label>
        <label class="row"><input type="checkbox" id="wmDark"> Dark text</label>
        <div class="row">
          <button type="button" class="chip" data-tool="mark">Pick area</button>
          <button type="button" id="wmMarkAll">Whole image</button>
        </div>
        <p class="meta" id="wmAreaOut">Covering the whole image.</p>
        <p class="meta">Only the visible mark is confined to the area you pick.
        The two hidden marks always cover the whole image &mdash; confining
        them would just make them easier to crop away.</p>
      </section>

      <section>
        <h2>Hidden message</h2>
        <label class="row"><input type="checkbox" id="wmStego" checked> Embed invisibly</label>
        <label class="field">Message
          <input type="text" id="wmStegoText" value="shared 2026-09-22" maxlength="200">
        </label>
        <label class="field">Strength <output id="wmStrengthOut">16</output>
          <input type="range" id="wmStrength" min="8" max="28" value="16">
        </label>
        <p class="meta" id="wmCapacity"></p>
        <p class="meta">Carries a whole sentence, and survives re-saving and
        JPEG down to quality 55. It does <strong>not</strong> survive cropping,
        resizing or a screenshot &mdash; it is tied to an 8&times;8 pixel grid,
        and those move it.</p>
      </section>

      <section>
        <h2>Robust ID</h2>
        <label class="row"><input type="checkbox" id="wmRobust" checked> Embed a tracking ID</label>
        <label class="field">ID (48 bits)
          <input type="text" id="wmRobustId" maxlength="12" spellcheck="false">
        </label>
        <div class="row"><button type="button" id="wmNewId">New ID</button></div>
        <p class="meta">A tougher second layer. Only 48 bits &mdash; a number,
        not a sentence &mdash; but it comes back after edits that wipe the
        message out: <strong>cropping</strong>, <strong>downscaling to about
        half</strong>, and JPEG. Keep a note of which ID went to whom.</p>
        <p class="meta">Past roughly 2&times; shrinking it is gone for good, and
        rotation defeats it. A screenshot usually does both, so the visible
        mark above remains your only defence against one.</p>
      </section>

      <section>
        <h2>Conceal areas</h2>
        <div class="row">
          <button type="button" class="chip is-on" data-tool="rect">Rectangle</button>
          <button type="button" class="chip" data-tool="brush">Brush</button>
        </div>
        <label class="field">Looks like
          <select id="wmStyle">
            <option value="defocus">Out of focus</option>
            <option value="motion">Camera shake</option>
            <option value="glare">Glare / blown highlight</option>
            <option value="lowlight">Low light smear</option>
          </select>
        </label>
        <label class="field">Strength <output id="wmFxOut">9</output>
          <input type="range" id="wmFx" min="4" max="16" value="9">
        </label>
        <label class="field">Edge softness <output id="wmFeatherOut">auto</output>
          <input type="range" id="wmFeather" min="4" max="40" value="0">
        </label>
        <label class="field" id="wmBrushRow" hidden>Brush size <output id="wmBrushOut">40</output>
          <input type="range" id="wmBrush" min="8" max="200" value="40">
        </label>
        <label class="field">Soften whole photo <output id="wmSoftenOut">off</output>
          <input type="range" id="wmSoften" min="0" max="10" value="0">
        </label>
        <div class="row">
          <button type="button" id="wmUndo">Undo area</button>
          <button type="button" id="wmClear">Clear all</button>
        </div>
        <p class="meta">Detail is destroyed before the effect is drawn, so what
        is underneath is gone rather than hidden. Overall brightness of the
        area still shows through &mdash; that is what makes it look like the
        same scene out of focus.</p>
      </section>

      <section>
        <h2>Save</h2>
        <label class="field">Format
          <select id="wmFormat">
            <option value="image/jpeg">JPEG</option>
            <option value="image/png">PNG</option>
          </select>
        </label>
        <label class="field" id="wmQualityRow">Quality <output id="wmQualityOut">92</output>
          <input type="range" id="wmQuality" min="55" max="100" value="92">
        </label>
        <div class="row">
          <button type="button" id="wmSave" class="primary">Download</button>
        </div>
        <p class="meta" id="wmSaveNote"></p>
        <p class="meta">This page cannot reach the network at all &mdash; its
        Content-Security-Policy forbids it, so the browser enforces that your
        image stays on this device. To share a marked file, download it and
        then drop it on the <a href="/">upload page</a>.</p>
      </section>

      <section>
        <h2>Read a hidden mark</h2>
        <p class="meta">Check whether an image carries one of your invisible
        marks, and what it says.</p>
        <label class="row"><input type="file" id="wmCheck" accept="image/*"></label>
        <p class="meta" id="wmCheckOut"></p>
      </section>
    </div>
  </div>
</div>

<script src="/static/stego.js"></script>
<script src="/static/robustmark.js"></script>
<script src="/static/imagefx.js"></script>
<script src="/static/watermark.js"></script>"""
    return _layout("Watermark & conceal", body, wide=True)
