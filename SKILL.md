---
name: image-to-3d-design-pipeline
description: 从一张平面图（位图/截图/照片）出发，端到端做完「空间与材质尺寸识读 → 可编辑 CAD 图纸(DXF/DWG) → 空间策划与效果图 → SketchUp 三维模型 → 交付验收」的完整设计流水线，并用「单一几何真源」保证三个阶段的尺寸/材料/家具机械一致。当用户说「把这张图做成完整项目」「从图到三维模型一条龙」「图片→CAD→方案→SketchUp 全流程」「图纸转模型，尺寸材料家具都要一致」「按这张平面图出方案再建模」「多阶段设计交付」时使用。触发词：全流程、端到端、从图到模型、图转三维、CAD 到 SketchUp、图纸复原并建模、设计流水线、方案+CAD+模型。
agent_created: true
---

# 从平面图到三维模型的全流程设计流水线

一条链把五个阶段串起来，**每个阶段都产出可交付物**，并且用一份共享的几何数据
（`scripts/plan_data.py`）保证「图纸、模型、方案」三者的尺寸与材料不会各说各话。

```
 一张平面图（位图）
      │
 ① 识读 ──────► 识读报告（尺寸链、房间、标注）+ 标注图
      │          工具：raster-cad-drawing-reading 技能
      ▼
 ② 几何复原 ──► plan_data.py（★ 唯一真源）+ DXF/DWG 图纸
      │          工具：本技能 make_dxf.py / image-to-cad 技能（转 DWG）
      ▼
 ③ 空间策划 ──► 方案正文（HTML 自包含）+ 效果图集 + 技术经济指标
      │          工具：design-proposal-package 技能
      ▼
 ④ 三维建模 ──► build_model.rb ← 由 plan_data 生成 → SketchUp 模型 + 渲染图
      │          工具：本技能 make_skp_rb.py / su_pipeline.py + sketchup-mcp-automation 技能
      ▼
 ⑤ 交付验收 ──► verify_consistency.py 一致性校验 + 归档脚本
```

---

## 0. 先确认三件事（不要跳过）

用 `AskUserQuestion` 一次问齐，方向错了整条链白做：

| 问题 | 典型选项 |
|---|---|
| **交付范围** | 只要 CAD / CAD+方案 / CAD+方案+三维模型（全链） / 方案+三维模型 |
| **设计定位** | 建筑+室内一体化 / 建筑设计 / 室内设计 / 开发策划 |
| **三维精细度** | 体量级（快，只做外轮廓与层数） / 构造级（墙体分层、门窗框、家具，推荐） / 精装级 |

其他默认：层高 2.90 m、净高 2.78 m（楼板 120）、外墙 240 / 内墙 200 / 栏板 120、
结构取框架、地点取用户所在城市。

---

## 1. 核心机制：单一几何真源

**这是本技能存在的理由。** 五个阶段里最容易出的不是「算错」，而是**同一个数字在三处对不上**：
平面图开间 16600、模型里写成 16650、方案正文又写 16.5 m —— 分开看都对，合起来是事故。

所以：**所有尺寸只在 `scripts/plan_data.py` 里写一次**，然后

```
plan_data.py ──┬──► make_dxf.py      → 二维 CAD 图纸
               └──► make_skp_rb.py   → SketchUp 建模脚本
```

`plan_data.py` 里写什么：

