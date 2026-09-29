# -*- coding: utf-8 -*-
# ============================================================================
# su_kit.rb —— SketchUp 参数化建模工具箱（项目无关，可直接复用）
# ----------------------------------------------------------------------------
# 用法（在 SketchUp 里）：
#   load '路径/su_kit.rb'
#   SUKit.setup(units_mm: true, materials: MATS, layers: LAYERS)
#   SUKit.box(model.entities, '名称', '图层', '材质', x0, y0, x1, y1, h0, h1)
#
# 坐标系约定（★ 最容易错的一点）：
#   SketchUp 世界坐标 **Z 轴向上**。设图纸平面坐标为 (X, Y)、高度为 H：
#       SketchUp X = 图纸 X
#       SketchUp Y = 图纸 Y
#       SketchUp Z = 高度 H
#   所有平面坐标参数沿用图纸读数（mm），无需在调用处换算。
#
#   ⚠️ 历史坑（务必不要退回）：早期版本曾用「Y 轴向上」建楼
#      （SU_Y = 高度 H、SU_Z = 图纸 Y）。几何外形看着没错，但会同时坏两件事：
#        1) 模型相对 SketchUp 世界整体旋转 90°——俯视图、视高、量距全部错位；
#        2) SketchUp 的太阳/阴影系统以世界 Z 为天顶，Y 轴向上的模型于是「躺着」，
#           太阳的相对仰角被算成 asin(sun_dir.y)（实测 −29.7°，即太阳在地平线以下），
#           表现为渲染全平、无阴影、无黄昏暖调，且怎么调 ShadowTime 都救不回来。
#      结论：高度一律走 Z。
#
# 单位：本工具箱所有入参单位为**毫米**，内部自动 .mm。读取返回值时必须 .to_m，
#       因为 SketchUp 内部一律以英寸存储。
# ============================================================================

