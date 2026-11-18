#!/usr/bin/env python3
"""modue KiCad library helpers (run on the machine that holds pcb-kicad-library; needs python3 + zstd CLI)."""
import base64, glob, json, os, re, struct, subprocess, sys, tempfile, uuid

# ---------- embedded 3D model (KiCad 9 "kicad-embed://") ----------
_M = 0xFFFFFFFFFFFFFFFF
def _rotl(x, r): return ((x << r) | (x >> (64 - r))) & _M
def _fmix(k):
    k ^= k >> 33; k = (k * 0xff51afd7ed558ccd) & _M; k ^= k >> 33; k = (k * 0xc4ceb9fe1a85ec53) & _M; k ^= k >> 33; return k
def kicad_mm3(data, seed=0xABBA2345):
    """Checksum KiCad stores next to an embedded file (its incremental MurmurHash3 x64_128 variant)."""
    c1 = 0x87c37b91114253d5; c2 = 0x4cf5ad432745937f
    h1 = h2 = seed; n = len(data); nb = n // 16
    for i in range(nb):
        k1, k2 = struct.unpack_from('<QQ', data, i * 16)
        k1 = (k1 * c1) & _M; k1 = _rotl(k1, 31); k1 = (k1 * c2) & _M; h1 ^= k1
        h1 = _rotl(h1, 27); h1 = (h1 + h2) & _M; h1 = (h1 * 5 + 0x52dce729) & _M
        k2 = (k2 * c2) & _M; k2 = _rotl(k2, 33); k2 = (k2 * c1) & _M; h2 ^= k2
        h2 = _rotl(h2, 31); h2 = (h2 + h1) & _M; h2 = (h2 * 5 + 0x38495ab5) & _M
    tail = data[nb * 16:]; t = len(tail); ln = n
    if t:
        pad = 4 - (t + 4) % 4; tail += b'\0' * pad; ln = n + pad; t = len(tail)
        if t >= 16: tail = b''; t = 0
    k1 = k2 = 0
    for i in range(t - 1, 7, -1): k2 ^= tail[i] << ((i - 8) * 8)
    if t > 8: k2 = (k2 * c2) & _M; k2 = _rotl(k2, 33); k2 = (k2 * c1) & _M; h2 ^= k2
    for i in range(min(t, 8) - 1, -1, -1): k1 ^= tail[i] << (i * 8)
    if t > 0: k1 = (k1 * c1) & _M; k1 = _rotl(k1, 31); k1 = (k1 * c2) & _M; h1 ^= k1
    h1 ^= ln; h2 ^= ln; h1 = (h1 + h2) & _M; h2 = (h2 + h1) & _M; h1 = _fmix(h1); h2 = _fmix(h2); h1 = (h1 + h2) & _M; h2 = (h2 + h1) & _M
    return '%016X%016X' % (h1, h2)

def _has_size(z): return z[:4] == b'\x28\xb5\x2f\xfd' and ((z[4] >> 6) != 0 or bool(z[4] & 0x20))
def embed_block(step_bytes, fname):
    """(embedded_files ...) text. zstd must compress from a FILE: KiCad rejects frames without the content size."""
    with tempfile.NamedTemporaryFile(suffix='.step', delete=False) as tf: tf.write(step_bytes); tmp = tf.name
    z = subprocess.run(['zstd', '-15', '-c', '-q', '--no-check', tmp], capture_output=True, check=True).stdout; os.unlink(tmp)
    assert _has_size(z) and subprocess.run(['zstd', '-d', '-c', '-q'], input=z, capture_output=True, check=True).stdout == step_bytes
    b = base64.b64encode(z).decode(); lines = [b[i:i + 76] for i in range(0, len(b), 76)]
    data = lines[0] + ''.join('\n\t\t\t\t' + l for l in lines[1:])
    return ('\t(embedded_files\n\t\t(file\n\t\t\t(name "%s")\n\t\t\t(type model)\n\t\t\t(data |%s|\n\t\t\t)\n\t\t\t(checksum "%s")\n\t\t)\n\t)\n' % (fname, data, kicad_mm3(step_bytes)))
