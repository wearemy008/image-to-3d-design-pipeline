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
13:57:36 INFO [SU-MCP][Session] Session session_2060 disconnected: EOFError
```

**怎么读**：
- 相邻两条 `ExecuteRuby` 的时间差 = 该请求实际耗时。差几百毫秒是正常的；
  某条之后**长时间没有下一条**，那条就是卡住的那个
- `Session ... disconnected: Errno::ECONNRESET` = 客户端被强杀（就是上面说的连锁反应）
- 出现 `already initialized constant Xxx::YYY` 是**无害噪声** —— 反复 `load` 同一个
  `.rb` 时 Ruby 必然警告常量重定义；用 `load` 而非 `require` 才有此现象，
  功能不受影响，不必处理
- `(eval):1: warning: Deprecated overload, use with_status: true overload instead`
  也是无害的 API 弃用提示
- 海量 `unknown: Not a TIFF or MDI file, bad magic number` 与 `WGLUtils::...`
  是 SketchUp 自身的日志噪声，与 su_mcp 无关，忽略

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
