#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_skp_rb.py — 从几何真源生成 SketchUp 建模脚本
================================================================================
读同目录的 plan_data.py，生成一份完整的 build_model.rb。
生成的脚本 `load` 同目录的 su_kit.rb（几何工具箱），因此工具箱更新会同步生效。

用法:
    python make_skp_rb.py                    # → ./build_model.rb
    python make_skp_rb.py -o D:/proj/bm.rb   # 指定输出

生成物由 su_pipeline.py 执行：
    python su_pipeline.py load
    python su_pipeline.py run D:/proj/bm.rb  WBAuto.build
    python su_pipeline.py verify

设计要点（都是实战踩出来的，改动时请保留）：
  · 标准层只建一次，再 array_floors 阵列 → 改一层同步 N 层
  · 家具只描述左户，右户由 mirror_box 自动镜像 **并重排 min/max**
  · 单元入口门单独放进首层组（放标准层会被阵列成 N 个门）
  · 湿区地面抬高 1mm 覆盖木地板，避免共面闪烁
  · 木格栅按「条宽 = 缝宽」细分，不用整块大板
  · 几何轴映射：SU_X = 图纸X，SU_Y = 高度，SU_Z = 图纸Y（SketchUp 是 Y 轴向上）
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import plan_data as P  # noqa: E402

KIT = os.path.join(HERE, "su_kit.rb").replace("\\", "/")


def ruby_openings(holes):
    """[(a, b, ':win'), ...] → Ruby 数组字面量"""
    if not holes:
        return "[]"
    items = ", ".join(f"[{a}, {b}, {k}]" for a, b, k in holes)
    return f"[{items}]"


def gen_header(g):
    return f'''# -*- coding: utf-8 -*-
# ============================================================================
# build_model.rb —— 由 make_skp_rb.py 从 plan_data.py 自动生成，请勿手工编辑
# ----------------------------------------------------------------------------
# 项目：{P.META["name"]}
# 数据源：{P.META["src_note"]}
# 重新生成：python make_skp_rb.py
#
# ★ 所有尺寸均取自 plan_data.py —— 与二维 CAD 图纸共用同一份数字，
#   因此「图纸与模型尺寸一致」是机械保证，不需要人工校对。
# ============================================================================
require 'json'
load '{KIT}'

module WBAuto
  MODEL = Sketchup.active_model

  # ------------------------------ 常量（mm）------------------------------
  W_TOT   = {g["w"]:.1f}          # 总开间
  D_TOT   = {g["d"]:.1f}          # 总进深
  Y_N     = {g["y_north"]:.1f}    # 北外墙轴线
  T_OUT   = {g["t_outer"]:.1f}    # 外墙厚
  T_IN    = {g["t_inner"]:.1f}    # 内墙厚
  T_LT    = {g["t_light"]:.1f}    # 轻质隔墙 / 栏板
  BAY     = {g["bay"]:.1f}        # 北向飘窗外挑
  BALC_D  = {g["balc_d"]:.1f}     # 南阳台外挑
  FLR_H   = {g["floor_h"]:.1f}    # 层高
  SLAB_T  = {g["slab_t"]:.1f}     # 楼板厚
  WALL_H  = {g["wall_h"]:.1f}     # 墙高
  N_FLOOR = {g["n_floor"]}
  SILL    = {g["sill"]:.1f}
  HEAD    = {g["head"]:.1f}
  HEAD_S  = {g["head_slide"]:.1f}
'''


def gen_materials():
    lines = ["\n  # ------------------------------ 材质（方案色卡）------------------------------",
             "  MATS = {"]
    for name, hexv in P.MATERIALS.items():
        lines.append(f"    '{name}' => 0x{hexv:06X},")
    lines.append("  }")
    lines.append("")
    lines.append("  ALPHA = {")
    for name, a in P.MATERIALS_ALPHA.items():
        lines.append(f"    '{name}' => {a},")
    lines.append("  }")
    lines.append("")
    lines.append("  LAYERS = %w[" + " ".join(P.LAYERS) + "]")
    return "\n".join(lines)


