# 踩坑全记录

每条都是实际踩过、有明确现象与根因的。按「会浪费多少时间」排序。

---

## 一、su_mcp 通道类

### 1. ★ 长请求假死（最坑的一个）

**现象**：把「建相机 + 8 次 `write_image` + sleep」打包进一个 `execute_ruby`，
客户端连续超时（3 分钟以上）拿不到任何响应。但同一批操作**拆成逐视角独立请求时，
每个只要 1.1—1.4 秒**，8 张图总共约 10 秒。

**更糟的连锁反应**：被 SIGTERM 强杀的请求**会继续占用 SketchUp 主线程**，
导致后续所有请求排队。表现为「连 400×250 的渲染都卡死」，让人误以为是渲染慢或模型太大，
于是继续重试 → 请求越堆越多 → 假死持续更久。

**解法**：
1. 每个视角发**独立请求**（`su_pipeline.py shots` 已如此实现）
2. 跨请求需要保持的状态放**全局变量**（如 `$su_render_snap`），不要放局部变量
3. 怀疑卡住时**先探活**：
   ```bash
   python su_pipeline.py probe    # 0.1s 返回「主线程空闲」= 可以继续
   ```
   探活不通就不要重试，等上一批跑完；必要时重启 SketchUp

**诊断利器：让 su_mcp 自己记下请求时间线。**
su_mcp 插件把**每一次连接、每一次 `execute_ruby`（含代码字符数）、每一次报错**都打到
SketchUp 进程的 **stdout**。所以只要启动时重定向输出，就得到一份完整的请求流水：

```bash
cd "/c/Program Files/SketchUp/SketchUp 2025/SketchUp" \
  && ./SketchUp.exe "C:\path\to\template.skp" > "/tmp/su_mcp.log" 2>&1 &
```

日志长这样（时间戳 + 代码长度就是「哪个请求慢」的直接证据）：

```
13:16:12 INFO [SU-MCP][TcpServer] TCP Server started on 127.0.0.1:9876
13:57:36 INFO [SU-MCP][ExecuteRuby] Executing Ruby code: 26 chars      ← 逐视角出图
13:57:36 INFO [SU-MCP][Session] Session session_2060 disconnected: EOFError   ← 正常收尾
```

**怎么读**：
- 相邻两条 `ExecuteRuby` 的时间差 = 该请求实际耗时。差几百毫秒是正常的；
  某条之后**长时间没有下一条**，那条就是卡住的那个
- `Session ... disconnected: EOFError` = **正常断开**（客户端跑完自己关了连接），
  一次成功的查询就是这样收尾的，**不是故障**
- `Session ... disconnected: Errno::ECONNRESET` = **异常断开**，客户端被强杀
  （就是上面说的连锁反应）。只有这一种才要警惕
- 出现 `already initialized constant Xxx::YYY` 是**无害噪声** —— 反复 `load` 同一个
  `.rb` 时 Ruby 必然警告常量重定义；用 `load` 而非 `require` 才有此现象，
  功能不受影响，不必处理
- `(eval):1: warning: Deprecated overload, use with_status: true overload instead`
  也是无害的 API 弃用提示
- 海量 `unknown: Not a TIFF or MDI file, bad magic number` 与 `WGLUtils::...`
  是 SketchUp 自身的日志噪声，与 su_mcp 无关，忽略

> 另注：日志是**块缓冲**的，重定向到文件后不会逐行实时出现，
> 会攒一批才刷一次。别因为「日志没动静」就以为插件没起来 —— 用
> `netstat` 看 9876 是否 LISTENING 更可靠。

### 2. `View#write_image` 不接受关键字哈希

```ruby
# ✗ TypeError: no implicit conversion of Hash into Integer（0.1s 内即失败）
view.write_image(path, width: 1800, height: 1120, antialias: true, compression: 0.92)
# ✓ 位置参数
view.write_image(path, 1800, 1120, true, 0.92)
```
写成 `begin 关键字 rescue 位置参数` 也能跑，但每次都白抛一次异常。

### 3. `Model#modified` 不存在

`get_model_info` 返回的 `modified` 是**插件自己算的字段**，Ruby 里 `model.modified`
会 `NoMethodError`。判断是否已保存请比对 `model.path` 与文件时间。