| 段落 | 内容 | 说明 |
|---|---|---|
| `META` | 项目名、比例、**尺寸链闭合记录** | 图上标注的尺寸链必须先加一遍闭合，闭合值存这里 |
| `GEOM` | 总开间/进深、墙厚、层高、洞口高度、轴网 | 「外皮/轴线」务必写清（踩过坑） |
| `MATERIALS` | 统一色卡 `<部位>_<构件>_<颜色>` → Hex | 中文可读，业主在 SketchUp 材质面板能直接看懂 |
| `LAYERS` | 图层名 | 二维三维同名，便于互查 |
| `WALLS_X/Y` | 每道墙：名称、材质、起止、厚度区间、洞口 | 洞口类型 `:win/:door/:slide` |
| `FURNITURE` | 家具：名称、材质、图层、平面矩形、高度区间 | **只写左户**，镜像户自动生成 |
| `BAYS/BALCONY/STAIR/FACADE/ROOF/BASE` | 飘窗、阳台、楼梯、立面附加、屋面、基座 | |
| `ROOMS` | 房间名 + 面积 + 标注位置 | 供 DXF 与方案「技术经济指标」章共用 |

改一个数字，重跑两条命令，图纸和模型同时更新 —— 这才是「一致」的实现方式。

---

## 2. 分阶段怎么做

### ① 识读：从位图拿到真实尺寸

**只能读图上的标注文字，绝不可由像素量测反推尺寸**（这是最常犯的错）。
加载 `raster-cad-drawing-reading` 技能做这一步，产出识读报告与标注图。

交付前必须做**尺寸链闭合校验**，把结果写进 `META['chains']`：

```python
"chains": {
    "上部开间": ([3300, 3700, 2600, 3700, 3300], 16600),   # 和为 16600 ✓
    "左进深":   ([4200, 3900, 2700, 600], 11400),
}
```
`verify_consistency.py` 会重算一遍，不闭合直接报失败。

同时记录**哪些数字是图上读出来的、哪些是假定的**（墙厚、洞口、层高、家具高度位图都看不出），
交付说明里要写清楚。

### ② 几何复原：填 plan_data，出 DXF

```bash
# 1) 填真源（照 §1 的表结构；本技能自带 A2 户型完整示例可直接改）
#    编辑 scripts/plan_data.py

# 2) 出平面图
python make_dxf.py -o D:/proj/plan.dxf
#    → plan.dxf + plan_preview.png（含轴网、轴号、尺寸链、房间名与面积）

# 3) 需要 DWG：交给 image-to-cad 技能走 accoreconsole 转（本技能只出 DXF）
```

**出图四条硬规矩**（`make_dxf.py` 已内置，改动时别破坏）：
1. 大字体写 `style.dxf.bigfont`（组码 4），**不要用 `set_extended_font_data`** —— 否则 DXF 里中文变问号
2. `ezdxf.new(setup=True)` 自带的 TTF 样式**只改字体、不删除** —— 删了会让实体引用悬空
3. 中间路径全 ASCII —— 中文路径在 accoreconsole 会失败，最后一步再复制成中文名
4. 线宽用出图绝对值（0.5mm = 50），**不随比例缩放** —— 线宽是打印属性

### ③ 空间策划与效果图

加载 `design-proposal-package` 技能。要点：
- 章节骨架、规范清单、造价单价、色卡与照明参数都在它的 `references/outline.md`
- **效果图生成前必须明说成本**（每张约 5—10 积分）
- 并行生成会**文件名撞秒覆盖**，必须给每次调用不同的 `output_dir`
- 平台水印统一按 `0.895 × 高` 裁切（自适应检测会被天空/亮部误判）
- 图片降到 JPEG（宽 1100—1500 / q86）再 base64 内嵌，否则 HTML 体积失控

方案正文里的**面积、开间、层高必须取自同一份 plan_data**，不要另起一套数字。

### ④ SketchUp 三维模型

