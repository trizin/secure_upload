# supload

A file drop that strips metadata, hands back an unguessable link, and deletes
everything after 48 hours. Runs on one small box: Python 3.13, SQLite, local
disk, Caddy in front.

## What happens to an upload

```
stream to a temp file, hard-capped at 100 MB
  -> detect the real type from magic bytes
     (the client's Content-Type and filename are never trusted)
  -> reject anything without a real sanitizer behind it
  -> sanitize in a sandboxed child process
  -> encrypt with a key derived from a fresh 256-bit token
  -> store; return a link that exists nowhere on the server
```

## The three promises

**Metadata is removed.** Per-format, by a sanitizer that exists:

| Type | Method | Lossless |
|---|---|---|
| JPEG | Drop every APPn and COM segment | yes* |
| PNG | Keep only render-affecting chunks | yes |
| GIF, WebP | Decode and re-encode | no |
| PDF | Clear DocInfo, XMP, per-object metadata | n/a |
| MP4, MOV, WebM, MP3, M4A, WAV, FLAC | `ffmpeg -map_metadata -1 -c copy` | yes |
| DOCX, XLSX, PPTX | Drop `docProps/`, flatten timestamps, strip embedded photos | yes |
| TXT, CSV, MD | Nothing to strip; encoding revalidated | yes |

\* Except when EXIF Orientation says the photo should be rotated. Dropping that
tag without baking the rotation into the pixels would serve the photo sideways,
so those are transposed and re-encoded at quality 95.

Everything else is refused. Archives and unknown binaries because their
metadata cannot be stripped reliably; HTML and SVG because both execute
JavaScript when opened.

**The link is unguessable.** 256 bits from `secrets.token_urlsafe(32)`. The
database stores `sha256(token)`, never the token, and the file key is derived
from the token via HKDF — so a stolen database decrypts nothing.

**It expires.** Two independent layers. Every read checks `expires_at` and
404s, so an expired file stops being served whether or not anything swept. A
sweeper then reclaims the bytes and cleans up blobs orphaned by a crash between
the file write and the database commit.

## Beyond the brief

Serving arbitrary user files is a stored-XSS engine, so:

- **Two origins.** `supload.org` serves the UI; `files.supload.org` serves
  bytes and nothing else. Enforced by `Host` header, both directions. A payload
  can only ever land on an origin with nothing to steal.
- **Always an attachment.** Nothing is rendered in a browser tab, which removes
  the "host a page, phish from a trusted domain" abuse class entirely.
- `nosniff`, `Content-Security-Policy: default-src 'none'; sandbox` on bytes,
  a strict no-`unsafe-inline` CSP on the UI, `X-Frame-Options: DENY`.
- **`Referrer-Policy: no-referrer`**, and Caddy rewrites tokens out of its
  access log. The token is in the URL, which makes every unredacted log line a
  working credential.
- Filenames are rebuilt from scratch, never used to address anything on disk,
  and the extension follows the bytes rather than the claim.
- Sanitizers run as children with `RLIMIT_AS`, `RLIMIT_CPU`, a wall-clock
  timeout and their own session, so a timeout kills the whole process group.
  systemd adds `IPAddressDeny=any`, backstopping ffmpeg's protocol whitelist.
- Image *pixels* are capped separately from file size: a 400 KB PNG can decode
  to 1.6 GB, which is the fastest way to OOM a 1 GB host.

## /watermark

A second tool, at `supload.org/watermark`. Entirely client-side, and the page
is served with `connect-src 'none'` so the browser itself forbids every network
request it could make - no fetch, no XHR, no WebSocket, no beacon. "Your image
is not uploaded" is therefore a rule the browser enforces rather than a claim
about the code. There is no upload button on the page and no endpoint behind
it; to share a marked file you download it and upload it from the home page.

(`blob:` is listed explicitly in that page's `img-src`. Blob URLs are how the
page reads the file you picked and `'self'` does not cover them - omit it and
the page silently cannot open any image at all.)

