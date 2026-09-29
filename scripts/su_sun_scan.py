# -*- coding: utf-8 -*-
"""su_sun_scan.py —— 扫描 SketchUp 的 ShadowTime → 太阳高度角/方位角，反推时区偏移。
用法： python su_sun_scan.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from su_client import SuClient  # noqa: E402

RB = (
    "si=Sketchup.active_model.shadow_info;"
    "$saved_sun=si['ShadowTime'];"
    "out=[];"
    "(0..23).each{|h| si['ShadowTime']=Time.new(2026,9,29,h,0,0);"
    "d=si['SunDirection']; out << [h,d.x,d.y,d.z];};"
    "si['ShadowTime']=$saved_sun;"
    "out.to_json"
)


def main():
    with SuClient() as c:
        r = c.tool("execute_ruby", code=RB)
    if r is None or not r.get("success"):
        print("[X]", r)
        return 1
    import json
    rows = json.loads(r["result"])
    print("ShadowTime 设定值（2026-09-29）→ SketchUp 实际给出的太阳位置：")
    print("%-8s %-10s %-10s %-10s" % ("设定时刻", "高度角", "方位角", "方向"))
    best = None
    for h, dx, dy, dz in rows:
        el = math.degrees(math.asin(max(-1.0, min(1.0, dz))))
        az = math.degrees(math.atan2(dx, dy)) % 360.0
        if el > 0:
            print("%02d:00     %7.2f°   %7.1f°" % (h, el, az))
            if best is None or abs(el - 13.5) < abs(best[1] - 13.5):
                best = (h, el, az)
    if best:
        print()
        print("  最接近目标高度角 13.5° 的设定时刻：%02d:00（高度角 %.2f°，方位角 %.1f°）"
              % best)
    return 0


if __name__ == "__main__":
    sys.exit(main())
