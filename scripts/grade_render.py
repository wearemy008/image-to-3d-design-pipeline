# -*- coding: utf-8 -*-
"""
grade_render.py —— SketchUp 素模渲染的黄昏调色（项目无关）

为什么需要它：SketchUp 内置渲染器对太阳色温的响应很弱——把 ShadowTime 调到
日落前 10 分钟，画面的明暗关系和影长是对的，但颜色依旧偏中性、偏冷，
和「黄昏效果图」的暖金氛围差一口气。与其硬改材质（会连白天图一起改坏），
不如在后期统一加一层轻调色：暖偏移 + 轻微 S 曲线 + 暗角。

设计原则：**轻**。宁可少调也不要把素模调成油画。

★ 目录约定（别改回「原地覆盖 + 备份」的写法）
    <出图目录>/_raw/   ← SketchUp 直出的原图（views.json 的 "out" 指向这里）
    <出图目录>/         ← 本脚本产出的成品图
  早期版本是「读成品图、备份到 _raw、原地覆盖成品图」，看着聪明，实际有坑：
  第二次出图时成品图已被覆盖成新图，脚本因为 _raw 里已有同名备份而**继续用旧备份**
  作源，于是**新一轮渲染结果被静默丢弃**（实测：连续三次重建重渲，成品图始终是
  第一版的内容）。分成两个目录、永远从 _raw 读、往成品目录写，就不会再有这个问题。

用法
----
    python grade_render.py <出图目录> [--src _raw] [--strength 1.0] [--vignette 0.10]
    · --strength 0 等于不调色（想对比看效果时用），1 为默认，1.5 偏重。
"""
import argparse
import os
import sys


def _luts(warm_r, warm_g, warm_b, contrast, brightness):
    """「通道增益 + 对比 + 亮度」的 256 级查找表。"""
    out = []
    for ch_gain in (warm_r, warm_g, warm_b):
        lut = []
        for i in range(256):
            v = i / 255.0
            v = v + (v - 0.5) * contrast          # 绕 0.5 的线性 S 曲线
            v = (v * ch_gain) + brightness
            lut.append(max(0, min(255, int(round(v * 255)))))
        out.append(lut)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="出图目录（其下的 _raw 为原图，成品写回本目录）")
    ap.add_argument("--src", default="_raw", help="原图子目录名")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--vignette", type=float, default=0.10, help="暗角强度 0—1")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        print("[X] 需要 Pillow： pip install pillow")
        return 1

    out_dir = args.folder
    src_dir = os.path.join(out_dir, args.src)
    if not os.path.isdir(src_dir):
        print(f"[X] 找不到原图目录 {src_dir}")
        print(f"    → 请把 views.json 的 \"out\" 指到该目录，重新出图后再调色。")
        return 1

    s = args.strength
    wr, wg, wb = 1.0 + 0.075 * s, 1.0 + 0.005 * s, 1.0 - 0.085 * s
    luts = _luts(wr, wg, wb, 0.16 * s, -0.012 * s)

    files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith(".png"))
    if not files:
        print(f"[X] {src_dir} 下没有 png")
        return 1

    print(f"=== 调色（strength={s}，暗角={args.vignette}）===")
    print(f"    源：{src_dir}")
    print(f"    出：{out_dir}")
    for f in files:
        src = os.path.join(src_dir, f)
        dst = os.path.join(out_dir, f)
        im = Image.open(src).convert("RGB")
        W, H = im.size
        r, g, b = im.split()
        im = Image.merge("RGB", (r.point(luts[0]), g.point(luts[1]), b.point(luts[2])))

        # 暗角：径向渐隐，只压四周，中心不动。
        # 先生成 1/8 分辨率的遮罩再双线性放大 —— 直接在 200 万像素上跑
        # Python 双层循环要十几秒一张，降采样后快到可忽略，效果无差别。
        if args.vignette > 0:
            import math
            sw, sh = max(2, W // 8), max(2, H // 8)
            small = Image.new("L", (sw, sh))
            px = small.load()
            cx, cy = (sw - 1) / 2.0, (sh - 1) / 2.0
            maxd = math.hypot(cx, cy)
            k = args.vignette * s
            for y in range(sh):
                dy = y - cy
                for x in range(sw):
                    d = math.hypot(x - cx, dy) / maxd
                    px[x, y] = max(0, min(255, int(255 * (1 - k * d * d))))
            mask = small.resize((W, H), Image.BILINEAR)
            im = Image.composite(im, Image.new("RGB", (W, H), (0, 0, 0)), mask)

        im.save(dst, "PNG", optimize=True)
        print(f"  {f:34s} {W}x{H}  {os.path.getsize(dst)/1024:6.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
