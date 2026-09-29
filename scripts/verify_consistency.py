#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_consistency.py — 跨阶段一致性校验（把「图纸/模型/方案三向对齐」变成机械检查）
================================================================================
流水线最容易出的问题不是「画错」，而是**同一个数字在三个地方对不上**：
平面图开间 16600，模型里写成 16650，方案正文里又写 16.5 m —— 三处各自看都没毛病，
合起来就是事故。本脚本把这类问题在交付前一次性抓出来。

检查四层：

  A. 真源自洽      plan_data 内部：尺寸链闭合、进深与轴线关系、墙厚/洞口合法性
  B. DXF ↔ 真源    二维图纸的外包范围、图层是否与真源一致
  C. SKP ↔ 真源    三维模型的外包尺寸是否等于「真源 + 外凸量」
  D. 方案 ↔ 真源    方案正文里的建筑面积/开间数字是否与真源一致（可选）

用法:
    python verify_consistency.py                     # 有 DXF 就一起查
    python verify_consistency.py --dxf D:/p/plan.dxf
    python verify_consistency.py --skp-report D:/p/skp_report.json
    python verify_consistency.py --report D:/p/方案.html

退出码：0 = 全部通过；1 = 有失败项；2 = 有警告（需人工确认）
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import plan_data as P  # noqa: E402

G = P.GEOM

PASS, FAIL, WARN = [], [], []


def ok(msg):
    PASS.append(msg)
    print(f"  [OK]   {msg}")


def bad(msg):
    FAIL.append(msg)
    print(f"  [X]    {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  [!]    {msg}")