def gen_setup():
    g = P.GEOM
    # ⚠️ 必须整体用括号包住：Python 里 `return X` 换行后接 `+ Y` 会被解析成
    #    两条语句（`+Y` 当成一元加表达式，结果丢弃），导致后半段静默消失。
    return (
        f'''
  # ------------------------------ 初始化 ------------------------------
  def self.setup
    SUKit.setup(materials: MATS, layers: LAYERS, transparent: ALPHA)
    SUKit.clear
    true
  end

  # ------------------------------ 楼板 ------------------------------
  def self.build_slab(f)
    # 室内木地板（整体）
    SUKit.box(f, '地面_木地板', '楼板', '室内_地面_橡木地板',
              240, 240, W_TOT - 240, {g["y_north"] - 240:.0f}, 0, 20)
    # 湿区柔光砖：抬高 {P.WET_LIFT:.0f}mm 覆盖木地板，避免共面闪烁
'''
        + "".join(
            f"    SUKit.box(f, '地面_砖{i+1}L', '楼板', '室内_地面_柔光砖', "
            f"{x0}, {y0}, {x1}, {y1}, 20, {20 + P.WET_LIFT:.0f})\n"
            f"    SUKit.box(f, '地面_砖{i+1}R', '楼板', '室内_地面_柔光砖', "
            f"{g['w'] - x1:.0f}, {y0}, {g['w'] - x0:.0f}, {y1}, 20, {20 + P.WET_LIFT:.0f})\n"
            for i, (x0, y0, x1, y1) in enumerate(P.WET_AREAS)
        )
        + f'''    # 公共区（楼梯间）与阳台
    SUKit.box(f, '地面_楼梯间', '楼板', '室内_地面_柔光砖',
              {P.STAIR["x0"]}, {P.STAIR["y0"]}, {P.STAIR["x1"]}, {g["y_north"]:.0f}, 20, 21)
    SUKit.box(f, '地面_阳台', '楼板', '室内_地面_柔光砖',
              {P.BALCONY["x0"]}, -{P.BALCONY["depth"]}, {P.BALCONY["x1"]}, 0, 0, 20)
    # 楼板实体（结构）
    SUKit.box(f, '结构楼板', '楼板', '结构_混凝土楼板',
              0, 0, W_TOT, D_TOT, -SLAB_T, 0)
  end
'''
    )


def gen_walls():
    out = ["\n  # ------------------------------ 墙体 ------------------------------",
           "  def self.build_walls(f)"]
    for name, mat, x0, x1, y0, y1, holes in P.WALLS_X:
        out.append(f"    SUKit.wall_x(f, '{name}', '{mat}', {x0}, {x1}, {y0}, {y1}, "
                   f"{ruby_openings(holes)}, wall_h: WALL_H, sill: SILL, head: HEAD, "
                   f"head_slide: HEAD_S, frame: '立面_门窗框_中灰', glass: '玻璃_LowE中空')")
    for name, mat, y0, y1, x0, x1, holes in P.WALLS_Y:
        out.append(f"    SUKit.wall_y(f, '{name}', '{mat}', {y0}, {y1}, {x0}, {x1}, "
                   f"{ruby_openings(holes)}, wall_h: WALL_H, sill: SILL, head: HEAD, "
                   f"head_slide: HEAD_S, frame: '立面_门窗框_中灰', glass: '玻璃_LowE中空')")
    out.append("  end")
    return "\n".join(out)


