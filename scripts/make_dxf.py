#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_dxf.py — 由几何真源生成二维 CAD 平面图（DXF + 预览 PNG）
================================================================================
读同目录的 plan_data.py，输出符合国标习惯的建筑平面图。

用法:
    python make_dxf.py                        # → plan.dxf + plan_preview.png
    python make_dxf.py -o D:/proj/plan.dxf

依赖: pip install ezdxf pillow matplotlib

--------------------------------------------------------------------------------
★ image-to-cad 的四条硬规矩（踩过才写进来的，别改）
--------------------------------------------------------------------------------
  1) 大字体写 `style.dxf.bigfont`（DXF 组码 4），**不要用 set_extended_font_data**
     ——后者写出来的 DXF 在 AutoCAD 里中文会变问号。
  2) `ezdxf.new(setup=True)` 自带的 TTF 样式（如 Annotative）**只改字体、不删除**
     ——删样式会让后续实体引用悬空。
  3) 中间路径全用 ASCII。中文文件名在 accoreconsole / 某些 CAD 版本里会失败，
     最后一步再复制成中文名。
  4) 线宽用「出图绝对值」（如 0.5mm = 50），**不随比例缩放**——线宽是打印属性。

--------------------------------------------------------------------------------
其它约定
--------------------------------------------------------------------------------
  · 模型 1:1，单位 mm；出图比例由 META['plot_den'] 控制（默认 1:100）。
  · 字高：模型单位 = 出图字高 × 比例分母。1:100 图上 2.5mm 字 → 模型 250。
  · 墙体切洞逻辑与三维端一致（复用同一套 segs），所以平面与模型不会「对不上」。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import plan_data as P  # noqa: E402

try:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment
    from ezdxf import bbox
except ImportError:
    print("[X] 缺少 ezdxf：pip install ezdxf")
    sys.exit(1)

G = P.GEOM
META = P.META

# ============================ §1 比例参数 ============================
PLOT_DEN = META.get("plot_den", 100.0)
MM = PLOT_DEN                     # 出图 mm → 模型单位
TXT_H = 2.5 * MM                  # 250  主要文字
TXT_H2 = 2.0 * MM                 # 200  次要文字 / 尺寸文字
TITLE_H = 3.5 * MM                # 350  图名


def m(v):
    """出图 mm → 模型单位"""
    return v * MM


# ============================ §2 文档与样式 ============================
doc = ezdxf.new("R2013", setup=True)
doc.units = units.MM
msp = doc.modelspace()

# 硬规矩 2：只改不删
for _s in doc.styles:
    _f = (_s.dxf.font or "").lower()
    if _s.dxf.name in ("Standard", "Annotative", "GB"):
        continue
    if _f.endswith(".ttf") or _f.endswith(".ttc") or not _f.endswith(".shx"):
        _s.dxf.font = "gbeitc.shx"
        _s.dxf.bigfont = "gbcbig.shx"

st = doc.styles.add("GB", font="gbeitc.shx")
st.dxf.bigfont = "gbcbig.shx"        # 硬规矩 1：大字体走 bigfont

# ============================ §3 图层 ============================
# (名称, 颜色, 线宽(1/100 mm), 线型)。线宽为出图绝对值，硬规矩 4。
LAYDEF = [
    ("轴线", 1, 13, "CENTER"),
    ("墙体", 7, 50, "Continuous"),
    ("门窗", 4, 25, "Continuous"),
    ("楼梯", 2, 25, "Continuous"),
    ("家具", 6, 13, "Continuous"),
    ("标注", 3, 18, "Continuous"),
    ("文字", 7, 18, "Continuous"),
    ("图框", 7, 50, "Continuous"),
    ("阳台", 4, 25, "Continuous"),
]
for _n, _c, _lw, _lt in LAYDEF:
    doc.layers.add(_n, color=_c, lineweight=_lw, linetype=_lt)

WALL = {"layer": "墙体"}
DOORW = {"layer": "门窗"}
BALC = {"layer": "阳台"}
FURN = {"layer": "家具"}
STAIR = {"layer": "楼梯"}
DIML = {"layer": "标注"}
TEXTL = {"layer": "文字", "style": "GB"}

# ============================ §4 标注样式 ============================
dim = doc.dimstyles.add("GB-100")
dim.dxf.dimtsz = m(2.5)      # 斜线端符（建筑惯例，不用箭头）
dim.dxf.dimtxt = TXT_H2
dim.dxf.dimexe = m(2)
dim.dxf.dimexo = m(2)
dim.dxf.dimgap = m(1)
dim.dxf.dimdec = 0
dim.dxf.dimscale = 1.0
dim.dxf.dimlfac = 1.0
dim.dxf.dimtxsty = "GB"
for _a in ("dimclrd", "dimclre", "dimclrt"):
    setattr(dim.dxf, _a, 256)


