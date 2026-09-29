# image-to-3d-design-pipeline

**一张平面图 → 识读 → CAD 图纸 → 空间策划与效果图 → SketchUp 三维模型 → 一致性验收。**

端到端的设计交付流水线，核心理念是**单一几何真源**：所有尺寸只在 `scripts/plan_data.py`
里写一次，二维图纸与三维模型由同一份数字驱动。这不是「尽量保持一致」，而是机械保证 ——
改一个数字、重跑两条命令，图纸和模型同时更新，不可能对不上。

已在 **A2 户型住宅（16.6 m × 10.8 m，6 层，双拼镜像）** 上完整实测：
DXF 356 图元、SketchUp 模型 12,042 面 / 20 材质 / 8 场景，外包尺寸
**16.72 × 18.65 × 13.20 m** 与图纸一致，一致性校验 **20 项通过 / 0 失败**。

---

## 流水线

```
 一张图纸 / 参考图（位图）
      │
      ├─── 有标注尺寸（施工图）───► ①-A 读标注
      │                              → raster-cad-drawing-reading
      │
      └─── 无标注（透视图/AIGC）─► ①-B 透视几何复原 ★
                                     → img_probe.py
      ▼
 ② 几何复原 ──► plan_data.py（★ 唯一真源，含 RECON 溯源段）+ DXF/DWG
      │          → make_dxf.py
      ▼
 ③ 空间策划 ──► 方案正文（自包含 HTML）+ 效果图集 + 技术经济指标
      │          → design-proposal-package
      ▼
 ④ 三维建模 ──► build_model.rb ← 由真源生成 → SketchUp 模型 + 渲染图
      │          → make_skp_rb.py / su_pipeline.py
      ▼
 ⑤ 交付验收 ──► verify_consistency.py 五段校验 + fidelity_report.py 保真度评估
```

每个阶段都有**可交付物**，不是内部中间态。

### ★ 无标注透视图的复原能力

输入是一张**没有尺寸标注**的透视效果图 / AIGC 渲染时，
尺寸不能靠「假定」，靠**透视几何**：

```
比例尺(mm/px) = h_cam / (y − y_h)
h_cam = H · (y_b − y_h) / h_px      ← 用一个已知高度的物体标定
```

焦距在推导中被消掉了 —— **不需要 EXIF，也不需要猜 AIGC 的虚构相机**。
标定一次之后，图上任意位置、任意深度的水平尺寸都能直接读出来。

```bash
python scripts/img_probe.py info     图.png     # 体检
python scripts/img_probe.py horizon  图.png --lines "..." --json h.json   # 测地平线
python scripts/img_probe.py scale    图.png --horizon 355 --anchors "..." # 标定相机高
python scripts/img_probe.py rectify  图.png --quad "x,y;x,y;x,y;x,y" --w 36000  # 矫正立面
python scripts/img_probe.py palette  图.png -k 6 --pair                    # 取色板
python scripts/img_probe.py probe    图.png --horizon 355 --anchors "..." # 一条龙
```

**最可靠的手段是 `rectify`**：给定一个实际为矩形立面的四点，
用单应性拉成正投影面，拉正后逐像素量测 = 真实尺寸。
看 `warp_ratio` 判质量（≤1.08 可靠，>1.25 作废）。

### ★ 诚实的边界

2026-09-29 实测（570×479 的 AIGC 黄昏效果图）：

| 目标 | 可行性 |
|---|---|
| 层数、开间节奏、悬挑比 | ★★★ 可信 |
| 体量块面关系 | ★★★ 可信 |
| 材质色系与相对明暗 | ★★ 可信 |
| **绝对尺寸** | **±10~25%，须标 B 级** |
| 施工级精度 | **做不到** |

**实测的头号限制**：AIGC 图上的人物只有 **14~18 像素**高，
端点定位误差 ±8~11%，叠加语义误差后总误差 **>20%**。

因此本技能引入 **RECON 溯源段**：每个数字都要打
**A 实测 / B 参考 / C 量级 / D 假定** 标签，
`verify_consistency.py` 的 **E 段**会交叉校验「等级与证据是否相符」。

