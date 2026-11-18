#!/usr/bin/env python3
"""Compose the modue library preview sheet: symbol / footprint / paste / 3D for every part.
usage: modue_preview.py <dir with lib_preview.svg pcb.svg pcb_paste.svg board.glb preview.json> <out.png> [title]
Needs numpy, Pillow and rsvg-convert. The 3D row is a simple software render of KiCad's GLB export (flat shading)."""
import json, re, struct, subprocess, sys, io
import numpy as np
from PIL import Image, ImageDraw, ImageFont
FB = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'; FR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
def font(sz, bold=False):
    try: return ImageFont.truetype(FB if bold else FR, sz)
    except OSError: return ImageFont.load_default()

# ---------- GLB ----------
_CT = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2), 5123: ('H', 2), 5125: ('I', 4), 5126: ('f', 4)}; _NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
def load_glb(path):
    d = open(path, 'rb').read(); assert d[:4] == b'glTF'; o = 12; js = binc = None
    while o < len(d):
        cl, ct = struct.unpack('<II', d[o:o + 8]); o += 8
        if ct == 0x4E4F534A: js = json.loads(d[o:o + cl])
        elif ct == 0x004E4942: binc = d[o:o + cl]
        o += cl
    def acc(i):
        a = js['accessors'][i]; bv = js['bufferViews'][a['bufferView']]; fmt, sz = _CT[a['componentType']]; n = _NC[a['type']]
        off = bv.get('byteOffset', 0) + a.get('byteOffset', 0); stride = bv.get('byteStride', 0) or sz * n; dt = np.dtype(fmt).newbyteorder('<')
        if stride == sz * n: arr = np.frombuffer(binc, dtype=dt, count=a['count'] * n, offset=off)
        else:
            raw = np.frombuffer(binc, dtype=np.uint8, count=stride * (a['count'] - 1) + sz * n, offset=off)
            arr = np.frombuffer(raw[(np.arange(a['count'])[:, None] * stride + np.arange(sz * n)[None, :]).ravel()].tobytes(), dtype=dt)
        return arr.reshape(a['count'], n).astype(np.float64 if fmt == 'f' else np.int64)
    def mat(n):
        if 'matrix' in n: return np.array(n['matrix'], float).reshape(4, 4).T
        M = np.eye(4)
        if 'scale' in n: M = np.diag(list(n['scale']) + [1.0]) @ M
        if 'rotation' in n:
            x, y, z, w = n['rotation']
            M = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0], [0, 0, 0, 1]]) @ M
        if 'translation' in n: T = np.eye(4); T[:3, 3] = n['translation']; M = T @ M
        return M
    tris = []; cols = []; names = []
    def walk(ni, M, path):
        n = js['nodes'][ni]; M2 = M @ mat(n); p = path + '/' + n.get('name', '')
        if 'mesh' in n:
            for pr in js['meshes'][n['mesh']]['primitives']:
                if pr.get('mode', 4) != 4: continue
                P = acc(pr['attributes']['POSITION']); P = (M2 @ np.c_[P, np.ones(len(P))].T).T[:, :3]
                I = acc(pr['indices']).ravel() if 'indices' in pr else np.arange(len(P)); T = P[I].reshape(-1, 3, 3)
                c = js['materials'][pr['material']].get('pbrMetallicRoughness', {}).get('baseColorFactor', [.7, .7, .7, 1]) if 'material' in pr else [.7, .7, .7, 1]
                tris.append(T); cols.append(np.tile(np.array(c[:3], float), (len(T), 1))); names.extend([p] * len(T))
        for ch in n.get('children', []): walk(ch, M2, p)
    for r in js['scenes'][js.get('scene', 0)]['nodes']: walk(r, np.eye(4), '')
    T = np.concatenate(tris)
    return np.stack([T[..., 0] * 1000, -T[..., 2] * 1000, T[..., 1] * 1000], -1), np.concatenate(cols), names   # mm; X east, Y north (= -pcb y), Z up

