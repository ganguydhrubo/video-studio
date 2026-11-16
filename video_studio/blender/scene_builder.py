"""Runs INSIDE Blender (blender -b --factory-startup -P scene_builder.py -- spec.json).

Builds a complete 3D shot from a JSON spec - title, hero object or bar chart -
with materials, three-point lighting, a reflective floor, an animated camera
move and depth of field, then renders PNG frames. Frames that already exist
are skipped, so an interrupted render resumes where it stopped.

Styles: "cinematic" (PBR, AgX) or "toon" (cel shading + ink outlines).
"""

import json
import math
import sys

import bpy
import bmesh
from mathutils import Vector

spec = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
W, H, FPS = spec["width"], spec["height"], spec["fps"]
FRAMES = max(2, int(round(spec["duration"] * FPS)))
LOOK = spec["look"]
STYLE = spec.get("style", "cinematic")
PORTRAIT = H > W


def srgb(c):
    c = c.lstrip("#")
    rgb = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in rgb) + (1.0,)


ACCENT, ACCENT2, INK = srgb(LOOK["accent"]), srgb(LOOK["accent2"]), srgb(LOOK["ink"])
METAL = tuple(LOOK["metal"]) + (1.0,)

# ----------------------------------------------------------------- scene setup
scene = bpy.context.scene
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
scene.frame_start, scene.frame_end = 1, FRAMES
scene.render.fps = FPS
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100

engine_set = False
if spec.get("engine", "eevee") == "eevee":
    for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = name
            engine_set = True
            break
        except TypeError:
            continue
