# -*- coding: utf-8 -*-
# ============================================================================
# su_render.rb —— SketchUp 出图工具箱（项目无关）
# ----------------------------------------------------------------------------
# 用法：
#   load '路径/su_render.rb'
#   SURender.configure(views: VIEWS, out_dir: '.../渲染', cx: 8.30, cz: 5.00)
#   SURender.prep            # 保存渲染快照 + 应用渲染设置（一次）
#   SURender.shot(i)         # 出第 i 个视角（★ 每次只出一个，见下方「长请求」）
#   SURender.finish          # 恢复渲染设置（一次）
#   SURender.make_scenes     # 生成 SketchUp 场景，便于在软件内一键切换
#   SURender.save_as(path)   # 保存 skp
#
# ★★ 最重要的一条：不要把多个视角打包进一次 execute_ruby ★★
#   su_mcp 的「长请求」会假死——把「建相机 + 8 次 write_image + sleep」放进一个请求，
#   客户端连续超时都拿不到响应（真实耗时其实只有约 10 秒）。必须逐视角发独立请求，
#   每个请求 1—2 秒返回。跨请求需要保持的状态（渲染选项快照）放全局变量。
#
# 踩坑：
#   1) View#write_image **不接受关键字哈希**：
#        write_image(path, width:, height:, antialias:)  → TypeError: no implicit
#        conversion of Hash into Integer（0.1s 内即失败）
#        必须位置参数：write_image(path, w, h, antialias, compression)
#   2) 平行投影的 Camera#height 以「英寸」计：写 cam.height = 22.0 等于视高 0.56 m，
#      画面会放大到贴着墙面。必须写 22.m。
#   3) 渲染选项的键各版本不一致（如 DisplayProfileEdges 会抛 ArgumentError），
#      且若在 ensure 里恢复时抛错，会**掩盖真正的错误**。必须逐键 rescue。
#   4) 平面图默认会「南朝上」——up=[0,0,-1] 时 +Z(北) 落在画面下方。
#      改 up=[0,0,1] 即旋转 180° 成北朝上，且不产生镜像。
#   5) 俯视平面视角会被上层楼板/屋面完全遮挡，需临时隐藏上层与屋面，出图后恢复。
#   6) 若 SketchUp 窗口在后台，write_image 可能显著变慢；必要时先置前
#      （见 scripts/activate_window.py）。
# ============================================================================