def gen_bays():
    g = P.GEOM
    out = ["\n  # ------------------------------ 北向飘窗 ------------------------------",
           "  def self.build_bays(f)"]
    for i, (bx0, bx1) in enumerate(P.BAYS, 1):
        y0, y1 = g["y_north"], g["y_north"] + P.GEOM["bay"]
        out.append(f"""    # 飘窗{i}
    SUKit.box(f, '飘窗{i}_底板', '墙体', '立面_主墙面_暖白', {bx0}, {g["y_north"]:.0f}, {bx1}, {y1:.0f}, 0, 200)
    SUKit.box(f, '飘窗{i}_窗台', '墙体', '立面_主墙面_暖白', {bx0}, {g["y_north"]:.0f}, {bx1}, {g["y_north"] + 60:.0f}, 200, SILL)
    SUKit.box(f, '飘窗{i}_西侧', '墙体', '立面_主墙面_暖白', {bx0}, {g["y_north"]:.0f}, {bx0 + 60}, {y1:.0f}, 200, HEAD)
    SUKit.box(f, '飘窗{i}_东侧', '墙体', '立面_主墙面_暖白', {bx1 - 60}, {g["y_north"]:.0f}, {bx1}, {y1:.0f}, 200, HEAD)
    SUKit.box(f, '飘窗{i}_顶板', '墙体', '立面_主墙面_暖白', {bx0}, {g["y_north"]:.0f}, {bx1}, {y1:.0f}, HEAD, WALL_H)
    SUKit.box(f, '飘窗{i}_框下', '门窗', '立面_门窗框_中灰', {bx0 + 60}, {y1 - 70:.0f}, {bx1 - 60}, {y1:.0f}, 250, 310)
    SUKit.box(f, '飘窗{i}_框上', '门窗', '立面_门窗框_中灰', {bx0 + 60}, {y1 - 70:.0f}, {bx1 - 60}, {y1:.0f}, HEAD - 80, HEAD - 20)
    SUKit.box(f, '飘窗{i}_框左', '门窗', '立面_门窗框_中灰', {bx0 + 60}, {y1 - 70:.0f}, {bx0 + 120}, {y1:.0f}, 310, HEAD - 80)
    SUKit.box(f, '飘窗{i}_框右', '门窗', '立面_门窗框_中灰', {bx1 - 120}, {y1 - 70:.0f}, {bx1 - 60}, {y1:.0f}, 310, HEAD - 80)
    SUKit.box(f, '飘窗{i}_玻璃N', '门窗', '玻璃_LowE中空', {bx0 + 105}, {y1 - 30:.0f}, {bx1 - 105}, {y1 - 12:.0f}, 295, HEAD - 65)""")
    out.append("  end")
    return "\n".join(out)


def gen_balcony():
    b = P.BALCONY
    t, h = b["railing_t"], b["railing_h"]
    return f'''
  # ------------------------------ 南阳台 ------------------------------
  def self.build_balcony(f)
    x0, x1, d = {b["x0"]}, {b["x1"]}, BALC_D
    SUKit.box(f, '阳台_西栏板', '阳台', '立面_主墙面_暖白', x0, -d, x0 + {t}, 0, 0, {h})
    SUKit.box(f, '阳台_东栏板', '阳台', '立面_主墙面_暖白', x1 - {t}, -d, x1, 0, 0, {h})
    SUKit.box(f, '阳台_南栏板', '阳台', '立面_主墙面_暖白', x0, -d, x1, -d + {t}, 0, {h})
    # 分户隔板用次色，立面有主次层次
    SUKit.box(f, '阳台_中分隔', '阳台', '立面_次墙面_暖灰', {b["divider_x"]}, -d, {b["divider_x"] + 120}, 0, 0, {h})
    SUKit.box(f, '阳台_扶手玻璃1', '阳台', '玻璃_LowE中空',
              x0 + 120, -d + 30, {b["divider_x"]}, -d + 60, {b["glass_h0"]}, {b["glass_h1"]})
    SUKit.box(f, '阳台_扶手玻璃2', '阳台', '玻璃_LowE中空',
              {b["divider_x"] + 120}, -d + 30, x1 - 20, -d + 60, {b["glass_h0"]}, {b["glass_h1"]})
  end
'''


def gen_stair():
    s = P.STAIR
    return f'''
  # ------------------------------ 楼梯（双跑 U 型）------------------------------
  def self.build_stair(f)
    x0, x1, y0, y1 = {s["x0"]}, {s["x1"]}, {s["y0"]}, {s["y1"]}
    land = {s["landing_y"]}
    SUKit.box(f, '楼梯_休息平台', '楼梯', '结构_楼梯踏步', x0, y0, x1, land, 0, {s["landing_h"]})
    n = {s["steps"]}
    ry0, ry1 = {s["run_y0"]}, {s["run_y1"]}
    step_d = (ry1 - ry0).to_f / n
    step_h = {s["landing_h"]}.to_f / n
    (0...n).each do |i|
      ya = ry1 - step_d * (i + 1)
      yb = ry1 - step_d * i
      h  = step_h * (n - i)
      SUKit.box(f, "楼梯_踏步L#{{i + 1}}", '楼梯', '结构_楼梯踏步',
                x0 + 250, ya, {s["well_x"]}, yb, 0, h)
      SUKit.box(f, "楼梯_踏步R#{{i + 1}}", '楼梯', '结构_楼梯踏步',
                {s["well_x"] + 50}, ya, x1 - 250, yb, 0, h)
    end
    SUKit.box(f, '楼梯_梯井左', '楼梯', '室内_五金_哑黑', {s["well_x"]}, land, {s["well_x"] + 50}, ry1, 0, 1100)
  end
'''


