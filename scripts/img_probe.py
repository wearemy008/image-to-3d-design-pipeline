# -*- coding: utf-8 -*-
"""
img_probe.py — 透视效果图复原工具箱（图像 → 尺度 / 色板 / 材质 / 比例）
================================================================================
解决的问题
--------------------------------------------------------------------------------
本流水线原本只会读**有尺寸标注的施工图**。遇到一张**没有标注的透视效果图**
（AIGC 渲染、参考图、照片）时，尺寸全靠「假定」，材质全靠「手挑」，
复原能力等于零。本脚本把「从图里量出真实尺寸」这件事变成可执行、可复核的流程。

三条独立能力（可单独用，也可联用）
--------------------------------------------------------------------------------
  1. 尺度标定 scale   —— 透视几何的核心。用一个已知高度的物体标定相机高度 h_cam，
                         之后图上**任意位置、任意深度**的水平尺寸都能读出来。
  2. 色板提取 palette —— k-means 主色提取 → 归一化 → 直接给出可写进 MATERIALS 的 Hex，
                         并按「受光面/背光面」成对给出（同一材质的两个色阶）。
  3. 图像矫正 rectify —— 四点单应性把一个透视立面拉成正投影面，
                         拉正后**逐像素量测就是真实尺寸**（最可靠的一招）。

命令行
--------------------------------------------------------------------------------
  python img_probe.py info    <图>                          图像体检（尺寸/直方图/动态范围/水印）
  python img_probe.py scale   <图> --horizon Y --anchors "x,yb,hp,H;..."   尺度标定
  python img_probe.py palette <图> -k 8 --pair             主色提取 + 明暗成对
  python img_probe.py rectify <图> --quad "x,y;x,y;x,y;x,y" --w 12000 -o r.png
  python img_probe.py probe   <图> --anchors "..." -k 6    一条龙，输出 JSON 供 plan_data 消费

坐标系与单位
--------------------------------------------------------------------------------
  · 图像坐标 (x, y)：原点在**左上角**，x 向右、y 向下，单位**像素**。
  · 全部长度参数用**毫米**（与 plan_data 一致），输入时直接写 1700 表示 1.7 m 人高。
  · --horizon 是地平线所在的**图像行号 y**（像素），由 §1 的方法测得。
  · --anchors 语法："像素x,基点y,像素高,真实高mm"，多个用分号分隔。
  · ★ 锚点像素高 <40px 时结果只能作量级参考（见 assumption_report 的分辨率检查）。

★ 为什么要自己标定而不是套相机 EXIF
--------------------------------------------------------------------------------
  效果图没有 EXIF；就算有，AIGC 渲染的「相机」也是虚构的（常是 16~24mm 等效广角）。
  本脚本**不猜焦距**，只用「已知高度锚点 + 地平线」这一个可观测量做标定，
  得到的比例在数学上是**精确的**（见 scale 的推导），与焦距无关。

单位：像素。返回 JSON 时所有长度给毫米。
"""

import sys
import os
import json
import math

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    print("[X] 缺少 Pillow：pip install pillow numpy")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("[X] 缺少 numpy：pip install numpy")
    sys.exit(1)


# ============================================================
# §0  通用工具
# ============================================================

def load_rgb(path):
    im = Image.open(path)
    im = im.convert("RGB")
    return im


