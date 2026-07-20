#!/usr/bin/env python3
"""
convert.py — Kommandozeilen-Port des txt→bbmodel-Konverters.

Macht dieselbe Logik wie index.html verfügbar, aber ohne Browser/Website —
jede KI mit Python-Code-Ausführung (Claude, ChatGPT Code Interpreter, ...)
kann dieses Skript direkt aufrufen, um aus einer TXT-Cube-Liste eine fertige
.bbmodel-Datei (inkl. eingebetteter Textur) zu erzeugen.

Benutzung:
    python convert.py eingabe.txt ausgabe.bbmodel
    python convert.py eingabe.txt ausgabe.bbmodel --texture-out ausgabe.png

Voraussetzung: Pillow (`pip install Pillow --break-system-packages`)

TXT-Format: siehe die "Vorlage für KI"-Datei aus dem Web-Tool (Button
"Vorlage für KI herunterladen"), z. B.:

    resolution; 64,64
    Kopf; -4,24,-4; 8,8,8
    Koerper; -4,12,-2; 8,12,4
    color; Kopf; all; #e0b088
    pixel; Kopf; north; 2,3; #000000

Enthält exakt dieselben Sicherheitsnetze wie das Web-Tool:
- Mindestgröße 1 pro Kante (sonst proportionale Hochskalierung des Modells)
- Maximalgröße 48 pro Kante (sonst automatischer, nahtloser Split)
- Rotations-Diagonale-Fix (zusätzlicher Split, falls die rotierte Bounding
  Box trotz lokal okayer Größe die Grenze sprengt)
- Auto-Zentrierung, falls das Modell zu weit vom Ursprung entfernt liegt
  (reine Verschiebung, ändert nichts an Form/Proportionen)
- Normalisierung invertierter Cubes (negative Größe / from > to)
- Warnung bei doppelten Cube-Namen
- Textur-Obergrenze (8192px hart, ab 2048px Warnung) gegen zu große/
  performance-kritische Texturen
- Alle vier oben genannten Mechanismen laufen als Fixpunkt-Schleife (bis zu
  8 Durchläufe): falls einer durch seine eigene Korrektur einen anderen
  erneut nötig macht, greift der nächste Durchlauf das automatisch auf
- Box-UV-Layout: jeder Cube bekommt einen eigenen, überlappungsfreien
  Texturbereich
- color/pixel-Direktiven zum direkten Bemalen der Textur
"""

import sys
import re
import json
import uuid
import argparse
import base64
import io
import math

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow wird benötigt: pip install Pillow --break-system-packages", file=sys.stderr)
    sys.exit(1)

MAX_CUBE_SIZE = 48
TEXTURE_WARN_DIM = 2048
TEXTURE_HARD_LIMIT_DIM = 8192
MAX_SAFETY_ITERATIONS = 8
VALID_FACES = ['north', 'south', 'east', 'west', 'up', 'down', 'all']


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_line(line, line_no):
    parts = [p.strip() for p in line.split(';')]
    if len(parts) < 3:
        raise ValueError(f"Zeile {line_no}: Zu wenige Werte. Erwartet mind. 'Name; x,y,z; sizeX,sizeY,sizeZ' — bekommen: {line!r}")

    name = parts[0]
    if not name:
        raise ValueError(f"Zeile {line_no}: Name fehlt.")

    pos = [float(v) for v in parts[1].split(',')]
    size = [float(v) for v in parts[2].split(',')]
    if len(pos) != 3 or len(size) != 3:
        raise ValueError(f"Zeile {line_no}: Position/Größe brauchen genau 3 Werte (x,y,z).")
    if any(s == 0 for s in size):
        raise ValueError(f"Zeile {line_no} ('{name}'): Eine Kantenlänge ist exakt 0 — nicht bemalbar/unsichtbar. Bitte Größe > 0 angeben.")

    rotation = [0.0, 0.0, 0.0]
    if len(parts) >= 4 and parts[3]:
        rotation = [float(v) for v in parts[3].split(',')]
        if len(rotation) != 3:
            raise ValueError(f"Zeile {line_no}: Rotation braucht genau 3 Werte.")

    from_pos = pos
    to_pos = [pos[i] + size[i] for i in range(3)]
    was_inverted = False
    for i in range(3):
        if from_pos[i] > to_pos[i]:
            from_pos[i], to_pos[i] = to_pos[i], from_pos[i]
            was_inverted = True

    if len(parts) >= 5 and parts[4]:
        pivot = [float(v) for v in parts[4].split(',')]
        if len(pivot) != 3:
            raise ValueError(f"Zeile {line_no}: Pivot braucht genau 3 Werte.")
    else:
        pivot = [(from_pos[i] + to_pos[i]) / 2 for i in range(3)]

    return {'name': name, 'from': from_pos, 'to': to_pos, 'rotation': rotation, 'pivot': pivot, 'was_inverted': was_inverted}