### 4. `save` 的路径不要用 Ruby 字符串插值

```python
# ✗ 插值没发生时，SketchUp 把字面量 #{SKP} 当文件名
code = f"Sketchup.active_model.save('#{{SKP}}')"
# → 静默存到 C:/Users/xxx/Documents/#{SKP}.skp，"保存成功"但目标文件没变
```
**用宿主语言拼好完整路径再传给 Ruby**：
```python
code = "Sketchup.active_model.save('" + path + "')"
```

### 5. `Sketchup.open_file` 会丢响应

切换文件时插件重载，请求响应会丢失（属于正常，不是故障）。
后续请求正常返回即说明文件已打开。

---

## 二、几何建模类

### 6. 材质被静默清除（交付前必查）

`entities.clear!` 之后 `definitions.purge_unused` 会**静默删掉**
没有任何构件引用的材质。

**实测事故**：脚本里声明 20 种材质，模型里只剩 16 种 —— 差的 4 种
（`立面_门窗框_中灰`、`立面_次墙面_暖灰`、`玻璃_磨砂`、`室内_金属_哑光黄铜`）
根本没落到几何上，**不报任何错**。

**解法**：`su_pipeline.py materials` 审计两条
① 有无「该有材质却为 `nil`」的子组（顶层容器组无材质是正常的）
② 每种材质真正覆盖多少个构件

### 7. 漏窗框 → 悬浮玻璃

只放一块玻璃板，渲染出来是一块悬在洞里的玻璃。
真实做法是**四件套**：上下贯通框 + 左右竖挺 + 玻璃内嵌（玻璃比框内边缘多伸 15 不露缝）。
整块窗框面积往往占门窗构件的一半以上（本项目 342 个框料构件）。

### 8. 墙玻璃坐标轴转置

沿 X 的墙长度在 X、厚度在 Y；沿 Y 的墙反之。
漏掉方向切换会把厚度中心塞进另一根轴，**实测表现为某个构件坐标飞到 16000+**
（本该 11700），外包进深从 13.2 m 变成 18.0 m。

`glazing` 的 `axis:` 参数就是为此存在，**不可省**。

### 9. 镜像后 min/max 未重排

```ruby
m = ->(x) { mirrored ? (总开间 - x) : x }
# ✗ 直接算：m(x1) 会小于 m(x0)
box(f, name, layer, mat, m.call(x0), y0, m.call(x1), y1, h0, h1)
# ✓ 先算再排
xa, xb = m.call(x0), m.call(x1); xa, xb = xb, xa if xa > xb
```
`box` 里 `px1 <= px0` 会返回 `nil`（不报错）→ **整户家具集体消失**。

### 10. 首层专属构件被阵列

把单元入口门建在「标准层」Group 里，`array_floors` 之后**每层都会出现一个门**（6 个入口门）。
首层专属构件（入口门、门头雨棚、基座）必须放进独立的 `build_base` 组。

### 11. 门樘堵死门洞

门樘写成一整块实心 `box(..., 0, h)` 会把门洞完全封住。
只能做「左右竖框 + 上框」三件，中间天然留空。

### 12. 整块大板不像格栅

木格栅做成 1300×2200 的整块木板，渲染出来是「一块板」而不是格栅。
必须按「条宽 = 缝宽」细分（本项目南向 6 条 / 北向 8 条，条宽约 118—127）。

### 13. 湿区地面共面闪烁

湿区砖直接铺在木地板上会 z-fighting。**抬高 1mm 覆盖**即可。

### 14. 天花会遮挡俯视平面

给每层加实体天花板后，俯视的标准层平面视角被完全遮挡，看不到家具。
**取舍**：天花不单独立模，以结构顶板涂装呈现，并在交付说明里写明。

---

## 三、出图与渲染类

### 15. 平行投影 `Camera#height` 以英寸计

```ruby
cam.perspective = false
cam.height = 22.0     # ✗ = 0.56 m，画面放大到贴着墙面
cam.height = 22.m     # ✓
```

### 16. 平面图南朝上

`up = [0, 0, -1]` 时 +Z（北）落在画面下方 → 南朝上。
改 `up = [0, 0, 1]` 即北朝上，且不产生镜像。