# --------------------------------------------------------------------------- #
# A. 真源自洽
# --------------------------------------------------------------------------- #
def check_source():
    print("\n== A. 几何真源自洽 ==")

    # A1 尺寸链闭合
    for name, (segs, total) in P.META.get("chains", {}).items():
        s = sum(segs)
        if abs(s - total) < 1e-6:
            ok(f"尺寸链「{name}」闭合：{' + '.join(str(x) for x in segs)} = {total}")
        else:
            bad(f"尺寸链「{name}」不闭合：和为 {s}，声明 {total}（差 {s - total}）")

    # A2 总开间与轴网最后一点一致
    if P.GEOM["ax_x"] and abs(P.GEOM["ax_x"][-1] - G["w"]) > 1e-6:
        bad(f"轴网 X 末点 {P.GEOM['ax_x'][-1]} ≠ 总开间 {G['w']}")
    else:
        ok(f"轴网 X 末点 = 总开间 = {G['w']:.0f}")

    # A3 北外墙内外皮（y_north 是外皮，不是轴线 —— 这点最容易搞混）
    ok(f"北外墙：外皮 {G['y_north']:.0f}，内皮 {G['y_north'] - G['t_outer']:.0f}"
       f"（墙厚 {G['t_outer']:.0f}）；墙体 Y 跨度 = {G['y_north']:.0f}")

    # A4 墙厚合理
    for k in ("t_outer", "t_inner", "t_light"):
        if not (60 <= G[k] <= 400):
            warn(f"{k} = {G[k]}，超出常规取值范围 60—400mm，请确认")

    # A5 层高与墙高
    if abs(G["wall_h"] - (G["floor_h"] - G["slab_t"])) > 1e-6:
        bad(f"墙高 {G['wall_h']} ≠ 层高 {G['floor_h']} − 楼板厚 {G['slab_t']}")
    else:
        ok(f"墙高 {G['wall_h']:.0f} = 层高 {G['floor_h']:.0f} − 楼板厚 {G['slab_t']:.0f}")

    # A6 门窗高度关系
    if G["sill"] >= G["head"]:
        bad(f"窗台高 {G['sill']} 不小于洞顶高 {G['head']}")
    if G["head_slide"] > G["wall_h"]:
        bad(f"推拉门顶高 {G['head_slide']} 超过墙高 {G['wall_h']}")

    # A7 洞口落在墙的范围内
    for name, mat, x0, x1, y0, y1, holes in P.WALLS_X:
        for a, b, kind in holes:
            if not (x0 - 1 <= a < b <= x1 + 1):
                bad(f"墙「{name}」洞口 [{a},{b}] 超出墙范围 [{x0},{x1}]")
    for name, mat, y0, y1, x0, x1, holes in P.WALLS_Y:
        for a, b, kind in holes:
            if not (y0 - 1 <= a < b <= y1 + 1):
                bad(f"墙「{name}」洞口 [{a},{b}] 超出墙范围 [{y0},{y1}]")
    ok(f"已核对 {len(P.WALLS_X) + len(P.WALLS_Y)} 道墙的洞口边界")

    # A8 洞口重叠（同墙上两个洞口相交会导致切洞后墙段为负）
    for label, walls, lo, hi in (("X", P.WALLS_X, 2, 3), ("Y", P.WALLS_Y, 0, 1)):
        for w in walls:
            holes = sorted([(h[0], h[1]) for h in w[6]], key=lambda t: t[0])
            for i in range(len(holes) - 1):
                if holes[i][1] > holes[i + 1][0]:
                    bad(f"墙「{w[0]}」洞口重叠：{holes[i]} 与 {holes[i+1]}")

    # A9 材质引用完整性：WALLS/FURNITURE 用到的材质必须在 MATERIALS 里定义
    declared = set(P.MATERIALS)
    used = set(getattr(P, "IMPLICIT_MATERIALS", []))   # 由工具箱按构造惯例自动使用
    for w in P.WALLS_X + P.WALLS_Y:
        used.add(w[1])
    for f in P.FURNITURE:
        used.add(f[1])
    missing = sorted(used - declared)
    if missing:
        bad(f"以下材质被构件引用但未在 MATERIALS 中定义（建模时会变成默认材质）：{missing}")
    else:
        ok(f"构件引用的 {len(used)} 种材质全部已在色卡中定义")

    unused = sorted(declared - used)
    if unused:
        warn(f"色卡里有 {len(unused)} 种材质没有任何构件引用，"
             f"clear! 后会被 purge_unused 静默删除：{unused}（若属有意保留可忽略）")
    else:
        ok(f"色卡 {len(declared)} 种材质全部会被真正使用（含工具箱隐式使用 {len(getattr(P, 'IMPLICIT_MATERIALS', []))} 种）")

    # A10 图层引用完整性
    lay = set(P.LAYERS)
    used_lay = {w[2] if len(w) > 2 else None for w in []}
    for f in P.FURNITURE:
        used_lay.add(f[2])
    miss_lay = sorted(x for x in used_lay if x and x not in lay)
    if miss_lay:
        bad(f"家具引用了未定义的图层：{miss_lay}")
    else:
        ok("家具图层全部已定义")

    # A11 家具落在建筑范围内（含阳台/飘窗外凸）
    ymin = -G["balc_d"]
    ymax = G["y_north"] + G["bay"]
    out = []
    for name, mat, layer, x0, y0, x1, y1, h0, h1 in P.FURNITURE:
        if x0 < 0 or x1 > G["w"] or y0 < ymin or y1 > ymax:
            out.append(name)
        if h1 > G["wall_h"] + 200:
            out.append(f"{name}(高 {h1} 超过墙高)")
    if out:
        warn(f"以下家具超出建筑外轮廓或过高，请确认是否为设计意图：{out}")
    else:
        ok(f"{len(P.FURNITURE)} 件家具均在建筑轮廓内且高度合理")

    # A12 湿区不重叠
    for i in range(len(P.WET_AREAS)):
        for j in range(i + 1, len(P.WET_AREAS)):
            a, b = P.WET_AREAS[i], P.WET_AREAS[j]
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                bad(f"湿区 {i+1} 与 {j+1} 重叠：{a} / {b}")
    ok("湿区范围无重叠")