module SUKit
  # ------------------------------------------------------------------ 配置 ----
  # 单位 / 材质 / 图层的一次性初始化。materials 传入 { '材质名' => 0xRRGGBB }
  # layers 传入字符串数组。
  def self.setup(materials: {}, layers: [], unit: 2, transparent: {}, fog: false)
    o = Sketchup.active_model.options['UnitsOptions']
    o['LengthUnit']      = unit   # 2 = 毫米
    o['LengthFormat']    = 0
    o['LengthPrecision'] = 0
    o['LengthSnapEnabled'] = false

    materials.each do |name, hex|
      mt = Sketchup.active_model.materials[name]
      mt = Sketchup.active_model.materials.add(name) if mt.nil?   # add 重名会抛异常，必须先查
      mt.color = Sketchup::Color.new((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF)
    end
    transparent.each { |name, a| m = Sketchup.active_model.materials[name]; m.alpha = a if m }

    layers.each { |n| Sketchup.active_model.layers.add(n) if Sketchup.active_model.layers[n].nil? }
    true
  end

  # ------------------------------------------------------------------ 清场 ----
  # 从模板 .skp 复制而来的模型含模板自带图元，整体清空后重建。
  # ⚠️ 副作用：clear! 之后 definitions.purge_unused 会**静默删掉所有未被引用的材质**。
  #    因此「声明了 N 种材质」≠「模型里有 N 种」，交付前必须做材质审计
  #    （见 verify_consistency.py）。
  # ⚠️ 图层也要一并清：只清实体不清图层的话，改了 LAYERS 重跑会**残留上一版的空图层**
  #    （实测：从真源里删掉 "0" 之后模型里仍有 "0"，审计报「图层 15 ≠ 14」）。
  #    默认标记（Layer0 / 0）不可删，跳过。
  # ⚠️ SketchUp 2025 已移除 `Layers#current`（改成 `Model#active_layer`）。
  #    写成 `m.layers.current` 会 NoMethodError；若这句在 begin/rescue 之外，
  #    异常会被**最外层**的 rescue 吞掉，整段清图层静默失效 —— 实测踩过。
  def self.clear
    m = Sketchup.active_model
    m.entities.clear!
    m.definitions.purge_unused
    cur = (m.respond_to?(:active_layer) ? m.active_layer : nil)
    dflt = m.layers[0]
    m.layers.to_a.each do |l|
      next if l == dflt || %w[Layer0 Untagged].include?(l.name.to_s)
      next if cur && cur == l
      begin
        m.layers.remove(l, false)
      rescue StandardError
        nil
      end
    end
    true
  rescue StandardError
    true
  end

  # ------------------------------------------------------------ 清空图层 ----
  # 只用一次「模型已在别处建好、只想删掉空图层」的场合（不想重建模型时）。
  # 只删**没有任何实体在用**的图层，在用图层原样保留，所以不会丢几何。
  def self.prune_unused_layers
    m = Sketchup.active_model
    used = Hash.new(0)
    walk = nil
    walk = lambda do |es|
      es.each do |e|
        used[e.layer.name] += 1 if e.respond_to?(:layer) && e.layer
        walk.call(e.entities) if e.respond_to?(:entities)
      end
    end
    walk.call(m.entities)
    n = 0
    dflt = m.layers[0]
    m.layers.to_a.each do |l|
      next if l == dflt || %w[Layer0 Untagged].include?(l.name.to_s)
      next if used[l.name] > 0
      begin
        m.layers.remove(l, false)
        n += 1
      rescue StandardError
        nil
      end
    end
    n
  end

  # ------------------------------------------------------------ 唯一图元 box ---
  # 用「矩形底面 + pushpull」生成一个正交六面体。
  # px/py 为图纸平面坐标，h0/h1 为高度区间（均 mm）。
  #
  # 两个必须保留的防御：
  #   1) 非法区间直接返回 nil —— 镜像户型时 x1 会小于 x0，不拦住会生成一堆废面；
  #   2) f.normal.z < 0 时 reverse! —— 否则 pushpull 朝下长，构件跑到地面以下。
  def self.box(parent, name, layer, mat, px0, py0, px1, py1, h0, h1)
    return nil if px1 <= px0 || py1 <= py0 || h1 <= h0
    g = parent.entities.add_group
    pts = [
      Geom::Point3d.new(px0.mm, py0.mm, h0.mm),
      Geom::Point3d.new(px1.mm, py0.mm, h0.mm),
      Geom::Point3d.new(px1.mm, py1.mm, h0.mm),
      Geom::Point3d.new(px0.mm, py1.mm, h0.mm),
    ]
    f = g.entities.add_face(pts)
    unless f
      g.erase!
      return nil
    end
    f.reverse! if f.normal.z < 0
    f.pushpull((h1 - h0).mm)
    g.name = name
    g.layer = layer if layer
    g.material = mat if mat
    g
  end

  # -------------------------------------------------------------- 墙体切洞 ----
  # 在 [a0, a1] 上挖去若干洞口，返回剩余实墙段 [[s,e], ...]
  def self.split(a0, a1, holes)
    out = []
    cur = a0
    holes.sort_by { |h| h[0] }.each do |s, e|
      out << [cur, s] if s > cur
      cur = [cur, e].max
    end
    out << [cur, a1] if cur < a1
    out
  end

  # 洞口类型 → [窗台高, 洞顶高]
  def self.opening(kind, sill: 900.0, head: 2100.0, head_slide: 2400.0)
    case kind
    when :win   then [sill, head]
    when :door  then [0.0,  head]
    when :slide then [0.0,  head_slide]
    else             [sill, head]
    end
  end

  # ------------------------------------------------- 洞口玻璃 + 铝框（四件套）--
  # ⚠️ 只放一块玻璃板 = 渲染出来是「悬浮玻璃」，不是窗。
  #    真实做法：上下贯通框 + 左右竖挺 + 玻璃内嵌（玻璃比框内边缘多伸 15，重叠不露缝）。
  # ⚠️ axis 参数不可省：沿 X 的墙长度在 X、厚度在 Y；沿 Y 的墙反之。
  #    漏掉它会把厚度中心塞进另一根轴，实测表现为构件 Z 坐标飞到 16000+。
  FW = 60.0   # 框料宽（沿墙长度方向）
  FT = 70.0   # 框料厚（垂直墙面，居墙中）

  def self.glazing(parent, name, kind, a, b, t0, t1, h0, h1,
                   axis: :x, layer: '门窗', frame: '门窗框', glass: '玻璃',
                   fw: FW, ft: FT)
    return unless kind == :win || kind == :slide

    mid = (t0 + t1) / 2.0
    tl  = mid - ft / 2.0
    th  = mid + ft / 2.0

    if axis == :x
      box(parent, "#{name}_框下", layer, frame, a, tl, b, th, h0, h0 + fw)
      box(parent, "#{name}_框上", layer, frame, a, tl, b, th, h1 - fw, h1)
      box(parent, "#{name}_框左", layer, frame, a, tl, a + fw, th, h0 + fw, h1 - fw)
      box(parent, "#{name}_框右", layer, frame, b - fw, tl, b, th, h0 + fw, h1 - fw)
      box(parent, "#{name}_玻璃", layer, glass,
          a + fw - 15, mid - 15, b - fw + 15, mid + 15, h0 + fw - 15, h1 - fw + 15)
    else
      box(parent, "#{name}_框下", layer, frame, tl, a, th, b, h0, h0 + fw)
      box(parent, "#{name}_框上", layer, frame, tl, a, th, b, h1 - fw, h1)
      box(parent, "#{name}_框左", layer, frame, tl, a, th, a + fw, h0 + fw, h1 - fw)
      box(parent, "#{name}_框右", layer, frame, tl, b - fw, th, b, h0 + fw, h1 - fw)
      box(parent, "#{name}_玻璃", layer, glass,
          mid - 15, a + fw - 15, mid + 15, b - fw + 15, h0 + fw - 15, h1 - fw + 15)
    end
  end

  # ------------------------------------------------------------ 双扇玻璃门 ----
  # ⚠️ 门樘绝不能是一整块实心 box（那会把门洞堵死）：只能做「左右竖框 + 上框」，
  #    中间天然留空。双扇各占一半净宽。
  # ⚠️ 门属于首层专属构件。若建在「标准层」Group 里再阵列，会得到 N 个入口门
  #    （实测 6 层楼出现 6 个单元门）。必须放进独立的首层组。
  def self.entry_door(parent, name, a, b, t0, t1,
                      h: 2100.0, fw: FW, ft: FT,
                      layer: '门窗', frame: '门窗框', glass: '玻璃', handle: nil)
    mid = (t0 + t1) / 2.0
    tl = mid - ft / 2.0
    th = mid + ft / 2.0

    box(parent, "#{name}_樘左", layer, frame, a, tl, a + fw, th, 0, h)
    box(parent, "#{name}_樘右", layer, frame, b - fw, tl, b, th, 0, h)
    box(parent, "#{name}_樘上", layer, frame, a + fw, tl, b - fw, th, h - fw, h)

    mw = (b - a - 2 * fw) / 2.0
    [[a + fw, a + fw + mw], [b - fw - mw, b - fw]].each_with_index do |(x0, x1), i|
      box(parent, "#{name}_扇#{i + 1}框", layer, frame, x0, mid - 20, x1, mid + 20, fw, h - fw)
      box(parent, "#{name}_扇#{i + 1}玻璃", layer, glass,
          x0 + 60, mid - 15, x1 - 60, mid + 15, fw + 90, h - fw - 60)
    end
    box(parent, "#{name}_竖挺", layer, frame,
        a + fw + mw - 30, mid - 25, a + fw + mw + 30, mid + 25, 0, h - fw)
    if handle
      box(parent, "#{name}_拉手", layer, handle,
          a + fw + mw + 40, mid + 30, a + fw + mw + 60, mid + 55, 900, 1200)
    end
  end

  # ------------------------------------------------------------------ 墙体 ----
  # 沿 X 的墙（厚度沿 Y）。openings = [[a, b, :win|:door|:slide], ...]
  # 自动补：窗台墙(0→sill) + 过梁(head→墙高) + 洞口玻璃四件套。
  def self.wall_x(parent, prefix, mat, px0, px1, py0, py1, openings = [], wall_h: 2780.0, **kw)
    split(px0, px1, openings.map { |o| [o[0], o[1]] }).each_with_index do |(a, b), i|
      box(parent, "#{prefix}_实墙#{i + 1}", kw[:layer] || '墙体', mat, a, py0, b, py1, 0, wall_h)
    end
    openings.each_with_index do |(a, b, kind), i|
      s, h = opening(kind, sill: kw[:sill] || 900.0, head: kw[:head] || 2100.0,
                     head_slide: kw[:head_slide] || 2400.0)
      box(parent, "#{prefix}_窗台墙#{i + 1}", kw[:layer] || '墙体', mat, a, py0, b, py1, 0, s) if s > 0
      box(parent, "#{prefix}_过梁#{i + 1}", kw[:layer] || '墙体', mat, a, py0, b, py1, h, wall_h) if h < wall_h
      glazing(parent, "#{prefix}_洞#{i + 1}", kind, a, b, py0, py1, s, h,
              axis: :x, frame: kw[:frame] || '门窗框', glass: kw[:glass] || '玻璃')
    end
  end

  # 沿 Y 的墙（厚度沿 X）
  def self.wall_y(parent, prefix, mat, py0, py1, px0, px1, openings = [], wall_h: 2780.0, **kw)
    split(py0, py1, openings.map { |o| [o[0], o[1]] }).each_with_index do |(a, b), i|
      box(parent, "#{prefix}_实墙#{i + 1}", kw[:layer] || '墙体', mat, px0, a, px1, b, 0, wall_h)
    end
    openings.each_with_index do |(a, b, kind), i|
      s, h = opening(kind, sill: kw[:sill] || 900.0, head: kw[:head] || 2100.0,
                     head_slide: kw[:head_slide] || 2400.0)
      box(parent, "#{prefix}_窗台墙#{i + 1}", kw[:layer] || '墙体', mat, px0, a, px1, b, 0, s) if s > 0
      box(parent, "#{prefix}_过梁#{i + 1}", kw[:layer] || '墙体', mat, px0, a, px1, b, h, wall_h) if h < wall_h
      glazing(parent, "#{prefix}_洞#{i + 1}", kind, a, b, px0, px1, s, h,
              axis: :y, frame: kw[:frame] || '门窗框', glass: kw[:glass] || '玻璃')
    end
  end

  # ---------------------------------------------------------------- 木格栅 ----
  # 按「条宽 = 缝宽」在 [x0,x1] 内分 n 条竖条。
  # ⚠️ 整块大板看起来不像格栅（实测 1300×2200 一块木板很假），必须细分。
  def self.louvers(parent, prefix, x0, x1, y0, y1, n, h0, h1,
                   layer: '立面', mat: nil)
    w = (x1 - x0) / (2 * n - 1).to_f
    (0...n).each do |k|
      a = x0 + k * 2 * w
      box(parent, "#{prefix}_条#{k + 1}", layer, mat, a, y0, a + w, y1, h0, h1)
    end
  end

  # ------------------------------------------------------------ 楼层阵列 ----
  # 标准层建成 Group 后复制定义，比逐层重建快得多，也便于改一层同步 N 层。
  def self.array_floors(std_group, n, floor_h, name_prefix: '标准层', layer: '楼板')
    defn = std_group.entities.parent
    (1...n).each do |i|
      inst = Sketchup.active_model.entities.add_instance(
        defn, Geom::Transformation.new([0, 0, (floor_h * i).mm]))
      inst.name = "#{name_prefix}_#{i + 1}F"
      inst.layer = layer
    end
    std_group.name = "#{name_prefix}_1F"
    n
  end

  # -------------------------------------------------------------- 镜像户型 ----
  # 返回一个「盒子构造器」，左户传 x 原样，右户按 mx(x) = 总开间 - x 镜像。
  # ⚠️ 镜像后 x1 < x0，必须重排 min/max，否则 box 会因 px1<=px0 全部返回 nil
  #    （实测事故：左户家具集体消失）。
  def self.mirror_box(parent, total_w, mirrored)
    lambda do |name, mat, layer, x0, y0, x1, y1, h0, h1|
      xa = mirrored ? (total_w - x0) : x0
      xb = mirrored ? (total_w - x1) : x1
      xa, xb = xb, xa if xa > xb
      box(parent, name, layer, mat, xa, y0, xb, y1, h0, h1)
    end
  end

  # ------------------------------------------------------------ 整体尺寸 ----
  # 交付前必做：用 .to_m 核对长宽高。⚠️ 返回的是英寸，不 .to_m 会得到错误结论。
  def self.size_m
    bb = Sketchup.active_model.bounds
    { width: bb.width.to_m, height: bb.height.to_m, depth: bb.depth.to_m }
  end
end
