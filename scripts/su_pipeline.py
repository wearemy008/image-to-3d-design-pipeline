#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
su_pipeline.py — SketchUp 建模/出图的分步编排器（★ 每次只发一个短请求）
================================================================================
为什么需要它：su_mcp 的「长请求」会假死——把「建相机 + 8 次 write_image + sleep」
打包成一个 execute_ruby，客户端连续超时都拿不到响应（真实耗时其实只有约 10 秒）。
更糟的是被强杀的请求会继续占着 SketchUp 主线程，导致后续所有请求排队，
表现成「连 400×250 都渲染不出来」。

本脚本把全流程拆成**单步短请求**，每步 0.1—2 秒返回，并统一处理：
  · 无响应（None）     → 提示「主线程可能被上一次请求占着」，建议先 probe
  · Ruby 异常          → 打印 error + backtrace 前几行
  · 中文路径/编码      → 全部 UTF-8，路径由 Python 拼好再传给 Ruby（不做 Ruby 插值）

用法
----
    # 0) 探活（0.1s 返回 = 主线程空闲，可以继续）
    python su_pipeline.py probe

    # 1) 载入工具箱（先 su_kit.rb，再 su_render.rb）
    python su_pipeline.py load

    # 2) 执行建模脚本（脚本须自己定义 build 方法并返回消息）
    python su_pipeline.py run  D:/proj/build_model.rb  MyModule.build

    # 3) 出图：准备 → 逐视角 → 收尾
    python su_pipeline.py prep   --views-json D:/proj/views.json --out D:/proj/渲染
    python su_pipeline.py shots
    python su_pipeline.py finish

    # 4) 建场景 + 保存
    python su_pipeline.py scenes
    python su_pipeline.py save  "D:/proj/model.skp"

    # 5) 结构 + 材质审计
    python su_pipeline.py verify
    python su_pipeline.py materials

views.json 格式：
    {"out": "D:/proj/渲染",
     "views": [["01_轴测_西南", [-13.7,24.3,-17.0], [8.3,9.3,5.0], [0,1,0], false, 26.0], ...]}
    每项 = [名称, 视点, 目标, 上方向, 是否透视, 平行投影视高(米)]
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from su_client import SuClient  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "su_kit.rb").replace("\\", "/")
RENDER = os.path.join(HERE, "su_render.rb").replace("\\", "/")


# --------------------------------------------------------------------------- #
# 执行一步；统一处理「无响应」与「Ruby 异常」
# --------------------------------------------------------------------------- #
def step(label, code, timeout=120, quiet=False):
    t = time.time()
    try:
        with SuClient(timeout=timeout) as c:
            r = c.tool("execute_ruby", code=code)
    except Exception as e:
        print(f"[X] {label}: 连接异常 {e}")
        return None

    dt = time.time() - t

    if r is None:
        print(f"[!] {label}: {dt:.1f}s 无响应")
        print("    → SketchUp 主线程可能仍被上一个长请求占着。")
        print("      先 `python su_pipeline.py probe` 探活；若 0.1s 返回则重试本步。")
        print("      切忌盲目重试堆叠请求——那会让假死持续更久。")
        return None

    if not isinstance(r, dict) or not r.get("success"):
        err = (r or {}).get("error", "unknown")
        print(f"[X] {label}: {dt:.1f}s {err}")
        for ln in ((r or {}).get("backtrace") or [])[:3]:
            print("      ", ln)
        return None

    res = r.get("result")
    if not quiet:
        print(f"[OK] {label}: {dt:.1f}s {str(res)[:160]}")
    return res


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# 各步骤
# --------------------------------------------------------------------------- #
def cmd_probe(_args):
    t = time.time()
    # ★ 探活表达式必须写 1+1，不能写 '1+1'。
    #   Ruby 里 '1+1' 是**字符串字面量**，求值结果是 "1+1" 而不是 "2"，
    #   于是判据 r == "2" 永远不成立 → probe 每次都报「主线程无响应」的假故障。
    #   这个假警报会让人误以为主线程卡住而白白重启 SketchUp（实测踩过）。
    r = step("probe", "1+1", timeout=15, quiet=True)
    ok = r == "2"
    print(f"{'[OK]' if ok else '[X]'} 主线程{'空闲，可继续' if ok else '无响应/异常'}  ({time.time()-t:.1f}s)")
    if not ok:
        print(f"     probe 返回：{r!r}（期望 '2'）")
        print("     检查：SketchUp 是否在运行、su_mcp 插件是否已 start_server（端口 9876 LISTENING）")
    return 0 if ok else 1