```bash
# 1) 由真源生成建模脚本
python make_skp_rb.py -o D:/proj/build_model.rb
#    → 生成物自带结构自检（def/end 数量核对），不通过会直接报错

# 2) 让 SketchUp 跑起来（首次要装 su_mcp 插件并自动启动，见 sketchup-mcp-automation 技能）
python su_pipeline.py probe          # 探活：0.1s 返回「主线程空闲，可继续」
python su_pipeline.py load           # 载入 su_kit.rb / su_render.rb
python su_pipeline.py run D:/proj/build_model.rb WBAuto.build

# 3) 出图（★ 逐视角独立请求，不要打包成一个长请求）
#    先写 views.json：
#    {"out": "D:/proj/渲染", "views": [["01_轴测_西南", [-13.7,24.3,-17.0], [8.3,9.3,5.0], [0,1,0], false, 26.0], ...]}
python su_pipeline.py prep --views-json D:/proj/views.json
python su_pipeline.py shots          # 每张 1—2 秒返回
python su_pipeline.py finish

# 4) 场景与保存
python su_pipeline.py scenes
python su_pipeline.py save "D:/proj/model.skp"

# 5) 结构与材质审计
python su_pipeline.py verify
python su_pipeline.py materials
```

**几何工具箱 `su_kit.rb` 提供**（详见 `references/su-kit-api.md`）：
`box` / `wall_x` / `wall_y`（自动切洞 + 补窗台墙过梁 + 嵌玻璃） / `glazing`（框+玻璃四件套） /
`entry_door`（双扇玻璃门） / `louvers`（格栅细分） / `array_floors`（楼层阵列） / `mirror_box`（镜像户型）

**出图工具箱 `su_render.rb` 提供**：`prep` / `shot(i)` / `finish` / `make_scenes` / `save_as`

### ⑤ 交付验收

```bash
python verify_consistency.py --dxf D:/proj/plan.dxf --report D:/proj/方案.html
```

四层校验，**20 项左右，必须 0 失败**：

| 层 | 检查内容 |
|---|---|
| A 真源自洽 | 尺寸链闭合、轴网末点=总开间、墙高=层高−楼板、洞口在墙内、洞口不重叠、材质引用完整、家具在轮廓内、湿区不重叠 |
| B DXF ↔ 真源 | **用「墙体」图层范围**核对开间/进深（全图幅含轴号与尺寸链，必然更大）、图层未超出允许集合 |
| C SKP ↔ 真源 | 外包尺寸 = 真源 + 外凸量（腰线 60 / 结构楼板底 120 / 雨棚 900 / 阳台 1500），容差 60mm |
| D 方案 ↔ 真源 | 正文里的开间、层高、房间名与面积是否与真源一致 |

**别跳过材质审计**：`clear!` 之后 `purge_unused` 会**静默删掉**没有任何构件引用的材质。
实测事故：脚本声明 20 种材质，模型里只剩 16 种 —— 差的 4 种（窗框、次墙面、磨砂玻璃、
黄铜五金）根本没落到几何上，且不报任何错。用 `su_pipeline.py materials` 抓出来。

---

## 3. 全局踩坑索引

按「会浪费你多少时间」排序，完整版见 `references/pitfalls.md`。