def parse_paint_line(line, line_no):
    m = re.match(r'^color\s*;\s*([^;]+);\s*([^;]+);\s*(#[0-9a-fA-F]{3,8})\s*$', line, re.I)
    if m:
        face = m.group(2).strip().lower()
        if face not in VALID_FACES:
            raise ValueError(f"Zeile {line_no}: Unbekannte Fläche '{m.group(2).strip()}'. Erlaubt: {', '.join(VALID_FACES)}.")
        return {'type': 'color', 'cube': m.group(1).strip(), 'face': face, 'color': m.group(3)}

    m = re.match(r'^pixel\s*;\s*([^;]+);\s*([^;]+);\s*(\d+)\s*,\s*(\d+)\s*;\s*(#[0-9a-fA-F]{3,8})\s*$', line, re.I)
    if m:
        face = m.group(2).strip().lower()
        if face not in VALID_FACES:
            raise ValueError(f"Zeile {line_no}: Unbekannte Fläche '{m.group(2).strip()}'. Erlaubt: {', '.join(VALID_FACES)}.")
        return {'type': 'pixel', 'cube': m.group(1).strip(), 'face': face,
                'x': int(m.group(3)), 'y': int(m.group(4)), 'color': m.group(5)}

    return None


# --------------------------------------------------------------------------
# Sicherheitsnetz 1: Mindestgröße -> ganzes Modell proportional hochskalieren
# --------------------------------------------------------------------------

def auto_scale_to_min_size(cubes):
    min_size = math.inf
    for c in cubes:
        for i in range(3):
            s = abs(c['to'][i] - c['from'][i])
            if 0 < s < min_size:
                min_size = s
    if not math.isfinite(min_size) or min_size >= 1:
        return 1.0
    factor = 1.0 / min_size
    for c in cubes:
        for i in range(3):
            c['from'][i] *= factor
            c['to'][i] *= factor
            c['pivot'][i] *= factor
    return factor


# --------------------------------------------------------------------------
# Sicherheitsnetz 2: Maximalgröße -> nahtloser Split
# --------------------------------------------------------------------------

def split_oversized_cube(cube):
    splits_per_axis = []
    for i in range(3):
        size = abs(cube['to'][i] - cube['from'][i])
        splits_per_axis.append(max(1, math.ceil(size / MAX_CUBE_SIZE)))

    if all(n == 1 for n in splits_per_axis):
        return [cube]

    segments_per_axis = []
    for axis in range(3):
        n = splits_per_axis[axis]
        start = cube['from'][axis]
        total = cube['to'][axis] - cube['from'][axis]
        step = total / n
        segments_per_axis.append([[start + i * step, start + (i + 1) * step] for i in range(n)])

    total_parts = splits_per_axis[0] * splits_per_axis[1] * splits_per_axis[2]
    parts = []
    counter = 0
    for sx in segments_per_axis[0]:
        for sy in segments_per_axis[1]:
            for sz in segments_per_axis[2]:
                counter += 1
                parts.append({
                    'name': f"{cube['name']}_{counter}" if total_parts > 1 else cube['name'],
                    'from': [sx[0], sy[0], sz[0]],
                    'to': [sx[1], sy[1], sz[1]],
                    'rotation': cube['rotation'][:],
                    'pivot': cube['pivot'][:],
                })
    return parts