### ★ 为什么需要 E 段与保真度评估

上一轮美术馆交付，A~D 四段校验 **28 项全过、0 失败**。
事后用 `fidelity_report.py` 量出：

    SSIM = 0.216（不合格）   尺度误差 = 52.5%   平均 ΔE*76 = 34

**四段验的是「图纸/模型/方案三方自洽」，不验「数字对不对」。**
三者确实自洽 —— 因为都从同一个 `plan_data.py` 取数。
但**没有任何一段能发现整套尺寸是错的**。

一致性校验 ≠ 正确性校验，**两者都要做**。

```bash
python scripts/fidelity_report.py --ref 原图.png --rep 渲染/SK_06_入口人视.png \
    --scale-ref "x1,y1,x2,y2;..." --scale-rep "..." -o 保真度报告.html
```

---

## 快速开始

```bash
# 0) 先看三件事的默认值：层高 2.90 / 外墙 240 / 内墙 200 / 楼板 120
#    交付范围、设计定位、三维精细度 —— 方向错了整条链白做

# 1) 改真源（自带 A2 户型完整示例，直接改数字）
#    scripts/plan_data.py

# 2) 出二维图
cd scripts
python make_dxf.py -o /path/to/plan.dxf        # → plan.dxf + plan_preview.png

# 3) 出三维脚本
python make_skp_rb.py -o /path/to/build_model.rb   # 生成物自带结构自检

# 4) 驱动 SketchUp（需 su_mcp 插件，见「依赖」）
python su_pipeline.py probe                     # 探活
python su_pipeline.py load                      # 载入建模/出图工具箱
python su_pipeline.py run /path/to/build_model.rb WBAuto.build

# 5) 逐视角出图（★ 每张独立请求，1—2 秒）
python su_pipeline.py prep --views-json /path/to/views.json
python su_pipeline.py shots
python su_pipeline.py finish

# 6) 场景 + 保存 + 审计
python su_pipeline.py scenes
python su_pipeline.py save "/path/to/model.skp"
python su_pipeline.py verify
python su_pipeline.py materials

# 7) 一致性校验（四层，必须 0 失败）
python verify_consistency.py --dxf /path/to/plan.dxf --report /path/to/方案.html
```

---

## 目录结构

```
SKILL.md                          完整方法论：阶段划分 / 真源字段 / 18 条踩坑索引
scripts/
  plan_data.py                    ★ 单一几何真源（含 A2 户型完整示例）
  make_dxf.py                     真源 → DXF + matplotlib 预览 PNG
  make_skp_rb.py                  真源 → SketchUp 建模脚本（带 def/end 结构自检）
  img_probe.py                    透视复原工具箱（地平线/尺度标定/单应性矫正/色板）★
  fidelity_report.py              保真度评估（尺度误差 + SSIM + 色差 -> HTML 报告）★
  verify_consistency.py           五层一致性校验（真源/DXF/SKP/方案/溯源）
  su_kit.rb                       SketchUp 参数化建模工具箱（项目无关）
  su_render.rb                    SketchUp 出图工具箱（项目无关）
  su_pipeline.py                  分步编排器（★ 解决长请求假死）
  su_client.py                    su_mcp 直连客户端（裸 TCP + 换行分隔 JSON-RPC）
  activate_window.py              把 SketchUp 窗口置前（后台渲染慢时用）
  post_render.py                  效果图后处理（裁平台水印 + 规范命名）
  embed_images.py                 占位符 → base64 内嵌 HTML
  qa_shot.js                      Edge 无头质检（查坏图）
references/
  reconstruction-from-perspective.md  透视复原方法论（公式推导 + 实测边界 + AIGC 陷阱）★
  pipeline-contract.md            阶段间数据契约（识读映射 / RECON 段 / views.json）
  su-kit-api.md                   su_kit.rb 与 su_render.rb 完整 API
  pitfalls.md                     32 条踩坑全记录（现象 → 根因 → 解法）
  consistency-checklist.md        交付前逐项核对清单 + 症状对照表
```

---

## 核心能力

