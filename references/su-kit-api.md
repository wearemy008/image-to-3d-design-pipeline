# su_kit.rb / su_render.rb API

两个工具箱**与项目无关**，可以直接复用到任何平面图建模任务。
调用前先 `load`：

```ruby
# 把 <skill-dir> 换成本机 skill 所在目录（即本文件上一级的上一级）
load '<skill-dir>/scripts/su_kit.rb'
load '<skill-dir>/scripts/su_render.rb'
```

---

## 坐标与单位（先读这条）

SketchUp **Y 轴向上**。图纸平面坐标 (X, Y) + 高度 H 的映射：

```
SketchUp X = 图纸 X
SketchUp Y = 高度 H        ← 向上
SketchUp Z = 图纸 Y
```

工具箱所有入参都是**毫米**，内部自动 `.mm`。
**读返回值必须 `.to_m`** —— SketchUp 内部一律以英寸存储，
`bounds.width` 直接读会得到英寸数（16600 的模型会显示 653 左右的怪数字）。

---

## su_kit.rb

### `SUKit.setup(materials:, layers:, transparent:, unit:)`
一次性初始化单位、材质、图层。
`materials` 传 `{'材质名' => 0xRRGGBB}`；`transparent` 传 `{'玻璃' => 0.35}`。
内部先查后建（`materials.add` 重名会抛异常）。

### `SUKit.clear`
`entities.clear!` + `definitions.purge_unused`，用于从模板复制的模型整体重建。
⚠️ **副作用**：会静默删掉所有未被引用的材质。

### `SUKit.box(parent, name, layer, mat, px0, py0, px1, py1, h0, h1)`
唯一图元：矩形底面 + pushpull 成正交六面体。
- 非法区间（`px1<=px0` 等）返回 `nil`，不报错
- `f.normal.y < 0` 时自动 `reverse!`，保证向上长

### `SUKit.split(a0, a1, holes)` → `[[s,e], ...]`
在 [a0, a1] 上挖去洞口，返回剩余实墙段。**与 DXF 端 `segs()` 算法一致**。

### `SUKit.opening(kind, sill:, head:, head_slide:)` → `[窗台高, 洞顶高]`
`:win` → `[900, 2100]`；`:door` → `[0, 2100]`；`:slide` → `[0, 2400]`

### `SUKit.glazing(parent, name, kind, a, b, t0, t1, h0, h1, axis:, ...)`
洞口「上下贯通框 + 左右竖挺 + 玻璃内嵌」四件套。
- `axis: :x` 用于沿 X 的墙，`:y` 用于沿 Y 的墙（**不可省**）
- `fw: 60` 框料宽 / `ft: 70` 框料厚（居墙中）
- 玻璃比框内边缘多伸 15，重叠不露缝
- `:door` 类型直接 return（门走 `entry_door`）

### `SUKit.entry_door(parent, name, a, b, t0, t1, h:, frame:, glass:, handle:)`
双扇玻璃门：门樘左右竖框 + 上框 + 双扇（外框 + 内嵌玻璃）+ 竖挺 + 可选拉手。
⚠️ 只在首层调用（放进 `build_base`）。

### `SUKit.wall_x(parent, prefix, mat, px0, px1, py0, py1, openings, wall_h:, sill:, head:, head_slide:, frame:, glass:)`
沿 X 的墙（长度在 X，厚度在 Y）。
自动：切洞 + 补窗台墙(0→sill) + 过梁(head→wall_h) + 洞口玻璃四件套。

### `SUKit.wall_y(parent, prefix, mat, py0, py1, px0, px1, openings, ...)`
沿 Y 的墙（长度在 Y，厚度在 X）。参数同上，顺序不同。

### `SUKit.louvers(parent, prefix, x0, x1, y0, y1, n, h0, h1, layer:, mat:)`
在 [x0,x1] 内按「条宽 = 缝宽」生成 n 条竖条。
条宽 = 缝宽 = `(x1-x0)/(2n-1)`。

### `SUKit.array_floors(std_group, n, floor_h, name_prefix:, layer:)`
把标准层 Group 的定义复制 n−1 次，每层抬高 `floor_h`。
返回层数。标准层本身会被命名为 `<name_prefix>_1F`。

### `SUKit.mirror_box(parent, total_w, mirrored)` → lambda
返回一个盒子构造器。`mirrored=false` 时 x 原样，`true` 时按 `total_w - x` 镜像
**并自动重排 min/max**。
用法：`b.call(name, mat, layer, x0, y0, x1, y1, h0, h1)`

### `SUKit.size_m` → `{width:, height:, depth:}`
整体外包尺寸（**米**）。交付前必查。

### 常量
`SUKit::FW = 60.0`（框料宽）、`SUKit::FT = 70.0`（框料厚）

---

## su_render.rb

### `SURender.configure(views:, out_dir:, cx: 0.0, cz: 0.0, style: {})`
`views` 格式见 `pipeline-contract.md`。
⚠️ 内部用实例变量承载可变配置 —— Ruby **不允许在方法内给常量赋值**（`dynamic constant assignment`）。

### `SURender.prep(time:)`
保存渲染选项快照到全局变量 `$su_render_snap`，并应用渲染设置。
**每个 SketchUp 会话只调一次**。

### `SURender.shot(i, w: 1800, h: 1120)`
出第 i 个视角。**每次调用只出一个**（这是规避长请求假死的核心）。
内部：设相机 → invalidate → sleep 0.5 → write_image → 返回字节数。

### `SURender.finish`
恢复渲染选项与相机。

### `SURender.make_scenes(prefix: 'SK_')`
生成 SketchUp 场景（页面），先清空旧场景。
`page.use_hidden = true` 让「平面」这一页单独记住隐藏状态。

### `SURender.save_as(path)`
保存并返回文件字节数。⚠️ `path` 必须是宿主语言拼好的字符串。

### 渲染细节
- `rset(k, v)` / `shset(k, v)`：**逐键 rescue** 的安全赋值（非法键跳过）
- `setup_render`：关雾、RenderMode=2（贴图）、背景 `247,246,244`、阴影开、Light 60 / Dark 45
- `floor_plan_only(on, name_prefix:, keep:, hide_extra:)`：隐藏上层与屋面（出俯视平面用）
- `write_image(path, w, h, antialias, compression)`：**位置参数**，不接受关键字哈希

---

## 常见调用序列

```ruby
require 'json'
load '<kit>/su_kit.rb'
load '<kit>/su_render.rb'

# —— 建模 ——
SUKit.setup(materials: MATS, layers: LAYERS, transparent: ALPHA)
SUKit.clear
f = Sketchup.active_model.entities.add_group
SUKit.wall_x(f, '外墙_北', '立面_主墙面_暖白', 0, 16600, 10560, 10800,
             [[600, 2700, :win], [7300, 9300, :door]],
             wall_h: 2780, frame: '立面_门窗框_中灰', glass: '玻璃_LowE中空')
SUKit.array_floors(f, 6, 2900, name_prefix: 'A2_标准层')
SUKit.size_m

# —— 出图（分三步，各自独立请求）——
SURender.configure(views: VIEWS, out_dir: 'D:/proj/渲染')
SURender.prep
SURender.shot(0)          # 逐个调用，不要写成循环打包在一个请求里
SURender.finish
SURender.make_scenes
SURender.save_as('D:/proj/model.skp')
```