| # | 坑 | 一句话解法 |
|---|---|---|
| 1 | **su_mcp 长请求假死** | 绝不把「建相机 + N 次 write_image + sleep」打包成一个请求；逐视角发独立请求（各 1—2s）。被强杀的请求会继续占主线程，让后续全部排队 → 先 `su_pipeline.py probe` 探活再重试 |
| 2 | **`write_image` 不吃关键字哈希** | 必须位置参数 `write_image(path, w, h, antialias, compression)` |
| 3 | **材质被静默清除** | 交付前做材质审计；材质数对不上就是有构件漏赋材质 |
| 4 | **漏窗框** | 只放一块玻璃板 = 悬浮玻璃；要「上下框 + 左右竖挺 + 玻璃内嵌」四件套 |
| 5 | **墙玻璃坐标轴转置** | 沿 X / 沿 Y 的墙，玻璃的长/厚方向必须切换；漏了会让构件坐标飞到 16000+ |
| 6 | **镜像后 min/max 未重排** | `mx(x)=总开间−x` 后 x1 < x0，`box` 会因 `px1<=px0` 全部返回 nil（整户家具消失） |
| 7 | **入口门被阵列成 N 个** | 首层专属构件必须放 `build_base`，不要放进会被 `array_floors` 的标准层组 |
| 8 | **门樘堵死门洞** | 门樘只能做「左右竖框 + 上框」，中间留空；写成一整块实心 box 就把洞封了 |
| 9 | **平行投影视高错** | `Camera#height` 以**英寸**计，必须写 `26.m`，写 `26.0` 等于 0.66 m |
| 10 | **平面图南朝上** | `up=[0,0,-1]` 会南朝上；改 `[0,0,1]` 即北朝上且不镜像 |
| 11 | **`save` 路径别用 Ruby 插值** | 插值没发生时会把字面量当文件名，静默存到 `~/Documents`。用宿主语言拼好完整路径 |
| 12 | **`Model#modified` 不存在** | Ruby 里调用会 NoMethodError（那是插件自己算的字段） |
| 13 | **整块大板不像格栅** | 木格栅按「条宽 = 缝宽」细分竖条 |
| 14 | **SketchUp 卡在欢迎界面** | 无参数启动不加载插件；用模板 `.skp` 作启动参数 |
| 15 | **渲染选项逐键 rescue** | 非法键会抛错；若在 `ensure` 里抛错会**掩盖真错误** |
| 16 | **matplotlib 中文变方块** | 预览图要显式指定 `Microsoft YaHei`/`SimHei` |
| 17 | **DIMENSION 在预览里不显示** | 是块引用，要用 `virtual_entities()` 展开 |
| 18 | **居中文字读不到 insert** | `set_placement(align=…)` 后位置在 `align_point`，读 `insert` 会拿到原点 |

---

## 4. 与其它技能的关系

本技能是**编排层**，不做各阶段内部最细的活，避免重复维护：

| 阶段 | 交给 | 什么时候亲自做 |
|---|---|---|
| ① 识读 | `raster-cad-drawing-reading` | — |
| ② 转 DWG | `image-to-cad`（accoreconsole） | 出 DXF 由本技能 `make_dxf.py` 做 |
| ③ 方案与效果图 | `design-proposal-package` | — |
| ④ SketchUp 底层 | `sketchup-mcp-automation`（启动/插件/API） | 建模与出图编排由本技能做 |
| ⑤ 一致性 | **本技能独有** | — |

`scripts/su_client.py` 与 `sketchup-mcp-automation` 里的同名文件内容一致；
若该技能已安装，可直接复用它的版本，无需重复维护。

---

## 5. 文件清单

```
image-to-3d-design-pipeline/
├── SKILL.md                     本文件
├── README.md                    面向 GitHub 的说明
├── scripts/
│   ├── plan_data.py             ★ 单一几何真源（含 A2 户型完整示例）
│   ├── make_dxf.py              真源 → DXF + 预览 PNG
│   ├── make_skp_rb.py           真源 → SketchUp 建模脚本（带结构自检）
│   ├── verify_consistency.py    四层一致性校验
│   ├── su_kit.rb                SketchUp 建模工具箱（项目无关）
│   ├── su_render.rb             SketchUp 出图工具箱（项目无关）
│   ├── su_pipeline.py           分步编排器（★ 解决长请求假死）
│   ├── su_client.py             su_mcp 直连客户端
│   ├── activate_window.py       把 SketchUp 窗口置前（后台渲染慢时用）
│   ├── post_render.py           效果图后处理（裁水印 + 规范命名）
│   ├── embed_images.py          占位符 → base64 内嵌 HTML
│   └── qa_shot.js               Edge 无头质检（查坏图）
└── references/
    ├── pipeline-contract.md     阶段间数据契约（含 views.json / 报告格式）
    ├── su-kit-api.md            su_kit.rb 与 su_render.rb 完整 API
    ├── pitfalls.md              踩坑全记录（含现象 → 根因 → 解法）
    └── consistency-checklist.md 交付前逐项核对清单
```
