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
 一张平面图（位图/截图/照片）
      │
 ① 识读 ──────► 识读报告（尺寸链、房间、标注）+ 标注图
      │          → raster-cad-drawing-reading
      ▼
 ② 几何复原 ──► plan_data.py（★ 唯一真源）+ DXF/DWG
      │          → make_dxf.py
      ▼
 ③ 空间策划 ──► 方案正文（自包含 HTML）+ 效果图集 + 技术经济指标
      │          → design-proposal-package
      ▼
 ④ 三维建模 ──► build_model.rb ← 由真源生成 → SketchUp 模型 + 渲染图
      │          → make_skp_rb.py / su_pipeline.py
      ▼
 ⑤ 交付验收 ──► verify_consistency.py 四层校验 + 脚本归档
```

每个阶段都有**可交付物**，不是内部中间态。

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
  verify_consistency.py           四层一致性校验（真源自洽 / DXF / SKP / 方案）
  su_kit.rb                       SketchUp 参数化建模工具箱（项目无关）
  su_render.rb                    SketchUp 出图工具箱（项目无关）
  su_pipeline.py                  分步编排器（★ 解决长请求假死）
  su_client.py                    su_mcp 直连客户端（裸 TCP + 换行分隔 JSON-RPC）
  activate_window.py              把 SketchUp 窗口置前（后台渲染慢时用）
  post_render.py                  效果图后处理（裁平台水印 + 规范命名）
  embed_images.py                 占位符 → base64 内嵌 HTML
  qa_shot.js                      Edge 无头质检（查坏图）
references/
  pipeline-contract.md            阶段间数据契约（识读映射表 / views.json / 报告格式）
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
| ① 位图识读 | `raster-cad-drawing-reading` |
| ② DXF → DWG | `image-to-cad` |
| ③ 方案与效果图 | `design-proposal-package` |
| ④ SketchUp 底层（插件/启动/API） | `sketchup-mcp-automation` |
| ⑤ 跨阶段一致性 | **本技能独有** |

---

## License

MIT