def split_all_oversized_cubes(cubes):
    result = []
    split_originals = 0
    total_new_parts = 0
    details = []
    for c in cubes:
        parts = split_oversized_cube(c)
        if len(parts) > 1:
            split_originals += 1
            total_new_parts += len(parts)
            orig_size = [round(abs(c['to'][i] - c['from'][i]), 2) for i in range(3)]
            details.append(f"\"{c['name']}\" ({'×'.join(map(str, orig_size))}) -> {len(parts)} Teile "
                            f"({', '.join(p['name'] for p in parts)})")
        result.extend(parts)
    return {'cubes': result, 'split_originals': split_originals, 'total_new_parts': total_new_parts, 'details': details}


# --------------------------------------------------------------------------
# Sicherheitsnetz 3: Rotations-Diagonale -> zusätzlicher Split
# --------------------------------------------------------------------------

def rotate_point_deg(p, pivot, rotation_deg):
    rx, ry, rz = [math.radians(d) for d in rotation_deg]
    x, y, z = p[0] - pivot[0], p[1] - pivot[1], p[2] - pivot[2]
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    return [x + pivot[0], y + pivot[1], z + pivot[2]]


def rotated_bounding_size(cube):
    if all(r == 0 for r in cube['rotation']):
        return [abs(cube['to'][i] - cube['from'][i]) for i in range(3)]
    corners = []
    for cx in (cube['from'][0], cube['to'][0]):
        for cy in (cube['from'][1], cube['to'][1]):
            for cz in (cube['from'][2], cube['to'][2]):
                corners.append(rotate_point_deg([cx, cy, cz], cube['pivot'], cube['rotation']))
    return [max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)]


SAFE_POSITION_RANGE = 64


def compute_model_world_bounds(cubes):
    mins = [math.inf, math.inf, math.inf]
    maxs = [-math.inf, -math.inf, -math.inf]
    for c in cubes:
        if all(r == 0 for r in c['rotation']):
            corners = [c['from'], c['to']]
        else:
            corners = []
            for cx in (c['from'][0], c['to'][0]):
                for cy in (c['from'][1], c['to'][1]):
                    for cz in (c['from'][2], c['to'][2]):
                        corners.append(rotate_point_deg([cx, cy, cz], c['pivot'], c['rotation']))
        for p in corners:
            for i in range(3):
                if p[i] < mins[i]:
                    mins[i] = p[i]
                if p[i] > maxs[i]:
                    maxs[i] = p[i]
    return mins, maxs


def auto_recenter_model(cubes):
    mins, maxs = compute_model_world_bounds(cubes)
    max_abs = max([abs(v) for v in mins] + [abs(v) for v in maxs])
    if not math.isfinite(max_abs) or max_abs <= SAFE_POSITION_RANGE:
        return {'shifted': False, 'shift': [0, 0, 0]}
    center = [(mins[i] + maxs[i]) / 2 for i in range(3)]
    shift = [-c for c in center]
    # Bei Modellen, die selbst breiter als 2x SAFE_POSITION_RANGE sind, kann
    # Zentrieren max_abs nie unter die Schwelle drücken. Stabilität wird daher
    # an der Größe der Verschiebung selbst festgemacht, nicht am Zielwert.
    if max(abs(s) for s in shift) < 0.5:
        return {'shifted': False, 'shift': [0, 0, 0]}
    for c in cubes:
        for i in range(3):
            c['from'][i] += shift[i]
            c['to'][i] += shift[i]
            c['pivot'][i] += shift[i]
    return {'shifted': True, 'shift': shift}


def split_in_half_along_largest_axis(cube):
    sizes = [abs(cube['to'][i] - cube['from'][i]) for i in range(3)]
    axis = sizes.index(max(sizes))
    mid = (cube['from'][axis] + cube['to'][axis]) / 2
    part_a = {'name': cube['name'], 'from': cube['from'][:], 'to': cube['to'][:],
              'rotation': cube['rotation'][:], 'pivot': cube['pivot'][:]}
    part_b = {'name': cube['name'], 'from': cube['from'][:], 'to': cube['to'][:],
              'rotation': cube['rotation'][:], 'pivot': cube['pivot'][:]}
    part_a['to'][axis] = mid
    part_b['from'][axis] = mid
    return [part_a, part_b]