def cmd_load(_args):
    # 自动加载 scripts/ 下所有 su_*.rb 工具箱，新增工具箱无需改这里。
    # ★ 逐个文件单独发请求：打包成一个长请求会触发假死（见 pitfalls 第 1 条）。
    import glob as _glob
    files = sorted(_glob.glob(os.path.join(HERE, "su_*.rb")))
    if not files:
        print("[X] scripts/ 下没有 su_*.rb 工具箱")
        return 1
    ok_all = True
    for f in files:
        p = f.replace("\\", "/")
        ok_all = step(f"load {os.path.basename(f)}", f"load '{p}'; 'ok'") is not None and ok_all
    return 0 if ok_all else 1


def cmd_run(args):
    """args: <rb路径> <调用表达式>"""
    if len(args) < 2:
        print("用法: run <脚本路径.rb> <调用表达式>  例如  run D:/p/build_model.rb  MyMod.build")
        return 2
    rb = args[0].replace("\\", "/")
    call = args[1]
    return 0 if step(f"run {os.path.basename(rb)}",
                     f"require 'json'; load '{rb}'; ({call}).to_json",
                     timeout=600) is not None else 1


def cmd_prep(args):
    """args: --views-json <path> --out <dir>  或  --views-json <path>（out 从 json 读）"""
    cfg_path = _opt(args, "--views-json")
    if not cfg_path:
        print("用法: prep --views-json views.json [--out 渲染目录]")
        return 2
    cfg = read_json(cfg_path)
    out = _opt(args, "--out") or cfg.get("out")
    if not out:
        print("[X] 未指定输出目录（--out 或 views.json 的 out 字段）")
        return 2
    out = out.replace("\\", "/")
    os.makedirs(out, exist_ok=True)

    views_rb = json.dumps(cfg["views"], ensure_ascii=False)
    cx = cfg.get("cx", 0.0)
    cz = cfg.get("cz", 0.0)

    # 氛围设置（黄昏图必用）：背景色 / 天空 / 渐变 / 太阳时刻
    #   "bg": 0xRRGGBB（十进制写也行）、"sky": true/false、"gradient": true/false、
    #   "sun": [2026, 9, 29, 17, 5]   → 低太阳高度角 → 长影 + 暖光
    #   "sun_local": true（默认）      → 对上面的时刻做 UTC 墙钟修正，见下
    extra = []
    if "bg" in cfg:
        extra.append(f"bg: {int(cfg['bg'])}")
    for k in ("sky", "gradient"):
        if k in cfg:
            extra.append(f"{k}: {str(bool(cfg[k])).lower()}")
    # 阴影面板「亮 / 暗」滑杆：拉开明暗对比（默认 70 / 25）
    for k in ("light", "dark"):
        if k in cfg:
            extra.append(f"{k}: {int(cfg[k])}")
    # 雾：去地平线硬边（fog_color 给 0xRRGGBB；fog_start / fog_end 单位米）
    if "fog_color" in cfg:
        extra.append(f"fog_color: {int(cfg['fog_color'])}")
    for k in ("fog_start", "fog_end"):
        if k in cfg:
            extra.append(f"{k}: {float(cfg[k])}")
    if "sun" in cfg:
        y, mo, d, hh, mm = (list(cfg["sun"]) + [0, 0])[:5]
        if cfg.get("sun_local", True):
            # ★ ShadowTime 时区陷阱（实测）：SketchUp 把 Time 的 **UTC 墙钟**
            #   当作场地当地时刻。直接填当地时刻会得到「8 小时前」的太阳 ——
            #   填 17:05 实际算出上午 09:05（高度角 34°、方位角 118°），
            #   完全不是黄昏。补上本机 UTC 偏移后才是 13.3° / 259°。
            #   关掉这个修正请设 "sun_local": false。
            extra.append(
                f"sun: (lambda {{ |t| t + t.utc_offset }}).call("
                f"Time.new({int(y)}, {int(mo)}, {int(d)}, {int(hh)}, {int(mm)}, 0))")
        else:
            extra.append(
                f"sun: Time.new({int(y)}, {int(mo)}, {int(d)}, {int(hh)}, {int(mm)}, 0)")
    if "style" in cfg:
        extra.append(f"style: {json.dumps(cfg['style'], ensure_ascii=False)}")
    extra_s = (", " + ", ".join(extra)) if extra else ""

    code = (
        f"require 'json'; load '{RENDER}'; "
        f"SURender.configure(views: JSON.parse('{views_rb}'), out_dir: '{out}', "
        f"cx: {cx}, cz: {cz}{extra_s}); "
        "SURender.prep()"
    )
    extra_keys = [k for k in ("bg", "sky", "gradient", "sun", "sun_local",
                              "light", "dark", "fog_color", "fog_start",
                              "fog_end", "style") if k in cfg]
    if extra_keys:
        print(f"    氛围：{', '.join(extra_keys)}")
    return 0 if step("prep", code, timeout=180) is not None else 1