def model_block(fname, offset='0 0 0', rotate='0 0 0'):
    return ('\t(model "kicad-embed://%s"\n\t\t(offset\n\t\t\t(xyz %s)\n\t\t)\n\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n\t\t\t(xyz %s)\n\t\t)\n\t)\n' % (fname, offset, rotate))
def set_model(fp_path, step_path, offset='0 0 0', rotate='0 0 0'):
    """(Re)embed step_path into an existing footprint and copy nothing else. The .step in 3dmodels/ must be the same file."""
    t = open(fp_path).read().rstrip(); name = os.path.basename(fp_path)[:-10]; assert t.endswith(')')
    i = t.find('\t(embedded_files'); j = t.find('\t(model "')
    cut = min(x for x in (i, j, len(t) - 1) if x >= 0)
    new = t[:cut].rstrip('\n') + '\n' + embed_block(open(step_path, 'rb').read(), name + '.step') + model_block(name + '.step', offset, rotate) + ')\n'
    assert new.count('(') == new.count(')'); open(fp_path, 'w').write(new)
def check_embeds(repo):
    """Every embedded model: zstd frame carries the content size, checksum matches, bytes equal the .step in 3dmodels/."""
    bad = 0
    for f in sorted(glob.glob(repo + '/footprints/*/*.kicad_mod')):
        t = open(f).read(); name = os.path.basename(f)[:-10]
        m = re.search(r'\(data \|(.*?)\|\s*\)\s*\(checksum "([0-9A-F]+)"\)', t, re.S)
        if not m: continue
        z = base64.b64decode(re.sub(r'\s', '', m.group(1))); d = subprocess.run(['zstd', '-d', '-c', '-q'], input=z, capture_output=True).stdout
        st = glob.glob(repo + '/3dmodels/*/' + name + '.step')
        err = [] if _has_size(z) else ['zstd frame without content size (KiCad will not load it)']
        if kicad_mm3(d) != m.group(2): err.append('checksum mismatch')
        if err: bad += 1; print('BAD ', name, '; '.join(err))
        elif st and open(st[0], 'rb').read() != d: print('NOTE', name, 'embedded model differs from', st[0][len(repo) + 1:])
    print('check_embeds: %d problem(s)' % bad); return bad

# ---------- footprint text (KiCad 9, library style: silk 0.12, courtyard 0.05, fab 0.1, roundrect 0.25) ----------
def num(v):
    s = ('%.4f' % float(v)).rstrip('0').rstrip('.'); return '0' if s in ('-0', '') else s
def _fx(size, th): return '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size %s %s)\n\t\t\t\t(thickness %s)\n\t\t\t)\n\t\t)\n' % (size, size, th)
def fp_prop(name, val, at, layer, hide=False, size='1', th='0.15'):
    return '\t(property "%s" "%s"\n\t\t(at %s)\n\t\t(layer "%s")\n' % (name, val, at, layer) + ('\t\t(hide yes)\n' if hide else '') + _fx(size, th) + '\t)\n'
def _stroke(w): return '\t\t(stroke\n\t\t\t(width %s)\n\t\t\t(type solid)\n\t\t)\n' % w
def fp_line(a, b, layer, w): return '\t(fp_line\n\t\t(start %s)\n\t\t(end %s)\n' % (a, b) + _stroke(w) + '\t\t(layer "%s")\n\t)\n' % layer
def fp_rect(a, b, layer, w): return '\t(fp_rect\n\t\t(start %s)\n\t\t(end %s)\n' % (a, b) + _stroke(w) + '\t\t(fill no)\n\t\t(layer "%s")\n\t)\n' % layer
def fp_poly(pts, layer, w, fill='no'):
    return '\t(fp_poly\n\t\t(pts\n\t\t\t%s\n\t\t)\n' % ' '.join('(xy %s %s)' % (num(x), num(y)) for x, y in pts) + _stroke(w) + '\t\t(fill %s)\n\t\t(layer "%s")\n\t)\n' % (fill, layer)