def fix_rotated_oversized_cubes(cubes):
    result = []
    fixed_originals = 0
    total_new_parts = 0
    details = []
    for c in cubes:
        if all(r == 0 for r in c['rotation']):
            result.append(c)
            continue
        queue = [c]
        produced = []
        iterations = 0
        while queue and iterations < 500:
            iterations += 1
            cur = queue.pop(0)
            rot_size = rotated_bounding_size(cur)
            if any(s > MAX_CUBE_SIZE + 1e-6 for s in rot_size):
                queue.extend(split_in_half_along_largest_axis(cur))
            else:
                produced.append(cur)
        if len(produced) > 1:
            fixed_originals += 1
            total_new_parts += len(produced)
            for i, p in enumerate(produced):
                p['name'] = f"{c['name']}_r{i + 1}"
            details.append(f"\"{c['name']}\" (Rotation {', '.join(str(r) for r in c['rotation'])}°) "
                            f"-> zusätzlich {len(produced)} Teile wegen Diagonal-Effekt")
        result.extend(produced)
    return {'cubes': result, 'fixed_originals': fixed_originals, 'total_new_parts': total_new_parts, 'details': details}


def run_safety_pass_once(cubes):
    changed = False
    log = []

    scale_factor = auto_scale_to_min_size(cubes)
    if scale_factor != 1:
        changed = True
        log.append({'type': 'scale', 'factor': scale_factor})

    split_result = split_all_oversized_cubes(cubes)
    cubes = split_result['cubes']
    if split_result['split_originals'] > 0:
        changed = True
        log.append({'type': 'split', 'result': split_result})

    rot_fix_result = fix_rotated_oversized_cubes(cubes)
    cubes = rot_fix_result['cubes']
    if rot_fix_result['fixed_originals'] > 0:
        changed = True
        log.append({'type': 'rotfix', 'result': rot_fix_result})

    recenter_result = auto_recenter_model(cubes)
    if recenter_result['shifted']:
        changed = True
        log.append({'type': 'recenter', 'result': recenter_result})

    return {'cubes': cubes, 'changed': changed, 'log': log}


def run_safety_pipeline(cubes):
    all_logs = []
    iterations = 0
    for iterations in range(1, MAX_SAFETY_ITERATIONS + 1):
        pass_result = run_safety_pass_once(cubes)
        cubes = pass_result['cubes']
        all_logs.extend(pass_result['log'])
        if not pass_result['changed']:
            break
    return {'cubes': cubes, 'iterations': iterations, 'logs': all_logs}


# --------------------------------------------------------------------------
# Box-UV-Layout
# --------------------------------------------------------------------------

def compute_box_uv_faces(dx, dy, dz, u, v):
    return {
        'up':    [u + dz, v, u + dz + dx, v + dz],
        'down':  [u + dz + dx, v, u + dz + 2 * dx, v + dz],
        'east':  [u, v + dz, u + dz, v + dz + dy],
        'north': [u + dz, v + dz, u + dz + dx, v + dz + dy],
        'west':  [u + dz + dx, v + dz, u + 2 * dz + dx, v + dz + dy],
        'south': [u + 2 * dz + dx, v + dz, u + 2 * dz + 2 * dx, v + dz + dy],
    }


def pack_uv_layout(cube_dims, min_width, min_height):
    boxes = [{'boxW': 2 * d['dx'] + 2 * d['dz'], 'boxH': d['dz'] + d['dy']} for d in cube_dims]
    max_box_w = max([b['boxW'] for b in boxes], default=1)
    shelf_width = max(min_width, max_box_w, 1)

    cursor_x, cursor_y, row_h = 0, 0, 0
    offsets = []
    for b in boxes:
        if cursor_x != 0 and cursor_x + b['boxW'] > shelf_width:
            cursor_x, cursor_y, row_h = 0, cursor_y + row_h, 0
        offsets.append([cursor_x, cursor_y])
        cursor_x += b['boxW']
        row_h = max(row_h, b['boxH'])
    final_height = max(min_height, cursor_y + row_h, 1)
    return {'offsets': offsets, 'width': shelf_width, 'height': final_height}