**单一几何真源** —— `META / GEOM / MATERIALS / LAYERS / WALLS_X-Y / FURNITURE / BAYS /
BALCONY / STAIR / FACADE / ROOF / BASE / ROOMS` 一份数据，DXF 与 SketchUp 双端消费。
家具只需写**左户** 42 件，镜像户由 `mirror_box` 自动生成。

**参数化建模工具箱** —— `box` / `wall_x` `wall_y`（自动切洞 + 补窗台墙过梁 + 嵌玻璃）/
`glazing`（框+玻璃四件套）/ `entry_door`（双扇玻璃门）/ `louvers`（格栅细分）/
`array_floors`（楼层阵列）/ `mirror_box`（镜像户型，自动重排 min/max）。

**四层一致性校验** —— 真源自洽 12 项（尺寸链闭合、洞口不重叠、材质引用完整…）、
DXF ↔ 真源、SKP ↔ 真源（含外凸量换算）、方案 ↔ 真源。
所有跨阶段风险都做成**可机械检查**，不靠人眼。

**材质审计** —— `clear!` + `purge_unused` 会**静默删掉**没有构件引用的材质。
实测事故：脚本声明 20 种、模型只剩 16 种，差的 4 种根本没落到几何上且不报错。

---

## 几个反直觉的坑

- **su_mcp 长请求假死**：把「建相机 + N 次 write_image」打包成一个请求会超时；
  更糟的是**被强杀的请求会继续占住 SketchUp 主线程**，后续全部排队。
  必须逐视角发独立请求，卡住先 `probe` 探活再重试
- **`View#write_image` 不吃关键字哈希**：必须位置参数 `write_image(path, w, h, aa, comp)`
- **镜像户型后必须重排 min/max**：`mx(x)=总开间−x` 会让 x1 < x0，
  `box` 因 `px1 <= px0` 全部返回 nil —— 整户家具凭空消失且不报错
- **首层专属构件不能放进标准层组**：否则入口门会被 `array_floors` 阵列成 6 个
- **门樘不能是整块实心 box**：那样就把门洞封死了，只能做「左右竖框 + 上框」
- **平行投影 `Camera#height` 以英寸计**：写 `26.0` 等于 0.66 m，要写 `26.m`
- **轴线/外皮要写清**：北墙 `y_north` 给的是**外皮**，当轴线用会多算一个墙厚
- **`save` 路径别用 Ruby 插值**：插值没发生时 SketchUp 会把字面量 `#{VAR}` 当文件名，
  静默存到 `~/Documents` 下

完整 32 条见 [references/pitfalls.md](references/pitfalls.md)。

---

## 依赖

| 组件 | 用途 | 备注 |
|---|---|---|
| Python 3.10+ | 真源 / 生成器 / 校验 | `ezdxf`、`matplotlib`（预览）、`Pillow`（后处理） |
| SketchUp 2025 | 三维建模与出图 | 需装 **su_mcp** 插件，监听 `127.0.0.1:9876` |
| su_mcp 插件 | SketchUp ↔ 脚本桥 | 裸 TCP + 换行分隔 JSON-RPC 2.0（**不是 HTTP**） |
| AutoCAD（可选） | DXF → DWG | 交给 `image-to-cad` 技能走 accoreconsole |
| playwright-core（可选） | 无头质检 | 用系统 Edge，无需下载浏览器 |

`su_mcp` 的安装与启动见 [sketchup-mcp-automation](https://github.com/wearemy008) 技能。

---

## 与其它技能的关系

本技能是**编排层**，各阶段最细的活交给专业技能做，避免重复维护：

| 阶段 | 交给 |
|---|---|
| ①-A 位图识读（有标注） | `raster-cad-drawing-reading` |
| ①-B 透视复原（无标注） | **本技能独有** `img_probe.py`（委托方只读标注文字） |
| ② DXF → DWG | `image-to-cad` |
| ③ 方案与效果图 | `design-proposal-package` |
| ④ SketchUp 底层（插件/启动/API） | `sketchup-mcp-automation` |
| ⑤ 跨阶段一致性 | **本技能独有** |

---

## License

MIT