def fp_circle(c, e, layer, w, fill='no'): return '\t(fp_circle\n\t\t(center %s)\n\t\t(end %s)\n' % (c, e) + _stroke(w) + '\t\t(fill %s)\n\t\t(layer "%s")\n\t)\n' % (fill, layer)
def fp_usertext(at='0 0 0', size='1', th='0.15'): return '\t(fp_text user "${REFERENCE}"\n\t\t(at %s)\n\t\t(layer "F.Fab")\n' % at + _fx(size, th) + '\t)\n'
def pad(n, shape, at, size, layers='"F.Cu" "F.Mask" "F.Paste"', rr='0.25', pre='', post=''):
    """pre/post: raw lines before/after (layers ...), e.g. pre='\\t\\t(property pad_prop_heatsink)\\n', post='\\t\\t(zone_connect 2)\\n'."""
    s = '\t(pad "%s" smd %s\n\t\t(at %s)\n\t\t(size %s)\n' % (n, shape, at, size) + pre + '\t\t(layers %s)\n' % layers
    return s + ('\t\t(roundrect_rratio %s)\n' % rr if shape == 'roundrect' else '') + post + '\t)\n'
def pad_poly(n, at, pts, radius=0.15, anchor='0.8 0.8', layers='"F.Cu" "F.Mask" "F.Paste"'):
    """Custom polygon pad with rounded corners. pts: outline in footprint coords (convex); it is inset by `radius` and drawn with a 2*radius stroke."""
    import math
    ax, ay = at; p = list(pts)
    if sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p))) < 0: p.reverse()
    L = []
    for i in range(len(p)):
        (x1, y1), (x2, y2) = p[i], p[(i + 1) % len(p)]; dx, dy = x2 - x1, y2 - y1; l = math.hypot(dx, dy)
        L.append((x1 - dy / l * radius, y1 + dx / l * radius, dx, dy))
    q = []
    for i in range(len(p)):
        x1, y1, dx1, dy1 = L[i - 1]; x2, y2, dx2, dy2 = L[i]; t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / (dx1 * dy2 - dy1 * dx2)
        q.append((x1 + t * dx1 - ax, y1 + t * dy1 - ay))
    return ('\t(pad "%s" smd custom\n\t\t(at %s %s)\n\t\t(size %s)\n\t\t(layers %s)\n\t\t(options\n\t\t\t(clearance outline)\n\t\t\t(anchor circle)\n\t\t)\n\t\t(primitives\n\t\t\t(gr_poly\n\t\t\t\t(pts\n\t\t\t\t\t%s\n\t\t\t\t)\n\t\t\t\t(width %s)\n\t\t\t\t(fill yes)\n\t\t\t)\n\t\t)\n\t)\n'
            % (n, num(ax), num(ay), anchor, layers, ' '.join('(xy %s %s)' % (num(x), num(y)) for x, y in q), num(2 * radius)))
def footprint(name, descr, tags, ref_at, val_at, body, step_bytes, offset='0 0 0', rotate='0 0 0'):
    s = '(footprint "%s"\n\t(version 20241229)\n\t(generator "modue")\n\t(layer "F.Cu")\n\t(descr "%s")\n\t(tags "%s")\n' % (name, descr, tags)
    s += fp_prop('Reference', 'REF**', ref_at, 'F.SilkS') + fp_prop('Value', name, val_at, 'F.Fab')
    s += fp_prop('Datasheet', '', '0 0 0', 'F.Fab', True, '1.27') + fp_prop('Description', '', '0 0 0', 'F.Fab', True, '1.27')
    s += '\t(attr smd)\n' + body + '\t(embedded_fonts no)\n' + embed_block(step_bytes, name + '.step') + model_block(name + '.step', offset, rotate) + ')\n'
    assert s.count('(') == s.count(')'); return s

# ---------- symbol text ----------
def sym_names(t): return [(m.group(1), m.start()) for m in re.finditer(r'^\t\(symbol "([^"]*)"', t, re.M)]
def sym_take(t, name):
    """-> (before, block, after) for a top-level symbol."""
    i = t.index('\t(symbol "%s"\n' % name); j = t.find('\n\t(symbol "', i + 10); j = j + 1 if j > 0 else t.rstrip().rfind(')')
    return t[:i], t[i:j], t[j:]