# ============================ §5 几何工具 ============================
def rect(x0, y0, x1, y1, attrib=None):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                       close=True, dxfattribs=attrib or WALL)


def segs(a0, a1, openings):
    """在一段 [a0,a1] 上挖去若干洞口，返回剩余实墙段。
    ★ 与三维端 su_kit.split 算法保持一致 —— 两边切法相同，平面与模型才对得上。"""
    out, cur = [], a0
    for s, e in sorted(openings):
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur < a1:
        out.append((cur, a1))
    return out


def wall_h(x0, x1, y0, y1, openings=()):
    """水平墙：直接给厚度区间 [y0, y1]（与 plan_data 的写法一致，避免 face 歧义）"""
    for s, e in segs(x0, x1, openings):
        rect(s, y0, e, y1)


def wall_v(y0, y1, x0, x1, openings=()):
    """垂直墙：厚度区间 [x0, x1]"""
    for s, e in segs(y0, y1, openings):
        rect(x0, s, x1, e)


def win_h(x0, x1, y0, y1):
    """水平墙上的窗：三线窗符号（内外皮 + 中线）"""
    for fy in (y0, (y0 + y1) / 2, y1):
        msp.add_line((x0, fy), (x1, fy), dxfattribs=DOORW)


def win_v(y0, y1, x0, x1):
    """垂直墙上的窗：三线窗符号"""
    for fx in (x0, (x0 + x1) / 2, x1):
        msp.add_line((fx, y0), (fx, y1), dxfattribs=DOORW)


def door_h(x0, x1, y0, y1, hinge="left"):
    """水平墙上的门：门扇线 + 90° 开启弧。hinge='left'/'right' 决定铰链侧。"""
    w = x1 - x0
    ys, yd = y1, y1 - w                 # 门扇向洞内（北）开启
    hx = x0 if hinge == "left" else x1
    if hinge == "left":
        msp.add_line((hx, ys), (hx, yd), dxfattribs=DOORW)
        msp.add_arc((hx, ys), w, 270, 360, dxfattribs=DOORW)
    else:
        msp.add_line((hx, ys), (hx, yd), dxfattribs=DOORW)
        msp.add_arc((hx, ys), w, 180, 270, dxfattribs=DOORW)


def txt(s, x, y, h=TXT_H, align=TextEntityAlignment.MIDDLE_CENTER, layer=TEXTL):
    t = msp.add_text(s, height=h, dxfattribs=layer)
    t.set_placement((x, y), align=align)
    return t


def dim_h(base_y, pts):
    """水平尺寸链：基准线在 base_y，测点为一串 x 坐标"""
    for a, b in zip(pts, pts[1:]):
        msp.add_linear_dim(base=(0, base_y), p1=(a, 0), p2=(b, 0),
                           angle=0, dimstyle="GB-100").render()


def dim_v(base_x, pts):
    """垂直尺寸链：基准线在 base_x，测点为一串 y 坐标"""
    for a, b in zip(pts, pts[1:]):
        msp.add_linear_dim(base=(base_x, 0), p1=(0, a), p2=(0, b),
                           angle=90, dimstyle="GB-100").render()


def axis_bubble(x, y, label, r=None):
    """轴号圆圈 + 编号。
    规范：圆直径 8—10mm（出图），字高 3.5mm。字高偏小会让轴号在缩略图上看不清。"""
    r = r or m(4)
    msp.add_circle((x, y), r, dxfattribs={"layer": "轴线"})
    txt(label, x, y, h=m(3.5), layer={"layer": "轴线", "style": "GB"})


# ============================ §6 墙体 ============================
print("生成墙体…")
for name, mat, x0, x1, y0, y1, holes in P.WALLS_X:
    wall_h(x0, x1, y0, y1, [(h[0], h[1]) for h in holes])
for name, mat, y0, y1, x0, x1, holes in P.WALLS_Y:
    wall_v(y0, y1, x0, x1, [(h[0], h[1]) for h in holes])

# ============================ §7 门窗 ============================
print("生成门窗…")
for name, mat, x0, x1, y0, y1, holes in P.WALLS_X:
    for a, b, kind in holes:
        if kind == ":win":
            win_h(a, b, y0, y1)
        elif kind == ":slide":
            # 推拉门：两道错开的扇
            msp.add_line((a, (y0 + y1) / 2 - 40), (b, (y0 + y1) / 2 - 40), dxfattribs=DOORW)
            msp.add_line((a, (y0 + y1) / 2 + 40), (b, (y0 + y1) / 2 + 40), dxfattribs=DOORW)
        elif kind == ":door":
            door_h(a, b, y0, y1, hinge="left")
