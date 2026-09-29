# -*- coding: utf-8 -*-
"""sun_test.py —— 同一视角、不同太阳时刻/明暗滑杆的快速对比出图。
用法： python sun_test.py <views.json> <视角序号> [输出子目录名]
每次改完参数立刻出图，用于在 1-2 秒/张的成本下迭代氛围。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from su_pipeline import step, read_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "su_render.rb").replace("\\", "/")

# (标签, 太阳[年,月,日,时,分], 亮, 暗)
CASES = [
    ("A_1705_70_25", [2026, 9, 29, 17, 5], 70, 25),
    ("B_1720_70_30", [2026, 9, 29, 17, 20], 70, 30),
    ("C_1732_65_35", [2026, 9, 29, 17, 32], 65, 35),
]


def main():
    cfg_path = sys.argv[1]
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    sub = sys.argv[3] if len(sys.argv) > 3 else "_sun_test"
    cfg = read_json(cfg_path)
    out = os.path.join(cfg["out"], sub).replace("\\", "/")
    os.makedirs(out, exist_ok=True)

    views_rb = json.dumps(cfg["views"], ensure_ascii=False)
    name = cfg["views"][idx][0]

    for tag, sun, lo, hi in CASES:
        y, mo, d, hh, mm = sun
        code = (
            f"require 'json'; load '{RENDER}'; "
            f"SURender.configure(views: JSON.parse('{views_rb}'), out_dir: '{out}', "
            f"bg: {int(cfg['bg'])}, sky: true, gradient: true, "
            f"light: {lo}, dark: {hi}, "
            f"fog_color: {int(cfg['fog_color'])}, "
            f"fog_start: {float(cfg['fog_start'])}, fog_end: {float(cfg['fog_end'])}, "
            f"sun: SURender.local_time({y}, {mo}, {d}, {hh}, {mm})); "
            "SURender.setup_render; SURender.shot(%d)" % idx
        )
        r = step(f"{tag}", code, timeout=60)
        if r:
            src = os.path.join(out, f"SK_{name}.png")
            dst = os.path.join(out, f"{tag}.png")
            if os.path.exists(src):
                os.replace(src, dst)
            print(f"      → {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