# --------------------------------------------------------------------------- #
# B. DXF ↔ 真源
# --------------------------------------------------------------------------- #
def check_dxf(path):
    print(f"\n== B. DXF ↔ 真源  ({os.path.basename(path)}) ==")
    try:
        import ezdxf
        from ezdxf import bbox
    except ImportError:
        warn("未安装 ezdxf，跳过 DXF 检查（pip install ezdxf）")
        return

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    box = bbox.extents(msp)
    if not box.has_data:
        bad("DXF 中没有可见图元")
        return

    print(f"  全图幅：{box.size.x:.0f} x {box.size.y:.0f} mm"
          f"（含轴号圆圈与尺寸链，必然大于建筑本体）")

    # ★ 核对建筑本体要用「墙体」图层的范围 —— 全图幅含轴号(±900)与尺寸链(±1500)，
    #   拿全图幅去比会对不上，那属于图纸内容的正常外扩。
    wall_ents = [e for e in msp if e.dxf.layer == "墙体"]
    if not wall_ents:
        warn("DXF 里没有「墙体」图层图元，改用全图幅核对（结果仅供参考）")
        ww, dd = box.size.x, box.size.y
    else:
        wb = bbox.extents(wall_ents)
        ww, dd = wb.size.x, wb.size.y
        print(f"  墙体范围：{ww:.0f} x {dd:.0f} mm")

    # 墙体 X = 总开间；墙体 Y = 南外皮(0) → 北外墙外皮(y_north)
    exp_wall_y = G["y_north"]
    if abs(ww - G["w"]) < 1.0:
        ok(f"DXF 墙体开间 {ww:.0f} = 真源总开间 {G['w']:.0f}")
    elif abs(ww - G["w"]) < 300:
        warn(f"DXF 墙体开间 {ww:.0f} 与真源 {G['w']:.0f} 差 {ww - G['w']:.0f}mm，请核对")
    else:
        bad(f"DXF 墙体开间 {ww:.0f} 与真源 {G['w']:.0f} 相差过大（{ww - G['w']:.0f}）")

    if abs(dd - exp_wall_y) < 1.0:
        ok(f"DXF 墙体进深 {dd:.0f} = 真源「南外皮 0 → 北外墙外皮 {exp_wall_y:.0f}」")
    elif abs(dd - exp_wall_y) < 300:
        warn(f"DXF 墙体进深 {dd:.0f} 与真源 {exp_wall_y:.0f} 差 {dd - exp_wall_y:.0f}mm，请核对")
    else:
        bad(f"DXF 墙体进深 {dd:.0f} 与真源 {exp_wall_y:.0f} 相差过大（{dd - exp_wall_y:.0f}）")

    # 图层：平面图有「轴线/标注/文字/图框」等二维专有图层，也有「屋面/楼板」等
    # 只在三维用到的图层，因此**只检查是否出现未声明的图层**，不要求全量覆盖。
    PLAN_ONLY = {"轴线", "标注", "文字", "图框"}
    allowed = set(P.LAYERS) | PLAN_ONLY | {"0", "Layer0", "Defpoints"}   # 0 是 DXF 默认图层
    dxf_layers = {l.dxf.name for l in doc.layers}
    unknown = sorted(dxf_layers - allowed)
    if unknown:
        warn(f"DXF 出现未声明的图层：{unknown}（建议并入 LAYERS 或 PLAN_ONLY）")
    else:
        ok(f"DXF 图层全部在允许集合内（三维图层 {len(P.LAYERS)} 个 + 二维专有 {len(PLAN_ONLY)} 个）")

    absent_3d = sorted(set(P.LAYERS) - dxf_layers)
    if absent_3d:
        print(f"  （平面图不含三维专有图层：{absent_3d}，属正常）")

    n = len(list(msp))
    print(f"  图元 {n} 个 / 图层 {len(dxf_layers)} 个")
    ok("DXF 读取正常")


# --------------------------------------------------------------------------- #
# C. SKP ↔ 真源
# --------------------------------------------------------------------------- #
def check_skp(report_path=None, live=None):
    print("\n== C. SKP ↔ 真源 ==")

    data = None
    if report_path and os.path.exists(report_path):
        data = json.load(open(report_path, encoding="utf-8"))
        print(f"  报告来源：{report_path}")
    elif live:
        data = live

    if data is None:
        # 尝试直接向 SketchUp 询问
        try:
            sys.path.insert(0, HERE)
            from su_pipeline import step
            res = step("读取模型尺寸",
                       "'%.2f,%.2f,%.2f' % [Sketchup.active_model.bounds.width.to_m,"
                       " Sketchup.active_model.bounds.height.to_m,"
                       " Sketchup.active_model.bounds.depth.to_m]",
                       timeout=30, quiet=True)
            if res:
                wx, hy, dz = (float(x) for x in res.split(","))
                data = {"size_m": [wx, hy, dz]}
            else:
                warn("SketchUp 未响应，跳过 SKP 检查（可先 su_pipeline.py probe 探活）")
                return
        except Exception as e:
            warn(f"无法连接 SketchUp（{e}），跳过 SKP 检查")
            return

    size = data.get("size_m") or data.get("size")
    if not size:
        warn("报告里没有 size_m 字段，跳过")
        return

    # 预期外包（米）：逐方向取所有构件的极值，把所有外凸构件算进去
    belt = float(P.FACADE.get("belt_out", 60))             # 腰线东西各外凸量
    exp_w = (G["w"] + 2 * belt) / 1000.0
    # 高：从结构楼板底（−slab_t）到女儿墙顶 + 压顶
    exp_h = (G["floor_h"] * G["n_floor"]
             + P.ROOF["parapet_h"] + P.ROOF["cap_t"] + G["slab_t"]) / 1000.0
    # 深：北侧最远构件（飘窗 or 入口雨棚）→ 南阳台外沿
    z_north = max(G["y_north"] + G["bay"], G["y_north"] + P.BASE["entry"]["depth"])
    exp_d = (z_north + G["balc_d"]) / 1000.0

    print(f"  模型实际：X {size[0]:.2f} / Y(高) {size[1]:.2f} / Z(深) {size[2]:.2f} m")
    print(f"  真源预期：X {exp_w:.2f} / Y(高) {exp_h:.2f} / Z(深) {exp_d:.2f} m")
    print(f"    推导：X = 开间 {G['w']:.0f} + 腰线 2×{belt:.0f}")
    print(f"          Y = 层高 {G['floor_h']:.0f}×{G['n_floor']} + 女儿墙 {P.ROOF['parapet_h']:.0f}"
          f" + 压顶 {P.ROOF['cap_t']:.0f} + 楼板底 {G['slab_t']:.0f}")
    print(f"          Z = max(北墙+飘窗 {G['y_north'] + G['bay']:.0f}, "
          f"北墙+雨棚 {G['y_north'] + P.BASE['entry']['depth']:.0f}) + 阳台 {G['balc_d']:.0f}")

    for label, act, exp in (("开间 X", size[0], exp_w), ("高度 Y", size[1], exp_h), ("进深 Z", size[2], exp_d)):
        if abs(act - exp) <= 0.06:                # 容差 60mm
            ok(f"{label} 一致（{act:.2f} ≈ {exp:.2f} m）")
        elif abs(act - exp) <= 0.5:
            warn(f"{label} 差 {abs(act-exp)*1000:.0f}mm（{act:.2f} vs {exp:.2f} m），请核对外凸构件")
        else:
            bad(f"{label} 偏差过大：实际 {act:.2f} m，真源 {exp:.2f} m（差 {abs(act-exp)*1000:.0f}mm）")

    mats = data.get("materials")
    if isinstance(mats, int):
        if mats == len(P.MATERIALS):
            ok(f"材质数 {mats} = 真源色卡 {len(P.MATERIALS)} 种")
        else:
            warn(f"材质数 {mats} ≠ 真源色卡 {len(P.MATERIALS)} 种"
                 f"（少了通常是 clear! 后被 purge_unused 删除，用 su_pipeline.py materials 审计）")