def cmd_shots(_args):
    """逐视角独立请求出图 —— 每张 1—2 秒返回"""
    n = step("读视角数", "SURender::VIEWS.size.to_s", timeout=30, quiet=True)
    if not n:
        return 1
    n = int(n)
    print(f"    共 {n} 个视角，逐个出图：")
    bad = 0
    for i in range(n):
        if step(f"  shot[{i}]", f"SURender.shot({i})", timeout=120) is None:
            bad += 1
    print(f"    完成 {n - bad}/{n}" + (f"，{bad} 张失败" if bad else "，全部成功"))
    return 1 if bad else 0


def cmd_finish(_args):
    return 0 if step("finish", "SURender.finish") is not None else 1


def cmd_scenes(_args):
    return 0 if step("make_scenes", "SURender.make_scenes.to_s") is not None else 1


def cmd_save(args):
    if not args:
        print('用法: save "D:/proj/model.skp"')
        return 2
    path = args[0].replace("\\", "/")
    # ★ 路径由 Python 拼好再传，不做 Ruby 字符串插值：
    #   一旦插值没发生，SketchUp 会把字面量 #{VAR} 当文件名，静默存到 ~/Documents 下。
    code = f"Sketchup.active_model.save('{path}'); File.size('{path}').to_s + ' | ' + Sketchup.active_model.path"
    return 0 if step("save", code, timeout=300) is not None else 1


VERIFY_RB = r"""
require 'json'
m = Sketchup.active_model
bb = m.bounds
faces = 0; edges = 0; groups = 0; insts = 0
walk = nil
walk = lambda do |ents|
  ents.each do |e|
    if e.is_a?(Sketchup::Face) then faces += 1
    elsif e.is_a?(Sketchup::Edge) then edges += 1
    elsif e.is_a?(Sketchup::Group) then groups += 1; walk.call(e.entities)
    elsif e.is_a?(Sketchup::ComponentInstance) then insts += 1; walk.call(e.definition.entities)
    end
  end
end
walk.call(m.entities)
{
  'path'       => m.path.to_s,
  'bounds_m'   => ('%.3f x %.3f x %.3f' % [bb.width.to_m, bb.height.to_m, bb.depth.to_m]),
  'bounds_mm'  => [bb.width.to_mm.round, bb.height.to_mm.round, bb.depth.to_mm.round],
  'faces'      => faces, 'edges' => edges, 'groups' => groups, 'instances' => insts,
  'defs_used'  => m.definitions.select { |d| d.count_used_instances > 0 }.size,
  'pages'      => m.pages.map { |p| p.name },
  'layers'     => m.layers.map { |l| l.name },
  'materials'  => m.materials.map { |x| x.name },
  'top'        => m.entities.map { |e| e.name.to_s.empty? ? e.typename : e.name }
}.to_json
"""

