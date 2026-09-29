# -*- coding: utf-8 -*-
"""su_env.py —— 读取 SketchUp 当前阴影/坐标状态，并核算太阳相对模型的真实仰角。
用法： python su_env.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from su_client import SuClient  # noqa: E402

RB = (
    "si=Sketchup.active_model.shadow_info;"
    "d=si['SunDirection'];"
    "bb=Sketchup.active_model.bounds;"
    "[si['Latitude'],si['Longitude'],si['NorthAngle'],si['City'],"
    "si['DisplayShadows'],si['UseSunForAllShading'],si['ShadowTime'].to_s,"
    "si['DisplayFog'],d.x,d.y,d.z,"
    "bb.min.x.to_m,bb.min.y.to_m,bb.min.z.to_m,"
    "bb.max.x.to_m,bb.max.y.to_m,bb.max.z.to_m].to_json"
)


def main():
    with SuClient() as c:
        r = c.tool("execute_ruby", code=RB)
    if r is None or not r.get("success"):
        print("[X] 读取失败：", r)
        return 1
    import json
    v = json.loads(r["result"])
    keys = ["纬度", "经度", "朝北角", "城市", "阴影开关", "太阳统一着色", "阴影时刻",
            "雾", "太阳方向X", "太阳方向Y", "太阳方向Z",
            "包围盒minX", "包围盒minY", "包围盒minZ",
            "包围盒maxX", "包围盒maxY", "包围盒maxZ"]
    for k, x in zip(keys, v):
        print(f"  {k:<14}{x}")

    _, _, north, *_rest = v
    dx, dy, dz = float(v[8]), float(v[9]), float(v[10])
    el_z = math.degrees(math.asin(max(-1.0, min(1.0, dz))))
    az = math.degrees(math.atan2(dx, dy)) % 360.0
    print()
    print(f"  太阳高度角（世界 Z 为天顶）：{el_z:6.2f}°")
    print(f"  太阳方位角（0=北 90=东 180=南 270=西）：{az:6.1f}°")
    print(f"  朝北角设定：{north}°")
    if el_z <= 0:
        print("  [X] 太阳在地平线以下 —— 模型仍是「躺着」的，光照会全平")
    elif el_z < 8:
        print("  [!] 太阳极低，影子极长，容易糊成一片")
    elif el_z < 25:
        print("  [OK] 黄金时段，长影 + 暖调")
    else:
        print("  [!] 太阳偏高，明暗对比弱，不像黄昏")
    return 0


if __name__ == "__main__":
    sys.exit(main())