def sym_put(t, name, block):
    """Insert a symbol block keeping the file's alphabetical order."""
    assert name not in [n for n, _ in sym_names(t)], name + ' already exists'
    pos = next((p for n, p in sym_names(t) if n > name), t.rstrip().rfind(')'))
    out = t[:pos] + block + t[pos:]; assert out.count('(') == out.count(')'); return out
def sym_setprop(block, key, val):
    out, n = re.subn(r'(\(property "%s" ")[^"]*(")' % re.escape(key), lambda m: m.group(1) + val + m.group(2), block, count=1); assert n == 1, key; return out
def sym_clone(libfile, src, new, props):
    """Copy an existing symbol (the normal way to add a passive or a same-pinout part) and overwrite property values."""
    t = open(libfile, encoding='utf-8').read(); _, b, _ = sym_take(t, src); b = b.replace('(symbol "' + src, '(symbol "' + new)
    for k, v in props.items(): b = sym_setprop(b, k, v)
    open(libfile, 'w', encoding='utf-8').write(sym_put(t, new, b))
def sym_prop(name, val, at, hide=False, size='1.27'):
    return ('\t\t(property "%s" "%s"\n\t\t\t(at %s)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size %s %s)\n\t\t\t\t)\n' % (name, val, at, size, size) + ('\t\t\t\t(hide yes)\n' if hide else '') + '\t\t\t)\n\t\t)\n')
_EFF = '\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n'
def sym_pin(etype, at, name, number, length='2.54', hide=False):
    return ('\t\t\t(pin %s line\n\t\t\t\t(at %s)\n\t\t\t\t(length %s)\n' % (etype, at, length) + ('\t\t\t\t(hide yes)\n' if hide else '')
            + '\t\t\t\t(name "%s"\n%s\t\t\t\t)\n\t\t\t\t(number "%s"\n%s\t\t\t\t)\n\t\t\t)\n' % (name, _EFF, number, _EFF))
def sym_ic(name, props, half_w, half_h, pins, ref_at=None, val_at=None):
    """Rectangular IC symbol in the library style. props: ordered dict incl. Footprint, Datasheet, Description, Manufacturer,
    Manufacturer PN, modue PN, JLC PN, ki_keywords, ki_fp_filters. pins: [(etype, 'x y angle', name, number, hidden)]."""
    s = '\t(symbol "%s"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n' % name
    s += sym_prop('Reference', 'U', ref_at or '0 %s 0' % num(half_h + 3.81)) + sym_prop('Value', name, val_at or '0 %s 0' % num(half_h + 1.27))
    for k, v in props.items(): s += sym_prop(k, v, '0 0 0', True)
    s += ('\t\t(symbol "%s_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start %s %s)\n\t\t\t\t(end %s %s)\n\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n\t\t\t\t(fill\n\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
          % (name, num(-half_w), num(half_h), num(half_w), num(-half_h)))
    s += '\t\t(symbol "%s_1_1"\n' % name + ''.join(sym_pin(e, at, n, k, hide=h) for e, at, n, k, h in pins) + '\t\t)\n\t\t(embedded_fonts no)\n\t)\n'
    assert s.count('(') == s.count(')'); return s

# ---------- preview project (schematic + board with the given parts) ----------
def _units(blk, name):
    """{unit: text of its sub-symbols}; unit 0 = graphics/pins common to all units."""
    idx = [(m.start(), int(m.group(1))) for m in re.finditer(r'\t\t\(symbol "%s_(\d+)_\d+"' % re.escape(name), blk)]
    out = {}
    for k, (a, u) in enumerate(idx): out[u] = out.get(u, '') + blk[a:idx[k + 1][0] if k + 1 < len(idx) else len(blk)]
    return out
