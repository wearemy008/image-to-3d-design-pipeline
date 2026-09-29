# -*- coding: utf-8 -*-
"""
把 HTML 模板里的 {{占位符}} 替换为压缩后的 base64 图片，产出单文件自包含 HTML。

用法：
  1) 改 CONFIG
  2) cd 到 venv 的 Scripts 目录后 ./python.exe embed_images.py

为什么必须压缩：
  效果图原图 1.5-2.5 MB/张，9 张 base64 后 HTML 会超过 20 MB。
  缩到宽 1100-1500 px + JPEG q86 后，9 张合计约 1.7 MB，成品 HTML 约 2.3 MB，浏览器秒开。
"""
import base64
import io
import os
from PIL import Image

# ==================== CONFIG ====================
TEMPLATE = r"C:\path\to\report_body.html"        # 含 {{IMG_XXX}} 占位符的模板
OUTPUT = r"D:\path\to\最终方案报告.html"
# 占位符 -> (源图路径, 最大宽度, JPEG 质量)
# 封面/全幅图给 1500-1800，两栏画廊小图给 1100 即可
JOBS = {
    "IMG_HERO": (r"D:\...\01_建筑外观_鸟瞰.png", 1800, 88),
    "IMG_R1":   (r"D:\...\01_建筑外观_鸟瞰.png", 1500, 86),
    "IMG_R2":   (r"D:\...\02_单元入口_人视.png", 1500, 86),
    # ...
}
# ================================================


def data_uri(path, maxw, quality):
    im = Image.open(path).convert("RGB")
    if im.width > maxw:
        h = round(im.height * maxw / im.width)
        im = im.resize((maxw, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    raw = buf.getvalue()
    print(f"  {os.path.basename(path):34s} {im.size[0]}x{im.size[1]}  {len(raw)/1024:6.0f} KB")
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def main():
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    print("内嵌图片：")
    for key, (path, maxw, q) in JOBS.items():
        ph = "{{" + key + "}}"
        n = html.count(ph)
        if n == 0:
            raise SystemExit(f"[错误] 模板里找不到占位符 {ph}")
        html = html.replace(ph, data_uri(path, maxw, q))
        print(f"    ^ 替换 {ph} x{n}")

    # 关键校验：残留占位符说明模板与配置对不上，必须报错而不是默默产出残缺 HTML
    if "{{" in html:
        leftover = html[html.index("{{"):html.index("{{") + 60]
        raise SystemExit(f"[错误] 仍有未替换的占位符: {leftover}")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n输出: {OUTPUT}")
    print(f"体积: {os.path.getsize(OUTPUT)/1024/1024:.2f} MB")


if __name__ == "__main__":
    main()