def gen_furniture():
    out = ["\n  # ------------------------------ 家具（左户 + 镜像右户）------------------------------",
           "  # ⚠️ 只描述左户；mirror_box 会自动镜像并重排 min/max（不重排会让整户家具消失）",
           "  def self.build_furniture(f, mirrored)",
           "    b = SUKit.mirror_box(f, W_TOT, mirrored)",
           "    s = mirrored ? '_R' : '_L'"]
    for name, mat, layer, x0, y0, x1, y1, h0, h1 in P.FURNITURE:
        out.append(f"    b.call('{name}' + s, '{mat}', '{layer}', "
                   f"{x0}, {y0}, {x1}, {y1}, {h0}, {h1})")
    out.append("  end")
    return "\n".join(out)


def gen_facade():
    fa = P.FACADE
    g = P.GEOM
    lines = ["\n  # ------------------------------ 立面附加 ------------------------------",
             "  def self.build_facade(std)",
             f"    # 腰线（每层，突出外皮 {fa['belt_out']}）",
             f"    SUKit.box(std, '腰线_南', '立面', '立面_线条_煤灰铝板', "
             f"{g['ax_x'][1]}, -{fa['belt_out']}, {g['w'] - g['ax_x'][1]}, {fa['belt_out']}, "
             f"{fa['belt_h0']}, {fa['belt_h1']})",
             f"    SUKit.box(std, '腰线_北', '立面', '立面_线条_煤灰铝板', "
             f"0, Y_N, W_TOT, Y_N + {fa['belt_out']}, {fa['belt_h0']}, {fa['belt_h1']})",
             f"    SUKit.box(std, '腰线_西', '立面', '立面_线条_煤灰铝板', "
             f"-{fa['belt_out']}, {g['ax_y'][1]}, 0, Y_N, {fa['belt_h0']}, {fa['belt_h1']})",
             f"    SUKit.box(std, '腰线_东', '立面', '立面_线条_煤灰铝板', "
             f"W_TOT, {g['ax_y'][1]}, W_TOT + {fa['belt_out']}, Y_N, {fa['belt_h0']}, {fa['belt_h1']})",
             "    # 木格栅：按「条宽 = 缝宽」细分（整块大板不像格栅）"]
    for i, ((gx0, gx1), n) in enumerate(fa.get("louvers_s", []), 1):
        lines.append(f"    SUKit.louvers(std, '格栅_南{i}', {gx0}, {gx1}, "
                     f"-{fa['louver_out']}, 0, {n}, {fa['louver_h0']}, {fa['louver_h1']}, "
                     f"mat: '立面_格栅_浅橡木')")
    for i, ((gx0, gx1), n) in enumerate(fa.get("louvers_n", []), 1):
        lines.append(f"    SUKit.louvers(std, '格栅_北{i}', {gx0}, {gx1}, Y_N, "
                     f"Y_N + {fa['louver_out']}, {n}, {fa['louver_h0']}, {fa['louver_h1']}, "
                     f"mat: '立面_格栅_浅橡木')")
        lines.append(f"    SUKit.louvers(std, '格栅_北{i}R', W_TOT - {gx1}, W_TOT - {gx0}, Y_N, "
                     f"Y_N + {fa['louver_out']}, {n}, {fa['louver_h0']}, {fa['louver_h1']}, "
                     f"mat: '立面_格栅_浅橡木')")
    lines.append("  end")
    return "\n".join(lines)