def _extent(blk, sub):
    """Half width / half height (mm) around the symbol origin: graphics, pins and visible field texts."""
    xs = [0.0]; ys = [0.0]
    for m in re.finditer(r'\((?:at|start|end|mid|center|xy) ([-\d.]+) ([-\d.]+)', sub): xs.append(float(m.group(1))); ys.append(float(m.group(2)))
    for m in re.finditer(r'\t\t\(property "[^"]*" "([^"]*)"\n\t\t\t\(at ([-\d.]+) ([-\d.]+) [-\d.]+\)\n(.*?)\n\t\t\)\n', blk, re.S):
        if 'hide yes' in m.group(4): continue
        sz = float(re.search(r'\(size ([\d.]+)', m.group(4)).group(1)); w = 0.95 * sz * len(m.group(1)) + 1; x = float(m.group(2)); y = float(m.group(3))
        l, r = (0, w) if 'justify left' in m.group(4) else (w, 0) if 'justify right' in m.group(4) else (w / 2, w / 2)
        xs += [x - l, x + r]; ys += [y - sz, y + sz]
    return max(-min(xs), max(xs)) + 3, max(-min(ys), max(ys)) + 3
def build_preview(repo, proj_dir, parts, name='lib_preview'):
    """parts: [{'symlib','symbol'} + optional 'ref','fplib','footprint' (default: the symbol's own),'title','sub','note'].
    Needs <proj_dir>/<name>.kicad_pcb created by KiCad (MCP create_project + close_project). Writes the schematic (every unit of
    every symbol), the board (one footprint per part) and <proj_dir>/preview.json for modue_preview.py."""
    U = lambda: str(uuid.uuid4()); root = U(); libsyms = inst = body = ''; cfg = []; items = []
    for k, p in enumerate(parts):
        blk = sym_take(open('%s/symbols/%s.kicad_sym' % (repo, p['symlib']), encoding='utf-8').read(), p['symbol'])[1]
        assert '(extends' not in blk, 'derived symbol: flatten it first'
        fpl, fpn = re.search(r'\(property "Footprint" "([^":]*):([^"]*)"', blk).groups()
        p = dict({'fplib': fpl, 'footprint': fpn, 'ref': re.search(r'\(property "Reference" "([^"]*)"', blk).group(1).rstrip('?') + str(k + 1), 'title': p['symbol'], 'sub': '', 'note': ''}, **p)
        un = _units(blk, p['symbol']); us = sorted(u for u in un if u > 0) or [0]
        ext = [_extent(blk, un.get(0, '') + un.get(u, '')) for u in us]
        items.append((p, blk, un, us, ext, max(e[0] for e in ext), sum(e[1] for e in ext)))
    SP = 2.54 * round(max(63.5, 2 * max(i[5] for i in items) + 10) / 2.54); PH = 2.54 * round(max(101.6, 2 * max(i[6] for i in items) + 10) / 2.54)
    for k, (p, blk, un, us, ext, hw, hh) in enumerate(items):
        X = SP / 2 + k * SP; ytop = PH / 2 - hh
        lb = blk.replace('\t(symbol "%s"\n' % p['symbol'], '\t(symbol "%s:%s"\n' % (p['symlib'], p['symbol']), 1)
        libsyms += ''.join('\t' + l + '\n' for l in lb.rstrip('\n').split('\n'))
        for u, (_, eh) in zip(us, ext):
            Y = 1.27 * round((ytop + eh) / 1.27); ytop += 2 * eh
            s = '\t(symbol\n\t\t(lib_id "%s:%s")\n\t\t(at %s %s 0)\n\t\t(unit %d)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(uuid "%s")\n' % (p['symlib'], p['symbol'], num(X), num(Y), max(u, 1), U())
            for m in re.finditer(r'\t\t\(property "([^"]*)" "([^"]*)"\n\t\t\t\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)\n(.*?)\n\t\t\)\n', blk, re.S):
                key, val, px, py, rot, rest = m.groups()
                if not key.startswith('ki_'): s += '\t\t(property "%s" "%s"\n\t\t\t(at %s %s %s)\n%s\n\t\t)\n' % (key, p['ref'] if key == 'Reference' else val, num(X + float(px)), num(Y - float(py)), rot, rest)
            for n in re.findall(r'\(number "([^"]*)"', un.get(0, '') + un.get(u, '')): s += '\t\t(pin "%s"\n\t\t\t(uuid "%s")\n\t\t)\n' % (n, U())
            inst += s + '\t\t(instances\n\t\t\t(project "%s"\n\t\t\t\t(path "/%s"\n\t\t\t\t\t(reference "%s")\n\t\t\t\t\t(unit %d)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n' % (name, root, p['ref'], max(u, 1))
        f = open('%s/footprints/%s.pretty/%s.kicad_mod' % (repo, p['fplib'], p['footprint'])).read().rstrip('\n')
        cy = [abs(float(v)) for e in re.findall(r'\(fp_\w+\n(?:(?!\n\t\)\n).)*?\(layer "F\.CrtYd"\)', f, re.S)
              for xy in re.findall(r'\((?:start|end|xy) ([-\d.]+) ([-\d.]+)\)', e) for v in xy]
        f = re.sub(r'\n\t\(version [^\n]*\n\t\(generator [^\n]*(\n\t\(generator_version [^\n]*)?', '', f, count=1)
        f = f.replace('(footprint "%s"' % p['footprint'], '(footprint "%s:%s"' % (p['fplib'], p['footprint']), 1)
        f, n1 = re.subn(r'\n\t\(layer "F\.Cu"\)', '\n\t(layer "F.Cu")\n\t(at @X%d@ @Y@)' % k, f, count=1)
        f, n2 = re.subn(r'\(property "Reference" "REF\*\*"', '(property "Reference" "%s"' % p['ref'], f, count=1); assert n1 == 1 and n2 == 1
        m = re.search(r'\(property "Value" "[^"]*"\n\t\t\(at [^)]*\)\n(?:\t\t\(unlocked yes\)\n)?\t\t\(layer "[^"]*"\)\n', f)      # long value text would cover the neighbours
        if m and not f[m.end():].startswith('\t\t(hide yes)'): f = f[:m.end()] + '\t\t(hide yes)\n' + f[m.end():]
        body += '\n'.join('\t' + l if l else l for l in f.split('\n')) + '\n'
        cfg.append(dict(p, sch=[X, PH / 2], sym_half=[max(hw, 8.5), max(hh, 8.5)], fp_half=(max(cy) if cy else 3.0) + 0.6))
    BP = max(20.0, 2 * max(c['fp_half'] for c in cfg) + 8); H = BP; W = BP * len(parts)
    for k, c in enumerate(cfg): c['pcb'] = [BP / 2 + k * BP, H / 2]; body = body.replace('@X%d@ @Y@' % k, '%s %s' % (num(c['pcb'][0]), num(H / 2)))
    sch = ('(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n\t(uuid "%s")\n\t(paper "User" %s %s)\n\t(lib_symbols\n%s\t)\n%s\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n'
           % (root, num(len(parts) * SP), num(PH), libsyms, inst))
    assert sch.count('(') == sch.count(')'); open('%s/%s.kicad_sch' % (proj_dir, name), 'w', encoding='utf-8').write(sch)
    t = open('%s/%s.kicad_pcb' % (proj_dir, name)).read().rstrip(); head = t[:t.index('\t(footprint "')] if '\t(footprint "' in t else t[:t.rfind(')')]
    gl = lambda a, b: '\t(gr_line\n\t\t(start %s %s)\n\t\t(end %s %s)\n\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n\t)\n' % (a + b)
    out = head.rstrip() + '\n' + body + gl((0, 0), (W, 0)) + gl((W, 0), (W, H)) + gl((W, H), (0, H)) + gl((0, H), (0, 0)) + ')\n'
    assert out.count('(') == out.count(')'); open('%s/%s.kicad_pcb' % (proj_dir, name), 'w').write(out)
    json.dump({'name': name, 'parts': cfg}, open(proj_dir + '/preview.json', 'w'), ensure_ascii=False, indent=1)
    print('preview project written: %d parts, sheet %sx%s mm, board %sx%s mm' % (len(parts), num(len(parts) * SP), num(PH), num(W), num(H)))

if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'check': sys.exit(1 if check_embeds(sys.argv[2]) else 0)
    if len(sys.argv) >= 5 and sys.argv[1] == 'preview': build_preview(sys.argv[2], sys.argv[3], json.load(open(sys.argv[4])), *(sys.argv[5:6]))
