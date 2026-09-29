---
name: image-to-3d-design-pipeline
description: 从一张图纸或参考图（位图/截图/照片/**无标注的透视效果图**）出发，端到端做完「空间与材质尺寸识读 → 可编辑 CAD 图纸(DXF/DWG) → 空间策划与效果图 → SketchUp 三维模型 → 交付验收」的完整设计流水线，并用「单一几何真源」保证三个阶段的尺寸/材料/家具机械一致。输入带标注尺寸时读标注；输入是透视图/AIGC 效果图时用 img_probe.py 做**透视几何复原**（地平线+尺度标定+单应性矫正+色板提取）并给每个数字打 A/B/C/D 置信度。当用户说「把这张图做成完整项目」「从图到三维模型一条龙」「图片→CAD→方案→SketchUp 全流程」「图纸转模型，尺寸材料家具都要一致」「按这张图出方案再建模」「多阶段设计交付」「按这张效果图建模」时使用。触发词：全流程、端到端、从图到模型、图转三维、CAD 到 SketchUp、图纸复原并建模、设计流水线、方案+CAD+模型、效果图建模、透视复原。
agent_created: true
---

# 从平面图到三维模型的全流程设计流水线

一条链把五个阶段串起来，**每个阶段都产出可交付物**，并且用一份共享的几何数据
（`scripts/plan_data.py`）保证「图纸、模型、方案」三者的尺寸与材料不会各说各话。

```
 一张图纸 / 参考图（位图）
      │
      ├─── 有标注尺寸（施工图）───► ①-A 读标注
      │                              工具：raster-cad-drawing-reading
      │
      └─── 无标注（透视图/AIGC）─► ①-B 透视复原 ★
                                     工具：本技能 img_probe.py
      ▼
 ② 几何复原 ──► plan_data.py（★ 唯一真源，含 RECON 溯源段）+ DXF/DWG 图纸
      │          工具：本技能 make_dxf.py / image-to-cad 技能（转 DWG）
      ▼
 ③ 空间策划 ──► 方案正文（HTML 自包含）+ 效果图集 + 技术经济指标
      │          工具：design-proposal-package 技能
      ▼
 ④ 三维建模 ──► build_model.rb ← 由 plan_data 生成 → SketchUp 模型 + 渲染图
      │          工具：本技能 make_skp_rb.py / su_pipeline.py + sketchup-mcp-automation 技能
      ▼
 ⑤ 交付验收 ──► verify_consistency.py 五段一致性校验
                 + fidelity_report.py 保真度评估（★ 证明复原得像）
```

> ★ **输入分类是第一步的分叉点。** 有标注尺寸就直接读，准确且快；
> 无标注的透视图必须走 ①-B 的透视几何复原，**否则所有尺寸都只能靠假定**。
> 上一轮实测：走 ①-B 但不标定、不评估，事后量出尺度误差 52.5%、色差 ΔE*76=34，
> 而四段一致性校验**全部通过** —— 因为图纸、模型、方案三方确实自洽，
> 只是整套尺寸是错的。这就是新增 E 段与 fidelity_report 的原因。

---

## 0. 先确认三件事（不要跳过）

用 `AskUserQuestion` 一次问齐，方向错了整条链白做：

| 问题 | 典型选项 |
|---|---|
| **★ 输入类型** | 带标注的施工图 / **无标注的透视图或 AIGC 效果图** / 实拍照片 |
| **交付范围** | 只要 CAD / CAD+方案 / CAD+方案+三维模型（全链） / 方案+三维模型 |
| **设计定位** | 建筑+室内一体化 / 建筑设计 / 室内设计 / 开发策划 |
| **三维精细度** | 体量级（快，只做外轮廓与层数） / 构造级（墙体分层、门窗框、家具，推荐） / 精装级 |
| **★ 精度承诺** | 关系精确（体量/比例对，绝对尺寸为假定值） / 尺寸实测（会走透视复原并出保真度报告，耗时更长） |

**★ 选「无标注透视图」时必须问精度承诺。** 这决定了走「体量级快速复原」还是
「尺度标定 + 保真度评估」的完整流程，也决定了交付说明里怎么写待核项。

其他默认：层高 2.90 m、净高 2.78 m（楼板 120）、外墙 240 / 内墙 200 / 栏板 120、
结构取框架、地点取用户所在城市。

---

## 0.5 环境准备（一次性）

```bash
python -m pip install ezdxf matplotlib Pillow      # ezdxf 必需，后两个是预览与后处理
```

**依赖装在哪个解释器上很关键**：`make_dxf.py` 找不到 `ezdxf` 会直接 `[X] 缺少 ezdxf`
退出（不会静默降级）。若用托管运行时，建议建独立 venv 并把依赖装进去，
后续所有命令都用该 venv 的 python 全路径调用；**不要把包装到全局环境**。

SketchUp 侧另需装 **su_mcp** 插件并让它监听 `127.0.0.1:9876`，
`su_pipeline.py probe` 能 0.1 秒返回才算通了（详见 `sketchup-mcp-automation` 技能）。

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
| `RECON` | **复原溯源**：输入类型、复原方法、每个数字的 A/B/C/D 置信度、色板、能力边界、保真度、待核项 | 无标注输入时**必填**；E 段会审计 |

改一个数字，重跑两条命令，图纸和模型同时更新 —— 这才是「一致」的实现方式。

---

## 2. 分阶段怎么做

### ① 识读：先分类，再选路

**★ 第一件事是判断输入类型，它决定后面全部工作。**

| 输入 | 判据 | 走哪条路 |
|---|---|---|
| **施工图 / 平面图** | 图上有尺寸文字、轴号、标注 | ①-A 读标注 |
| **透视效果图 / AIGC 渲染 / 照片** | 无任何尺寸文字，只有形体与材质 | ①-B 透视复原 |

**不要把 ①-A 的纪律套到 ①-B 上，也不要反过来。** ①-A 里「绝不可由像素量测反推尺寸」
是对的（图上有权威标注）；①-B 里图上根本没标注，不做透视几何就只剩假定。

---

#### ①-A 有标注尺寸（施工图）

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

---

#### ①-B 无标注（透视图 / AIGC 效果图）—— ★ 透视几何复原

**完整方法论见 `references/reconstruction-from-perspective.md`**（含推导、实测边界、
AIGC 特有陷阱）。这里是速查。

核心是一个不依赖焦距的公式：

```
比例尺(mm/px) = h_cam / (y − y_h)
h_cam = H · (y_b − y_h) / h_px        ← 用一个已知高度的物体标定
```

即：**只要标定一次，图上任意位置、任意深度的水平尺寸都能直接读出来。**

```bash
IP=scripts/img_probe.py

# 0 体检：尺寸、动态范围、水印
python $IP info 原图.png

# 1 ★ 测地平线（RMS ≤ 3px 才算过；地平线错则全图尺寸按比例全错）
python $IP horizon 原图.png --lines "x1,y1,x2,y2;..." --json h.json

# 2 ★ 标定相机高（多锚点，离散度 ≤ 8% 才可信）
python $IP scale 原图.png --horizon 355 --anchors "183,369.5,16,1700;254,370,18,1700" --json s.json

# 3 ★ 矫正关键立面（最可靠手段；warp_ratio ≤ 1.08 可逐像素量测）
python $IP rectify 原图.png --quad "152,310;340,322;340,378;152,378" --w 36000 -o 矫正.png

# 4 读其余跨度
python $IP measure 原图.png --scale s.json --segs "x1,y1,x2,y2;..." --vertical "x,yt,yb"

# 5 取色板（基色 = 明明暗中值；黄昏图降饱和 15~20% 再用）
python $IP palette 原图.png -k 6 --pair --json c.json

# 6 一条龙
python $IP probe 原图.png --horizon 355 --anchors "..." -k 6 --json recon.json

# 7 目视复核（网格是**等深线**，同一条线上比例尺相同）
python $IP grid 原图.png --horizon 355 --h-cam 1541 --anchors "..." -o g.png
```

**★ 实测出来的头号限制**（2026-09-29，570×479 的 AIGC 图）：

| 锚点 | 像素高 | 定位误差 | 可靠性 |
|---|---|---|---|
| 远景人物 | **14–18 px** | ±8~11%，叠加语义误差 **>20%** | 不可作实测依据 |
| 中景人物 | 40–60 px | ±3% | 可用 |
| 近景人物/门/台阶 | ≥100 px | ±1.5% | ★ 可靠 |

**AIGC 效果图上的人物通常只有 14~18 px 高**，这是复原精度上不去的头号原因。
`img_probe.py` 会在锚点 <40px 时自动降级、<15px 直接判 `bad` —— **不要手动忽略它**。

**心法：优先复原「比值」而非「绝对值」。** 层数、开间节奏、悬挑与柱距的比值
从图上直接读，误差小；绝对值要标定，误差大。先定比值，再整体缩放到合理量级。

**★ AIGC 特有陷阱：不要矫正远端面。** AI 把远处的平行线画得不平行，
强行拉正等于**把畸变编造成尺寸**。远端只用近似法。

---

#### ①-C 不管哪条路，都要填 RECON 溯源段

**这是本次最重要的新增。** `plan_data.py` 里的 `RECON` 段要求给**每个关键数字**
打来源标签，`verify_consistency.py` 的 E 段会逐条审计：

| 级别 | 含义 | 判据 | 允许用途 |
|---|---|---|---|
| **A 实测** | 可作施工依据 | 矫正后量测，warp_ratio ≤ 1.08，离散度 ≤ 8% | 直接用于真源 |
| **B 参考** | 体量可信，绝对值待复核 | 尺度法，锚点 ≥40px，离散度 ≤ 15% | 用于真源 + 标注待核 |
| **C 量级** | 只有数量级对 | 锚点 <25px，或远景 | 仅作合理性检查 |
| **D 假定** | 图上无从判断（墙厚、洞口宽高、构造做法） | — | **必须列入交付说明** |

E 段会交叉校验**等级与证据是否相符**（实测注入验证过，能抓 6 类错误）：

- 锚点离散度 >15% 却仍有 A/B 级 → **失败**
- `warp_ratio` >1.08 却仍有 A 级 → **失败**
- 关键几何量（`GEOM.w/d/floor_h/t_outer/sill/head`）未标来源 → **失败**
- D 级没写假定依据 / 色板没免责声明 / 能力边界为空 → **失败**
- `fidelity.scale_err_pct` >25% → **失败**

**把 B 级数字当 A 级交付，比不做复原更危险。**

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
# 五段一致性校验
python verify_consistency.py --dxf D:/proj/plan.dxf --report D:/proj/方案.html

# ★ 保真度评估（无标注输入时必跑）
python fidelity_report.py --ref 原图.png --rep 渲染/SK_06_入口人视.png \
    --scale-ref "x1,y1,x2,y2;..." --scale-rep "x1,y1,x2,y2;..." \
    -o 保真度报告.html
```

**五段校验，20 项左右，必须 0 失败**：

| 层 | 检查内容 |
|---|---|
| A 真源自洽 | 尺寸链闭合、轴网末点=总开间、墙高=层高−楼板、洞口在墙内、洞口不重叠、材质引用完整、家具在轮廓内、湿区不重叠 |
| B DXF ↔ 真源 | **用「墙体」图层范围**核对开间/进深（全图幅含轴号与尺寸链，必然更大）、图层未超出允许集合 |
| C SKP ↔ 真源 | 外包尺寸 = 真源 + 外凸量（腰线 60 / 结构楼板底 120 / 雨棚 900 / 阳台 1500），容差 60mm |
| D 方案 ↔ 真源 | 正文里的开间、层高、房间名与面积是否与真源一致 |
| **E 复原溯源** ★ | 每个关键数字是否有 A/B/C/D 标签；**等级与证据是否相符**（离散度/warp_ratio）；色板有无免责声明；能力边界是否如实；保真度是否已评估 |

**★ E 段为什么必须单独有**：A~D 只验「三方自洽」，**不验「数字对不对」**。
上一轮实测四段全过、SSIM 仅 0.216、尺度误差 52.5% —— 数字全错但三方完全一致。
E 段 + fidelity_report 才是抓这类错误的。

**保真度三条判据**：

| 指标 | 含义 | 合格线 |
|---|---|---|
| 尺度误差 | 同一量测线两侧长度之比 | ±10% 可作实测；>25% 体量有误 |
| SSIM | 亮度/对比/结构乘积 | ≥0.75 合格；<0.60 体量关系有误 |
| ΔE*76 | Lab 主色距离 | <5 肉眼难辨；>15 材质映射错 |

**★ 视角必须一致** —— 用 views.json 的同一相机参数重渲。
视角不对齐时 SSIM 一定低，那是对比方法的错，不是复原的错。

**别跳过材质审计**：`clear!` 之后 `purge_unused` 会**静默删掉**没有任何构件引用的材质。
实测事故：脚本声明 20 种材质，模型里只剩 16 种 —— 差的 4 种（窗框、次墙面、磨砂玻璃、
黄铜五金）根本没落到几何上，且不报任何错。用 `su_pipeline.py materials` 抓出来。

**审计要按「组」数，不是按「面」数**：`SUKit.box` 把材质赋在**组**上
（`g.material = mat`），所以遍历面读 `Face#material` 会全是 `nil`，
得出「声明 20 种 / 引用 0 种」的假警报。看到这个数字先怀疑自己读错层级。

---

## 3. 全局踩坑索引

按「会浪费你多少时间」排序，完整版见 `references/pitfalls.md`（共 58 条）。
**第 1、2 条与第 38—43 条是高频重灾区**，动手前先扫一遍。
**输入是无标注透视图时，第 51—58 条是决定成败的关键。**

| # | 坑 | 一句话解法 |
|---|---|---|
| 1 | **su_mcp 长请求假死** | 绝不把「建相机 + N 次 write_image + sleep」打包成一个请求；逐视角发独立请求（各 1—2s）。被强杀的请求会继续占主线程，让后续全部排队 → 先 `su_pipeline.py probe` 探活再重试。想定位到底是哪条卡住：启动 SketchUp 时把 stdout 重定向到文件，插件会逐条打出 `ExecuteRuby: N chars` 的时间线 |
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
| 19 | **依赖装错解释器** | `make_dxf.py` 找不到 `ezdxf` 会直接退出不降级；逐个解释器试 `import ezdxf` 找到装了依赖的那个 |
| 20 | **材质赋在「组」上不在「面」上** | 审计材质**必须走组级**；读 `Face#material` 全是 `nil`，会得出「材质一个都没用上」的假警报 |
| 21 | **`su_client.ruby()` 吞异常** | Ruby 报错时返回 `None`，错误信息丢失；排查期用 `tool()` 打印完整返回才能看到原因 |
| 22 | **多行 Ruby 别用命令行 `-c` 传** | bash→Python→Ruby 四层转义会把代码弄坏；写成 `.rb` 文件再读入执行 |
| 38 | ★★ **建模必须 Z 轴向上** | 用 Y 轴向上建模时，SketchUp 的太阳相对模型仰角变成 `asin(SunDirection.y)`——实测 **−29.7°，太阳在地平线以下**，画面全平、无影、无暖调，怎么调 ShadowTime 都救不回来。同时模型相对世界旋转 90°，原生工具全错位。映射固定为 `SU_X=图纸X / SU_Y=图纸Y / SU_Z=高度`，并同步改 `north_angle`（90→0）与 views.json 的 `up`（[0,1,0]→[0,0,1]）。验算：`python su_env.py` 打出的太阳高度角必须为正，黄昏图在 8°—20° |
| 39 | ★ **ShadowTime 用 UTC 墙钟** | 直接填当地 17:05 得到的是**上午 9 点**的太阳（高度角 34° 而非 13°）。必须补本机 UTC 偏移：`t + t.utc_offset`（或 `SURender.local_time(...)`、views.json 的 `"sun_local": true`）。用 `su_sun_scan.py` 扫全天即可看出偏移量 |
| 40 | **`shadow_info['City']` 覆盖经纬度** | 写 City 会触发城市库查询并覆盖刚写入的 Lat/Lon（实测被改回模板默认的北京 39.93/116.39）。顺序必须是 City → 经纬度 → NorthAngle |
| 41 | **SketchUp 无限地面渲成沙色板** | `DisplayGround` 默认开着，按 z=0 无限延伸，是「模型摆在一张桌上」的元凶。关掉它，另在真源里铺一块够大的环境地面 |
| 42 | **地平线硬边** | 环境地面再大也有尽头。用雾化掉，且雾色要与天空近地色一致；`FogStartDist` 必须大于相机到主体的距离，否则主体被洗白 |
| 43 | **素模出不来黄昏暖调** | SketchUp 对太阳**色温**响应很弱，实测 17:05/17:20/17:32 三档几乎无差别（`su_render.py sun_test.py` 可复现）。真正的杠杆是：① 材质基色偏暖（冷白混凝土 → 暖白）；② `Light`/`Dark` 滑杆拉对比；③ 后期 `grade_render.py` 轻调色 |
| 44 | **尺寸界线飞出几十米** | `add_linear_dim` 的 `p1/p2` 是**被测点**，写成图纸原点会让界线横跨全图，看着像画错一堆矩形。测量点要贴被测边、基点就近放 |
| 45 | **DXF 中文图层名变 `\U+56fe`** | R2000 及更早按 code page 写盘会转义非 ASCII。改用 `ezdxf.new("R2010")`；导出预览 PNG 要显式指定白底 |
| 46 | **球心 `k·r` 未两头验算** | 展品球心写 `0.42r` 导致半径 3200 的球沉到楼板下 1856 mm（审计表现为「建筑总高 25856 ≠ 24000」）。球底 `(k−1)r`、球顶 `(k+1)r` 都要验 |
| 47 | **审计按想当然的组名写前缀** | 本次三条假故障：针叶树种被命名成「乔木N」、栏杆组叫「露台栏杆_南」、人物组叫「人1」。判据要照生成器**实际写出的名字**写 |
| 48 | **审计脚本 import 到同名模块** | 项目与技能目录都有 `plan_data.py`；技能目录要 `sys.path.append` 而不是 `insert(0)`，否则拿到技能自带那份，`AttributeError` |
| 49 | **SU 2025 移除 `Layers#current`** | 写它会在 begin/rescue 之外抛 NoMethodError，被最外层 rescue 吞掉 → 清图层整段静默失效。用 `Model#active_layer`；另 `su_kit.prune_unused_layers` 只删零引用图层 |
| 50 | **调色脚本「备份+原地覆盖」丢图** | 第二次出图时因备份已存在而继续用旧备份当源，新一轮渲染被静默丢弃。改成 `_raw/`（原图）与成品目录分离 |

### 透视复原专项（无标注输入时必读，2026-09-29 实测补录）

| # | 坑 | 一句话解法 |
|---|---|---|
| 51 | ★★★ **AIGC 图上的人物只有 14~18 px 高** | 端点定位误差 ±1.5px → **±8~11%**，叠加「这个像素是不是人脚底」的语义误差，总误差 **>20%**。找 ≥100px 的近景锚点；`img_probe.py scale` 会自动降级判定，**别手动忽略** |
| 52 | ★★★ **四段一致性校验全过，但尺寸全错** | A~D 只验「图纸/模型/方案三方自洽」，**不验数字对不对**。上一轮实测 SSIM 0.216、尺度误差 52.5%、色差 ΔE 34，四段却全绿。必须加 E 段溯源审计 + `fidelity_report.py` |
| 53 | ★★ **把画面中线当地平线** | 只有光轴完全水平时二者才重合，效果图几乎都是俯/仰视，会偏几百像素 → 全图尺寸按比例错且**看着都合理**。用灭点法（两组水平线求交点），RMS >3px 即不合格 |
| 54 | ★★ **拿不同深度的线混测地平线** | 池岸有弧度、水面线不与之平行，混测得到 RMS 15px。用建筑的楼板线/檐口线族（真平行）。`horizon` 的 RMS 就是自检，别忽略 |
| 55 | ★★ **矫正 AIGC 图的远端面** | AI 把远处平行线画得不平行，强行拉正等于**把畸变编造成尺寸**。只用近景矫正，且看 `warp_ratio`：≤1.08 可靠，>1.25 作废 |
| 56 | ★ **直接取屏幕像素当材质色** | 你看到的是受光面。取 k-means 各簇**亮部25%与暗部25%的中值**作基色（albedo）。AIGC 黄昏图还要整体降饱和 15~20%、色相往冷推 10° |
| 57 | ★ **HTML 报告用 `%` 格式化被百分号反噬** | CSS 的 `width:100%`、正文的「±10%」、`ΔE*76 &lt;` 全会触发 `unsupported format character`。**写 HTML 一律用 f-string 或 `.format()`**（本次连翻 4 次） |
| 58 | ★ **Pillow `Image.transform(PERSPECTIVE)` 跨版本 TypeError** | Pillow ≥10 改了系数形式，`coeffs=` 关键字直接报错。`img_probe.py` 改用**纯 numpy 逆映射 + 双线性**，跨版本稳定。附带：`json.dump` 遇 numpy 标量要加 `default=float` |

---

## 4. 与其它技能的关系

本技能是**编排层**，不做各阶段内部最细的活，避免重复维护：

| 阶段 | 交给 | 什么时候亲自做 |
|---|---|---|
| ①-A 识读（有标注） | `raster-cad-drawing-reading` | — |
| ①-B 透视复原（无标注） | **本技能独有** `img_probe.py` | 委托方只读标注文字，不做透视几何 |
| ② 转 DWG | `image-to-cad`（accoreconsole） | 出 DXF 由本技能 `make_dxf.py` 做 |
| ③ 方案与效果图 | `design-proposal-package` | — |
| ④ SketchUp 底层 | `sketchup-mcp-automation`（启动/插件/API） | 建模与出图编排由本技能做 |
| ⑤ 一致性 + 保真度 | **本技能独有** | — |

`scripts/su_client.py` 与 `sketchup-mcp-automation` 里的同名文件内容一致；
若该技能已安装，可直接复用它的版本，无需重复维护。

---

## 5. 文件清单

```
image-to-3d-design-pipeline/
├── SKILL.md                     本文件
├── README.md                    面向 GitHub 的说明
├── scripts/
│   ├── plan_data.py             ★ 单一几何真源（含 A2 户型示例 + RECON 溯源段）
│   ├── img_probe.py             ★★ 透视复原工具箱（地平线/尺度标定/单应性矫正/色板）
│   ├── fidelity_report.py       ★ 保真度评估（尺度误差 + SSIM + 色差 → HTML 报告）
│   ├── make_dxf.py              真源 → DXF + 预览 PNG
│   ├── make_skp_rb.py           真源 → SketchUp 建模脚本（带结构自检）
│   ├── verify_consistency.py    五层一致性校验（A 真源 / B DXF / C SKP / D 方案 / E 溯源）
│   ├── su_kit.rb                SketchUp 建模工具箱（项目无关）
│   ├── su_landscape.rb          SketchUp 景观工具箱（乔木/灌木/水体/台阶/栏杆/人物）
│   ├── su_render.rb             SketchUp 出图工具箱（项目无关）
│   ├── su_pipeline.py           分步编排器（★ 解决长请求假死）
│   ├── su_client.py             su_mcp 直连客户端
│   ├── su_env.py                读回阴影/坐标状态并核算太阳真实仰角（★ 查「渲染全平」）
│   ├── su_sun_scan.py           扫描全天太阳位置，反推 SketchUp 的 ShadowTime 时区偏移
│   ├── sun_test.py              同视角多组光照参数快速对比出图（氛围迭代用）
│   ├── grade_render.py          素模渲染后期轻调色（暖偏移 + 对比 + 暗角）
│   ├── activate_window.py       把 SketchUp 窗口置前（后台渲染慢时用）
│   ├── post_render.py           效果图后处理（裁水印 + 规范命名）
│   ├── embed_images.py          占位符 → base64 内嵌 HTML
│   └── qa_shot.js               Edge 无头质检（查坏图）
└── references/
    ├── pipeline-contract.md     阶段间数据契约（含 views.json / RECON 段格式）
    ├── reconstruction-from-perspective.md ★★ 透视复原方法论（公式推导 + 实测边界 + AIGC 陷阱）
    ├── su-kit-api.md            su_kit.rb 与 su_render.rb 完整 API
    ├── pitfalls.md              踩坑全记录（58 条，含现象 → 根因 → 解法）
    └── consistency-checklist.md 交付前逐项核对清单
```
