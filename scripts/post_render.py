# -*- coding: utf-8 -*-
"""
效果图后处理：统一裁掉右下角平台水印 + 规范中文命名 + 核对尺寸。

用法：
  1) 改下面 CONFIG 区
  2) cd 到 venv 的 Scripts 目录后 ./python.exe post_render.py
     （注意：不能用绝对路径直接调 python.exe，会报 127）

为什么统一裁切而不是自适应检测：
  自适应「在右下角找白像素」会被亮部内容（天空/白墙/白衣）误判，
  实测把阳台图的天空当成水印，多裁了 15%。平台水印位置是固定的
  （约在 y = 0.905H ~ 0.975H），统一按 0.895 x H 裁切最可靠，
  且所有图尺寸一致，HTML 画廊排版更整齐。
"""
import os
from PIL import Image

# ==================== CONFIG ====================
RAW_DIR = r"D:\path\to\效果图"          # 生成图所在目录
CROP_RATIO = 0.895                       # 裁切后保留的比例（原高 1024 -> 916）
# 源文件名 -> 最终规范名（按需要的展示顺序编号）
RENAME = {
    "Architectural_visualization__a_xxxx.png": "01_建筑外观_鸟瞰.png",
    "Architectural_visualization__e_xxxx.png": "02_单元入口_人视.png",
    # ...
}
KEEP_RAW = False                         # False = 裁切后删除原始长名文件
# ================================================


def main():
    print("=== 裁切并重命名 ===")
    for src, dst in RENAME.items():
        p = os.path.join(RAW_DIR, src)
        if not os.path.isfile(p):
            print(f"  [跳过] 源文件不存在: {src}")
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        tgt = round(H * CROP_RATIO)
        out = im.crop((0, 0, W, tgt))
        out.save(os.path.join(RAW_DIR, dst), "PNG", optimize=True)
        print(f"  {dst:26s} {W}x{H} -> {out.size[0]}x{out.size[1]}")

    if not KEEP_RAW:
        print("=== 清理原始文件 ===")
        keep = set(RENAME.values())
        for f in os.listdir(RAW_DIR):
            fp = os.path.join(RAW_DIR, f)
            if os.path.isfile(fp) and f.endswith(".png") and f not in keep:
                os.remove(fp)
                print("  removed:", f)

    print("\n=== 最终结果 ===")
    for f in sorted(os.listdir(RAW_DIR)):
        fp = os.path.join(RAW_DIR, f)
        if os.path.isfile(fp):
            im = Image.open(fp)
            print(f"  {f:26s} {im.size[0]}x{im.size[1]}  {os.path.getsize(fp)/1024:.0f} KB")


if __name__ == "__main__":
    main()