def gen_roof():
    r = P.ROOF
    g = P.GEOM
    base = "(FLR_H * N_FLOOR)"
    return f'''
  # ------------------------------ 屋面 ------------------------------
  def self.build_roof(base_h)
    r = MODEL.entities.add_group
    r.name = 'A2_屋面'
    SUKit.box(r, '屋面板', '屋面', '结构_混凝土楼板', 0, 0, W_TOT, D_TOT, base_h - SLAB_T, base_h)
    SUKit.box(r, '屋面_找坡层', '屋面', '立面_基座_深灰花岗岩', 0, 0, W_TOT, D_TOT, base_h, base_h + {r["slope_t"]})
    i = {r["parapet_inset"]}.0
    h = {r["parapet_h"]}.0
    SUKit.box(r, '女儿墙_南', '屋面', '立面_主墙面_暖白', i, i, W_TOT - i, i + T_OUT, base_h, base_h + h)
    SUKit.box(r, '女儿墙_北', '屋面', '立面_主墙面_暖白', i, D_TOT - i - T_OUT, W_TOT - i, D_TOT - i, base_h, base_h + h)
    SUKit.box(r, '女儿墙_西', '屋面', '立面_主墙面_暖白', i, i, i + T_OUT, D_TOT - i, base_h, base_h + h)
    SUKit.box(r, '女儿墙_东', '屋面', '立面_主墙面_暖白', W_TOT - i - T_OUT, i, W_TOT - i, D_TOT - i, base_h, base_h + h)
    c = {r["cap_t"]}.0
    o = {r["cap_out"]}.0
    SUKit.box(r, '压顶_南', '屋面', '立面_线条_煤灰铝板', i - o, i - o, W_TOT - i + o, i + T_OUT + o, base_h + h, base_h + h + c)
    SUKit.box(r, '压顶_北', '屋面', '立面_线条_煤灰铝板', i - o, D_TOT - i - T_OUT - o, W_TOT - i + o, D_TOT - i + o, base_h + h, base_h + h + c)
    SUKit.box(r, '压顶_西', '屋面', '立面_线条_煤灰铝板', i - o, i - o, i + T_OUT + o, D_TOT - i + o, base_h + h, base_h + h + c)
    SUKit.box(r, '压顶_东', '屋面', '立面_线条_煤灰铝板', W_TOT - i - T_OUT - o, i - o, W_TOT - i + o, D_TOT - i + o, base_h + h, base_h + h + c)
    r
  end
'''


def gen_base():
    b = P.BASE
    e = b["entry"]
    g = P.GEOM
    return f'''
  # ------------------------------ 首层基座 + 单元入口（★ 仅首层）------------------------------
  # ⚠️ 入口门若是建在标准层里，阵列后每层都会出现一个门 —— 必须放这里。
  def self.build_base
    b = MODEL.entities.add_group
    b.name = 'A2_首层基座'
    SUKit.box(b, '基座_南', '立面', '立面_基座_深灰花岗岩', {g["ax_x"][1] - b["out"]}, -{b["out"]}, {g["w"] - g["ax_x"][1] + b["out"]}, {b["out"]}, 0, {b["h"]})
    SUKit.box(b, '基座_北', '立面', '立面_基座_深灰花岗岩', -{b["out"]}, Y_N, W_TOT + {b["out"]}, Y_N + {b["out"]}, 0, {b["h"]})
    SUKit.box(b, '基座_西', '立面', '立面_基座_深灰花岗岩', -{b["out"]}, {g["ax_y"][1]}, {b["out"]}, Y_N, 0, {b["h"]})
    SUKit.box(b, '基座_东', '立面', '立面_基座_深灰花岗岩', W_TOT - {b["out"]}, {g["ax_y"][1]}, W_TOT + {b["out"]}, Y_N, 0, {b["h"]})
    # 入口门头（雨棚）
    SUKit.box(b, '门头_立柱W', '立面', '立面_线条_煤灰铝板', {e["x0"] - e["col_w"]}, Y_N, {e["x0"]}, Y_N + {e["depth"]}, 0, {e["slab_h"]})
    SUKit.box(b, '门头_立柱E', '立面', '立面_线条_煤灰铝板', {e["x1"]}, Y_N, {e["x1"] + e["col_w"]}, Y_N + {e["depth"]}, 0, {e["slab_h"]})
    SUKit.box(b, '门头_顶板', '立面', '立面_线条_煤灰铝板', {e["x0"] - e["col_w"]}, Y_N, {e["x1"] + e["col_w"]}, Y_N + {e["depth"]}, {e["slab_h"]}, {e["head_h1"]})
    SUKit.box(b, '门头_木格栅', '立面', '立面_格栅_浅橡木', {e["x0"]}, Y_N + 60, {e["x1"]}, Y_N + {e["depth"] - 60}, {e["head_h0"]}, {e["head_h1"]})
    # 双扇玻璃门
    SUKit.entry_door(b, '单元入口', {e["x0"]}, {e["x1"]}, Y_N - T_OUT, Y_N,
                     h: HEAD, frame: '立面_门窗框_中灰', glass: '玻璃_LowE中空',
                     handle: '室内_金属_哑光黄铜')
    b
  end
'''