for name, mat, y0, y1, x0, x1, holes in P.WALLS_Y:
    for a, b, kind in holes:
        if kind == ":win":
            win_v(a, b, x0, x1)

# 飘窗（外挑 BAY，三面围合）
for i, (bx0, bx1) in enumerate(P.BAYS, 1):
    y0 = G["y_north"]
    y1 = G["y_north"] + G["bay"]
    rect(bx0, y0, bx1, y1, DOORW)
    msp.add_line((bx0 + 60, y1 - 60), (bx1 - 60, y1 - 60), dxfattribs=DOORW)

# ============================ §8 阳台 ============================
print("生成阳台…")
B = P.BALCONY
bx0, bx1, bd, bt = B["x0"], B["x1"], B["depth"], B["railing_t"]
rect(bx0, -bd, bx1, 0, BALC)
rect(bx0, -bd, bx1, -bd + bt, BALC)
rect(bx0, -bd, bx0 + bt, 0, BALC)
rect(bx1 - bt, -bd, bx1, 0, BALC)
msp.add_line((B["divider_x"], -bd), (B["divider_x"], 0), dxfattribs=BALC)

# ============================ §9 楼梯 ============================
print("生成楼梯…")
S = P.STAIR
rect(S["x0"], S["y0"], S["x1"], S["y1"], STAIR)
msp.add_line((S["well_x"], S["y0"]), (S["well_x"], S["run_y1"]), dxfattribs=STAIR)
msp.add_line((S["x0"] + 250, S["run_y1"]), (S["well_x"], S["run_y1"]), dxfattribs=STAIR)
msp.add_line((S["well_x"] + 50, S["run_y1"]), (S["x1"] - 250, S["run_y1"]), dxfattribs=STAIR)
msp.add_line((S["x0"] + 250, S["landing_y"]), (S["x1"] - 250, S["landing_y"]), dxfattribs=STAIR)
n = S["steps"]
step_d = (S["run_y1"] - S["run_y0"]) / n
for i in range(n):                                  # 踏步线
    y = S["run_y0"] + step_d * (i + 1)
    msp.add_line((S["x0"] + 250, y), (S["well_x"], y), dxfattribs=STAIR)
    msp.add_line((S["well_x"] + 50, y), (S["x1"] - 250, y), dxfattribs=STAIR)

# ============================ §10 家具 ============================
print(f"生成家具 {len(P.FURNITURE)} 件（含镜像户）…")


def draw_furniture(mirrored):
    for name, mat, layer, x0, y0, x1, y1, h0, h1 in P.FURNITURE:
        if mirrored:
            xa, xb = G["w"] - x1, G["w"] - x0
        else:
            xa, xb = x0, x1
        rect(xa, y0, xb, y1, FURN)


draw_furniture(False)
if P.MIRROR.get("enabled"):
    draw_furniture(True)

# ============================ §11 房间名与面积 ============================
print("生成房间名与面积…")
for name, area, rx, ry in P.ROOMS:
    txt(f"{name}", rx, ry, h=TXT_H)
    txt(f"{area:.2f}m²", rx, ry - TXT_H * 1.3, h=TXT_H2)

# ============================ §12 轴网与轴号 ============================
print("生成轴网…")
ax_x, ax_y = G["ax_x"], G["ax_y"]
for x in ax_x:
    msp.add_line((x, -G["balc_d"] - 700), (x, G["y_north"] + G["bay"] + 700),
                 dxfattribs={"layer": "轴线"})
for y in ax_y:
    msp.add_line((-700, y), (G["w"] + 700, y), dxfattribs={"layer": "轴线"})

for i, x in enumerate(ax_x, 1):
    axis_bubble(x, -G["balc_d"] - 900, str(i))
    axis_bubble(x, G["y_north"] + G["bay"] + 900, str(i))
for j, y in enumerate(ax_y, 1):
    axis_bubble(-900, y, chr(64 + j))               # A, B, C ...
    axis_bubble(G["w"] + 900, y, chr(64 + j))

# ============================ §13 尺寸标注 ============================
print("生成尺寸标注…")
dim_h(-G["balc_d"] - 1500, ax_x)                     # 南侧开间尺寸链
dim_h(G["y_north"] + G["bay"] + 1500, ax_x)          # 北侧开间尺寸链
dim_v(-1500, ax_y)                                   # 西侧进深尺寸链
dim_v(G["w"] + 1500, ax_y)                           # 东侧进深尺寸链

# ============================ §14 图名与说明 ============================
txt(f"{META['name']} 平面图  1:{PLOT_DEN:.0f}", G["w"] / 2, -G["balc_d"] - 2400,
    h=TITLE_H, align=TextEntityAlignment.MIDDLE_CENTER)