if engine_set:
    ee = scene.eevee
    for attr, val in (("taa_render_samples", spec.get("samples", 16)), ("use_raytracing", True), ("use_gtao", True),
                      ("use_bloom", True), ("use_shadows", True)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except (AttributeError, TypeError):
                pass
else:
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = max(8, spec.get("samples", 16))
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except TypeError:
        pass
scene.render.threads_mode = "FIXED"
scene.render.threads = spec.get("threads", 4)

try:
    scene.view_settings.view_transform = "AgX" if STYLE != "toon" else "Standard"
    if STYLE != "toon":
        scene.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    try:
        scene.view_settings.view_transform = "Filmic"
    except TypeError:
        pass

# world
world = bpy.data.worlds.new("World") if not bpy.data.worlds else bpy.data.worlds[0]
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
wc = LOOK["world"]
if bg:
    bg.inputs[0].default_value = (wc[0], wc[1], wc[2], 1)
    bg.inputs[1].default_value = 1.0 if STYLE != "toon" else 1.0

# ------------------------------------------------------------------ materials


def principled(name, color, metallic=0.0, rough=0.4, emission=None, strength=0.0, transmission=0.0, coat=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    p = nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = color
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = rough
    for key in ("Transmission Weight", "Transmission"):
        if key in p.inputs:
            p.inputs[key].default_value = transmission
            break
    for key in ("Coat Weight", "Clearcoat"):
        if key in p.inputs and coat:
            p.inputs[key].default_value = coat
            break
    if emission:
        for key in ("Emission Color", "Emission"):
            if key in p.inputs:
                p.inputs[key].default_value = emission
                break
        if "Emission Strength" in p.inputs:
            p.inputs["Emission Strength"].default_value = strength
    if transmission and hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = "DITHERED"
        except TypeError:
            pass
    return mat


def toon(name, color):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    diffuse = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (1, 1, 1, 1)
    to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, tuple(c * 0.35 for c in color[:3]) + (1,)
    els[1].position, els[1].color = 0.55, color
    mid = els.new(0.22)
    mid.color = tuple(c * 0.7 for c in color[:3]) + (1,)
    hi = els.new(0.88)
    hi.color = tuple(min(1.0, c * 1.25 + 0.15) for c in color[:3]) + (1,)
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(diffuse.outputs[0], to_rgb.inputs[0])
    nt.links.new(to_rgb.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], emit.inputs[0])
    nt.links.new(emit.outputs[0], out.inputs[0])
    return mat


def material(kind, color=None):
    color = color or METAL
    if STYLE == "toon" and kind != "neon":
        return toon(kind, {"gold": srgb("#F2B33D"), "chrome": srgb("#C9D3E0"), "glass": srgb("#9BE7FF")}.get(kind, color))
    if kind == "chrome":
        return principled("chrome", (0.9, 0.92, 0.95, 1), 1.0, 0.12)
    if kind == "gold":
        return principled("gold", (1.0, 0.71, 0.29, 1), 1.0, 0.22)
    if kind == "glass":
        return principled("glass", (0.85, 0.95, 1.0, 1), 0.0, 0.03, transmission=1.0)
    if kind == "neon":
        return principled("neon", (0.02, 0.02, 0.02, 1), 0.0, 0.4, emission=color, strength=7.0)
    if kind == "matte":
        return principled("matte", color, 0.0, 0.6, coat=0.3)
    return principled("accent", color, 0.55, 0.28, coat=0.5)


def assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def smooth(obj, bevel=0.0, subdiv=0):
    if obj.type == "MESH":
        for poly in obj.data.polygons:
            poly.use_smooth = True
        if bevel:
            m = obj.modifiers.new("bevel", "BEVEL")
            m.width, m.segments = bevel, 4
        if subdiv:
            m = obj.modifiers.new("subd", "SUBSURF")
            m.levels = m.render_levels = subdiv
    return obj


def add(kind, **kw):
    getattr(bpy.ops.mesh, f"primitive_{kind}_add")(**kw)
    return bpy.context.active_object


def parent(children, root):
    for c in children:
        c.parent = root
    return root


def empty(name, loc=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    e.location = loc
    scene.collection.objects.link(e)
    return e


# --------------------------------------------------------------- 3D text


def font_obj():
    path = spec.get("font")
    if path:
        try:
            return bpy.data.fonts.load(path)
        except RuntimeError:
            pass
    return None


def text3d(body, size=1.0, extrude=0.12, mat=None, loc=(0, 0, 0)):
    curve = bpy.data.curves.new("txt", "FONT")
    curve.body = body
    f = font_obj()
    if f:
        curve.font = f
    curve.size = size
    curve.extrude = extrude
    curve.bevel_depth = extrude * 0.25
    curve.bevel_resolution = 3
    curve.align_x, curve.align_y = "CENTER", "CENTER"
    obj = bpy.data.objects.new("txt", curve)
    scene.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (math.radians(90), 0, 0)
    if mat:
        curve.materials.append(mat)
    return obj


def fit_text(obj, max_w):
    bpy.context.view_layer.update()
    w = obj.dimensions.x
    if w > max_w:
        s = max_w / w
        obj.scale = (s, s, s)


# ---------------------------------------------------------------- objects


def make_star(points=5, r1=1.0, r2=0.45, depth=0.35):
    mesh = bpy.data.meshes.new("star")
    bm = bmesh.new()
    verts = []
    for i in range(points * 2):
        a = math.pi / 2 + i * math.pi / points
        r = r1 if i % 2 == 0 else r2
        verts.append(bm.verts.new((r * math.cos(a), 0, r * math.sin(a))))
    face = bm.faces.new(verts)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    bmesh.ops.translate(bm, verts=[v for v in ext["geom"] if isinstance(v, bmesh.types.BMVert)], vec=(0, depth, 0))
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("star", mesh)
    scene.collection.objects.link(obj)
    obj.location.y -= depth / 2
    m = obj.modifiers.new("bevel", "BEVEL")
    m.width, m.segments = 0.05, 3
    return obj


def hero(kind, mat):
    accent = material("accent", ACCENT)
    dark = principled("dark", (0.02, 0.02, 0.025, 1), 0.3, 0.35, coat=0.6)
    screen = principled("screen", (0, 0, 0, 1), 0, 0.2, emission=ACCENT2, strength=3.0)
    root = empty("hero")
    parts = []
    if kind == "orb":
        o = smooth(add("uv_sphere", radius=1.0, segments=64, ring_count=32)); assign(o, mat); parts.append(o)
        r = smooth(add("torus", major_radius=1.55, minor_radius=0.045)); r.rotation_euler = (math.radians(70), math.radians(15), 0)
        assign(r, material("neon", ACCENT)); parts.append(r)
    elif kind == "cube":
        o = smooth(add("cube", size=1.5), bevel=0.18); assign(o, mat); parts.append(o)
        o.rotation_euler = (math.radians(35), math.radians(45), 0)
    elif kind == "ring":
        o = smooth(add("torus", major_radius=1.0, minor_radius=0.32, major_segments=96, minor_segments=32)); assign(o, mat)
        o.rotation_euler = (math.radians(75), 0, 0); parts.append(o)
    elif kind == "gem":
        o = add("ico_sphere", radius=1.1, subdivisions=1); o.scale = (1, 1, 1.35); assign(o, mat); parts.append(o)
    elif kind == "coin":
        o = smooth(add("cylinder", radius=1.1, depth=0.22, vertices=96), bevel=0.04); o.rotation_euler = (math.radians(90), 0, 0)
        assign(o, material("gold")); parts.append(o)
        t = text3d(spec.get("coin_symbol", "₹"), 1.1, 0.06, material("gold"), (0, -0.14, -0.05)); parts.append(t)
    elif kind == "rocket":
        body = smooth(add("cylinder", radius=0.42, depth=1.8, vertices=48), bevel=0.05); assign(body, mat)
        nose = smooth(add("cone", radius1=0.42, depth=0.8, vertices=48, location=(0, 0, 1.3))); assign(nose, accent)
        win = smooth(add("uv_sphere", radius=0.18, location=(0, -0.38, 0.35))); assign(win, screen)
        parts += [body, nose, win]
        for i in range(3):
            a = i * 2 * math.pi / 3
            fin = smooth(add("cube", size=1, location=(0.55 * math.cos(a), 0.55 * math.sin(a), -0.75)), bevel=0.03)
            fin.scale = (0.32, 0.05, 0.45); fin.rotation_euler = (0, 0, a); assign(fin, accent); parts.append(fin)
        flame = smooth(add("cone", radius1=0.3, depth=0.9, location=(0, 0, -1.4))); flame.rotation_euler = (math.pi, 0, 0)
        assign(flame, material("neon", srgb("#FF8A00"))); parts.append(flame)
    elif kind == "phone":
        o = smooth(add("cube", size=1), bevel=0.12); o.scale = (0.95, 0.09, 1.9); assign(o, dark); parts.append(o)
        s = add("plane", size=1, location=(0, -0.1, 0)); s.scale = (0.84, 1.72, 1); s.rotation_euler = (math.radians(90), 0, 0)
        assign(s, screen); parts.append(s)
    elif kind == "laptop":
        base = smooth(add("cube", size=1, location=(0, 0.6, -0.55)), bevel=0.04); base.scale = (2.2, 1.5, 0.08); assign(base, mat)
        lid = smooth(add("cube", size=1, location=(0, 1.3, 0.35)), bevel=0.04); lid.scale = (2.2, 0.06, 1.45)
        lid.rotation_euler = (math.radians(-12), 0, 0); assign(lid, mat)
        s = add("plane", size=1, location=(0, 1.24, 0.37)); s.scale = (2.0, 1.28, 1); s.rotation_euler = (math.radians(78), 0, 0)
        assign(s, screen); parts += [base, lid, s]
    elif kind == "globe":
        o = smooth(add("uv_sphere", radius=1.0, segments=48, ring_count=24)); assign(o, material("glass")); parts.append(o)
        g = add("uv_sphere", radius=1.03, segments=24, ring_count=12)
        wf = g.modifiers.new("wire", "WIREFRAME"); wf.thickness = 0.02; assign(g, material("neon", ACCENT)); parts.append(g)
    elif kind == "pyramid":
        o = add("cone", vertices=4, radius1=1.3, depth=1.8); assign(o, mat); parts.append(o)
    elif kind == "capsule":
        c = smooth(add("cylinder", radius=0.5, depth=1.2, vertices=48)); assign(c, mat)
        t = smooth(add("uv_sphere", radius=0.5, location=(0, 0, 0.6))); assign(t, mat)
        b = smooth(add("uv_sphere", radius=0.5, location=(0, 0, -0.6))); assign(b, accent)
        parts += [c, t, b]
    elif kind == "shield":
        o = smooth(add("cylinder", radius=1.1, depth=0.25, vertices=6), bevel=0.06); o.rotation_euler = (math.radians(90), 0, math.radians(30))
        o.scale = (1, 1.25, 1); assign(o, mat); parts.append(o)
        chk = text3d("✓", 1.2, 0.08, material("neon", ACCENT), (0, -0.2, -0.1)); parts.append(chk)
    elif kind == "bulb":
        g = smooth(add("uv_sphere", radius=0.9, location=(0, 0, 0.35))); assign(g, principled("bulb", (1, 0.9, 0.6, 1), 0, 0.1, emission=srgb("#FFD27A"), strength=4))
        base = smooth(add("cylinder", radius=0.38, depth=0.6, location=(0, 0, -0.7)), bevel=0.03); assign(base, material("chrome"))
        parts += [g, base]
    elif kind == "clock":
        face = smooth(add("cylinder", radius=1.1, depth=0.18, vertices=96), bevel=0.03); face.rotation_euler = (math.radians(90), 0, 0); assign(face, mat)
        rim = smooth(add("torus", major_radius=1.1, minor_radius=0.07)); rim.rotation_euler = (math.radians(90), 0, 0); assign(rim, accent)
        hand = add("cube", size=1, location=(0, -0.12, 0.3)); hand.scale = (0.06, 0.03, 0.6); assign(hand, dark)
        hand2 = add("cube", size=1, location=(0, -0.13, 0.2)); hand2.scale = (0.05, 0.03, 0.42); assign(hand2, material("neon", ACCENT2))
        pivot = empty("pivot", (0, 0, 0)); hand.parent = pivot; hand2.parent = pivot
        pivot.rotation_euler = (0, 0, 0); pivot.keyframe_insert("rotation_euler", frame=1)
        pivot.rotation_euler = (0, math.radians(360), 0); pivot.keyframe_insert("rotation_euler", frame=FRAMES)
        parts += [face, rim, pivot]
    elif kind == "chat":
        o = smooth(add("cube", size=1), bevel=0.3); o.scale = (1.9, 0.35, 1.3); assign(o, mat)
        tail = add("cone", vertices=3, radius1=0.35, depth=0.6, location=(-0.6, 0, -0.85)); tail.rotation_euler = (math.radians(180), 0, 0); assign(tail, mat)
        parts += [o, tail]
        for i in range(3):
            d = smooth(add("uv_sphere", radius=0.16, location=(-0.5 + i * 0.5, -0.38, 0))); assign(d, material("neon", ACCENT)); parts.append(d)
    elif kind == "heart":
        a = smooth(add("uv_sphere", radius=0.62, location=(-0.45, 0, 0.35))); b = smooth(add("uv_sphere", radius=0.62, location=(0.45, 0, 0.35)))
        c = smooth(add("cone", radius1=1.05, depth=1.4, vertices=48, location=(0, 0, -0.45))); c.rotation_euler = (math.pi, 0, 0)
        heart_mat = material("accent", srgb("#FF2E63"))
        for o in (a, b, c):
            assign(o, heart_mat)
        parts += [a, b, c]
    elif kind == "star":
        o = make_star(); assign(o, material("gold")); parts.append(o)
    elif kind == "gear":
        o = smooth(add("cylinder", radius=0.9, depth=0.35, vertices=64), bevel=0.03); o.rotation_euler = (math.radians(90), 0, 0); assign(o, mat)
        hole = add("cylinder", radius=0.3, depth=0.5, vertices=48); hole.rotation_euler = (math.radians(90), 0, 0); hole.hide_render = True
        bool_mod = o.modifiers.new("hole", "BOOLEAN"); bool_mod.object = hole; bool_mod.operation = "DIFFERENCE"
        parts += [o, hole]
        for i in range(12):
            a = i * math.pi / 6
            tooth = smooth(add("cube", size=1, location=(1.0 * math.cos(a), 0, 1.0 * math.sin(a))), bevel=0.02)
            tooth.scale = (0.28, 0.33, 0.22); tooth.rotation_euler = (0, -a, 0); assign(tooth, mat); parts.append(tooth)
    else:  # monkey and fallback
        o = smooth(add("monkey", size=1.6), subdiv=2); o.rotation_euler = (math.radians(15), 0, 0); assign(o, mat); parts.append(o)
    parent([p for p in parts if p.parent is None], root)
    return root


def chart(values, labels, mat):
    root = empty("chart")
    vmax = max(values) or 1
    n = len(values)
    gap = 1.25
    start = -(n - 1) * gap / 2
    bars = []
    for i, v in enumerate(values):
        h = 0.3 + 2.6 * (v / vmax)
        bar = smooth(add("cube", size=1, location=(start + i * gap, 0, 0)), bevel=0.05)
        bar.scale = (0.8, 0.8, h)
        bar.location.z = h / 2 - 1.2
        assign(bar, material("accent", ACCENT if i == n - 1 else METAL) if i == n - 1 else mat)
        bar.parent = root
        delay = int(i * FPS * 0.18)
        bar.scale = (0.8, 0.8, 0.001)
        bar.location.z = -1.2
        bar.keyframe_insert("scale", frame=1 + delay)
        bar.keyframe_insert("location", frame=1 + delay)
        bar.scale = (0.8, 0.8, h)
        bar.location.z = h / 2 - 1.2
        bar.keyframe_insert("scale", frame=1 + delay + int(FPS * 0.8))
        bar.keyframe_insert("location", frame=1 + delay + int(FPS * 0.8))
        num = text3d(f"{v:g}", 0.38, 0.04, material("neon", INK), (start + i * gap, -0.5, h - 1.2 + 0.35))
        num.parent = root
        if i < len(labels) and labels[i]:
            lab = text3d(labels[i], 0.3, 0.03, material("matte", (0.8, 0.8, 0.85, 1)), (start + i * gap, -0.5, -1.55))
            fit_text(lab, gap * 0.95)
            lab.parent = root
        bars.append(bar)
    return root


def pop_in(obj, start, end):
    final = tuple(obj.scale)
    obj.scale = (0.001, 0.001, 0.001)
    obj.keyframe_insert("scale", frame=max(1, start))
    obj.scale = final
    obj.keyframe_insert("scale", frame=max(2, end))


def light_card(loc, size, strength=6.0):
    card = add("plane", size=size, location=loc)
    direction = Vector((0, 0, 0)) - Vector(loc)
    card.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    assign(card, principled("card", (0, 0, 0, 1), 0, 1, emission=(1, 1, 1, 1), strength=strength))
    for attr in ("visible_camera", "visible_shadow"):
        if hasattr(card, attr):
            setattr(card, attr, False)
    return card


# ------------------------------------------------------------------ build shot
v = spec["visual"]
mat_kind = v.get("material", "chrome")
if v["type"] == "title3d" and mat_kind in ("chrome", "glass"):
    mat_kind = "accent"  # readable, colour-matched hero type; chrome titles read as hollow on dark worlds
main_mat = material(mat_kind, ACCENT)
subject = None
if v["type"] == "title3d":
    lines = v.get("text", "TITLE").upper().split()
    # wrap to 2 lines for portrait
    if PORTRAIT and len(lines) > 1:
        half = (len(lines) + 1) // 2
        body = " ".join(lines[:half]) + "\n" + " ".join(lines[half:])
    else:
        body = " ".join(lines)
    subject = text3d(body, 1.0, 0.18, main_mat)
    subject.data.space_line = 0.85
    fit_text(subject, 4.2 if PORTRAIT else 6.4)
    if v.get("subtitle"):
        sub = text3d(v["subtitle"], 0.32, 0.02, material("neon", ACCENT), (0, 0, -1.45 if PORTRAIT else -1.05))
        fit_text(sub, 3.8 if PORTRAIT else 5.5)
        pop_in(sub, int(FPS * 0.9), int(FPS * 1.3))
elif v["type"] == "chart3d":
    subject = chart(v.get("values") or [3, 5, 8], v.get("labels") or [], main_mat)
else:
    subject = hero(v.get("object", "orb"), main_mat)
    if v.get("label") and spec.get("show_label", True):
        label = text3d(v["label"].upper(), 0.34, 0.04, material("neon", INK), (0, -0.3, -1.95 if PORTRAIT else -1.6))
        fit_text(label, 3.6 if PORTRAIT else 5.0)
        label.keyframe_insert("scale", frame=1)
        pop_in(label, int(FPS * 0.8), int(FPS * 1.2))
        label.scale = (0.001, 0.001, 0.001)
        label.keyframe_insert("scale", frame=1)

# entrance + idle motion for the subject
if v["type"] != "chart3d":
    final_scale = tuple(subject.scale)
    subject.scale = (0.001, 0.001, 0.001)
    subject.keyframe_insert("scale", frame=1)
    subject.scale = tuple(s * 1.08 for s in final_scale)
    subject.keyframe_insert("scale", frame=int(FPS * 0.55))
    subject.scale = final_scale
    subject.keyframe_insert("scale", frame=int(FPS * 0.8))
    spin = math.radians(spec.get("spin_degrees", 50 if v["type"] == "title3d" else 140))
    subject.rotation_euler.z = -spin / 2 if v["type"] != "title3d" else -spin / 4
    subject.keyframe_insert("rotation_euler", index=2, frame=1)
    subject.rotation_euler.z = spin / 2 if v["type"] != "title3d" else 0.0
    subject.keyframe_insert("rotation_euler", index=2, frame=FRAMES)
    subject.location.z = 0
    for f in range(1, FRAMES + 1, max(1, FPS)):
        subject.location.z = 0.06 * math.sin(f / FPS * 2.2)
        subject.keyframe_insert("location", index=2, frame=f)

# floor (reflective for cinematic; none for toon, where a flat backdrop reads better)
if STYLE != "toon":
    floor = add("plane", size=60, location=(0, 0, -2.3 if v["type"] != "chart3d" else -1.22))
    assign(floor, principled("floor", (wc[0] * 1.5, wc[1] * 1.5, wc[2] * 1.5, 1), 0.2, 0.18, coat=0.8))
    # light cards: seen only in reflections, they give chrome/gold something to mirror
    light_card((-3.5, -6, 3.5), 5, 5.0)
    light_card((4, -5, 1.5), 3, 3.0)
    light_card((0, 4, 6), 6, 2.0)
else:
    bg.inputs[0].default_value = tuple(min(1.0, c * 6 + 0.04) for c in wc) + (1,)

# lights


def area(name, loc, energy, color, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.color, data.size = energy, color[:3], size
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = loc
    direction = Vector((0, 0, 0)) - Vector(loc)
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


area("key", (-4, -5, 4), 900, (1, 0.97, 0.92), 4)
area("rim", (4.5, 4, 3), 1300, ACCENT, 3)
area("fill", (5, -4, 0.5), 250, ACCENT2, 5)
area("top", (0, 0, 7), 300, (1, 1, 1), 6)

# camera
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50 if not PORTRAIT else 42
cam_data.sensor_fit = "AUTO"
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
target = empty("target", (0, 0, 0.1 if not PORTRAIT else -0.1))
track = cam.constraints.new("TRACK_TO")
track.target, track.track_axis, track.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
dist = 10.5 if PORTRAIT else 8.0
if v["type"] == "chart3d":
    dist *= 1.15
move = spec.get("camera", "push_in")
paths = {
    "push_in": ((0, -dist * 1.15, 1.2), (0, -dist * 0.88, 0.7)),
    "pull_out": ((0, -dist * 0.85, 0.6), (0, -dist * 1.12, 1.3)),
    "dolly_left": ((dist * 0.28, -dist, 0.9), (-dist * 0.28, -dist, 0.9)),
    "dolly_right": ((-dist * 0.28, -dist, 0.9), (dist * 0.28, -dist, 0.9)),
    "crane_up": ((0, -dist, -0.4), (0, -dist * 0.95, 2.6)),
    "static": ((0, -dist, 0.8), (0, -dist, 0.8)),
    "handheld": ((0, -dist, 0.8), (0.05, -dist * 0.97, 0.85)),
}
if move == "orbit":
    pivot = empty("orbit")
    cam.parent = pivot
    cam.location = (0, -dist, 1.0)
    pivot.rotation_euler.z = math.radians(-22)
    pivot.keyframe_insert("rotation_euler", index=2, frame=1)
    pivot.rotation_euler.z = math.radians(22)
    pivot.keyframe_insert("rotation_euler", index=2, frame=FRAMES)
else:
    a, b = paths.get(move, paths["push_in"])
    cam.location = a
    cam.keyframe_insert("location", frame=1)
    cam.location = b
    cam.keyframe_insert("location", frame=FRAMES)
    if move == "handheld" and cam.animation_data:
        for fc in cam.animation_data.action.fcurves if hasattr(cam.animation_data.action, "fcurves") else []:
            noise = fc.modifiers.new("NOISE")
            noise.strength, noise.scale = 0.06, 18

# smooth every keyframe (ease in/out)
for obj in scene.objects:
    ad = obj.animation_data
    if ad and ad.action and hasattr(ad.action, "fcurves"):
        for fc in ad.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"
                kp.easing = "AUTO"

if STYLE != "toon":
    cam_data.dof.use_dof = True
    cam_data.dof.focus_object = target
    cam_data.dof.aperture_fstop = 3.2
else:
    scene.render.use_freestyle = True
    try:
        scene.render.line_thickness_mode = "ABSOLUTE"
        scene.render.line_thickness = max(1.0, W / 900)
        ls = scene.view_layers[0].freestyle_settings.linesets[0].linestyle
        ls.color = (0.03, 0.02, 0.04)
    except (AttributeError, IndexError):
        pass

# ----------------------------------------------------------------- render
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.compression = 15
scene.render.filepath = spec["frames_dir"].rstrip("/\\") + "/f_"
scene.render.use_overwrite = False
scene.render.use_placeholder = True
if spec.get("preview_frame"):
    scene.frame_set(int(spec["preview_frame"]))
    scene.render.filepath = spec["preview_path"]
    bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
print("VS_RENDER_DONE", scene.render.engine, FRAMES)