# 材质审计：① 有无「该有材质却为 nil」的构件（顶层容器组无材质是正常的）
#            ② 每种材质真正覆盖多少个构件
AUDIT_RB = r"""
require 'json'
m = Sketchup.active_model
cov = Hash.new(0)
nil_groups = 0
no_mat_detail = []
walk = nil
walk = lambda do |ents, depth|
  ents.each do |e|
    next unless e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
    if e.material.nil?
      nil_groups += 1
      no_mat_detail << "#{e.name}[#{e.layer.name}]d#{depth}"
    else
      cov[e.material.name] += 1
    end
    walk.call(e.entities, depth + 1) if e.respond_to?(:entities)
  end
end
walk.call(m.entities, 0)
{
  'declared'   => m.materials.map { |x| x.name },
  'covered'    => cov.map { |k, v| [k, v] }.sort_by { |k, v| -v },
  'nil_groups' => nil_groups,
  'nil_sample' => no_mat_detail.first(10)
}.to_json
"""


def cmd_verify(_args):
    res = step("结构验收", VERIFY_RB, timeout=180)
    if res is None:
        return 1
    d = json.loads(res)
    print(f"    文件      : {d['path']}")
    print(f"    外包尺寸  : {d['bounds_m']} m   ({d['bounds_mm']} mm)")
    print(f"    面/边/组  : {d['faces']} / {d['edges']} / {d['groups']}")
    print(f"    图层 {len(d['layers'])} 个  材质 {len(d['materials'])} 种  场景 {len(d['pages'])} 个")
    print("    顶层实体  : " + ", ".join(d["top"][:10]))
    return 0


def cmd_materials(_args):
    res = step("材质审计", AUDIT_RB, timeout=180)
    if res is None:
        return 1
    d = json.loads(res)
    declared, covered = d["declared"], dict(d["covered"])
    print(f"    声明 {len(declared)} 种 / 实际被引用 {len(covered)} 种")
    missing = [n for n in declared if covered.get(n, 0) == 0]
    if missing:
        print("    ⚠️ 声明了但没有任何构件引用（clear! 后会被 purge_unused 静默删除）：")
        for n in missing:
            print(f"         - {n}")
        print("       → 说明这些材质根本没落到几何上，检查是否忘了给对应构件赋材质。")
    else:
        print("    ✓ 声明的材质全部有构件引用")
    for n, c in d["covered"]:
        print(f"         {n}: {c} 个构件")
    if d["nil_groups"]:
        print(f"    ⚠️ 无材质的组 {d['nil_groups']} 个（顶层容器组无材质属正常；"
              f"子组无材质才是漏赋）：")
        for s in d["nil_sample"]:
            print(f"         - {s}")
    return 0


def _opt(args, key):
    return args[args.index(key) + 1] if key in args and args.index(key) + 1 < len(args) else None


CMDS = {
    "probe": cmd_probe, "load": cmd_load, "run": cmd_run,
    "prep": cmd_prep, "shots": cmd_shots, "finish": cmd_finish,
    "scenes": cmd_scenes, "save": cmd_save,
    "verify": cmd_verify, "materials": cmd_materials,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        print("可用命令:", ", ".join(CMDS))
        return 2
    return CMDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
