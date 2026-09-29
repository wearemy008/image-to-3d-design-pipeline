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
  def self.configure(views:, out_dir:, cx: 0.0, cz: 0.0, style: {},
                     bg: nil, sky: nil, gradient: nil, sun: nil,
                     light: nil, dark: nil,
                     fog_color: nil, fog_start: nil, fog_end: nil)
    VIEWS.replace(views)
    @out      = out_dir
    @cx       = cx
    @cz       = cz
    @style    = style
    @bg       = bg          # 背景色 0xRRGGBB；黄昏图要暖灰蓝，不是纯白
    @sky      = sky         # false 关天空（否则天空色会盖过背景色）
    @gradient = gradient    # false 关天空渐变，得到纯色背景
    @sun      = sun         # Sketchup::Time，控制太阳高度角 → 长影与暖光
    @light    = light       # 阴影面板「亮」滑杆 0—100，默认 70
    @dark     = dark        # 阴影面板「暗」滑杆 0—100，默认 25（调高 → 影子更重）
    @fog      = fog_color   # 雾色 0xRRGGBB；★ 出图去地平线硬边的关键，见下
    @fog0     = fog_start   # 雾起距离（米）
    @fog1     = fog_end     # 雾终距离（米）
    true
  end

  def self.bg
    @bg
  end

  def self.sky
    @sky
  end

  def self.gradient
    @gradient
  end

  def self.sun
    @sun
  end

  def self.light
    @light
  end

  def self.dark
    @dark
  end

  def self.fog
    @fog
  end

  def self.fog0
    @fog0
  end

  def self.fog1
    @fog1
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

  # --------------------------------------------------- 当地时刻 → ShadowTime ---
  # ★ 实测坑（很隐蔽）：SketchUp 取 Time 的 **UTC 墙钟** 当作场地当地时刻。
  #   直接 si['ShadowTime'] = Time.new(2026,9,29,17,5) 得到的是「上午 09:05」的太阳
  #   （实测高度角 34.4°、方位角 117.5°），无论怎么调都不像黄昏；
  #   补上本机 UTC 偏移后，17:05 才给出高度角 13.3°、方位角 259.0°（正西略偏南）。
  #   凡是想表达「当地某点几分的太阳」都走这个函数。
  def self.local_time(y, mo, d, hh, mm = 0)
    t = Time.new(y, mo, d, hh, mm, 0)
    t + t.utc_offset
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
    rset('RenderMode', 2)              # 2 = 着色贴图（Textured）
    rset('EdgeDisplayMode', 1)
    rset('DisplayColorByLayer', false)

    # 背景：默认暖白；黄昏图传 bg 覆盖。天空与渐变要显式关掉，
    # 否则天空色会盖过 BackgroundColor，画面被洗成一片灰蓝（实测踩过）。
    if bg
      rset('BackgroundColor', Sketchup::Color.new((bg >> 16) & 0xFF, (bg >> 8) & 0xFF, bg & 0xFF))
    else
      rset('BackgroundColor', Sketchup::Color.new(247, 246, 244))
    end
    rset('DisplaySky', sky)            unless sky.nil?
    rset('DisplayGradient', gradient)  unless gradient.nil?

    # ★ SketchUp 的「无限地面」一定要关。它是一块按世界 z=0 无限延伸的板，
    #   颜色与设计无关（浅沙色），一出图就是「模型摆在桌上」的既视感，
    #   而且把设计场地里低于 z=0 的部分整个托起来盖住。
    #   正确做法：关掉它，自己铺一块够大的环境地面（见 plan_data.CONTEXT）。
    rset('DisplayGround', false)

    # ★ 雾：出图去「地平线硬边」的关键。自己铺的环境地面再大也有尽头，
    #   它的远端边缘会在画面上切出一条硬线，露出背景色（实测很扎眼）。
    #   把雾色设成与天空近地色一致，远处地面就自然融进天空里。
    #   雾起距离要大于「相机到主体」的距离，否则主体也会被雾洗白。
    if fog
      rset('DisplayFog', true)
      rset('FogColor', Sketchup::Color.new((fog >> 16) & 0xFF, (fog >> 8) & 0xFF, fog & 0xFF))
      rset('FogStartDist', (fog0 || 150).m)
      rset('FogEndDist', (fog1 || 800).m)
    else
      rset('DisplayFog', false)        # 白雾会洗白画面
    end

    shset('DisplayShadows', true)
    shset('UseSunForAllShading', true)
    t = time || sun
    shset('ShadowTime', t) if t
    # 阴影面板的两根滑杆：决定「受光面亮度」与「背光面暗度」，
    # 是 SketchUp 出黄昏图唯一能拉开明暗对比的旋钮（默认偏平）。
    shset('Light', (light || 70))
    shset('Dark',  (dark  || 25))
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