txt(f"数据源：{META.get('src_note', '')}", G["w"] / 2, -G["balc_d"] - 2900,
    h=TXT_H2, align=TextEntityAlignment.MIDDLE_CENTER)
txt("注：家具高度为设计假定值，需业主确认；墙厚/洞口/层高为按常规做法的假定值。",
    G["w"] / 2, -G["balc_d"] - 3250, h=TXT_H2,
    align=TextEntityAlignment.MIDDLE_CENTER)


# ============================ §15 输出 ============================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "plan.dxf"))
    ap.add_argument("--preview", default=None,
                    help="预览 PNG 路径（默认与 DXF 同名）")
    args = ap.parse_args()

    doc.saveas(args.out)
    n = len(list(msp))
    size = os.path.getsize(args.out)
    print(f"\n[OK] DXF: {args.out}  ({n} 个图元, {size // 1024} KB)")

    box = bbox.extents(msp)
    if box.has_data:
        print(f"     图幅范围：{box.size.x:.0f} x {box.size.y:.0f} mm"
              f"（建筑本体 {G['w']:.0f} x {G['y_north'] + G['bay']:.0f}）")

    # 预览图（需要 matplotlib）
    prev = args.preview or os.path.splitext(args.out)[0] + "_preview.png"
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # ★ 不设中文字体会让所有中文变成方块（方框）——DXF 本身用的是 gbcbig.shx，
        #   在 AutoCAD 里正常，只有 matplotlib 预览需要这里指定。
        for _f in ("Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"):
            try:
                matplotlib.font_manager.findfont(_f, fallback_to_default=False)
                matplotlib.rcParams["font.sans-serif"] = [_f]
                break
            except Exception:
                continue
        matplotlib.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(16, 12), dpi=110)

        def iter_drawable():
            """★ DIMENSION 是匿名块引用，直接画看不到尺寸线与数字，
            必须用 virtual_entities() 展开成实际几何。"""
            for e in msp:
                if e.dxftype() == "DIMENSION":
                    try:
                        for v in e.virtual_entities():
                            yield v
                        continue
                    except Exception:
                        pass
                yield e

        for e in iter_drawable():
            t = e.dxftype()
            c = "#333333"
            if e.dxf.layer == "墙体":
                c = "#111111"
            elif e.dxf.layer == "门窗":
                c = "#1f6fb4"
            elif e.dxf.layer in ("家具",):
                c = "#9a9a9a"
            elif e.dxf.layer == "轴线":
                c = "#c83232"
            elif e.dxf.layer in ("标注", "文字"):
                c = "#2e7d32"

            if t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts = pts + [pts[0]]
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, lw=0.8)
            elif t == "LINE":
                ax.plot([e.dxf.start.x, e.dxf.end.x], [e.dxf.start.y, e.dxf.end.y],
                        color=c, lw=0.6)
            elif t == "CIRCLE":
                import matplotlib.patches as mp
                ax.add_patch(mp.Circle((e.dxf.center.x, e.dxf.center.y),
                                       e.dxf.radius, fill=False, color=c, lw=0.6))
            elif t == "ARC":
                import matplotlib.patches as mp
                ax.add_patch(mp.Arc((e.dxf.center.x, e.dxf.center.y),
                                    e.dxf.radius * 2, e.dxf.radius * 2,
                                    theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                    color=c, lw=0.6))
            elif t in ("TEXT", "MTEXT"):
                try:
                    # ★ 用 set_placement(align=CENTER/…) 设过对齐的 TEXT，
                    # 真实位置在 align_point（组码 11），insert（组码 10）往往留在原点。
                    # 只读 insert 会把所有居中文字画到 (0,0) 堆成一团。
                    pos = None
                    for attr in ("align_point", "insert"):
                        try:
                            p = getattr(e.dxf, attr)
                            if p is not None and (abs(p.x) > 1e-9 or abs(p.y) > 1e-9):
                                pos = p
                                break
                        except Exception:
                            continue
                    if pos is None:
                        pos = e.dxf.insert
                    s = e.dxf.text if t == "TEXT" else e.text
                    ax.text(pos.x, pos.y, s, fontsize=5.5, color=c,
                            ha="center", va="center")
                except Exception:
                    pass
        ax.set_aspect("equal")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(prev, facecolor="white")
        plt.close(fig)
        print(f"[OK] 预览: {prev}")
    except ImportError:
        print("[!] 未安装 matplotlib，跳过预览图（pip install matplotlib）")
    except Exception as e:
        print(f"[!] 预览生成失败（不影响 DXF）：{e}")

    print("\n下一步： python verify_consistency.py --dxf " +
          args.out.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