### 17. 渲染选项逐键 rescue

键在不同 SketchUp 版本不一致（`DisplayProfileEdges`、`LineWidth` 会抛 `ArgumentError`）。
**若在 `ensure` 里恢复时抛错，会掩盖真正的错误** —— 实测只看到 restore 的报错，
看不到原始故障，白查半小时。

```ruby
def self.rset(k, v); M.rendering_options[k] = v; true; rescue StandardError; false; end
```

### 18. 窗口在后台时渲染可能显著变慢

必要时先置前：`python activate_window.py`（枚举窗口 → `ShowWindow(SW_RESTORE)` →
`SetForegroundWindow`）。

### 19. SketchUp 卡在「欢迎使用 SketchUp」

无参数启动时**插件根本没加载**（Plugins 目录无日志、9876 端口无监听）。
必须用模板 `.skp` 作启动参数：
```
resources/zh-cn/Templates/Temp03a - Arch.skp
```

### 20. su_mcp 插件不会自动启动

`main.rb` 只注册菜单项，且 `start_server` 成功后会弹**模态 `UI.messagebox`**，
把主线程的定时器轮询卡死。
解法：放一个自启插件，先替换 `UI.messagebox` 再延迟调 `start_server`。

### 21. 沙箱会拦 GUI 进程

普通 Bash 起 SketchUp 会静默失败，需 `dangerouslyDisableSandbox`；
且 `&` 后台方式起的进程在命令结束后被回收，要用工具的 `run_in_background`。

---

## 四、DXF / 二维类

### 22. 中文字体四条硬规矩

1. 大字体写 `style.dxf.bigfont`（组码 4），**不要用 `set_extended_font_data`**
2. `ezdxf.new(setup=True)` 的 TTF 样式**只改不删**
3. 中间路径全 ASCII
4. 线宽用出图绝对值，不随比例缩放

### 23. matplotlib 预览中文变方块

DXF 本身用 `gbcbig.shx` 在 AutoCAD 里正常，只有 matplotlib 预览需要显式指定字体：
```python
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
matplotlib.rcParams["axes.unicode_minus"] = False
```

### 24. DIMENSION 在预览里不显示

`add_linear_dim(...).render()` 生成的是**匿名块引用**，遍历 `msp` 直接画看不到尺寸线与数字。
要用 `virtual_entities()` 展开：
```python
for e in msp:
    if e.dxftype() == "DIMENSION":
        for v in e.virtual_entities():
            yield v
        continue
    yield e
```

### 25. 居中文字读不到 `insert`

`set_placement((x,y), align=CENTER/…)` 之后真实位置存在 `align_point`（组码 11），
`insert`（组码 10）常留在原点。只读 `insert` 会把所有居中文字画到 (0,0) 堆成一团。

### 26. 用全图幅核对建筑尺寸会误报

DXF 全图幅含轴号圆圈（±900）与尺寸链（±1500），必然大于建筑本体。
**核对建筑本体要用「墙体」图层的范围**。

### 27. `y_north` 是外皮还是轴线

这是最容易搞混的一处。本项目 `y_north = 10800` 是**北外墙外皮**（4200+3900+2700），
墙体 Y 跨度 = 0 → 10800。若误当成轴线再 `+ 外墙厚` 去核，会差 240mm 并误报失败。

---

## 五、Python 脚本类

### 28. `return X` 换行接 `+ Y` 会被解析成两条语句

```python
def gen():
    return f'''...'''
    + "".join(...)        # ✗ 这是独立的一元加表达式，结果被丢弃！
```
Python 把 `+ Y` 当成新的表达式语句（一元正号），**前半段的返回值照常返回，
后半段静默消失**。生成的 Ruby 会缺整个方法体，报 `syntax error, unexpected end-of-input`
却看不出缺在哪。

**解法**：整体用括号包住
```python
def gen():
    return (
        f'''...'''
        + "".join(...)
    )
```
生成器里请保留 `make_skp_rb.py` 的**结构自检**（数 `def` 与 `end` 是否相等）—— 它能立刻抓到这类静默截断。

### 29. Ruby 不允许在方法内给常量赋值

