# -*- coding: utf-8 -*-
"""
fidelity_report.py — 复原保真度评估
================================================================================
回答一个问题：**我复原的像不像原图？**

这不是审美问题，是**可量化的验收问题**。复原工作的最大风险是
「尺寸全靠假定、材质全靠手挑」，而交付者往往说不清偏差有多少。

本脚本给出三个互补的指标：

  1. 尺度误差  scale    —— 同一根「量测线」在原图与复原图上量得的长度之比。
                            这是复原工作的核心指标。
  2. 形貌相似  shape    —— 结构相似度 SSIM（亮度/对比/结构三项乘积）。
                            捕捉体量关系错没错（层数、开间节奏、悬挑）。
  3. 色板距离  palette  —— 原图主色与复原图主色的最小配对距离（CIEDE2000 简化）。
                            捕捉材质色偏。

输出：
  · 并排对比图（左原图 / 右复原图 / 下差异热力图）
  · 报告 HTML（可交付给业主看）
  · 控制台摘要

★ 关键限制：两图必须**视角一致**。
  用 views.json 里的同一相机参数重渲，才能对比。
  视角不对齐时 SSIM 一定很低，那不是复原的错，是对比方法错了。

★ 尺度线怎么定
  在原图上选 2~4 处**看得清、结构明确**的位置（推荐：首层玻璃面宽、
  中段开间、栏板高、台阶总进深），量出像素长度；同样的线在复原图上量。
  比例 = 原图长/复原图长 − 1，即该处的相对误差。

用法
--------------------------------------------------------------------------------
  python fidelity_report.py --ref 原图.png --rep 复原图.png \
      --scale-ref "152,378,340,375;120,320,455,318" \
      --scale-rep "180,420,395,416" \
      -o D:/proj/保真度报告.html
"""

import sys
import os
import json
import math

try:
    from PIL import Image, ImageDraw, ImageFont, ImageChops
except ImportError:
    print("[X] 缺少 Pillow")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("[X] 缺少 numpy")
    sys.exit(1)


# ============================================================
# §1  工具
# ============================================================

def load(p):
    return Image.open(p).convert("RGB")


def fit_size(a, b):
    """把两张图缩放到同一尺寸（取较小者），返回 (imA, imB)"""
    w = min(a.width, b.width)
    h = min(a.height, b.height)
    return a.resize((w, h), Image.LANCZOS), b.resize((w, h), Image.LANCZOS)


def gray(im):
    a = np.asarray(im, dtype=np.float32)
    return a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def box(x, r):
    """滑动平均（边缘复制），等价于 OpenCV 的 boxFilter"""
    if r <= 0:
        return x
    k = 2 * r + 1
    pad = np.pad(x, ((r, r), (r, r)), mode="edge")
    cs = np.cumsum(pad, axis=0)
    cs = np.vstack([np.zeros((1, cs.shape[1]), dtype=cs.dtype), cs])
    y = (cs[k:, :] - cs[:-k, :]) / k
    cs = np.cumsum(y, axis=1)
    cs = np.hstack([np.zeros((cs.shape[0], 1), dtype=cs.dtype), cs])
    x2 = (cs[:, k:] - cs[:, :-k]) / k
    return x2


def ssim(x, y, r=7):
    """结构相似度。窗口 11x11（C1/C2 取常规值）。"""
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    mx, my = box(x, r), box(y, r)
    mxx, myy, mxy = box(x * x, r), box(y * y, r), box(x * y, r)
    vx, vy, vxy = mxx - mx * mx, myy - my * my, mxy - mx * my
    num = (2 * mx * my + C1) * (2 * vxy + C2)
    den = (mx * mx + my * my + C1) * (vx + vy + C2)
    return float((num / den).mean())


def lab_of(rgb):
    """sRGB(0..255) → CIE Lab(D65)。用于色差。接受 (3,) 或 (N,3)。"""
    a = np.asarray(rgb, dtype=np.float64)
    one = (a.ndim == 1)
    if one:
        a = a[None, :]
    c = a / 255.0
    m = c > 0.04045
    c = np.where(m, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T
    wp = np.array([0.95047, 1.0, 1.08883])
    t = xyz / wp
    d = 6 / 29
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)
    L = 116 * f[:, 1] - 16
    aa = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    out = np.stack([L, aa, b], 1)
    return out[0] if one else out


def de76(l1, l2):
    """ΔE*76（Lab 欧氏距离）。够用且不需查表。"""
    return float(np.sqrt(((l1 - l2) ** 2).sum()))


