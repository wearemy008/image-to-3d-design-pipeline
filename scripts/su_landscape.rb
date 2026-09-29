# -*- coding: utf-8 -*-
# ============================================================================
# su_landscape.rb —— SketchUp 景观工具箱（项目无关，可直接复用）
# ----------------------------------------------------------------------------
# 与 su_kit.rb 配套：su_kit 管建筑构件（墙/洞/门窗/格栅），本文件管景观与配景
# （乔木 / 灌木 / 绿篱 / 水体 / 台阶 / 铺装 / 栏杆 / 人物尺度参照）。
#
# 用法：
#   load '路径/su_kit.rb'
#   load '路径/su_landscape.rb'
#   SULand.tree(ents, '乔木01', 3000, -9000, 9000, 3200, '景观_乔木_干', '景观_乔木_冠')
#
# 坐标约定与 su_kit.rb 完全一致（★ 高度走 Z 轴，见 su_kit.rb 头部「历史坑」）：
#   SketchUp X = 图纸 X      SketchUp Y = 图纸 Y      SketchUp Z = 高度 H
#   所有入参单位 = 毫米；读返回值必须 .to_m（SketchUp 内部存英寸）。
#
# ★ 本文件刻意不操心「绕序」：
#   球体与锥台的三角形绕序极易写反，反过来法向朝内会让构件渲染成死黑。
#   这里一律用 faces_outward() 按「面法向 vs 形体形心」逐面修正，
#   因此下面的 mesh 生成代码可以只求点序正确、不管正反。
# ============================================================================