def pil_font(size=18):
    """取一个能渲染中文的字体；找不到就退回默认（ASCII 仍可用）。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        try:
            f = ImageFont.truetype("C:/Windows/Fonts/" + name, size)
            return f
        except Exception:
            continue
    return ImageFont.load_default()


def hx(rgb):
    return "#%02X%02X%02X" % tuple(int(round(c)) for c in rgb[:3])


def unhex(h):
    h = h.strip().lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def parse_anchors(s):
    """'x,yb,hp,H;x,yb,hp,H' → [ (x, yb, hp, H_mm), ... ]"""
    out = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        v = [float(t) for t in part.split(",")]
        if len(v) != 4:
            raise ValueError("anchor 需要 4 个数 x,yb,hp,H，得到 %r" % part)
        out.append(tuple(v))
    return out


def parse_quad(s):
    """'x,y;x,y;x,y;x,y' → [(x,y) x4]，顺序必须 左上→右上→右下→左下"""
    pts = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        v = [float(t) for t in part.split(",")]
        if len(v) != 2:
            raise ValueError("quad 每点需要 2 个数 x,y，得到 %r" % part)
        pts.append((v[0], v[1]))
    if len(pts) != 4:
        raise ValueError("quad 需要 4 个点")
    return pts


# ============================================================
# §1  地平线测量
# ============================================================
#
# 地平线 y_h 是整套尺度换算的基准，**测错则所有尺寸按比例全错**。
# 三个测法，按可信度排序：
#
#   (a) 灭点法 ★★★ 最可靠
#       找两组**水平方向**的平行线（楼板线 / 檐口线 / 地面拼缝 / 幕墙横缝），
#       各自延长求交点。两个交点都在同一条水平线上 → 那条线就是地平线。
#       手工作法：在图上用画线工具延长两族水平线，读交点行号取平均。
#
#   (b) 灭点法（自动）★★★
#       python img_probe.py horizon <图> --lines "x1,y1,x2,y2;..."  传 ≥6 条水平线
#       脚本对每族做最小二乘直线拟合并求交，抗噪。
#
#   (c) 参照物法 ★★ 应急
#       找不到平行线时，用「人 / 树 / 车」这些**站立物**的基点连线
#       —— 站立物基点都在地面上，其连线在远处收敛于地平线。
#       但要注意：只有当这些基点**与相机高度接近或更低**、且站位**足够远**时才准。
#
#   ✗ 错误做法：把「画面中线」当地平线。只有相机光轴完全水平时二者才重合，
#     而效果图几乎都是俯视/仰视，画面中线会偏几百像素，整张图的尺寸全错。


def cmd_horizon(args):
    """由多条水平线求地平线。--lines 'x1,y1,x2,y2;...'"""
    im = load_rgb(args.img)
    lines = []
    for part in args.lines.split(";"):
        part = part.strip()
        if not part:
            continue
        v = [float(t) for t in part.split(",")]
        if len(v) != 4:
            raise ValueError("每条线 4 个数 x1,y1,x2,y2，得到 %r" % part)
        if abs(v[2] - v[0]) < 1e-6:
            raise ValueError("水平线两端 x 不能相同（那是竖线）: %r" % part)
        lines.append(tuple(v))

    if len(lines) < 4:
        print("[X] 至少给 4 条水平线（建议 6 条，分布在画面左右与高低不同处）")
        return 1

    # 最小二乘拟合 y = a x + b
    A = np.zeros((2, 2))
    B = np.zeros(2)
    for x1, y1, x2, y2 in lines:
        xm = (x1 + x2) / 2.0
        ym = (y1 + y2) / 2.0
        dx = (x2 - x1) / 2.0
        A[0, 0] += 2 * dx * dx
        A[0, 1] += 2 * dx
        A[1, 1] += 2.0
        B[0] += 2 * dx * ym
        B[1] += 2.0 * ym
    a, b = np.linalg.solve(A, B)

    # 残差
    res = [abs(y1 - (a * x1 + b)) for x1, y1, x2, y2 in lines]
    rms = math.sqrt(sum(r * r for r in res) / len(res))

    # 交点：x_h = -b / a   （a<0 表示线向下收敛，即地平线在上方）
    if abs(a) < 1e-9:
        x_h, y_h = None, None
    else:
        x_h = -b / a
        y_h = 0.0

    print("[地平线] y_h = %.1f px" % y_h if y_h is not None else "[地平线] 线族近乎平行，无有限交点")
    print("  拟合    y = %.6f x + %.2f" % (a, b))
    print("  交点x   %s" % ("%.1f" % x_h if x_h is not None else "—"))
    print("  残差RMS %.2f px  （>3 px 说明线没选准，或图有畸变）" % rms)
    print("  图像    %d x %d" % im.size)
    print("  交点是否在画面内：%s" % ("是" if (x_h is not None and -im.size[0] <= x_h <= 2 * im.size[0]) else "否（在画外，属正常）"))

    json_out = {
        "img": os.path.basename(args.img),
        "size": list(im.size),
        "horizon_y": y_h,
        "horizon_x": x_h,
        "slope": a,
        "intercept": b,
        "rms_px": rms,
        "n_lines": len(lines),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(json_out, f, ensure_ascii=False, indent=2, default=float)
        print("  → 已写 %s" % args.json)
    return 0


# ============================================================
# §2  尺度标定（本脚本的核心）
# ============================================================
#
# 推导（水平相机，针孔模型，光轴与地面平行）
# ------------------------------------------------------------------
#   设相机高 h_cam，地平线行号 y_h，图像焦距 f（像素）。
#   地面上一深距 Z 的点，其像点行号满足：
#        y − y_h = f · h_cam / Z            …… (1)
#   即 **比例尺（米/像素）= Z / f = h_cam / (y − y_h)**      …… (2)
#
#   ★ 式 (2) 里 f 被消掉了 —— 这就是本方法的价值：
#     **不需要知道焦距，也不需要 EXIF。**
#
#   标定：取一个已知真实高度 H 的站立物（人 1700 / 树 8000 / 车 1500 / 栏杆 1100），
#         量出它的像素高 h_px 与基点行号 y_b。由 (1) 对**基点**与**顶���**各写一次：
#             y_b − y_h = f·h_cam/Z
#             y_t − y_h = f·(h_cam − H)/Z
#         两式相减：
#             h_px = y_b − y_t = (y_b − y_h)·H / h_cam
#         移项即得相机高度：
#             ★ h_cam = H · (y_b − y_h) / h_px              …… (3)
#
#   之后任意位置：给一个像点行号 y，取 y_mid = (y_top + y_bot)/2，
#                  该处水平距离 L(mm) = Δx_px · h_cam / (y_mid − y_h)   …… (4)
#
#   垂直方向同理：高度 H = Δy_px · h_cam / (y_bot − y_h)。
#
# ★ 适用前提（不满足则结果不可信，务必读 §2 的 assumption_report）
#     1. 相机光轴水平（俯视图会系统性高估尺寸）
#     2. 锚点物的**基点确实落在同一水平地面**上
#     3. 锚点物的像素高用**垂直像素量**，且它大体平行于像平面（人/树/车都满足）
#
# ★ 多锚点交叉验证
#     提供多个锚点时，脚本会分别算 h_cam 并给出离散度。
#     离散度 >8% 说明：地平线测错 / 锚点选错 / 地面不水平 / 图有畸变。
#     **离散度是本方法唯一诚实的自检指标，不要跳过看。**


def solve_cam_height(y_h, anchors):
    """由多个锚点解相机高度。返回 (中位 h_cam, 各点结果, 离散度%)"""
    res = []
    for x, y_b, h_px, H in anchors:
        if abs(y_b - y_h) < 1e-6:
            res.append({"x": x, "y_base": y_b, "h_px": h_px, "H_mm": H,
                        "h_cam": None, "err": "基点落在地平线上，无法解算"})
            continue
        h_cam = H * (y_b - y_h) / h_px
        res.append({"x": x, "y_base": y_b, "h_px": h_px, "H_mm": H, "h_cam": h_cam})
    vals = sorted(r["h_cam"] for r in res if r.get("h_cam"))
    if not vals:
        return None, res, None
    med = vals[len(vals) // 2]
    if len(vals) == 1:
        return med, res, 0.0
    spread = (vals[-1] - vals[0]) / med * 100.0
    return med, res, spread


def assumption_report(im, y_h, anchors, h_cam, spread):
    """把「这个标定可不可信」讲清楚。返回 (等级, 说明列表)"""
    W, Hh = im.size
    notes = []
    lvl = "good"

    if y_h is None:
        return "bad", ["地平线未测得 —— 整套换算无从谈起"]

    if y_h <= 0:
        notes.append("地平线在画面之上：相机近乎平视或微仰，尺度换算仍然有效（这反而是好情况）")
    if y_h >= Hh * 0.92:
        notes.append("地平线贴近画面底部：相机**高角度俯视**，式(2)的水平假设被破坏，"
                     "解出的 h_cam 偏小、远景尺寸会被**高估**，请只用近景锚点并逐项复核")
        lvl = "warn"
    if y_h > Hh * 0.75 and y_h < Hh * 0.92:
        notes.append("地平线偏下：中等俯角，建议只用画面下半部（近景）做量测")
        lvl = "warn"

    # ★ 分辨率硬约束（2026-09-29 实测加入）
    # 人物在 AIGC 效果图上常常只有 14~18 px 高，边缘模糊 2~3 px。
    # 端点定位误差 ±1.5 px → 相对误差 ±1.5/h_px，h_px=16 时就是 ±9%，
    # 再叠加「这个像素到底是不是人头顶/脚底」的语义误差，实测总误差 >20%。
    if anchors:
        hmin = min(a[2] for a in anchors)
        hmax = max(a[2] for a in anchors)
        if hmax < 25:
            notes.append("锚点像素高最大仅 %.0f px —— 端点定位误差已达 ±%.0f%%，"
                         "结果只能作**量级参考**。" % (hmax, 1.5 / hmax * 100))
            lvl = "bad" if hmax < 15 else ("warn" if lvl == "good" else lvl)
        elif hmax < 40:
            notes.append("锚点像素高最大 %.0f px，定位误差约 ±%.0f%%，建议再找更高精度的锚点"
                         "（如近处人物 ≥60px、或已知跨度的窗/柱/台阶）" % (hmax, 1.5 / hmax * 100))
            if lvl == "good":
                lvl = "warn"
        else:
            notes.append("锚点像素高最大 %.0f px，定位误差约 ±%.0f%% —— 分辨率充足 ★"
                         % (hmax, 1.5 / hmax * 100))

    if h_cam is not None:
        if h_cam < 900:
            notes.append("解出相机高 %.0f mm（<0.9 m）明显偏低 —— 锚点多半选错（"
                         "如把栏杆当人、把树冠当整树），或地平线偏低" % h_cam)
            lvl = "warn"
        if h_cam > 60000:
            notes.append("解出相机高 %.0f mm（>60 m）多半是把远处物体当近景锚点了" % h_cam)
            lvl = "warn"

    if spread is not None:
        if spread > 15:
            notes.append("多锚点离散度 %.1f%% —— **结果不可用**。要么地平线错，"
                         "要么锚点不在同一地面，要么图片经过畸变/AI 重绘" % spread)
            lvl = "bad"
        elif spread > 8:
            notes.append("多锚点离散度 %.1f%% —— 偏大，建议只信中位值对应的锚点，"
                         "并对每条量测结果留 ±10%% 余量" % spread)
            if lvl == "good":
                lvl = "warn"
        elif spread > 0 and spread <= 8:
            notes.append("多锚点离散度 %.1f%% —— 各锚点互相印证，标定可靠 ★" % spread)
    else:
        notes.append("只给了 1 个锚点：**无法交叉验证**。强烈建议再找一个同类锚点复核")

    return lvl, notes


def cmd_scale(args):
    im = load_rgb(args.img)
    W, Hh = im.size
    y_h = args.horizon
    anchors = parse_anchors(args.anchors)

    print("=" * 64)
    print("尺度标定  %s  (%d x %d px)" % (os.path.basename(args.img), W, Hh))
    print("=" * 64)
    print("地平线 y_h = %.1f" % y_h)
    print("锚点 %d 个：" % len(anchors))
    print("  %-8s %-10s %-10s %-10s %-12s" % ("x", "基点y", "像素高", "真实高", "解出相机高"))
    print("  " + "-" * 54)

    h_cam, res, spread = solve_cam_height(y_h, anchors)
    for r in res:
        if r.get("h_cam") is None:
            print("  %-8.0f %-10.0f %-10.0f %-10.0f %-12s" %
                  (r["x"], r["y_base"], r["h_px"], r["H_mm"], r.get("err", "-")))
        else:
            print("  %-8.0f %-10.0f %-10.0f %-10.0f %-12.1f" %
                  (r["x"], r["y_base"], r["h_px"], r["H_mm"], r["h_cam"]))

    print("-" * 54)
    if h_cam is None:
        print("[X] 无法解出相机高度")
        return 1
    print("相机高 h_cam = %.0f mm  (%.2f m)" % (h_cam, h_cam / 1000.0))
    if spread is not None:
        print("多锚点离散度 = %.1f%%" % spread)

    lvl, notes = assumption_report(im, y_h, anchors, h_cam, spread)
    print("\n【可信度】%s" % {"good": "良好 ★", "warn": "存疑 △", "bad": "不可用 ✗"}[lvl])
    for n in notes:
        print("  · " + n)

    out = {
        "img": os.path.basename(args.img),
        "size": [W, Hh],
        "horizon_y": y_h,
        "h_cam_mm": h_cam,
        "spread_pct": spread,
        "anchors": res,
        "confidence": lvl,
        "notes": notes,
        # 常用比例尺：给一行 y，返回 m/px，方便人肉查表
        "scale_table": [
            {"y": y, "mm_per_px": h_cam / (y - y_h) if abs(y - y_h) > 1e-9 else None}
            for y in range(int(max(0, y_h) + 1), Hh, max(1, Hh // 20))
        ],
    }

    print("\n【比例尺表】行号 y 处，每 1 像素 = ? 毫米")
    for r in out["scale_table"]:
        if r["mm_per_px"] is None:
            continue
        print("  y=%-6d %8.2f mm/px   (1 m = %6.1f px)" %
              (r["y"], r["mm_per_px"], 1000.0 / r["mm_per_px"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=float)
        print("\n→ 已写 %s" % args.json)
    return 0


# ============================================================
# §3  水平量测
# ============================================================
#
#   L(mm) = Δx_px · h_cam / (y_mid − y_h)
#   y_mid 取被测段两端行号的平均（该段在图上应是水平的）。
#
# ★ 什么时候不能用
#   · 被测段在图上是**斜的**（不平行于该深度的水平面）→ 先用 rectify 拉正
#   · 两端深度差很大（近大远小）→ 分段量，别横跨半个画面
#   · 画面大仰角 → 见 assumption_report 的 warn


def cmd_measure(args):
    with open(args.scale, "r", encoding="utf-8") as f:
        sc = json.load(f)
    h_cam = sc["h_cam_mm"]
    y_h = sc["horizon_y"]

    segs = []
    for part in args.segs.split(";"):
        part = part.strip()
        if not part:
            continue
        v = [float(t) for t in part.split(",")]
        if len(v) != 4:
            raise ValueError("每段 4 个数 x1,y1,x2,y2，得到 %r" % part)
        segs.append(tuple(v))

    print("尺度文件 %s   h_cam=%.0f mm  y_h=%.1f" % (args.scale, h_cam, y_h))
    print("-" * 68)
    print("%-26s %-8s %-12s %s" % ("段(x1,y1)-(x2,y2)", "Δx_px", "实测(mm)", "备注"))
    print("-" * 68)
    out = []
    for x1, y1, x2, y2 in segs:
        dx = abs(x2 - x1)
        y_mid = (y1 + y2) / 2.0
        den = y_mid - y_h
        if abs(den) < 1e-6:
            print("%-26s %-8.1f %-12s %s" % ("(%.0f,%.0f)-(%.0f,%.0f)" % (x1, y1, x2, y2),
                                              dx, "-", "位于地平线，无法解算"))
            continue
        L = dx * h_cam / den
        slope = abs(y2 - y1) / max(1e-6, dx)
        note = "" if slope < 0.02 else ("斜段(%.1f%%)，非水平 → 用 rectify" % (slope * 100))
        if den < 0:
            note = ("▲ 该段在地平线**上方**（y_mid=%.0f < y_h=%.0f）—— 公式不适用。"
                    "上方尺寸请改用 rectify 矫正该立面后量测" % (y_mid, y_h))
        print("%-26s %-8.1f %-12.0f %s" %
              ("(%.0f,%.0f)-(%.0f,%.0f)" % (x1, y1, x2, y2), dx, L, note))
        out.append({"p1": [x1, y1], "p2": [x2, y2], "dx_px": dx, "y_mid": y_mid,
                    "length_mm": L, "note": note, "above_horizon": den < 0})

    # 竖向换算
    if args.vertical:
        print("-" * 68)
        print("竖向量测：H = Δy_px · h_cam / (y_bot − y_h)")
        for part in args.vertical.split(";"):
            v = [float(t) for t in part.split(",")]
            if len(v) != 3:
                raise ValueError("竖向量测 3 个数 x,y_top,y_bot，得到 %r" % part)
            x, yt, yb = v
            den = yb - y_h
            if abs(den) < 1e-6:
                print("  (%.0f,%.0f)-(%.0f,%.0f)  地平线处，无法解算" % (x, yt, x, yb))
                continue
            Hv = (yb - yt) * h_cam / den
            print("  x=%-7.0f y=%.0f→%.0f  高 = %8.0f mm (%.2f m)" % (x, yt, yb, Hv, Hv / 1000.0))
            out.append({"vertical": v, "height_mm": Hv})

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"segments": out}, f, ensure_ascii=False, indent=2, default=float)
        print("\n→ 已写 %s" % args.json)
    return 0


# ============================================================
# §4  色板提取（k-means 主色）
# ============================================================
#
#   从效果图里取材质颜色，**不能直接取屏幕上的像素值** —— 那是你看到的那一面，
#   受光照影响极大。正确做法：
#     1. 提取主色簇（k-means，聚在 sRGB 直方图上）
#     2. 对每个簇同时给出「亮部样本」与「暗部样本」→ 构成一对明暗色阶
#     3. 取两者的中值作为材质**基色**（albedo 近似）
#
#   为什么取中值：AIGC/照片里，同一材质的受光面与背光面通常差 15~40% 明度，
#   albedo 落在两者之间。直接用暗部会得到过暗的"水泥黑"，直接用亮部会过曝。
#
#   ★ 交付时必须同时说明：这是**屏幕色的近似反推**，不是色卡实测。
#     AIGC 图的镜头压缩、辉光、色调映射都会偏色，色卡以实物为准。


def kmeans(px, k=6, iters=24, seed=7):
    """px: (N,3) float32。用 k-means++ 初始化，返回 (labels, centers)"""
    rng = np.random.default_rng(seed)
    n = len(px)
    centers = [px[rng.integers(n)]]
    d2 = ((px - centers[0]) ** 2).sum(1)
    for _ in range(k - 1):
        tot = d2.sum()
        if tot <= 0:
            centers.append(px[rng.integers(n)])
            continue
        p = d2 / tot
        centers.append(px[rng.choice(n, p=p)])
        d2 = np.minimum(d2, ((px - centers[-1]) ** 2).sum(1))
    centers = np.array(centers, dtype=np.float32)
    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        d = ((px[:, None, :] - centers[None, :, :]) ** 2).sum(2)
        nl = d.argmin(1)
        if np.array_equal(nl, labels):
            break
        labels = nl
        for i in range(k):
            m = labels == i
            if m.any():
                centers[i] = px[m].mean(0)
    return labels, centers


def rgb_to_hsv_arr(rgb):
    """rgb: (N,3) 0..255 → hsv, h∈0..360, s,v∈0..100"""
    r = rgb[:, 0] / 255.0
    g = rgb[:, 1] / 255.0
    b = rgb[:, 2] / 255.0
    mx = np.max(rgb, 1) / 255.0
    mn = np.min(rgb, 1) / 255.0
    d = mx - mn
    h = np.zeros_like(mx)
    m = (d > 1e-9)
    idx = m & (mx == r)
    h[idx] = (60 * ((g[idx] - b[idx]) / d[idx]) + 360) % 360
    idx = m & (mx == g)
    h[idx] = (60 * ((b[idx] - r[idx]) / d[idx]) + 120) % 360
    idx = m & (mx == b)
    h[idx] = (60 * ((r[idx] - g[idx]) / d[idx]) + 240) % 360
    s = np.where(mx > 1e-9, d / np.maximum(mx, 1e-9) * 100, 0)
    return h, s, mx * 100


def guess_material(rgb):
    """按 HSV 猜一个中文材质名 + 英文 key。给 MATERIALS 命名用。"""
    h, s, v = rgb_to_hsv_arr(np.array([rgb], dtype=np.float32))
    h, s, v = float(h[0]), float(s[0]), float(v[0])
    if s < 8:
        if v > 88:
            return "白色涂料", "paint_white"
        if v > 62:
            return "浅灰涂料", "paint_lightgrey"
        if v > 32:
            return "中灰面砖", "tile_grey"
        return "深灰石材", "stone_dark"
    if s < 22:
        if v > 85:
            return "米白涂料", "paint_cream"
        if v > 58:
            return "暖灰混凝土", "concrete_warm"
        if v > 30:
            return "灰混凝土", "concrete_grey"
        return "深灰金属", "metal_dark"
    if v < 22:
        return "深色金属", "metal_black"
    if h < 15 or h >= 345:
        return ("砖红石材", "stone_brick") if s < 60 else ("红色涂料", "paint_red")
    if h < 40:
        return ("暖木饰面", "wood_warm") if v > 45 else ("深木饰面", "wood_dark")
    if h < 70:
        return "暖黄涂料", "paint_yellow"
    if h < 165:
        return "植物绿", "plant_green"
    if h < 200:
        return "青绿玻璃", "glass_teal"
    if h < 255:
        return "蓝灰玻璃", "glass_blue"
    if h < 300:
        return "蓝紫面材", "panel_violet"
    return "品红面材", "panel_magenta"


def cmd_palette(args):
    im = load_rgb(args.img)
    W, Hh = im.size
    a = np.asarray(im, dtype=np.float32).reshape(-1, 3)

    # 量化到 5bit 网格，天然降重并让 k-means 更快
    q = (a // 8).astype(np.int32)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    rep = np.zeros((len(uk), 3), dtype=np.float32)
    np.add.at(rep, inv, a)
    rep /= cnt[:, None]

    px = rep
    wts = cnt.astype(np.float32)
    k = min(args.k, len(px))
    labels, centers = kmeans(px, k=k)
    order = np.argsort(-np.bincount(labels, weights=wts, minlength=k))
    total = wts.sum()

    print("=" * 74)
    print("色板提取  %s  (%d x %d px)" % (os.path.basename(args.img), W, Hh))
    print("=" * 74)
    print("%-4s %-10s %-8s %-8s %-8s %-6s %-14s %s" %
          ("#", "Hex", "占比", "V明度", "S饱和", "H色相", "建议材质名", "明暗配对"))
    print("-" * 74)

    result = []
    for i, ci in enumerate(order):
        n = int(wts[labels == ci].sum())
        share = n / total * 100
        c = centers[ci]
        hsv = rgb_to_hsv_arr(np.array([c], dtype=np.float32))
        h, s, v = float(hsv[0][0]), float(hsv[1][0]), float(hsv[2][0])
        name_cn, name_en = guess_material(c)

        pair = None
        if args.pair:
            # 该簇的亮/暗分位
            m = labels == ci
            sub = px[m]
            lum = sub.mean(1)
            sub_sorted = sub[np.argsort(lum)]
            nsub = len(sub_sorted)
            dark = sub_sorted[:max(1, int(nsub * 0.25))].mean(0)
            lite = sub_sorted[-max(1, int(nsub * 0.25)):].mean(0)
            base = (dark + lite) / 2.0
            pair = {"dark": hx(dark), "light": hx(lite), "base": hx(base),
                    "dark_v": float(dark.mean()), "light_v": float(lite.mean()),
                    "base_v": float(base.mean())}

        print("%-4d %-10s %7.2f%% %-8.1f %-8.1f %-6.0f %-14s %s" %
              (i + 1, hx(c), share, v, s, h, name_cn,
               ("暗%s/亮%s→基%s" % (pair["dark"], pair["light"], pair["base"])) if pair else "—"))
        result.append({
            "rank": i + 1, "hex": hx(c), "share_pct": round(share, 2),
            "v": round(v, 1), "s": round(s, 1), "h": round(h, 1),
            "material_cn": name_cn, "material_en": name_en,
            "pair": pair,
        })

    print("-" * 74)
    print("★ 上面是**屏幕色**。材质基色请优先用「基色」列（明暗中值），")
    print("  它是 albedo 的近似值。AIGC 图有色调映射与辉光，实测会偏 ±8%。")
    print("  配色建议：同一材质至少给 2 阶（受光/背光），否则渲染出来会像塑料。")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"img": os.path.basename(args.img), "size": [W, Hh],
                       "colors": result}, f, ensure_ascii=False, indent=2,
                      default=float)
        print("→ 已写 %s" % args.json)
    return 0


# ============================================================
# §5  四点单应性矫正（把透视立面拉成正投影面）
# ============================================================
#
#   这是**最可靠**的量测手段：给定一个实际为矩形的立面上四点，
#   用单应性把四点拉成矩形。拉正后的图里，**逐像素量测 = 真实尺寸**，
#   完全不受俯仰角影响（因为矩形性本身就是约束，把畸变一并吸收了）。
#
#   典型用法：给定立面左上/右上/右下/左下四点 + 实际开间宽(mm)，
#   矫正后 1px = ? mm，整片立面都能量。
#
#   ★ 前提：四点围成的必须是**平面矩形**（真矩形，不是梯形）。
#     若强拉会导致明显失真（输出里的 warp_ratio 偏大），说明四点没选准
#     或该面本身不是矩形（如弧形幕墙、透视变形镜头）。
#
#   ★ 对 AIGC 效果图：**不要矫正 AIGC 图的远端面**。AI 生成图常把远处的平行线
#     画得不平行，强行拉正等于把畸变编造成尺寸。远端只用 §2 的近似法。


def solve_homography(src, dst):
    """src: 4x2, dst: 4x2 → 3x3 H（映射 src→dst）"""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    A = np.array(A, dtype=np.float64)
    _, _, Vt = np.linalg.svd(A)
    H = Vt[-1].reshape(3, 3)
    return H / H[2, 2]


def apply_h(H, pts):
    p = np.asarray(pts, dtype=np.float64)
    q = np.concatenate([p, np.ones((len(p), 1))], 1) @ H.T
    q = q[:, :2] / q[:, 2:3]
    return q


def warp_perspective(im, H, out_w, out_h):
    """用逆映射做透视矫正。

    不用 Image.transform(PERSPECTIVE)：该 API 要传 8 元 coeffs 且在
    Pillow ≥10 改成接受完整 3x3 系数，跨版本极易 TypeError。
    这里直接求 H⁻¹ 并用 numpy 做最近邻 + 双线性，跨版本稳定。
    """
    src = np.asarray(im, dtype=np.float32)
    Hi = np.linalg.inv(np.asarray(H, dtype=np.float64))

    # 目标像素网格 → 源坐标
    ys, xs = np.mgrid[0:out_h, 0:out_w].astype(np.float64)
    ones = np.ones_like(xs)
    P = np.stack([xs.ravel(), ys.ravel(), ones.ravel()], 0)
    Q = Hi @ P
    Q = Q[:2] / np.where(np.abs(Q[2:3]) < 1e-12, 1e-12, Q[2:3])
    sx = Q[0].reshape(out_h, out_w)
    sy = Q[1].reshape(out_h, out_w)

    # 双线性采样
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]
    Hh, Ww = src.shape[:2]

    def gather(xx, yy):
        ok = (xx >= 0) & (xx < Ww) & (yy >= 0) & (yy < Hh)
        xc = np.clip(xx, 0, Ww - 1)
        yc = np.clip(yy, 0, Hh - 1)
        v = src[yc, xc]
        return v, ok

    v00, ok00 = gather(x0, y0)
    v10, ok10 = gather(x0 + 1, y0)
    v01, ok01 = gather(x0, y0 + 1)
    v11, ok11 = gather(x0 + 1, y0 + 1)
    top = v00 * (1 - fx) + v10 * fx
    bot = v01 * (1 - fx) + v11 * fx
    out = top * (1 - fy) + bot * fy
    ok = ok00 & ok10 & ok01 & ok11
    out[~ok] = 255.0
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def cmd_rectify(args):
    im = load_rgb(args.img)
    W, Hh = im.size
    quad = parse_quad(args.quad)
    real_w_mm = args.w

    # 目标矩形的宽由实宽给定；高由**矩形性**自动解出（取四条边中点距离的均值）
    top = math.dist(quad[0], quad[1])
    bot = math.dist(quad[3], quad[2])
    lft = math.dist(quad[0], quad[3])
    rgt = math.dist(quad[1], quad[2])
    # 用「上边+下边」的平均像素宽反推高，其余边用于一致性检查
    est_h_mm = real_w_mm * ((lft + rgt) / 2.0) / ((top + bot) / 2.0)

    out_w = 1400
    scale = out_w / real_w_mm
    out_h = max(1, int(round(est_h_mm * scale)))
    out_w = int(round(real_w_mm * scale))

    dst = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    H = solve_homography(quad, dst)

    # warp_ratio：四边长度比的最大偏差，衡量四点选得准不准 / 该面平不平
    t, b = math.dist(quad[0], quad[1]), math.dist(quad[3], quad[2])
    l, r = math.dist(quad[0], quad[3]), math.dist(quad[1], quad[2])
    wr = max(t, b) / max(1e-6, min(t, b)), max(l, r) / max(1e-6, min(l, r))
    warp_ratio = max(wr)

    pil = warp_perspective(im, H, out_w, out_h)

    # 画比例尺尺
    dr = ImageDraw.Draw(pil)
    fnt = pil_font(16)
    bar_px = int(1000 * scale)          # 1 m
    if bar_px < 10:
        bar_px = int(out_w * 0.2)
        unit = "%.0f px = 约 %d mm" % (bar_px, bar_px / scale)
    else:
        unit = "1 m"
    yb = out_h - 24
    dr.line([(16, yb), (16 + bar_px, yb)], fill=(255, 0, 0), width=3)
    dr.text((16, yb - 22), unit, fill=(255, 0, 0), font=fnt)
    out_path = args.o or (os.path.splitext(args.img)[0] + "_rectified.png")
    pil.save(out_path)

    mm_per_px = 1.0 / scale
    print("=" * 68)
    print("四点矫正  %s → %s" % (os.path.basename(args.img), os.path.basename(out_path)))
    print("=" * 68)
    print("输出尺寸      %d x %d px" % (out_w, out_h))
    print("实宽          %.0f mm (%.2f m)" % (real_w_mm, real_w_mm / 1000.0))
    print("推定实高      %.0f mm (%.2f m)" % (est_h_mm, est_h_mm / 1000.0))
    print("★ 比例尺     %.3f mm/px   (1 m = %.1f px)" % (mm_per_px, 1000.0 / mm_per_px))
    print("warp_ratio    %.3f" % warp_ratio)
    if warp_ratio > 1.25:
        print("  ✗ 失真过大：四点没选准，或该面不是平面矩形（弧形/镜头畸变）。不要用这份结果。")
    elif warp_ratio > 1.08:
        print("  △ 轻微失真（>8%）：结果可参考，关键尺寸请用 §2 尺度法交叉验证。")
    else:
        print("  ★ 四边形规整，拉正质量好，可直接逐像素量测。")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"img": os.path.basename(args.img), "out": out_path,
                       "size": [out_w, out_h], "real_w_mm": real_w_mm,
                       "est_h_mm": est_h_mm, "mm_per_px": mm_per_px,
                       "warp_ratio": warp_ratio, "quad": quad}, f,
                      ensure_ascii=False, indent=2, default=float)
        print("→ 已写 %s" % args.json)
    return 0


# ============================================================
# §6  图像体检
# ============================================================


def cmd_info(args):
    im = load_rgb(args.img)
    W, Hh = im.size
    a = np.asarray(im, dtype=np.float32)
    lum = a @ np.array([0.299, 0.587, 0.114])

    print("=" * 68)
    print("图像体检  %s" % args.img)
    print("=" * 68)
    print("尺寸        %d x %d px   比例 %.3f" % (W, Hh, W / max(1, Hh)))
    print("文件        %.1f KB" % (os.path.getsize(args.img) / 1024.0))
    print("亮度        均值 %.1f  中位 %.1f  范围 %.0f~%.0f  标准差 %.1f"
          % (lum.mean(), np.median(lum), lum.min(), lum.max(), lum.std()))

    # 直方图分位 → 判断是不是低对比（AI 图常糊）
    qs = [1, 5, 25, 50, 75, 95, 99]
    vals = np.percentile(lum, qs)
    print("亮度分位    " + "  ".join("p%d=%.0f" % (q, v) for q, v in zip(qs, vals)))
    p5, p95 = vals[1], vals[5]
    if p95 - p5 < 60:
        print("  △ 动态范围窄（p95−p5 = %.0f），图像偏灰/低对比 —— AIGC 图常见，"
              "取色会偏灰，建议在色板里手动调饱和" % (p95 - p5))
    if lum.std() < 40:
        print("  △ 标准差低，画面可能缺明确的明暗关系，复原体量时注意别把阴影当成材质色")

    # 天/地分割：给地平线一个粗略估计
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    sky = (sat < 40) & (val > 120)
    row_sky = sky.mean(1)
    # 从上往下扫，找到 sky 比例骤降的行
    cand = [y for y in range(2, Hh - 2) if row_sky[y - 2] > 0.5 and row_sky[y + 2] < 0.35]
    if cand:
        print("天空/地面分界 y ≈ %d px（占画高 %.1f%%）—— 这是**地平线粗估**，"
              "仅供交叉参考" % (cand[0], cand[0] / Hh * 100))
        print("  ★ 真正的地平线要用 §4 horizon 精确测，勿直接用这一行")
    else:
        print("未找到清晰天空/地面分界（可能是室内/夜景/满构图），地平线需手工测")

    # 水印检测：底部条带的高频突变
    band = lum[int(Hh * 0.88):, :]
    if band.size:
        rowv = band.mean(1)
        jump = np.abs(np.diff(rowv)).max() if len(rowv) > 1 else 0
        if jump > 8:
            print("△ 底部 %.0f%% 高度处亮度突变（%.1f），疑似水印带 —— "
                  "量测时避开该区域" % (12, jump))

    print("\n下一步：")
    print("  1) 先测地平线   python img_probe.py horizon %s --lines \"...\" --json h.json" % os.path.basename(args.img))
    print("  2) 再标尺度     python img_probe.py scale   %s --horizon Y --anchors \"...\" --json s.json" % os.path.basename(args.img))
    print("  3) 顺手取色     python img_probe.py palette %s -k 6 --pair --json c.json" % os.path.basename(args.img))
    return 0


# ============================================================
# §7  一条龙：probe
# ============================================================
#
#   一次跑完 info + scale + palette，产出一份 recon.json，
#   字段形状**与 plan_data 的 RECON 段对齐**，可直接粘贴使用。


def cmd_probe(args):
    im = load_rgb(args.img)
    W, Hh = im.size
    print("[1/3] 图像体检")
    a = np.asarray(im, dtype=np.float32)
    lum = a @ np.array([0.299, 0.587, 0.114])
    info = {
        "img": os.path.basename(args.img), "size": [W, Hh],
        "lum_mean": round(float(lum.mean()), 2), "lum_std": round(float(lum.std()), 2),
        "lum_p5": round(float(np.percentile(lum, 5)), 1),
        "lum_p95": round(float(np.percentile(lum, 95)), 1),
    }

    print("[2/3] 尺度标定")
    anchors = parse_anchors(args.anchors) if args.anchors else []
    if anchors and args.horizon is not None:
        h_cam, res, spread = solve_cam_height(args.horizon, anchors)
        lvl, notes = assumption_report(im, args.horizon, anchors, h_cam, spread)
        scale_info = {"horizon_y": args.horizon, "h_cam_mm": h_cam,
                      "spread_pct": spread, "anchors": res,
                      "confidence": lvl, "notes": notes}
        print("   h_cam = %s mm   离散度 %s%%   可信度 %s"
              % ("%.0f" % h_cam if h_cam else "-",
                 "%.1f" % spread if spread is not None else "-", lvl))
    else:
        print("   跳过（未给 --horizon / --anchors）")
        scale_info = None

    print("[3/3] 色板提取")
    b = np.asarray(im, dtype=np.float32).reshape(-1, 3)
    q = (b // 8).astype(np.int32)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    rep = np.zeros((len(uk), 3), dtype=np.float32)
    np.add.at(rep, inv, b)
    rep /= cnt[:, None]
    k = min(args.k, len(rep))
    labels, centers = kmeans(rep, k=k)
    order = np.argsort(-np.bincount(labels, weights=cnt.astype(np.float32), minlength=k))
    total = cnt.sum()
    colors = []
    for i, ci in enumerate(order):
        m = labels == ci
        c = centers[ci]
        sub = rep[m][np.argsort(rep[m].mean(1))]
        ns = len(sub)
        dark = sub[:max(1, int(ns * 0.25))].mean(0)
        lite = sub[-max(1, int(ns * 0.25)):].mean(0)
        base = (dark + lite) / 2.0
        name_cn, name_en = guess_material(c)
        colors.append({
            "rank": i + 1, "hex": hx(c),
            "share_pct": round(float(cnt[m].sum() / total * 100), 2),
            "dark": hx(dark), "light": hx(lite), "base": hx(base),
            "material_cn": name_cn, "material_en": name_en,
        })
        print("   %d. %s  %5.2f%%  基色 %s  %s" %
              (i + 1, hx(c), cnt[m].sum() / total * 100, hx(base), name_cn))

    out = {"recon": info, "scale": scale_info, "colors": colors}

    print("\n" + "=" * 68)
    print("可直接粘贴进 plan_data.py 的 RECON 段：")
    print("=" * 68)
    print('RECON = {')
    print('    "src": %r,' % info["img"])
    if scale_info:
        print('    "method": "img_probe.py 尺度标定（已知高度锚点 + 地平线）",')
        print('    "h_cam_mm": %s,' % ("%.0f" % scale_info["h_cam_mm"] if scale_info["h_cam_mm"] else "None"))
        print('    "horizon_y": %.1f,' % scale_info["horizon_y"])
        print('    "confidence": %r,' % scale_info["confidence"])
    else:
        print('    "method": "未标定 —— 尺寸全部为假定值",')
        print('    "h_cam_mm": None,')
        print('    "confidence": "none",')
    print('    "caveat": "屏幕色近似反推，实测偏 ±8%；AIGC 图不可作实测依据",')
    print('    "palette": [')
    for cc in colors:
        print('        # %-12s 占 %5.2f%%  →  %s_%s'
              % (cc["base"], cc["share_pct"], cc["material_cn"], cc["base"][1:].lower()))
    print('    ],')
    print('}')

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=float)
        print("\n→ 已写 %s" % args.json)
    return 0


# ============================================================
# §8  标注图（在原图上叠加尺度网格与量测结果）
# ============================================================
#
#   复核用。把地平线、等距比例尺网格、锚点标出来给人眼看。
#   网格是**等深线**（y 恒定 → 深度恒定 → 比例恒定），比等距网格有用得多。


def cmd_grid(args):
    im = load_rgb(args.img).convert("RGB")
    W, Hh = im.size
    dr = ImageDraw.Draw(im, "RGBA")
    fnt = pil_font(15)

    # 地平线
    if args.horizon is not None:
        y_h = args.horizon
        dr.line([(0, y_h), (W, y_h)], fill=(255, 40, 40, 255), width=2)
        dr.text((8, y_h - 20), "地平线 y_h = %.0f" % y_h, fill=(255, 40, 40, 255), font=fnt)

    # 等深线 + 比例尺标注
    if args.horizon is not None and args.h_cam:
        h_cam = args.h_cam
        y_h = args.horizon
        step = max(1, Hh // 16)
        for y in range(int(max(0, y_h)) + step, Hh, step):
            den = y - y_h
            if abs(den) < 1e-9:
                continue
            mmpp = h_cam / den
            dr.line([(0, y), (W, y)], fill=(80, 160, 255, 110), width=1)
            lbl = "y=%d  %.1f mm/px" % (y, mmpp)
            tw = dr.textlength(lbl, font=fnt)
            dr.rectangle([6, y + 2, 10 + tw, y + 20], fill=(0, 0, 0, 120))
            dr.text((8, y + 3), lbl, fill=(180, 215, 255, 255), font=fnt)

    # 锚点
    for i, part in enumerate(args.anchors.split(";")) if args.anchors else []:
        part = part.strip()
        if not part:
            continue
        x, y_b, h_px, H = [float(t) for t in part.split(",")]
        dr.rectangle([x - h_px / 2, y_b - h_px, x + h_px / 2, y_b],
                     outline=(0, 255, 120, 255), width=3)
        lbl = "锚%d %gmm" % (i + 1, H)
        dr.text((x - h_px / 2, y_b - h_px - 20), lbl, fill=(0, 255, 120, 255), font=fnt)

    out = args.o or (os.path.splitext(args.img)[0] + "_grid.png")
    im.save(out)
    print("→ 已写 %s（%d x %d）" % (out, W, Hh))
    print("  网格是**等深线**：同一条线上比例尺相同，可直接沿线量测。")
    return 0


# ============================================================

USAGE = """img_probe.py — 透视效果图复原工具箱

  info    <图>                                        图像体检
  horizon <图> --lines "x1,y1,x2,y2;..." --json h.json  由水平线族求地平线
  scale   <图> --horizon Y --anchors "x,yb,hp,H;..." --json s.json
  measure <图> --scale s.json --segs "x1,y1,x2,y2;..." [--vertical "x,yt,yb;..."]
  palette <图> -k 8 --pair --json c.json              主色提取（明暗成对）
  rectify <图> --quad "x,y;x,y;x,y;x,y" --w 12000 -o r.png [--json r.json]
  grid    <图> --horizon Y --h-cam 1700 --anchors "..." -o g.png
  probe   <图> --horizon Y --anchors "..." -k 6 --json recon.json   一条龙