# --------------------------------------------------------------------------- #
# D. 方案正文 ↔ 真源
# --------------------------------------------------------------------------- #
def check_report(path):
    print(f"\n== D. 方案正文 ↔ 真源  ({os.path.basename(path)}) ==")
    try:
        html = open(path, encoding="utf-8").read()
    except UnicodeDecodeError:
        html = open(path, encoding="gbk", errors="ignore").read()

    text = re.sub(r"<[^>]+>", " ", html)

    # 总开间 / 总进深：正文里出现的米制数字应能对应真源
    pats = [
        (f"{G['w']/1000:.2f}", "总开间"),
        (f"{G['w']/1000:.1f}", "总开间"),
        (f"{G['w']:.0f}", "总开间(mm)"),
    ]
    found = [p for p, _ in pats if p in text]
    if found:
        ok(f"正文明文含总开间数字：{', '.join(sorted(set(found)))}")
    else:
        warn(f"正文未找到总开间 {G['w']/1000:.2f} m 或 {G['w']:.0f} 的表述，请确认是否漏写")

    # 房间面积
    miss_room = []
    for name, area, rx, ry in P.ROOMS:
        if name not in text:
            miss_room.append(name)
    if miss_room:
        warn(f"正文未提及以下房间：{miss_room}")
    else:
        ok(f"{len(P.ROOMS)} 个房间名称在正文中全部出现")

    # 层高
    if f"{G['floor_h']/1000:.2f}" in text or f"{G['floor_h']/1000:.1f}" in text:
        ok(f"正文含层高 {G['floor_h']/1000:.2f} m")
    else:
        warn(f"正文未找到层高 {G['floor_h']/1000:.2f} m")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dxf", default=os.path.join(HERE, "plan.dxf"))
    ap.add_argument("--skp-report", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--skip-dxf", action="store_true")
    args = ap.parse_args()

    print("=" * 74)
    print(f"跨阶段一致性校验 · {P.META['name']}")
    print("=" * 74)

    check_source()

    if not args.skip_dxf:
        if os.path.exists(args.dxf):
            check_dxf(args.dxf)
        else:
            warn(f"未找到 DXF（{args.dxf}），跳过 B 段（--skip-dxf 可显式关闭）")

    check_skp(args.skp_report)

    if args.report:
        if os.path.exists(args.report):
            check_report(args.report)
        else:
            warn(f"未找到方案正文 {args.report}")

    print("\n" + "=" * 74)
    print(f"通过 {len(PASS)} 项 / 警告 {len(WARN)} 项 / 失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败项：")
        for m in FAIL:
            print("  - " + m)
    if WARN:
        print("\n警告项（需人工确认，不一定是错）：")
        for m in WARN:
            print("  - " + m)
    print("=" * 74)

    if "--no-warn-exit" not in sys.argv and not FAIL and WARN:
        return 2
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