Node is a test-time dependency only; the server runs none of it.

**Visible mark.** Tiled repeating text across the image, with size, opacity,
angle and spacing. Stroked in the opposite colour so it stays readable over
both light and dark areas.

**Hidden mark.** A short message embedded in the mid-frequency DCT
coefficients of each 8x8 luma block. The coefficient pair was chosen by
measurement, not by eye: it has to sit symmetrically across the diagonal so
JPEG quantises both halves with a near-identical step, and that step has to be
small enough that the embedding margin outlives it. `(1,3)/(3,1)` (steps 19
and 17) holds to quality 55; `(3,4)/(4,3)` (steps 51 and 56) is lost by quality
85. Measured at 43 dB PSNR - invisible.

Survives: re-saving, JPEG re-compression to quality 55, mild brightness
changes, a region being concealed, and supload's own metadata stripper.
Does **not** survive: cropping, resizing, rotation, screenshots. All asserted
in `tests/test_stego_robustness.py`, including the failures - a wrong answer
would be worse than no answer, so a CRC makes it fail closed.

**Robust ID.** A second invisible layer that makes the opposite trade: 48 bits
instead of a sentence, but it comes back after edits that destroy the message.

Where the message mark is tied to an 8x8 grid anchored at pixel (0,0) - which
is why a crop misaligns it - this one is a *tiled* pseudorandom carrier. One
128x128 tile repeats across the image, so any crop still contains many whole
copies. Detection folds the entire image onto a single tile: every repeat adds
together while the picture underneath, having no relation to the tile period,
averages towards flat. A crop then shows up only as an unknown offset, and
folding a periodic signal at the wrong offset returns the tile circularly
shifted, so one cross-correlation against a known sync carrier recovers it.
Downscaling shrinks the tile period, so detection scans a range of periods and
keeps whichever gives the sharpest sync peak.

The carrier is band-limited to 4-24 cycles per tile: low enough in frequency to
stay invisible, high enough to still exist after the image is halved.
Strength 1.6 was chosen by measurement - 45.9 dB, the quietest setting that
still survives crop-then-JPEG and downscale-then-JPEG.

Survives: cropping (any offset, down to a third of the picture), downscaling to
~50%, JPEG to quality 60, and all of those combined. Does **not** survive:
downscaling much past 2x - the carrier falls below the new Nyquist limit and
stops existing in the file, which no decoder can undo - or rotation, which
would need Fourier-Mellin embedding. Asserted either way in
`tests/test_robustmark.py`.

The two layers coexist in one image, tested together. Detection reads whichever
survived, and reports the scale factor when the copy was resized.

**Concealment that reads as a camera fault.** An obvious black box is itself a
leak: it marks exactly where the sensitive data is and announces that you had
something to hide. So areas can be covered as out-of-focus, camera shake,
glare, or low-light smear instead.

The order of operations is the point:

1. **Destroy.** The region collapses to a handful of samples and expands
   again. Not invertible - which matters, because plain Gaussian blur can be
   deconvolved and clean pixelation can be brute-forced against block
   averages.
2. **Synthesise.** A plausible defect is drawn on the ruins. Defocus uses a
   disk kernel, because real out-of-focus light forms a disk and a Gaussian
   does not look like a camera missing focus.
3. **Feather irregularly.** The drawn shape is grown before rasterising so the
   falloff sits outside it and the interior stays fully opaque, and the edge is
   cut from a wide ramp at a noise-varying threshold so the boundary wanders.
4. **Put the grain back.** Measured from the untouched part of the image. A
   smoothed patch with no sensor noise, in a photo that has noise everywhere
   else, is the giveaway - to the eye and to anyone checking noise statistics.