module SURender
  # 视角表可在模块体内用 replace 就地修改（常量本身不能重新赋值）
  VIEWS = []          # [[名称, eye[x,y,z], target[x,y,z], up[x,y,z], 是否透视, 平行投影视高m], ...]

  # ⚠️ Ruby 不允许在方法内给常量赋值（dynamic constant assignment），
  #    因此这些可变配置一律用实例变量承载。
  def self.configure(views:, out_dir:, cx: 0.0, cz: 0.0, style: {})
    VIEWS.replace(views)
    @out   = out_dir
    @cx    = cx
    @cz    = cz
    @style = style
    true
  end

  def self.cx
    @cx || 0.0
  end

  def self.cz
    @cz || 0.0
  end

  def self.style
    @style || {}
  end

  def self.model
    Sketchup.active_model
  end

  def self.view
    model.active_view
  end

  def self.out_dir
    @out
  end

  def self.pt(x, y, z)
    Geom::Point3d.new(x.m, y.m, z.m)
  end

  def self.vec(x, y, z)
    Geom::Vector3d.new(x, y, z)
  end

  # ------------------------------------------------------------ 渲染选项 ----
  # 逐键安全赋值：非法键直接跳过，绝不抛异常（抛了就掩盖真错误）
  def self.rset(k, v)
    model.rendering_options[k] = v
    true
  rescue StandardError
    false
  end

  def self.shset(k, v)
    model.shadow_info[k] = v
    true
  rescue StandardError
    false
  end

  def self.snapshot
    {
      fog:      model.rendering_options['DisplayFog'],
      shadows:  model.shadow_info['DisplayShadows'],
      shadowt:  model.shadow_info['ShadowTime'],
      edges:    model.rendering_options['EdgeDisplayMode'],
      profiles: (model.rendering_options['DisplayProfileEdges'] rescue nil),
      bg:       model.rendering_options['BackgroundColor'],
      camera:   view.camera,
    }
  end

  def self.restore(s)
    rset('DisplayFog',          s[:fog])
    rset('EdgeDisplayMode',     s[:edges])
    rset('DisplayProfileEdges', s[:profiles]) if s[:profiles]
    rset('BackgroundColor',     s[:bg])       if s[:bg]
    shset('DisplayShadows',     s[:shadows])
    shset('ShadowTime',         s[:shadowt])
    view.camera = s[:camera]
    true
  end

  def self.setup_render(time: nil)
    rset('DisplayFog', false)          # 白雾会洗白画面
    rset('RenderMode', 2)              # 2 = 着色贴图（Textured）
    rset('EdgeDisplayMode', 1)
    rset('DisplayColorByLayer', false)
    rset('BackgroundColor', Sketchup::Color.new(247, 246, 244))
    shset('DisplayShadows', true)
    shset('UseSunForAllShading', true)
    shset('ShadowTime', time) if time
    shset('Light', 60)
    shset('Dark', 45)
    shset('DisplayNorth', false)
    style.each { |k, v| rset(k, v) }
    true
  end

  # ------------------------------------------------------------ 单层平面 ----
  # 隐藏上层与屋面，让俯视平面视角能看到室内。用 name_prefix / roof_name 指定。
  def self.floor_plan_only(on, name_prefix: '标准层', keep: '_1F', hide_extra: [])
    model.entities.each do |e|
      next unless e.is_a?(Sketchup::ComponentInstance) || e.is_a?(Sketchup::Group)
      n = e.name.to_s
      if n.start_with?(name_prefix) && !n.end_with?(keep)
        e.visible = !on
      elsif hide_extra.any? { |h| n.include?(h) }
        e.visible = !on
      end
    end
    true
  end

  # ---------------------------------------------------------------- 出图 ----
  # ⚠️ 位置参数，不是关键字哈希
  def self.write_image(path, w = 1800, h = 1120, antialias = true, compression = 0.92)
    view.write_image(path, w, h, antialias, compression)
    File.size(path)
  end

  # 一次性准备：快照 + 渲染设置。放在全局变量里以便跨请求使用。
  def self.prep(time: nil)
    $su_render_snap = snapshot
    setup_render(time: time)
    "prepared, views=#{VIEWS.size}"
  end

  # 出第 i 个视角（★ 每次调用只出一个）
  def self.shot(i, w: 1800, h: 1120)
    n = VIEWS[i]
    name, eye, tgt, up, persp, vh = n
    floor_plan_only(name.include?('平面'), keep: '_1F', hide_extra: ['屋面', '基座'])

    cam = Sketchup::Camera.new(pt(*eye), pt(*tgt), vec(*up))
    if persp
      cam.perspective = true
      cam.fov = 55.0
    else
      cam.perspective = false
      cam.height = vh.m          # ★ 英寸计，必须 .m
    end
    view.camera = cam
    view.invalidate
    sleep 0.5
    t = Time.now
    f = File.join(out_dir, "SK_#{name}.png")
    b = write_image(f, w, h)
    "#{name}: #{b} bytes  shot #{'%.1f' % (Time.now - t)}s"
  end

  def self.finish
    floor_plan_only(false, keep: '_1F', hide_extra: ['屋面', '基座'])
    restore($su_render_snap)
    view.invalidate
    'restored'
  end

  # ---------------------------------------------------------------- 场景 ----
  def self.make_scenes(prefix: 'SK_')
    model.pages.to_a.each { |p| model.pages.erase(p) } if model.pages.count > 0
    VIEWS.each do |name, eye, tgt, up, persp, vh|
      floor_plan_only(name.include?('平面'), keep: '_1F', hide_extra: ['屋面', '基座'])
      cam = Sketchup::Camera.new(pt(*eye), pt(*tgt), vec(*up))
      if persp
        cam.perspective = true
        cam.fov = 55.0
      else
        cam.perspective = false
        cam.height = vh.m
      end
      page = model.pages.add("#{prefix}#{name}")
      page.use_camera = true
      page.use_hidden = true        # 让「平面」这一页单独记住隐藏状态
      view.camera = cam
      page.update
    end
    floor_plan_only(false, keep: '_1F', hide_extra: ['屋面', '基座'])
    model.pages.selected_page = model.pages[0]
    model.pages.count
  rescue StandardError => e
    "scenes skipped: #{e.class}: #{e.message}"
  end

  # ---------------------------------------------------------------- 保存 ----
  # ⚠️ path 必须是宿主语言拼好的完整字符串。若用 Ruby 插值又恰好没插上，
  #    SketchUp 会把字面量 #{VAR} 当文件名，静默存到 ~/Documents 下。
  def self.save_as(path)
    model.save(path)
    File.size(path)
  end
end