# --------------------------------------------------------------------------
# Textur (Pillow statt Canvas)
# --------------------------------------------------------------------------

def hex_to_rgba(hex_color):
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) == 6:
        h += 'ff'
    r, g, b, a = (int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    return (r, g, b, a)


def resolve_target_cube_names(face_rects_by_cube, name):
    if name in face_rects_by_cube:
        return [name]
    prefix = name + '_'
    return [k for k in face_rects_by_cube if k.startswith(prefix)]


def build_texture_image(width, height, face_rects_by_cube, paint_directives):
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    warnings = []

    for d in paint_directives:
        target_names = resolve_target_cube_names(face_rects_by_cube, d['cube'])
        if not target_names:
            warnings.append(f"Cube \"{d['cube']}\" aus einer Mal-Zeile wurde nicht gefunden — ignoriert.")
            continue
        for target_name in target_names:
            rects = face_rects_by_cube[target_name]
            target_faces = list(rects.keys()) if d['face'] == 'all' else [d['face']]
            for f in target_faces:
                rect = rects.get(f)
                if not rect:
                    continue
                u1, v1, u2, v2 = rect
                color = hex_to_rgba(d['color'])
                if d['type'] == 'color':
                    draw.rectangle([u1, v1, u2 - 1, v2 - 1], fill=color)
                else:
                    face_w, face_h = u2 - u1, v2 - v1
                    if d['x'] >= face_w or d['y'] >= face_h:
                        warnings.append(f"Pixel ({d['x']},{d['y']}) außerhalb von \"{d['cube']}/{f}\" "
                                         f"(Fläche ist {face_w}×{face_h} groß) — ignoriert.")
                        continue
                    img.putpixel((int(u1 + d['x']), int(v1 + d['y'])), color)

    return img, warnings


def build_uv_template_image(width, height, face_rects_by_cube):
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    palette = ['#5ec9ff', '#ff9d5e', '#7ee08a', '#ff7ac6', '#ffe066', '#c792ea']
    for idx, (cube_name, rects) in enumerate(face_rects_by_cube.items()):
        color = hex_to_rgba(palette[idx % len(palette)])
        for face, rect in rects.items():
            u1, v1, u2, v2 = rect
            draw.rectangle([u1, v1, u2 - 1, v2 - 1], outline=color)
    return img


# --------------------------------------------------------------------------
# bbmodel-Aufbau
# --------------------------------------------------------------------------

def build_element(cube, uv_offset, dims, texture_index):
    faces = compute_box_uv_faces(dims['dx'], dims['dy'], dims['dz'], uv_offset[0], uv_offset[1])
    faces_out = {}
    for face, uv in faces.items():
        faces_out[face] = {'uv': uv, 'texture': texture_index}
    return {
        'name': cube['name'], 'box_uv': False, 'rescale': False, 'locked': False,
        'light_emission': 0, 'render_order': 'default', 'allow_mirror_modeling': True,
        'from': cube['from'], 'to': cube['to'], 'autouv': 0, 'color': 0,
        'origin': cube['pivot'], 'rotation': cube['rotation'], 'faces': faces_out,
        'type': 'cube', 'uuid': str(uuid.uuid4()),
    }


def build_model(elements, model_name, resolution, textures):
    return {
        'meta': {'format_version': '4.10', 'model_format': 'free', 'box_uv': False},
        'name': model_name, 'model_identifier': '', 'visible_box': [1, 1, 0],
        'variable_placeholders': '', 'variable_placeholders_buttons': [],
        'timeline_setups': [], 'unhandled_root_fields': {},
        'resolution': {'width': resolution[0], 'height': resolution[1]},
        'elements': elements, 'outliner': [e['uuid'] for e in elements],
        'textures': textures,
    }


def image_to_data_url(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/png;base64,{b64}"


# --------------------------------------------------------------------------
# Hauptlogik
# --------------------------------------------------------------------------

def convert(text, model_name, base_resolution=(16, 16)):
    cubes = []
    paint_directives = []
    resolution_from_file = None

    for line_no, raw_line in enumerate(text.split('\n'), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        m = re.match(r'^resolution\s*[;:]\s*(\d+)\s*,\s*(\d+)\s*$', line, re.I)
        if m:
            resolution_from_file = [int(m.group(1)), int(m.group(2))]
            continue

        paint = parse_paint_line(line, line_no)
        if paint:
            paint_directives.append(paint)
            continue

        cubes.append(parse_line(line, line_no))

    if not cubes:
        raise ValueError("Keine gültigen Cube-Zeilen gefunden.")

    name_counts = {}
    for c in cubes:
        name_counts[c['name']] = name_counts.get(c['name'], 0) + 1
    duplicate_names = [f"\"{n}\" ({cnt}×)" for n, cnt in name_counts.items() if cnt > 1]

    inverted_count = sum(1 for c in cubes if c.get('was_inverted'))

    safety = run_safety_pipeline(cubes)
    cubes = safety['cubes']

    min_resolution = resolution_from_file if resolution_from_file else list(base_resolution)

    cube_dims = [{
        'dx': max(1, round(c['to'][0] - c['from'][0])),
        'dy': max(1, round(c['to'][1] - c['from'][1])),
        'dz': max(1, round(c['to'][2] - c['from'][2])),
    } for c in cubes]

    layout = pack_uv_layout(cube_dims, min_resolution[0], min_resolution[1])
    resolution = [layout['width'], layout['height']]

    if resolution[0] > TEXTURE_HARD_LIMIT_DIM or resolution[1] > TEXTURE_HARD_LIMIT_DIM:
        raise ValueError(
            f"Die benötigte Textur wäre {resolution[0]}×{resolution[1]} groß — das überschreitet die harte "
            f"Grenze von {TEXTURE_HARD_LIMIT_DIM}px. Bitte weniger/kleinere Cubes verwenden oder das Modell "
            f"in mehrere separate TXT-Dateien aufteilen."
        )
    texture_size_warning = None
    if resolution[0] > TEXTURE_WARN_DIM or resolution[1] > TEXTURE_WARN_DIM:
        texture_size_warning = f"Textur ist mit {resolution[0]}×{resolution[1]} sehr groß — kann in Blockbench langsam werden."

    face_rects_by_cube = {}
    for c, dims, offset in zip(cubes, cube_dims, layout['offsets']):
        faces = compute_box_uv_faces(dims['dx'], dims['dy'], dims['dz'], offset[0], offset[1])
        face_rects_by_cube[c['name']] = faces

    elements = [build_element(c, offset, dims, 0) for c, offset, dims in zip(cubes, layout['offsets'], cube_dims)]

    img, paint_warnings = build_texture_image(resolution[0], resolution[1], face_rects_by_cube, paint_directives)
    data_url = image_to_data_url(img)

    textures = [{
        'uuid': str(uuid.uuid4()), 'name': model_name, 'id': '0', 'path': '',
        'relative_path': model_name + '.png', 'width': resolution[0], 'height': resolution[1],
        'uv_width': resolution[0], 'uv_height': resolution[1], 'particle': False,
        'render_mode': 'default', 'render_sides': 'auto', 'internal': True,
        'source': data_url, 'mode': 'bitmap', 'saved': False, 'visible': True,
        'folder': '', 'namespace': '',
    }]

    model = build_model(elements, model_name, resolution, textures)
    uv_template_img = build_uv_template_image(resolution[0], resolution[1], face_rects_by_cube)

    report = {
        'cube_count': len(cubes),
        'resolution': resolution,
        'inverted_count': inverted_count,
        'duplicate_names': duplicate_names,
        'safety': safety,
        'paint_count': len(paint_directives),
        'paint_warnings': paint_warnings,
        'texture_size_warning': texture_size_warning,
    }

    return model, img, uv_template_img, report


def print_report(report):
    print(f"✓ {report['cube_count']} Cube(s) erfolgreich verarbeitet. "
          f"UV-Layout: {report['resolution'][0]}×{report['resolution'][1]}")

    if report['inverted_count'] > 0:
        print(f"↔️ {report['inverted_count']} Cube(s) hatten eine invertierte Größe — automatisch normalisiert.")
    if report['duplicate_names']:
        print(f"⚠ Doppelte Cube-Namen: {', '.join(report['duplicate_names'])}. "
              f"Mal-Direktiven wirken auf alle davon.")

    logs = report['safety']['logs']
    scale_logs = [l for l in logs if l['type'] == 'scale']
    split_logs = [l for l in logs if l['type'] == 'split']
    rotfix_logs = [l for l in logs if l['type'] == 'rotfix']
    recenter_logs = [l for l in logs if l['type'] == 'recenter']

    if scale_logs:
        total_factor = 1.0
        for l in scale_logs:
            total_factor *= l['factor']
        print(f"📐 Modell um Faktor {round(total_factor, 2)}× vergrößert (Mindestkante war < 1).")
    if split_logs:
        total_orig = sum(l['result']['split_originals'] for l in split_logs)
        total_parts = sum(l['result']['total_new_parts'] for l in split_logs)
        print(f"✂️ {total_orig} zu große(r) Cube(s) in {total_parts} Teile gesplittet:")
        for l in split_logs:
            for d in l['result']['details']:
                print(f"   {d}")
    if rotfix_logs:
        total_orig = sum(l['result']['fixed_originals'] for l in rotfix_logs)
        total_parts = sum(l['result']['total_new_parts'] for l in rotfix_logs)
        print(f"🔄 {total_orig} Cube(s) wegen Rotations-Diagonale zusätzlich gesplittet ({total_parts} Teile):")
        for l in rotfix_logs:
            for d in l['result']['details']:
                print(f"   {d}")
    if recenter_logs:
        shift = [round(s, 2) for s in recenter_logs[0]['result']['shift']]
        print(f"🎯 Modell war zu weit vom Ursprung entfernt — automatisch um {tuple(shift)} verschoben.")
    if scale_logs or split_logs or rotfix_logs or recenter_logs:
        print(f"🛡️ Sicherheits-Pipeline: {report['safety']['iterations']} Durchlauf(e) benötigt.")

    if report['paint_count'] > 0:
        print(f"🎨 {report['paint_count']} Mal-Direktive(n) angewendet.")
    if report['texture_size_warning']:
        print(f"⚠ {report['texture_size_warning']}")
    for w in report['paint_warnings']:
        print(f"⚠ {w}")


def main():
    ap = argparse.ArgumentParser(description="TXT-Cube-Liste in .bbmodel umwandeln (inkl. Textur).")
    ap.add_argument('input', help="Eingabe-TXT-Datei")
    ap.add_argument('output', help="Ausgabe .bbmodel-Datei")
    ap.add_argument('--texture-out', help="Optional: Textur zusätzlich als eigenständige PNG-Datei speichern")
    ap.add_argument('--uv-template-out', help="Optional: UV-Vorlage (Schablone) als PNG speichern")
    ap.add_argument('--resolution', default='16,16', help="Basis-Auflösung, falls nicht per 'resolution;'-Zeile gesetzt (Standard: 16,16)")
    args = ap.parse_args()

    with open(args.input, encoding='utf-8') as f:
        text = f.read()

    model_name = args.output.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    base_res = tuple(int(v) for v in args.resolution.split(','))

    try:
        model, img, uv_template_img, report = convert(text, model_name, base_res)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(model, f, indent=2)

    print_report(report)
    print(f"\nGespeichert: {args.output}")

    if args.texture_out:
        img.save(args.texture_out)
        print(f"Textur gespeichert: {args.texture_out}")

    if args.uv_template_out:
        uv_template_img.save(args.uv_template_out)
        print(f"UV-Vorlage gespeichert: {args.uv_template_out}")


if __name__ == '__main__':
    main()
