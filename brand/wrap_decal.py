"""Composite the reversed Your Painter logo onto a photo of the van, as vinyl."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


def homography(src, dst):
    """3x3 H with H @ [sx,sy,1] ~ [dx,dy,1], from 4 point pairs."""
    A, b = [], []
    for (sx, sy), (dx, dy) in zip(src, dst):
        A.append([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]); b.append(dx)
        A.append([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]); b.append(dy)
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def apply_h(H, pts):
    p = np.hstack([np.asarray(pts, float), np.ones((len(pts), 1))])
    q = p @ H.T
    return q[:, :2] / q[:, 2:3]


def build_artwork(logo_path, tagline, contact=None, pad_ratio=0.06):
    """Logo + tagline stacked, on transparent ground. Returns RGBA, exact aspect kept."""
    logo = Image.open(logo_path).convert("RGBA")
    lw, lh = logo.size
    gap = int(lh * 0.10)
    tag_h = int(lh * 0.115)
    extra = gap + tag_h
    if contact:
        extra += int(lh * 0.06) + int(lh * 0.10)
    art = Image.new("RGBA", (lw, lh + extra), (0, 0, 0, 0))
    art.paste(logo, (0, 0), logo)
    d = ImageDraw.Draw(art)
    f = ImageFont.truetype(FONT, tag_h)
    d.text((2, lh + gap), tagline, font=f, fill=(255, 255, 255, 255))
    return art


def warp_decal(base, art, quad, exposure=0.74, gloss=0.34, feather=1.1):
    """Inverse-warp `art` into image-space `quad` (TL,TR,BR,BL) and blend as vinyl."""
    base_a = np.asarray(base.convert("RGB")).astype(np.float64)
    H, W = base_a.shape[:2]
    aw, ah = art.size
    art_a = np.asarray(art).astype(np.float64)

    quad = np.asarray(quad, float)
    Hm = homography([(0, 0), (aw, 0), (aw, ah), (0, ah)], quad)
    Hinv = np.linalg.inv(Hm)

    x0 = max(int(np.floor(quad[:, 0].min())) - 2, 0)
    x1 = min(int(np.ceil(quad[:, 0].max())) + 2, W)
    y0 = max(int(np.floor(quad[:, 1].min())) - 2, 0)
    y1 = min(int(np.ceil(quad[:, 1].max())) + 2, H)

    yy, xx = np.mgrid[y0:y1, x0:x1]
    pts = np.stack([xx.ravel(), yy.ravel(), np.ones(xx.size)], 1)
    src = pts @ Hinv.T
    src = src[:, :2] / src[:, 2:3]
    u, v = src[:, 0], src[:, 1]

    inside = (u >= 0) & (u <= aw - 1) & (v >= 0) & (v <= ah - 1)
    u = np.clip(u, 0, aw - 1.001); v = np.clip(v, 0, ah - 1.001)
    u0 = u.astype(int); v0 = v.astype(int)
    fu = (u - u0)[:, None]; fv = (v - v0)[:, None]
    s = (art_a[v0, u0] * (1 - fu) * (1 - fv) + art_a[v0, u0 + 1] * fu * (1 - fv)
         + art_a[v0 + 1, u0] * (1 - fu) * fv + art_a[v0 + 1, u0 + 1] * fu * fv)

    hgt, wid = y1 - y0, x1 - x0
    dec = s[:, :3].reshape(hgt, wid, 3)
    alpha = (s[:, 3] * inside).reshape(hgt, wid) / 255.0
    alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(feather))).astype(np.float64) / 255.0

    # --- lighting: borrow the panel's own shading and reflections ---
    patch = base_a[y0:y1, x0:x1]
    lum = patch @ np.array([0.299, 0.587, 0.114])
    sel = lum[alpha > 0.05]
    if sel.size < 24:
        sel = lum.ravel()
    mid = max(np.percentile(sel, 60), 1e-6)
    hi = np.percentile(sel, 88)

    shade = np.clip(lum / mid, 0.52, 1.55)[..., None]
    out = dec * shade * exposure
    spec = np.clip((lum - hi) / max(255.0 - hi, 1e-6), 0, 1)[..., None] * gloss
    out = out + (255.0 - out) * spec
    out = np.clip(out, 0, 255)

    a3 = alpha[..., None]
    base_a[y0:y1, x0:x1] = patch * (1 - a3) + out * a3
    return Image.fromarray(np.clip(base_a, 0, 255).astype(np.uint8))