```ruby
def self.configure(style:)
  STYLE = style     # ✗ dynamic constant assignment
  @style = style    # ✓
end
```
大写开头的标识符在方法内赋值会报 `dynamic constant assignment`。
可变配置一律用实例变量。常量数组可以 `replace` 改内容，但不能重新赋值。

### 30. `plan_data.py` 里的 f-string 与 Ruby 花括号冲突

生成 Ruby 代码时，f-string 里的 `{` 必须写成 `{{`。
更稳的做法：**Ruby 片段用普通字符串 + 占位符替换**（`tmpl.replace("@NAME@", ...)`），
完全避开花括号转义问题。

### 31. 依赖装错解释器 → `make_dxf.py` 直接退出

`make_dxf.py` 找不到 `ezdxf` 会打印 `[X] 缺少 ezdxf：pip install ezdxf` 并退出，
**不会静默降级**。用托管运行时的时候特别容易踩：默认 python 是干净的，
依赖其实装在另一个 venv 里。

排查：逐个解释器试 `python -c "import ezdxf; print(ezdxf.__version__)"`，
找到装了依赖的那个，后续命令全部用它的**全路径**调用。

另注：venv 的 `python.exe` 在 `envs/<name>/Scripts/` 下，本机**用绝对路径调用是正常的**；
若真遇到 `No such file or directory`（退出码 127），先怀疑路径本身写错
（Git Bash 的路径转换、盘符大小写、目录里有空格），而不是「venv 不能绝对路径调用」。

### 32. 命令里的 `$` 变量要转义

命令串含 `$INSUNITS` 这类会被 bash 展开成空串。用 `\$` 转义或整体用单引号。

### 33. 公开仓库前要清掉机器专属路径

技能目录里出现的 `C:/Users/<你的用户名>/...`、内网绝对路径等，公开前要改成
`<skill-dir>` 之类的占位符。注意**文档里的示例路径**（`D:/proj/plan.dxf`）是无害的，
真正要清的是指向**真实存在的本机目录**的那种。
`.gitignore` 里也要把 `*.dxf / *.dwg / *.skp / __pycache__` 排除掉，
避免把几百 MB 的运行产物推上去。

### 34. ★ 材质赋在「组」上，不在「面」上 —— 审计时读 `Face#material` 必得 nil

`SUKit.box` 的做法是「一个构件 = 一个组，材质赋给组」：

```ruby
g = parent.entities.add_group
# ... 画这个构件的面 ...
g.material = mat if mat        # ← 材质在这里，不在面上
```

后果：**遍历面去读 `Face#material` 会全部得到 `nil`**，于是审计脚本报
「声明 20 种材质 / 实际引用 0 种 / 20 种未被引用」——一个彻头彻尾的**假警报**，
会让人以为模型要渲染成一片白，进而去改根本没坏的东西。（实测踩过。）

正确姿势：**走组级**，与建模时赋值的层级对齐。

```ruby
cov = Hash.new(0)
walk = lambda { |es|
  es.each { |e|
    next unless e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
    cov[e.material.name] += 1 if e.material        # ★ 组级
    walk.call(e.entities) if e.respond_to?(:entities)
  }
}
walk.call(Sketchup.active_model.entities)
```

`su_pipeline.py materials` 里的 `AUDIT_RB` 就是这么写的，直接用它。
**判断依据**：如果审计结果里出现「声明 N 种 / 引用 0 种」，先怀疑自己读错了层级，
而不是模型坏了。

### 35. `Sketchup::Material#count` 不存在

想统计材质用量时容易顺手写 `m.materials.select { |x| x.count > 0 }`，
报 `NoMethodError: undefined method 'count' for Sketchup::Material`。
没有这个 API，只能自己遍历实体数引用次数（见第 34 条的 `cov` 写法）。

### 36. `su_client.ruby()` 静默吞掉 Ruby 异常

```python
def ruby(self, code):
    r = self.tool("execute_ruby", code=code)
    if isinstance(r, dict) and r.get("success"):
        return r.get("result")
    return None            # ← 出错时也是 None，错误信息丢了
```

Ruby 抛异常时返回 `None`，**看不到任何错误原因**，只能看到一句 `None`，
非常容易误判成「连不上」或「主线程卡住」而白白重启 SketchUp。