What it cannot do: **overall brightness of the area survives**, because
preserving it is what makes the result look like the same scene out of focus.
A heavily inked field stays faintly distinguishable from a blank one, though
neither can be read. The glare and low-light styles discard even that. Tested
and bounded in `tests/test_watermark_pipeline.py`.

It is not forensically undetectable. Error-level analysis or noise statistics
can show an image was altered. It conceals from a person looking at the photo,
not from an examiner.

## What this does not protect against

Worth being honest about, because some of it is structural:

- **The server sees your file in plaintext.** It has to, in order to strip
  metadata. Encryption at rest protects a stolen disk or backup, not a live
  compromise of the running host. If you need the server never to see the
  content, that is a different design (key in the URL fragment, stripping done
  in the browser) and it can only clean common image types.
- **Anyone with the link has the file.** That is what "unguessable link" means.
  The token persists in browser history, clipboards and chat logs.
- **Metadata is not content.** Text inside a PDF, a face in a photo, or data
  hidden steganographically all survive. Only container metadata is removed.
- **No malware scanning.** ClamAV is not installed. PDF and DOCX remain the
  most common phishing delivery formats, stripped or not.
- **Rate limits are per-IP** and evadable with a pool of addresses.
- GIF and WebP are re-encoded, so they are not bit-identical.

Because it is public and anonymous, a handful of bad uploads can get
`supload.org` flagged by Google Safe Browsing, which would poison the domain in
Chrome, Firefox and Gmail. `robots.txt` denies indexing, `/abuse` is published,
every file can be reported, and `app.admin` can kill one immediately. Point
`abuse@supload.org` somewhere you actually read.

## Running it

```bash
./run.sh                          # http://127.0.0.1:8000, host checks off
./.venv/bin/python -m pytest -q   # 45 tests
```

Deploying (opens ports, requests real certificates, starts accepting uploads
from the internet):

```bash
sudo deploy/install.sh
```

It creates a `supload` service user, installs to `/srv/supload`, adds a 2 GB
swapfile, installs Caddy with the two-origin config, the systemd unit and
sweeper timer, and an nftables ruleset allowing only 22, 80 and 443.

## Operating

On the deployed host, run these from `/srv/supload` as the service user:

```bash
cd /srv/supload
sudo -u supload SUPLOAD_DATA=/srv/supload/data .venv/bin/python -m app.admin stats
```

The commands themselves:

```bash
python -m app.admin stats
python -m app.admin reported
python -m app.admin block <url>   # kill the link, keep the record
python -m app.admin purge <url>   # delete the bytes now
python -m app.admin sweep
```

## Configuration

All `SUPLOAD_*` environment variables, set in `deploy/supload.service`:

| Variable | Default |
|---|---|
| `SUPLOAD_DATA` | `/root/vault/data` |
| `SUPLOAD_APP_HOST` / `SUPLOAD_FILE_HOST` | `supload.org` / `files.supload.org` |
| `SUPLOAD_MAX_FILE_SIZE` | 100 MB |
| `SUPLOAD_TOTAL_QUOTA` | 20 GB |
| `SUPLOAD_RETENTION` | 172800 (48 h) |
| `SUPLOAD_MAX_IMAGE_PIXELS` | 50,000,000 |
| `SUPLOAD_RATE_UPLOADS` / `SUPLOAD_RATE_BYTES` | 10/hour / 500 MB/day |
| `SUPLOAD_SANITIZE_CONCURRENCY` | 1 |

## Layout

```
app/
  main.py        routes, host split, security headers
  config.py      settings
  crypto.py      token handling, chunked AES-256-GCM
  detect.py      magic-byte detection and the allowlist
  db.py          SQLite metadata
  ratelimit.py   per-IP limits (addresses stored hashed)
  sweeper.py     expiry and orphan reconciliation
  admin.py       operator CLI
  pages.py       HTML
  sanitize/      dispatch, sandbox, and one module per format
deploy/          Caddyfile, systemd units, nftables, install.sh
tests/           45 tests
```