module SULand
  # ------------------------------------------------------------------ 依赖 ----
  # 本文件复用 su_kit.rb 的 box()（唯一图元）。SUKit 与 SULand 是两个独立模块，
  # 模块内不能裸调对方的方法，因此这里做一层委托。
  # ★ 载入顺序必须先 su_kit.rb 再 su_landscape.rb。
  def self.box(parent, name, layer, mat, px0, py0, px1, py1, h0, h1)
    unless defined?(SUKit) && SUKit.respond_to?(:box)
      raise "su_landscape.rb 依赖 su_kit.rb，请先 load 'su_kit.rb' 再 load 'su_landscape.rb'"
    end
    SUKit.box(parent, name, layer, mat, px0, py0, px1, py1, h0, h1)
  end

  # ------------------------------------------------------------ 确定性随机 ----
  # 树冠/灌木需要「看起来自然」的扰动，但每次重建必须一致（否则每次渲染都不同）。
  # 用自带的线性同余，不依赖 Random 的默认种子。
  def self.rng(seed)
    s = (seed.to_i & 0x7FFFFFFF) | 1
    lambda do
      s = (s * 1103515245 + 12345) & 0x7FFFFFFF
      s / 2147483647.0
    end
  end

  # ------------------------------------------------------- 法向统一修正 ----
  # 对凸形体（球、锥台、柱）有效：法向背离形心的朝外，朝向形心的 reverse!。
  def self.faces_outward(g)
    c = g.bounds.center
    g.entities.grep(Sketchup::Face).each do |f|
      n = f.normal
      next if n.length == 0
      f.reverse! if n.dot(f.bounds.center - c) < 0
    end
    g
  end

  # -------------------------------------------------------------- 网格成组 ----
  # ★ add_faces_from_mesh 的第二个参数必须是**整数标志**，且 SketchUp 2025 已移除
  #   Sketchup::MeshHelper 常量 —— 写 `Sketchup::MeshHelper::SMOOTH_SOFT_EDGES`
  #   会 NameError，写 `true` 会 `TypeError: no implicit conversion from boolean`。
  #   实测 2025 版：不传、传 0、传 1、传 4 都可以。4 = 软边平滑（球冠更圆）。
  MESH_SMOOTH = 4

  def self.mesh_group(parent, name, layer, mat, mesh)
    g = parent.entities.add_group
    begin
      g.entities.add_faces_from_mesh(mesh, MESH_SMOOTH)
    rescue StandardError
      g.entities.add_faces_from_mesh(mesh)
    end
    faces_outward(g)
    g.name = name
    g.layer = layer if layer
    g.material = mat if mat
    g
  end

  # ---------------------------------------------------------------- 球面网格 ----
  # 图纸 (cx, cy) + 高度 cz，半径 r。极轴沿 Z（高度方向）。
  def self.sphere_mesh(cx, cy, cz, r, seg = 14, rings = 9)
    mesh = Geom::PolygonMesh.new(seg * rings + 2, seg * rings)
    rows = []
    (0..rings).each do |i|
      phi = Math::PI * i / rings.to_f
      sz = cz + r * Math.cos(phi)          # 高度分量（Z）
      rho = r * Math.sin(phi)              # 平面半径
      if i == 0 || i == rings
        rows << [mesh.add_point(Geom::Point3d.new(cx.mm, cy.mm, sz.mm))]
      else
        row = []
        seg.times do |j|
          th = 2 * Math::PI * j / seg.to_f
          row << mesh.add_point(Geom::Point3d.new(
            (cx + rho * Math.cos(th)).mm,
            (cy + rho * Math.sin(th)).mm,
            sz.mm))
        end
        rows << row
      end
    end

    (0...rings).each do |i|
      a = rows[i]
      b = rows[i + 1]
      seg.times do |j|
        j2 = (j + 1) % seg
        if a.size == 1
          mesh.add_polygon(a[0], b[j2], b[j])
        elsif b.size == 1
          mesh.add_polygon(a[j], a[j2], b[0])
        else
          mesh.add_polygon(a[j], a[j2], b[j2], b[j])
        end
      end
    end
    mesh
  end

  # -------------------------------------------------------------- 锥台网格 ----
  # 带收分的柱体：下半径 r0、上半径 r1，可分别封底/封顶。
  def self.frustum_mesh(cx, cy, h0, h1, r0, r1, seg = 12, cap0 = true, cap1 = true)
    mesh = Geom::PolygonMesh.new(2 * seg + 2, 3 * seg)
    p0 = []
    p1 = []
    seg.times do |j|
      th = 2 * Math::PI * j / seg.to_f
      c = Math.cos(th)
      s = Math.sin(th)
      p0 << mesh.add_point(Geom::Point3d.new((cx + r0 * c).mm, (cy + r0 * s).mm, h0.mm))
      p1 << mesh.add_point(Geom::Point3d.new((cx + r1 * c).mm, (cy + r1 * s).mm, h1.mm))
    end
    seg.times do |j|
      j2 = (j + 1) % seg
      mesh.add_polygon(p1[j], p1[j2], p0[j2], p0[j])
    end
    if cap0
      c0 = mesh.add_point(Geom::Point3d.new(cx.mm, cy.mm, h0.mm))
      seg.times { |j| mesh.add_polygon(c0, p0[(j + 1) % seg], p0[j]) }
    end
    if cap1
      c1 = mesh.add_point(Geom::Point3d.new(cx.mm, cy.mm, h1.mm))
      seg.times { |j| mesh.add_polygon(c1, p1[j], p1[(j + 1) % seg]) }
    end
    mesh
  end

  # ------------------------------------------------------------------ 乔木 ----
  # h  = 树总高（树冠顶到地面，函数会精确校正到该值）
  # cr = 树冠半径
  # crown_base_ratio = 树冠起始高度占总高的比例（0.42 左右像阔叶乔木）
  def self.tree(parent, name, cx, cy, h, cr, trunk_mat, crown_mat,
                layer: '景观', tiers: 3, seg: 12, rings: 8,
                crown_base_ratio: 0.42, seed: 1, base_h: 0)
    return nil if h <= 0 || cr <= 0
    rnd = rng(seed)
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer

    # 树干：下粗上细的两段锥台，略带一点摆动
    tw = cr * 0.14
    mesh_group(g, "#{name}_干下", layer, trunk_mat,
               frustum_mesh(cx, cy, base_h, base_h + h * 0.30, tw, tw * 0.74, 8))
    lean = (rnd.call - 0.5) * tw * 1.6
    mesh_group(g, "#{name}_干上", layer, trunk_mat,
               frustum_mesh(cx + lean, cy + lean * 0.6, base_h + h * 0.30,
                            base_h + h * crown_base_ratio + h * 0.06,
                            tw * 0.74, tw * 0.44, 8))

    crown_base = base_h + h * crown_base_ratio
    hc = h - h * crown_base_ratio

    # 分层球冠。比例固定，扰动用种子随机器 → 同一次重建结果一致
    frac = { 3 => [0.20, 0.55, 0.88], 4 => [0.15, 0.42, 0.68, 0.90] }[tiers] || [0.20, 0.55, 0.88]
    rfac = { 3 => [1.00, 0.80, 0.52], 4 => [1.00, 0.88, 0.70, 0.45] }[tiers] || [1.00, 0.80, 0.52]

    specs = []
    frac.each_with_index do |f, k|
      rr = cr * rfac[k]
      ox = (rnd.call - 0.5) * cr * 0.42
      oy = (rnd.call - 0.5) * cr * 0.42
      # ★ 先把「实际球半径」定下来再算校正量。
      #   若先按 rr 算 shift、建球时再用随机的 w，随机量会叠加到高度上，
      #   实测表现为「12 m 的树量出 13.2 m」，外包尺寸对不上真源。
      w = (rr * (0.92 + 0.16 * rnd.call)).round
      specs << [crown_base + hc * f, w, ox, oy]
    end

    # 把最高的球顶精确压到 h（绝对高度，已含 base_h），使外包可核验
    top = specs.map { |cy_i, w, _ox, _oy| cy_i + w }.max
    shift = top - h

    specs.each_with_index do |(cy_i, w, ox, oy), k|
      mesh_group(g, "#{name}_冠#{k + 1}", layer, crown_mat,
                 sphere_mesh(cx + ox, cy + oy, cy_i - shift, w, seg, rings))
    end
    g
  end

  # ------------------------------------------------------------------ 针叶树 ----
  # 松柏类：锥形叠冠。用球冠做不出针叶树，必须用锥台层层收进。
  # h = 总高；r = 底部冠幅半径（针叶树冠幅窄，一般 r ≈ 0.20—0.28 h）
  def self.conifer(parent, name, cx, cy, h, r, trunk_mat, crown_mat,
                   layer: '景观', tiers: 4, seg: 10, base_h: 0)
    return nil if h <= 0 || r <= 0
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer

    # 树干只露出下部一小截
    mesh_group(g, "#{name}_干", layer, trunk_mat,
               frustum_mesh(cx, cy, base_h, base_h + h * 0.24, r * 0.16, r * 0.13, 8))

    tiers.times do |k|
      f0 = 0.10 + 0.80 * k / tiers.to_f
      f1 = 0.10 + 0.80 * (k + 1) / tiers.to_f + 0.13
      f1 = 1.0 if k == tiers - 1
      rr = r * (1.0 - 0.70 * k / tiers.to_f)
      mesh_group(g, "#{name}_锥#{k + 1}", layer, crown_mat,
                 frustum_mesh(cx, cy, base_h + h * f0, base_h + h * f1,
                              rr, rr * 0.10, seg, true, false))
    end
    g
  end

  # ------------------------------------------------------------------ 灌木 ----
  # 半球状丛生：若干球体下沿埋入地面，形成起伏的灌木团。
  def self.shrub(parent, name, cx, cy, r, h, mat, layer: '景观', lobes: 5, seed: 7, base_h: 0)
    return nil if r <= 0 || h <= 0
    rnd = rng(seed)
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer
    lobes.times do |k|
      rr = h * (0.62 + 0.38 * rnd.call)
      rw = [rr, h * 0.78].min + r * 0.15 * rnd.call
      ox = (rnd.call - 0.5) * 2 * (r - rw * 0.5)
      oy = (rnd.call - 0.5) * 2 * (r - rw * 0.5)
      cyc = base_h + rr * 0.58
      cyc = base_h + h - rr * 0.55 if cyc + rr > base_h + h + h * 0.10
      mesh_group(g, "#{name}_团#{k + 1}", layer, mat,
                 sphere_mesh(cx + ox, cy + oy, cyc, rw, 10, 7))
    end
    g
  end

  # ------------------------------------------------------------------ 绿篱 ----
  # 连续矮灌木带（整形绿篱），现代景观里最常见的直线条。
  def self.hedge(parent, name, x0, x1, y0, y1, h, mat, layer: '景观', base_h: 0)
    return nil if h <= 0
    box(parent, name, layer, mat, x0, y0, x1, y1, base_h, base_h + h)
  end

  # ------------------------------------------------------------------ 水体 ----
  # 池底 + 池壁（四周形成一圈边框）+ 水面（略低于场地）。
  # edge = 池边石宽度；depth = 池深；water_drop = 水面低于场地的高度
  def self.water(parent, name, x0, y0, x1, y1,
                 edge: 400.0, depth: 900.0, water_drop: 180.0, lip: 60.0,
                 mat_water: nil, mat_bottom: nil, mat_edge: nil,
                 layer: '水体', layer_edge: '景观')
    return nil if x1 <= x0 || y1 <= y0
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer

    # 池底
    box(g, "#{name}_池底", layer, mat_bottom, x0, y0, x1, y1, -depth, -depth + 120)
    # 四周池壁：外扩 edge 的一圈边框
    box(g, "#{name}_壁西", layer_edge, mat_edge, x0 - edge, y0 - edge, x0, y1 + edge, -depth, 0)
    box(g, "#{name}_壁东", layer_edge, mat_edge, x1, y0 - edge, x1 + edge, y1 + edge, -depth, 0)
    box(g, "#{name}_壁南", layer_edge, mat_edge, x0, y0 - edge, x1, y0, -depth, 0)
    box(g, "#{name}_壁北", layer_edge, mat_edge, x0, y1, x1, y1 + edge, -depth, 0)
    # 水面：比池内壁略内收，避免与池壁面重合产生闪面（z-fighting）
    box(g, "#{name}_水面", layer, mat_water,
        x0 + lip, y0 + lip, x1 - lip, y1 - lip, -water_drop - 40, -water_drop)
    g
  end

  # ------------------------------------------------------------------ 台阶 ----
  # n 级台阶，沿 axis 方向逐级升高。每级都是从自己前缘到末端的通长实体，
  # 因此天然形成踏步堆叠，不会出现悬空薄板。
  def self.steps(parent, name, a0, a1, b0, b1, n, rise,
                 mat, layer: '景观', base_h: 0, axis: :y)
    return nil if n <= 0 || a1 <= a0 || b1 <= b0
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer
    d = (b1 - b0) / n.to_f
    n.times do |i|
      h0 = base_h + i * rise
      h1 = base_h + (i + 1) * rise
      ys = b0 + i * d
      if axis == :y
        box(g, "#{name}_踏步#{i + 1}", layer, mat, a0, ys, a1, b1, h0, h1)
      else
        xs = a0 + i * ((a1 - a0) / n.to_f)
        box(g, "#{name}_踏步#{i + 1}", layer, mat, xs, b0, a1, b1, h0, h1)
      end
    end
    g
  end

  # ------------------------------------------------------------------ 栏杆 ----
  # 立柱 + 上下横杆。a0..a1 为长度方向，at 为横向位置，t 为厚度。
  def self.railing(parent, name, a0, a1, at, t, h, post_mat, rail_mat,
                   layer: '栏杆', base_h: 0, axis: :x, spacing: 1500.0,
                   post_w: 60.0)
    return nil if a1 <= a0 || h <= 0
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer
    half = t / 2.0

    if axis == :x
      box(g, "#{name}_扶手", layer, rail_mat, a0, at - half, a1, at + half,
          base_h + h - 60, base_h + h)
      box(g, "#{name}_横杆", layer, rail_mat, a0, at - half * 0.6, a1, at + half * 0.6,
          base_h + h * 0.52, base_h + h * 0.52 + 40)
      n = [(a1 - a0) / spacing, 1].max.round
      (0..n).each do |k|
        x = a0 + (a1 - a0) * k / n.to_f
        x = a1 - post_w if x + post_w > a1
        box(g, "#{name}_柱#{k + 1}", layer, post_mat, x, at - half, x + post_w, at + half,
            base_h, base_h + h)
      end
    else
      box(g, "#{name}_扶手", layer, rail_mat, at - half, a0, at + half, a1,
          base_h + h - 60, base_h + h)
      box(g, "#{name}_横杆", layer, rail_mat, at - half * 0.6, a0, at + half * 0.6, a1,
          base_h + h * 0.52, base_h + h * 0.52 + 40)
      n = [(a1 - a0) / spacing, 1].max.round
      (0..n).each do |k|
        y = a0 + (a1 - a0) * k / n.to_f
        y = a1 - post_w if y + post_w > a1
        box(g, "#{name}_柱#{k + 1}", layer, post_mat, at - half, y, at + half, y + post_w,
            base_h, base_h + h)
      end
    end
    g
  end

  # -------------------------------------------------------------- 人物参照 ----
  # 尺度参照是效果图可信度的关键：没有人的图，评委不知道这楼多大。
  # 简化三段式（双腿 + 躯干 + 头），总高精确 = h。
  def self.person(parent, name, cx, cy, h, mat, layer: '人物', rot: 0, base_h: 0)
    return nil if h <= 0
    s = h / 1700.0
    g = parent.entities.add_group
    g.name = name
    g.layer = layer if layer

    box(g, "#{name}_腿1", layer, mat, cx - 105 * s, cy - 65 * s, cx - 25 * s, cy + 65 * s, base_h, base_h + 820 * s)
    box(g, "#{name}_腿2", layer, mat, cx + 25 * s, cy - 65 * s, cx + 105 * s, cy + 65 * s, base_h, base_h + 820 * s)
    box(g, "#{name}_躯干", layer, mat, cx - 190 * s, cy - 95 * s, cx + 190 * s, cy + 95 * s,
        base_h + 820 * s, base_h + 1440 * s)
    mesh_group(g, "#{name}_头", layer, mat,
               sphere_mesh(cx, cy, base_h + 1560 * s, 118 * s, 10, 6))

    # 落到精确高度
    bb = g.bounds
    dz = (base_h + h) - bb.max.z.to_mm
    g.transform!(Geom::Transformation.new([0, 0, dz.mm])) if dz.abs > 1

    if rot != 0
      g.transform!(Geom::Transformation.rotation(
        Geom::Point3d.new(cx.mm, cy.mm, 0), Geom::Vector3d.new(0, 0, 1), rot.degrees))
    end
    g
  end

  # ------------------------------------------------------------ 场地铺装板 ----
  # 语义化别名，让生成器读起来更像在「铺地」而不是「放盒子」。
  def self.slab(parent, name, layer, mat, x0, y0, x1, y1, h0, h1)
    box(parent, name, layer, mat, x0, y0, x1, y1, h0, h1)
  end

  # ------------------------------------------------------------------ 树池 ----
  # 树木落地处的方形/圆形种植池边框，让树看起来是「种在场地里」而不是插在地上。
  def self.tree_pit(parent, name, cx, cy, r, h, mat, layer: '景观', base_h: 0, seg: 12)
    mesh_group(parent, name, layer, mat,
               frustum_mesh(cx, cy, base_h, base_h + h, r, r, seg, false, true))
  end
end