排查期的正确写法：直接看完整的 `tool()` 返回。

```python
r = c.tool('execute_ruby', code=code)
print(r.get('result') if r.get('success') else json.dumps(r, ensure_ascii=False))
# → {"success":false,"error":"NoMethodError: undefined method `count' for ..."}
```

**注意区分**：`None` 有两种含义 —— 「Ruby 执行报错」与「请求超时」。想分清就先跑
`c.ruby('1+1')`，能返回 `'2'` 说明通道正常，那 `None` 就是 Ruby 报错了。

### 37. 用命令行 `-c` 传多行 Ruby 会被 shell 与 Python 双重转义吃掉

把 Ruby 代码塞进 `python -c "..."` 然后 `'''...'''`，四层转义（bash → Python →
Ruby 字符串）很容易把 `\n` 变成真换行、引号错位，最后 Ruby 收到的是语法坏掉的代码。
**把 Ruby 写成 `.rb` 文件再读进来执行**，一次解决：

```python
code = open('query_model.rb', encoding='utf-8').read()
r = c.tool('execute_ruby', code=code)
```

顺带一提：`.skb` 是 SketchUp 的**自动备份**（保存时把上一版挪过去）。
若 `.skp` 出问题，`.skb` 就是上一版的救命稻草 —— 交付时**不要删它**。

---

## 渲染氛围（黄昏 / 夜景）专项 —— 2026-09-29 实战补录

这一组是「素模出图不像效果图」的全部根因。按重要性排序，第 38 条是一切的源头。

### 38. ★★ 坐标系必须 Z 轴向上，否则太阳在地平线以下（渲染全平的真凶）

早期版本的 `su_kit.rb` 用「Y 轴向上」建模：

    SU_X = 图纸X    SU_Y = 高度H    SU_Z = 图纸Y

几何外形看着没错，但会同时坏两件事：

1. **模型相对 SketchUp 世界整体旋转 90°** —— 俯视图、视高、量距全部错位，
   用户在软件里用原生工具交互时会觉得「这楼是躺着的」；
2. **SketchUp 的太阳/阴影系统以世界 Z 为天顶**，于是太阳的相对仰角被算成
   `asin(SunDirection.y)`。实测武汉黄昏 `SunDirection = (0.69, −0.49, 0.53)`，
   相对 Z 天顶是 +31.8°（正常黄昏），相对 Y 天顶却是 **−29.7°（太阳在地平线以下）**。
   表现是：**画面全平、没有影子、没有暖调，而且怎么调 ShadowTime 都救不回来**
   （因为不是时间问题，是轴的问题）。

**正确做法**：高度一律走 Z。

    SU_X = 图纸X    SU_Y = 图纸Y    SU_Z = 高度H

改完之后必须同步改的地方（漏一处就偏 90°）：

| 位置 | 改动 |
|---|---|
| `su_kit.box` | 顶点 `(px, py, h)`；法向判据 `f.normal.z < 0` |
| `su_kit.array_floors` | 平移向量 `[0, 0, floor_h*i]` |
| `su_landscape.sphere_mesh / frustum_mesh` | 点的三个分量顺序换过来 |
| `su_landscape.person` | 校正量取 `bb.max.z`；旋转轴 `(0,0,1)`，绕点 `(cx, cy, 0)` |
| `plan_data.GEO['north_angle']` | 正北从 SU+Z 挪到 SU+Y，取值由 **90 → 0** |
| `views.json` 的 eye/target/up | up 由 `[0,1,0]` 改 `[0,0,1]`，坐标第三位换成高度 |

验证手段：`python su_env.py` 会打出「世界 Z 为天顶的太阳高度角」。
**黄昏图应在 8°—20°；若打印出负值，就是本条没做到。**

### 39. ★ ShadowTime 的 UTC 墙钟陷阱（时区）

即使坐标系对了，直接填当地时刻也是错的。实测：

    si['ShadowTime'] = Time.new(2026, 9, 29, 17, 5)      # 本机 UTC+8
    → 太阳高度角 34.4°、方位角 117.5°（东南）           # 这是「上午 9 点」的太阳！

**SketchUp 取 Time 的 UTC 墙钟当作场地当地时刻**，等于整体偏移一个本机 UTC 偏移量。
修正只需一行：

```ruby
t = Time.new(2026, 9, 29, 17, 5, 0)
si['ShadowTime'] = t + t.utc_offset     # → 高度角 13.3°、方位角 259.0°（正西略偏南）
```

`su_render.rb` 提供了 `SURender.local_time(y, mo, d, hh, mm)` 封装；
`su_pipeline.py prep` 读 `views.json` 的 `"sun"` + `"sun_local": true` 自动套用。
关掉修正写 `"sun_local": false`。

`su_sun_scan.py` 可以扫描一天 24 小时，把 SketchUp 给的太阳位置与你自己的天文计算并列，
一眼看出偏移量。

### 40. `shadow_info['City']` 会覆盖刚写进去的经纬度

```ruby
si['Latitude']  = 30.58      # 武汉
si['Longitude'] = 114.30
si['City']      = '武汉'      # ← 这一步触发城市库查询，把上面两行覆盖成模板默认
# 结果读回来是 39.93 / 116.39（北京）
```

**赋值顺序必须是：先 `City`，再经纬度，最后 `NorthAngle`。** 或者干脆不写 `City`。

### 41. SketchUp 的「无限地面」会渲成一块沙色背景板

`rendering_options['DisplayGround']` 默认开着，它是一块按世界 z=0 无限延伸的板，
颜色与设计无关（浅沙色）。一出图就是「模型摆在一张桌子上」的既视感，
而且会把设计场地里低于 z=0 的部分整个托起来盖住。

**做法**：① `rset('DisplayGround', false)`；
② 自己在真源里铺一块够大的环境地面（本项目 `CONTEXT` = 1600 × 1600 m，
单列在「辅助」图层，出正式平面图时整层隐藏）。

### 42. 地平线硬边 → 用雾把它化掉

自己铺的环境地面再大也有尽头，远端边缘会在画面上切出一条硬线，露出背景色。
修法是雾，而且**雾色要与天空近地色一致**：

```ruby
rset('DisplayFog', true)
rset('FogColor', SketchUp::Color.new(0xE3, 0xBB, 0xA0))   # 黄昏暖灰
rset('FogStartDist', 200.m)     # ★ 必须大于「相机到主体」的距离，否则主体被雾洗白
rset('FogEndDist',   780.m)     # 小于环境地面的半宽，让边缘 100% 溶进天空
```

### 43. 阴影面板的「亮 / 暗」滑杆是 SketchUp 唯一能拉对比的旋钮

`shadow_info['Light']` / `['Dark']`（各 0—100）。默认偏平，黄昏图可用 70 / 25 起步。
**注意**：SketchUp 内置渲染器对太阳**色温**的响应很弱 —— 把时间调到日落前 10 分钟，
明暗关系和影长是对的，但颜色依旧中性偏冷。想真正出暖调得靠材质基色偏暖
（把「冷白混凝土」`0xEDEBE6` 改成「暖白」`0xE9E1D3`），而不是继续调时间。
实测三档时间（17:05 / 17:20 / 17:32）出图几乎看不出差别。

### 44. 尺寸标注的测量点必须落在被测的那条边上

```python
add_linear_dim(base=(0, y1b), p1=(x0, 0), p2=(x1, 0))   # ✗ y 写成 0
```

`p1/p2` 是**被测点**，界线从它们引到尺寸线。若图省事把 y 写成 0（图纸原点），
AutoCAD 会从原点引出几十米长的界线，满图长竖线，看着像画错了一堆矩形
（本次实测：以为是图层配色错了，查了半天）。

正确写法：`p1=(x0, S['y0'])`、`p2=(x1, S['y0'])`，基点也**就近**放
（建筑自己的尺寸线贴着建筑放，不要全堆到用地边界外面）。

### 45. DXF 里中文图层名变 `\U+56fe` —— 版本选 R2010

`ezdxf.new("R2000")` 及更早的文件按 code page 写盘，非 ASCII 会被转义成
`\U+56fe` 这种形式。**改用 `ezdxf.new("R2010")`**（AC1024，规范强制 UTF-8），
中文图层名与文字才会原样写入。AutoCAD 2021 读 R2010 毫无压力。

顺带：导出预览 PNG 时要显式指定白底，否则默认深色底，
浅色图层（灰、浅绿）几乎看不见，看起来像「少画了东西」：

```python
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy
cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(...)
```

### 46. 球体中心高度写成 `k·r` 之后要两头验算

`k < 1` 球会沉到地面以下，`k > 1` 球会顶穿上层楼板。
本次踩法：展品球心取 `0.42r`，半径 3200 的球沉到楼板下 **1856 mm**，
在审计里表现为「建筑总高 25856 ≠ 24000」（一个和建筑毫无关系的数字）。

**写完就验算**：球底 `(k−1)r` 是否可接受（略沉被楼板挡住最好看）、
球顶 `(k+1)r` 是否低于层高。本项目取 `k = 0.85`：
球底沉 0.15r（看不见），球顶 1.85r（3200 的球 → 5920 < 层高 6000）。

### 47. 审计脚本里判/数构件，别按「想当然的名字」写前缀

本次审计报了三条假故障，全是判据写错，不是模型错：

| 报错 | 真实原因 |
|---|---|
| 针叶 0 / 5 | 针叶树也被生成器命名成「乔木N」→ 已改为独立命名「针叶N」 |
| 栏杆 0 / 6 | 组名是「露台栏杆_南」「水池栏杆_北」，用 `start_with?('栏杆')` 数不到，要 `include?` |
| 人物 0 / 21 | 组名是「人1」…「人21」（无「物」字） |

**原则**：审计的判据要照着**生成器实际写出的名字**写，先把真实名字打出来再写匹配规则。

### 48. 审计脚本 import 同名模块：技能目录要用 append，不能 insert(0)

项目里和技能目录里都有 `plan_data.py`。审计脚本若把技能目录 `insert(0)` 到
`sys.path` 最前面，`import plan_data` 拿到的是技能自带的那份（里面没有
`building_extent`），直接 `AttributeError`。

```python
sys.path.insert(0, HERE)      # 项目自己的真源优先
sys.path.append(SKILL)        # 技能脚本（su_client 等）放后面
```

### 49. SketchUp 2025 已移除 `Layers#current`（清图层会整段静默失效）