_MAIN_TMPL = '''
  # ============================== 主流程 ==============================

  def self.build
    setup

    std = MODEL.entities.add_group
    std.name = 'A2_标准层'
    build_slab(std)
    build_walls(std)
    build_bays(std)
    build_balcony(std)
    build_stair(std)
    build_furniture(std, false)      # 左户
    build_facade(std)

    # 阵列 @NFLOOR@ 层（标准层建一次，复制定义，改一层同步 N 层）
    SUKit.array_floors(std, N_FLOOR, FLR_H, name_prefix: 'A2_标准层')

    build_roof(FLR_H * N_FLOOR)
    build_base

    MODEL.active_view.zoom_extents
    report
  end

  def self.report
    sz = SUKit.size_m
    {
      project:   '@NAME@',
      floors:    N_FLOOR,
      floor_h:   FLR_H,
      total_h:   (FLR_H * N_FLOOR + 1130).round(0),
      size_m:    [sz[:width].round(2), sz[:height].round(2), sz[:depth].round(2)],
      materials: MODEL.materials.size,
      layers:    MODEL.layers.map(&:name),
      top:       MODEL.entities.map { |e| e.name.to_s.empty? ? e.typename : e.name }
    }
  end
end
'''


def gen_main():
    return (_MAIN_TMPL
            .replace("@NFLOOR@", str(P.GEOM["n_floor"]))
            .replace("@NAME@", P.META["name"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "build_model.rb"))
    args = ap.parse_args()

    g = P.GEOM
    parts = [
        gen_header(g),
        gen_materials(),
        gen_setup(),
        gen_walls(),
        gen_bays(),
        gen_balcony(),
        gen_stair(),
        gen_furniture(),
        gen_facade(),
        gen_roof(),
        gen_base(),
        gen_main(),
    ]
    src = "\n".join(parts)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(src)

    # ---------------- 生成物自检 ----------------
    # 防「Python 字符串拼接静默截断」：曾因 `return X` 换行接 `+ Y` 被解析成
    # 两条语句，导致整个方法体丢失、Ruby 报 end-of-input 却看不出缺在哪。
    n_def = src.count("\n  def self.")
    n_end = src.count("\n  end\n")
    if n_def != n_end:
        print(f"[X] 生成物结构异常：def {n_def} 个，方法级 end {n_end} 个（应相等）")
        print("    多半是某个 gen_* 的字符串拼接没生效，请检查该函数的 return 表达式。")
        return 1
    for token in ("WBAuto", "def self.build", "def self.build_slab"):
        if token not in src:
            print(f"[X] 生成物缺少 {token}")
            return 1

    # 顺手报一下外包尺寸，便于与图纸核对
    print(f"[OK] 生成 {args.out}  ({len(src)} 字符, {src.count(chr(10)) + 1} 行, "
          f"{n_def} 个方法)")
    print(f"     预期外包尺寸：{g['w']:.0f} x {g['d']:.0f} mm"
          f"（屋面顶标高 = {g['floor_h'] * g['n_floor'] + 1130:.0f}）")
    print(f"     层数 {g['n_floor']} / 层高 {g['floor_h']:.0f} / 墙高 {g['wall_h']:.0f}")
    print(f"     材质 {len(P.MATERIALS)} 种 / 图层 {len(P.LAYERS)} 个 / 家具 {len(P.FURNITURE)} 件（左户）")
    print("     下一步： python su_pipeline.py load && "
          f"python su_pipeline.py run {args.out.replace(chr(92), '/')} WBAuto.build")
    return 0


if __name__ == "__main__":
    main()