单位：长度一律毫米；图像坐标原点在左上角，y 向下。
"""


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return 1
    cmd = sys.argv[1]
    rest = sys.argv[2:]

    sub = {"info": cmd_info, "horizon": cmd_horizon, "scale": cmd_scale,
           "measure": cmd_measure, "palette": cmd_palette, "rectify": cmd_rectify,
           "probe": cmd_probe, "grid": cmd_grid}

    if cmd not in sub:
        print("[X] 未知子命令 %r" % cmd)
        print(USAGE)
        return 1

    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("img")
    ap.add_argument("--horizon", type=float, default=None)
    ap.add_argument("--h-cam", type=float, default=None, dest="h_cam")
    ap.add_argument("--anchors", default=None)
    ap.add_argument("--segs", default=None)
    ap.add_argument("--vertical", default=None)
    ap.add_argument("--lines", default=None)
    ap.add_argument("--scale", default=None)
    ap.add_argument("--quad", default=None)
    ap.add_argument("--w", type=float, default=None)
    ap.add_argument("-o", default=None)
    ap.add_argument("-k", type=int, default=6)
    ap.add_argument("--pair", action="store_true")
    ap.add_argument("--json", default=None)
    try:
        args = ap.parse_args(rest)
    except SystemExit:
        print(USAGE)
        return 1

    if not os.path.exists(args.img):
        print("[X] 找不到图片：%s" % args.img)
        return 1

    try:
        return sub[cmd](args) or 0
    except Exception as e:
        print("[X] %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