```ruby
m.layers.to_a.each do |l|
  next if m.layers.current == l      # ← NoMethodError
  m.layers.remove(l, false)
end
```

SU 2025 移除了 `Layers#current`（改用 `Model#active_layer`），`Layers#remove` 还在。
最坑的是：这句若写在 `begin/rescue` **之外**，异常会被外层的 `rescue StandardError` 吞掉，
**整段清图层逻辑静默失效** —— 表面看一切正常，实际一个图层都没删
（实测：真源里删掉 "0" 之后模型里仍有 "0"，审计报「图层 15 ≠ 14」，查了半天才发现）。

安全写法：

```ruby
def self.clear
  m = Sketchup.active_model
  m.entities.clear!
  m.definitions.purge_unused
  cur = (m.respond_to?(:active_layer) ? m.active_layer : nil)
  dflt = m.layers[0]                       # 默认标记恒在 index 0
  m.layers.to_a.each do |l|
    next if l == dflt || %w[Layer0 Untagged].include?(l.name.to_s)
    begin
      m.layers.remove(l, false)            # ★ 只这句在 rescue 里
    rescue StandardError
      nil
    end
  end
  true
rescue StandardError
  true
end
```

另给 `su_kit.rb` 加了 `prune_unused_layers`：遍历实体统计各图层引用数，只删**零引用**图层，
不重建模型也能清掉历史残留（在用图层原样保留，不会丢几何）。

### 50. 调色脚本「备份 + 原地覆盖」会静默丢图

「读成品图 → 备份到 _raw → 原地覆盖成品图」看着聪明，第二次出图时成品图已被覆盖成新图，
脚本却因为 `_raw` 里已有同名备份而**继续用旧备份当源** ——
**新一轮渲染结果被静默丢弃**。实测：连续三次重建重渲，成品图始终是第一版的内容。

改成两目录、永远单向：

    <出图目录>/_raw/   ← SketchUp 直出（views.json 的 "out" 指向这里）
    <出图目录>/         ← 调色成品（grade_render.py 从 _raw 读、写回这里）

这样重跑多少次都幂等。