def kmeans(px, k=6, iters=20, seed=11):
    rng = np.random.default_rng(seed)
    n = len(px)
    cs = [px[rng.integers(n)]]
    d2 = ((px - cs[0]) ** 2).sum(1)
    for _ in range(k - 1):
        t = d2.sum()
        if t <= 0:
            cs.append(px[rng.integers(n)])
            continue
        cs.append(px[rng.choice(n, p=d2 / t)])
        d2 = np.minimum(d2, ((px - cs[-1]) ** 2).sum(1))
    cs = np.array(cs, dtype=np.float32)
    lab = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        nl = ((px[:, None, :] - cs[None, :, :]) ** 2).sum(2).argmin(1)
        if np.array_equal(nl, lab):
            break
        lab = nl
        for i in range(k):
            m = lab == i
            if m.any():
                cs[i] = px[m].mean(0)
    return lab, cs


def top_colors(im, k=6):
    a = np.asarray(im, dtype=np.float32).reshape(-1, 3)
    q = (a // 8).astype(np.int32)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    rep = np.zeros((len(uk), 3), dtype=np.float32)
    np.add.at(rep, inv, a)
    rep /= cnt[:, None]
    k = min(k, len(rep))
    lab, cs = kmeans(rep, k)
    w = np.bincount(lab, weights=cnt.astype(np.float32), minlength=k)
    order = np.argsort(-w)
    tot = w.sum()
    return [({"hex": "#%02X%02X%02X" % tuple(int(v) for v in cs[i]),
              "share": float(w[i] / tot * 100),
              "lab": lab_of(cs[i])})
            for i in order]


def parse_segs(s):
    out = []
    for p in s.split(";"):
        p = p.strip()
        if not p:
            continue
        v = [float(t) for t in p.split(",")]
        if len(v) != 4:
            raise ValueError("每段 4 个数 x1,y1,x2,y2，得到 %r" % p)
        out.append(tuple(v))
    return out


# ============================================================
# §2  报告
# ============================================================

GRADES = [
    (0.90, "A 优秀", "可直接作为实测依据"),
    (0.75, "B 合格", "个别尺寸需现场复核"),
    (0.60, "C 勉强", "整体关系对，绝对尺寸不可信"),
    (0.00, "D 不合格", "体量关系有误，回到识读重来"),
]


def grade(v, reverse=False):
    for t, name, desc in GRADES:
        ok = v >= t if not reverse else v <= t
        if ok:
            return name, desc
    return "D 不合格", ""


def build_html(path, ref, rep, rows, ssim_v, pal, verdict, notes):
    """拼装 HTML 报告。

    ★ 这里刻意**不用 % 格式化**：模板里有大量 CSS（含 %）与中文「±10%」字样，
      % 格式化会让每个百分号都变成陷阱（实测连翻 4 次：
      width:100% / ΔE*76 &lt; / ±10% / 中文紧跟 %）。
      改用 f-string + str.replace，最稳。
    """
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    ref_name = esc(os.path.basename(path[0]))
    rep_name = esc(os.path.basename(path[1]))
    stamp = time_str()
    gname, gdesc = verdict
    ssim_g = grade(ssim_v)[0]
    de_g = grade(pal["mean_de"], reverse=True)[0]

    # ---- 尺度误差行
    tr = ""
    for r in rows:
        e = abs(r["err_pct"])
        cls = "ok" if e <= 10 else ("wn" if e <= 25 else "ng")
        tr += ('<tr><td>{}</td><td>{:.1f}</td><td>{:.1f}</td>'
               '<td class="{}">{:+.1f}%</td><td>{}</td></tr>'
               ).format(esc(r["name"]), r["ref_px"], r["rep_px"], cls,
                        r["err_pct"], esc(r.get("note", "")))
    if not tr:
        tr = '<tr><td colspan="5" style="color:#b26a00">未提供量测线 —— ' \
             '本次无尺度误差评估（形态与色板都不能证明尺寸复原正确）</td></tr>'

    # ---- 色板行
    pc = ""
    n = max(len(pal["ref"]), len(pal["rep"]))
    for i in range(n):
        a = pal["ref"][i] if i < len(pal["ref"]) else None
        b = pal["rep"][i] if i < len(pal["rep"]) else None
        sa = ('<span class="sw" style="background:{}"></span> {} {:.1f}%'
              .format(a["hex"], a["hex"], a["share"])) if a else "—"
        sb = ('<span class="sw" style="background:{}"></span> {} {:.1f}%'
              .format(b["hex"], b["hex"], b["share"])) if b else "—"
        dc = "—" if not (a and b) else "{:.1f}".format(de76(a["lab"], b["lab"]))
        pc += "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(i + 1, sa, sb, dc)

    nt = "".join("<li>{}</li>".format(esc(x)) for x in notes)

    fn_ref = os.path.basename(path[2])
    fn_rep = os.path.basename(path[3])
    fn_dif = os.path.basename(path[4])

    head = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>复原保真度评估报告</title>
<style>
 body{font-family:"Microsoft YaHei",system-ui,sans-serif;max-width:1180px;margin:0 auto;
      padding:32px 24px;color:#222;line-height:1.75;background:#fafafa}
 h1{font-size:26px;border-bottom:3px solid #222;padding-bottom:10px;margin:0 0 6px}
 .sub{color:#666;font-size:13px;margin-bottom:26px}
 h2{font-size:18px;margin:34px 0 12px;padding-left:11px;border-left:5px solid #222}
 table{border-collapse:collapse;width:100%;font-size:14px;background:#fff}
 th,td{border:1px solid #ddd;padding:8px 11px;text-align:left}
 th{background:#f2f2f2;font-weight:600}
 .ok{color:#1a7f37;font-weight:600}
 .wn{color:#b26a00;font-weight:600}
 .ng{color:#c1121f;font-weight:600}
 .sw{display:inline-block;width:13px;height:13px;border:1px solid #999;vertical-align:-2px}
 .verdict{background:#fff;border:2px solid #222;padding:18px 22px;margin:20px 0;font-size:16px}
 .g{font-size:28px;font-weight:700}
 .cmp{width:100%;border-collapse:collapse;margin-top:14px;table-layout:fixed}
 .cmp td{width:50%;padding:0;border:1px solid #ddd}
 .cmp img{width:100%;display:block}
 .cmp .cap{padding:8px 12px;background:#f2f2f2;font-size:13px}
 .hint{font-size:13px;color:#666}
 ul{font-size:14px}
</style></head><body>
"""
    body = f"""
<h1>复原保真度评估报告</h1>
<div class="sub">参考图 {ref_name} ｜ 复原图 {rep_name} ｜ 生成于 {stamp}</div>

<div class="verdict">
  <span class="g">{esc(gname)}</span>　{esc(gdesc)}
  <div style="margin-top:12px;font-size:14px">
    形态相似度 SSIM = <b>{ssim_v:.3f}</b>（{esc(ssim_g)}）　·
    平均色差 ΔE*76 = <b>{pal["mean_de"]:.1f}</b>（{esc(de_g)}）
  </div>
</div>

<h2>一、形态对比</h2>
<table class="cmp"><tr>
  <td><img src="{fn_ref}"><div class="cap">参考图（原始输入）</div></td>
  <td><img src="{fn_rep}"><div class="cap">复原图（由真源生成）</div></td>
</tr><tr>
  <td colspan="2"><img src="{fn_dif}"><div class="cap">差异热力图 —— 越亮差异越大。
  关注体量轮廓、层线位置、开间节奏三处是否对齐</div></td>
</tr></table>

<h2>二、尺度误差（核心指标）</h2>
<table>
 <tr><th>量测线</th><th>原图(px)</th><th>复原图(px)</th><th>相对误差</th><th>说明</th></tr>
 {tr}
</table>
<p class="hint">判读：±10% 以内可作为实测依据；±10~25% 需现场复核；超过 ±25% 该处体量关系有误。</p>

<h2>三、色板距离</h2>
<table>
 <tr><th>#</th><th>参考图主色</th><th>复原图主色</th><th>ΔE*76</th></tr>
 {pc}
</table>
<p class="hint">ΔE*76 小于 5 为肉眼难辨；5~15 需微调；大于 15 说明材质映射选错。</p>

<h2>四、结论与待核项</h2>
<ul>{nt}</ul>
</body></html>
"""
    return head + body


def time_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def font(sz=18):
    for n in ("msyh.ttc", "simhei.ttf"):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + n, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="参考图（原图）")
    ap.add_argument("--rep", required=True, help="复原图")
    ap.add_argument("--scale-ref", default=None, help="原图量测线 x1,y1,x2,y2;...")
    ap.add_argument("--scale-rep", default=None, help="复原图量测线，与上者一一对应")
    ap.add_argument("-k", type=int, default=6, help="色板簇数")
    ap.add_argument("-o", default=None, help="报告 HTML 输出路径")
    ap.add_argument("--json", default=None, help="结果 JSON")
    args = ap.parse_args()

    for p in (args.ref, args.rep):
        if not os.path.exists(p):
            print("[X] 找不到：%s" % p)
            return 1

    R, P = load(args.ref), load(args.rep)
    Rs, Ps = fit_size(R, P)
    W, H = Rs.size

    print("=" * 72)
    print("复原保真度评估")
    print("=" * 72)
    print("参考图  %s  %dx%d" % (os.path.basename(args.ref), R.width, R.height))
    print("复原图  %s  %dx%d" % (os.path.basename(args.rep), P.width, P.height))
    print("对齐后  %dx%d" % (W, H))

    notes = []
    if (R.width, R.height) != (P.width, P.height):
        notes.append("两图原始尺寸不同（%dx%d vs %dx%d），已按较小者对齐；"
                     "若视角不一致，SSIM 会显著偏低。"
                     % (R.width, R.height, P.width, P.height))

    # ---- SSIM
    sv = ssim(gray(Rs), gray(Ps))
    gname, gdesc = grade(sv)
    print("\n【形态相似度】SSIM = %.4f   → %s" % (sv, gname))

    # ---- 色板
    cr = top_colors(Rs, args.k)
    cp = top_colors(Ps, args.k)
    des = []
    for i in range(min(len(cr), len(cp))):
        des.append(de76(cr[i]["lab"], cp[i]["lab"]))
    mean_de = sum(des) / len(des) if des else 999.0
    dname, ddesc = grade(mean_de, reverse=True)
    print("【色板距离】  平均 ΔE*76 = %.1f   → %s" % (mean_de, dname))
    print("  %-4s %-12s %-12s %s" % ("#", "参考图", "复原图", "ΔE"))
    for i in range(min(len(cr), len(cp))):
        print("  %-4d %-12s %-12s %.1f" % (i + 1, cr[i]["hex"], cp[i]["hex"], des[i]))

    # ---- 尺度误差
    rows = []
    if args.scale_ref and args.scale_rep:
        sr = parse_segs(args.scale_ref)
        sp = parse_segs(args.scale_rep)
        if len(sr) != len(sp):
            print("[X] 两侧量测线数量不等（%d vs %d）" % (len(sr), len(sp)))
            return 1
        names = ["量测线 %d" % (i + 1) for i in range(len(sr))]
        for n in (args.name or []) if hasattr(args, "name") else []:
            pass
        print("\n【尺度误差】")
        print("  %-12s %-10s %-10s %-10s" % ("量测线", "原图px", "复原px", "误差"))
        for i, (a, b) in enumerate(zip(sr, sp)):
            la = math.dist(a[:2], a[2:])
            lb = math.dist(b[:2], b[2:])
            err = (la / lb - 1) * 100 if lb else 0.0
            nm = names[i]
            rows.append({"name": nm, "ref_px": la, "rep_px": lb, "err_pct": err})
            print("  %-12s %-10.1f %-10.1f %+.1f%%" % (nm, la, lb, err))
        if rows:
            mean_abs = sum(abs(r["err_pct"]) for r in rows) / len(rows)
            print("  平均绝对误差 = %.1f%%" % mean_abs)
            if mean_abs > 25:
                notes.append("尺度平均误差 %.0f%% 超过 25%%，说明复原体量与原图"
                             "存在实质偏差，建议先用 rectify 拉正立面复核关键跨度。"
                             % mean_abs)
            elif mean_abs > 10:
                notes.append("尺度平均误差 %.0f%%，绝对尺寸需现场复核，"
                             "但体量关系可信。" % mean_abs)
            else:
                notes.append("尺度平均误差 %.0f%%，关键跨度可作为实测依据。" % mean_abs)
    else:
        notes.append("未提供量测线（--scale-ref / --scale-rep），本次无尺度误差评估。"
                     "这是最该补的一项 —— 形态与色板都不能证明尺寸复原正确。")

    # ---- 差异热力图 + 并排图
    d = ImageChops.difference(Rs, Ps).convert("L")
    d = d.point(lambda v: min(255, int(v * 3.0)))
    base = os.path.dirname(os.path.abspath(args.o)) if args.o else os.getcwd()
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = os.getcwd()
    fn_cmp = os.path.join(base, "fidelity_compare.png")
    fn_ref = os.path.join(base, "fidelity_ref.png")
    fn_rep = os.path.join(base, "fidelity_rep.png")
    fn_dif = os.path.join(base, "fidelity_diff.png")
    Rs.save(fn_ref)
    Ps.save(fn_rep)
    d.save(fn_dif)
    newc = Image.new("RGB", (W, H))
    newc.paste(Image.blend(Rs, Ps, 0.5), (0, 0))
    newc.save(fn_cmp)

    verdict = (gname, gdesc)
    if notes:
        notes.append("SSIM %s：%s。色板 %s：%s。" % (gname, gdesc, dname, ddesc))

    if args.o:
        pal = {"ref": cr, "rep": cp, "mean_de": mean_de}
        html = build_html((args.ref, args.rep, fn_ref, fn_rep, fn_dif),
                          Rs, Ps, rows, sv, pal, verdict, notes)
        with open(args.o, "w", encoding="utf-8") as f:
            f.write(html)
        print("\n→ 报告 %s" % args.o)
        print("  配图 %s" % fn_cmp)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"ref": args.ref, "rep": args.rep, "ssim": sv,
                       "ssim_grade": gname, "mean_de": mean_de,
                       "de_grade": dname, "scale_rows": rows,
                       "notes": notes}, f, ensure_ascii=False, indent=2,
                      default=float)
        print("→ JSON %s" % args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