def render(P, C, N, centre, half, W, H, SS=3, eye=(-0.42, -0.80, 0.78)):
    e = np.array(eye, float); e /= np.linalg.norm(e); f = -e; r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r); u = np.cross(r, f)
    Q = P - np.array(centre); w, h = W * SS, H * SS; s = (w / 2) / half
    sx = (Q @ r) * s + w / 2; sy = h / 2 - (Q @ u) * s; Z = Q @ e
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]); ln = np.linalg.norm(n, axis=1); ok = ln > 1e-12; n[ok] /= ln[ok, None]; n[(n @ e) < 0] *= -1
    L = np.array([-0.25, -0.45, 0.86]); L /= np.linalg.norm(L); col = np.clip(C * (0.42 + 0.62 * np.clip(n @ L, 0, 1))[:, None], 0, 1)
    ids = {k: i for i, k in enumerate(sorted(set(N)))}; oid = np.array([ids[k] for k in N])
    img = np.ones((h, w, 3)); zb = np.full((h, w), -1e9); nb = np.zeros((h, w, 3)); ob = np.full((h, w), -1, int)
    vis = ok & (sx.max(1) >= 0) & (sy.max(1) >= 0) & (sx.min(1) < w) & (sy.min(1) < h)
    for i in np.nonzero(vis)[0]:
        xa, xb = max(int(sx[i].min()), 0), min(int(np.ceil(sx[i].max())) + 1, w); ya, yb = max(int(sy[i].min()), 0), min(int(np.ceil(sy[i].max())) + 1, h)
        (ax, bx, cx), (ay, by, cy) = sx[i], sy[i]; den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12 or xa >= xb or ya >= yb: continue
        gx, gy = np.meshgrid(np.arange(xa, xb) + 0.5, np.arange(ya, yb) + 0.5)
        l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den; l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den; l3 = 1 - l1 - l2
        z = l1 * Z[i, 0] + l2 * Z[i, 1] + l3 * Z[i, 2]; sub = zb[ya:yb, xa:xb]; m = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6) & (z > sub + 1e-7)
        sub[m] = z[m]; img[ya:yb, xa:xb][m] = col[i]; nb[ya:yb, xa:xb][m] = n[i]; ob[ya:yb, xa:xb][m] = oid[i]
    edge = np.zeros((h, w), bool)                                   # outlines: object change, crease or depth step
    for dy, dx in ((0, 1), (1, 0)):
        a = (slice(0, h - dy), slice(0, w - dx)); b = (slice(dy, h), slice(dx, w))
        edge[a] |= ((np.sum(nb[a] * nb[b], -1) < 0.75) | (np.abs(zb[a] - zb[b]) > 0.12) | (ob[a] != ob[b])) & ((ob[a] >= 0) | (ob[b] >= 0))
    edge[1:, :] |= edge[:-1, :].copy(); edge[:, 1:] |= edge[:, :-1].copy()
    out = np.where(img <= 0.0031308, 12.92 * img, 1.055 * np.clip(img, 1e-9, 1) ** (1 / 2.4) - 0.055); out[edge] *= 0.35
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.LANCZOS)

# ---------- SVG crops ----------
def crop_svg(svg, x, y, w, h, W, H, bg='white'):
    """Rasterize the (x, y, w, h) mm window of a KiCad SVG into W x H px (window is letterboxed to keep the aspect)."""
    k = max(w / W, h / H); w2, h2 = W * k, H * k; x -= (w2 - w) / 2; y -= (h2 - h) / 2
    t, n = re.subn(r'width="[^"]*"\s+height="[^"]*"\s+viewBox="[^"]*"', 'width="%dpx" height="%dpx" viewBox="%.4f %.4f %.4f %.4f"' % (W, H, x, y, w2, h2), svg, count=1); assert n == 1
    png = subprocess.run(['rsvg-convert', '-b', bg, '-f', 'png'], input=t.encode(), capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert('RGB'), W / w2

def main(d, out, title='modue KiCad library — podgląd części'):
    cfg = json.load(open(d + '/preview.json')); parts = cfg['parts']
    sch = open(d + '/%s.svg' % cfg['name']).read(); pcb = open(d + '/pcb.svg').read(); pst = open(d + '/pcb_paste.svg').read()
    P, C, N = load_glb(d + '/board.glb')
    PER = 6; bands = [parts[i:i + PER] for i in range(0, len(parts), PER)]; ncol = max(len(b) for b in bands)
    CW, PAD, HH, SH, FH, TH, NH, LW, TOP = 600, 18, 92, 540, 450, 400, 64, 205, 70
    BH = HH + SH + 2 * FH + TH + NH; Wd = LW + ncol * CW + PAD; Hd = TOP + len(bands) * BH + PAD
    img = Image.new('RGB', (Wd, Hd), (246, 246, 243)); dr = ImageDraw.Draw(img); dr.text((PAD, 18), title, font=font(26, True), fill=(30, 30, 30))
    def bar(x, y, pxmm, mm, label):
        dr.line((x, y, x + pxmm * mm, y), fill=(40, 40, 40), width=3)
        for xx in (x, x + pxmm * mm): dr.line((xx, y - 5, xx, y + 5), fill=(40, 40, 40), width=2)
        dr.text((x + pxmm * mm + 8, y - 9), label, font=font(14), fill=(40, 40, 40))
    for bi, band in enumerate(bands):
        y0 = TOP + bi * BH; y = y0 + HH
        for label, hh in (('Symbol', SH), ('Footprint\nF.Cu, F.SilkS,\nF.CrtYd, F.Fab', FH), ('Pasta\nF.Paste\n+ obrys F.Fab', FH), ('Model 3D\n(eksport GLB\nz KiCada)', TH)):
            dr.multiline_text((PAD, y + hh // 2 - 30), label, font=font(17, True), fill=(70, 70, 70), spacing=6); y += hh
        for ci, p in enumerate(band):
            x = LW + ci * CW; y = y0; cw = CW - PAD
            dr.rectangle((x, y, x + cw, y + HH - 10), fill=(40, 48, 60)); tf = font(25, True)
            while dr.textlength(p['title'], font=tf) > cw - 24 and tf.size > 12: tf = font(tf.size - 1, True)
            dr.text((x + 14, y + 12), p['title'], font=tf, fill=(255, 255, 255)); dr.text((x + 14, y + 50), p.get('sub', ''), font=font(16), fill=(205, 215, 230)); y += HH
            hw, hh = p['sym_half']; cell, k = crop_svg(sch, p['sch'][0] - hw, p['sch'][1] - hh, 2 * hw, 2 * hh, cw, SH - PAD, '#F5F4EF')
            img.paste(cell, (x, y)); dr.rectangle((x, y, x + cw - 1, y + SH - PAD - 1), outline=(190, 190, 185)); bar(x + 16, y + SH - PAD - 18, k, 2.54, '2,54 mm'); y += SH
            fh = p['fp_half'] * 1.25 + 0.9
            for src in (pcb, pst):                                   # the plotted page starts at the board outline's outer edge (-0.05 mm)
                cell, k = crop_svg(src, p['pcb'][0] + 0.05 - fh, p['pcb'][1] + 0.05 - fh * 1.08, 2 * fh, 2 * fh * 0.95, cw, FH - PAD)
                img.paste(cell, (x, y)); dr.rectangle((x, y, x + cw - 1, y + FH - PAD - 1), outline=(190, 190, 185)); bar(x + 16, y + FH - PAD - 18, k, 1, '1 mm'); y += FH
            sel = np.array([('/' + p['ref'] + '/') in (n + '/') for n in N])        # a model can be an assembly: //U4/BODY, //U4/FRAME
            if sel.any():
                V = P[sel].reshape(-1, 3); lo, hi = V.min(0), V.max(0)
                cell = render(P, C, N, (lo + hi) / 2, max(hi[0] - lo[0], hi[1] - lo[1]) * 0.62 + (hi[2] - lo[2]) * 0.35 + 1.3, cw, TH - PAD)
            else:
                cell = Image.new('RGB', (cw, TH - PAD), (235, 235, 232)); ImageDraw.Draw(cell).text((20, 20), 'brak modelu 3D w eksporcie', font=font(18, True), fill=(170, 40, 40))
            img.paste(cell, (x, y)); dr.rectangle((x, y, x + cw - 1, y + TH - PAD - 1), outline=(190, 190, 185)); y += TH
            dr.multiline_text((x + 4, y), p.get('note', ''), font=font(15), fill=(60, 60, 60), spacing=5)
    img.save(out); print('saved', out, img.size)

if __name__ == '__main__': main(*sys.argv[1:4])
