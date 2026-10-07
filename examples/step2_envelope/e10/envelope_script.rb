# Step 2 envelope script e10 (design agent: claude-opus-5-5). e07 shading (awnings 0.99 m / 28.7% drop everywhere; fins north/south/east tower and west stories 9-18) with milder story grading: tower east/south/west WWR 0.45, stories 1-5 south 0.597 and east/west 0.540, stories 6-9 0.50; north uniform 0.50.
require 'json'
PARAMS = JSON.parse(<<'JSON_PARAMS')
{
  "north": {
    "wwr": 0.5,
    "head_m": 3.3,
    "side_margin_m": 0.06,
    "awning_out_m": 0.99,
    "awning_drop": 0.287,
    "fin_out_m": 0.99,
    "fin_spacing_m": 3.0,
    "fin_stories": [
      10,
      18
    ]
  },
  "east": {
    "wwr": 0.5,
    "head_m": 3.3,
    "side_margin_m": 0.06,
    "awning_out_m": 0.99,
    "awning_drop": 0.287,
    "fin_out_m": 0.99,
    "fin_spacing_m": 3.0,
    "fin_stories": [
      10,
      18
    ],
    "wwr_by_story": [
      [
        1,
        5,
        0.54
      ],
      [
        10,
        18,
        0.45
      ]
    ]
  },
  "south": {
    "wwr": 0.5,
    "head_m": 3.3,
    "side_margin_m": 0.06,
    "awning_out_m": 0.99,
    "awning_drop": 0.287,
    "fin_out_m": 0.99,
    "fin_spacing_m": 3.0,
    "fin_stories": [
      10,
      18
    ],
    "wwr_by_story": [
      [
        1,
        5,
        0.597
      ],
      [
        10,
        18,
        0.45
      ]
    ]
  },
  "west": {
    "wwr": 0.5,
    "head_m": 3.3,
    "side_margin_m": 0.06,
    "awning_out_m": 0.99,
    "awning_drop": 0.287,
    "fin_out_m": 0.99,
    "fin_spacing_m": 3.0,
    "fin_stories": [
      9,
      18
    ],
    "wwr_by_story": [
      [
        1,
        5,
        0.54
      ],
      [
        10,
        18,
        0.45
      ]
    ]
  }
}
JSON_PARAMS

# Shared body of the Step 2 envelope scripts of this run (authored by the design agent).
# Each case file = a PARAMS block + this body, concatenated into one self-contained .rb by make_case.py.
#
# PARAMS (per orientation key 'north','east','south','west'; orientation = quadrant of the wall's true azimuth):
#   wwr            window-to-wall ratio realized on every wall of that orientation
#   wwr_by_story   optional [[first, last, wwr], ...] overriding wwr for 1-based story ranges
#   head_m         window head height above the story floor (window hangs down from it)
#   side_margin_m  wall edge to window edge
#   awning_out_m   outstand of the horizontal/sloped board at the window head (0 = none), < 1.0
#   awning_drop    drop of the board's outer edge as a fraction of window height (0 = flat overhang);
#                  it equals the share of the window the board covers seen along the facade normal
#   fin_out_m      outstand of vertical fins (0 = none), < 1.0
#   fin_spacing_m  minimum fin spacing (>= 3.0)
#   stories        optional [first, last] 1-based story range in which the awning is built (default all)
#   fin_stories    optional [first, last] story range of the fins (default: all stories)
# Fins stand at the far edge of each >= 3 m module of a window (not at its first edge), so neighbouring walls
# do not double up fins at their shared edge; windows narrower than the spacing get no fin.
require 'openstudio'

model = OpenStudio::OSVersion::VersionTranslator.new.loadModel(OpenStudio::Path.new(ENV.fetch('INPUT_OSM')))
raise "Could not load #{ENV['INPUT_OSM']}" if model.empty?
model = model.get

def orientation(azimuth_deg)
  %w[north east south west][(((azimuth_deg + 45.0) % 360.0) / 90.0).floor]
end

north_axis = model.getBuilding.northAxis
glazing = model.getSubSurfaces.map { |s| s.construction.get }.uniq
raise 'expected one base glazing construction' unless glazing.size == 1
glazing = glazing.first

group = OpenStudio::Model::ShadingSurfaceGroup.new(model)
group.setName('Step2 added facade shading')
group.setShadingSurfaceType('Building')

log = []
shade_count = 0
shade_area = 0.0
walls = model.getSurfaces.select { |s| s.surfaceType == 'Wall' && s.outsideBoundaryCondition == 'Outdoors' }
walls.sort_by(&:nameString).each do |wall|
  space = wall.space.get
  t = space.transformation
  wall.subSurfaces.each(&:remove)
  v = wall.vertices.to_a
  zmin = v.map(&:z).min
  zmax = v.map(&:z).max
  bottom = v.select { |p| (p.z - zmin).abs < 1e-6 }
  raise "wall #{wall.nameString} is not a vertical rectangle" unless bottom.size == 2 && v.size == 4
  p1, p2 = bottom
  length = Math.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2)
  ux = (p2.x - p1.x) / length
  uy = (p2.y - p1.y) / length
  height = zmax - zmin
  n = wall.outwardNormal
  # true azimuth of the wall in building coordinates (space rotation included), as the rules compute it
  wp = v.map { |p| t * p }
  nx = ny = 0.0
  wp.each_with_index do |p, i|
    q = wp[(i + 1) % wp.size]
    nx += (p.y - q.y) * (p.z + q.z)
    ny += (p.z - q.z) * (p.x + q.x)
  end
  azimuth = (Math.atan2(nx, ny) * 180.0 / Math::PI + north_axis) % 360.0
  o = orientation(azimuth)
  prm = PARAMS.fetch(o)
  story = (zmin / 4.0).round + 1

  margin = [prm['side_margin_m'], length * 0.07].min
  w = length - 2.0 * margin
  wwr = prm['wwr']
  (prm['wwr_by_story'] || []).each { |a, b, value| wwr = value if story >= a && story <= b }
  h = wwr * length * height / w
  head = [prm['head_m'], height - 0.05].min
  sill = head - h
  if sill < 0.10
    sill = 0.10
    head = sill + h
  end
  raise "window does not fit wall #{wall.nameString} (h=#{h.round(3)})" if head > height - 0.02
  pt = lambda { |along, z, out = 0.0|
    OpenStudio::Point3d.new(p1.x + ux * along + n.x * out, p1.y + uy * along + n.y * out, zmin + z)
  }
  pts = [pt.call(margin, head), pt.call(margin, sill), pt.call(margin + w, sill), pt.call(margin + w, head)]
  win = OpenStudio::Model::SubSurface.new(pts, model)
  if win.outwardNormal.dot(n) < 0
    win.remove
    win = OpenStudio::Model::SubSurface.new(pts.reverse, model)
  end
  win.setSurface(wall)
  win.setSubSurfaceType('FixedWindow')
  win.setConstruction(glazing)
  win.setName("Step2 window #{wall.nameString}")

  stories = prm['stories']
  shaded = stories.nil? || (story >= stories[0] && story <= stories[1])
  add_shade = lambda { |points, name|
    world = points.map { |p| t * p }
    s = OpenStudio::Model::ShadingSurface.new(world, model)
    s.setShadingSurfaceGroup(group)
    s.setName(name)
    shade_count += 1
    shade_area += s.grossArea
  }
  if shaded && prm['awning_out_m'].to_f > 0.0 && w >= 0.3
    out = prm['awning_out_m']
    # the drop never exceeds 0.90 m, so a board is never steeper than 45 degrees (repair after e09)
    drop = [prm['awning_drop'].to_f * h, 0.90].min
    add_shade.call([pt.call(margin, head), pt.call(margin + w, head),
                    pt.call(margin + w, head - drop, out), pt.call(margin, head - drop, out)],
                   "Step2 awning #{wall.nameString}")
  end
  fin_stories = prm['fin_stories']
  finned = fin_stories.nil? || (story >= fin_stories[0] && story <= fin_stories[1])
  if finned && prm['fin_out_m'].to_f > 0.0 && w >= 0.3
    out = prm['fin_out_m']
    spacing = [prm['fin_spacing_m'].to_f, 3.0].max
    gaps = (w / spacing).floor            # number of >= spacing gaps that fit across the window
    positions = gaps >= 1 ? (1..gaps).map { |k| margin + w * k / gaps } : []
    z0 = [sill - 0.10, 0.02].max
    z1 = head
    positions.each_with_index do |a, k|
      add_shade.call([pt.call(a, z1), pt.call(a, z0), pt.call(a, z0, out), pt.call(a, z1, out)],
                     "Step2 fin #{wall.nameString} #{k}")
    end
  end
  log << format('%s story=%d %s L=%.2f win=%.2fx%.2f sill=%.2f head=%.2f', wall.nameString, story, o, length, w, h, sill, head)
end
group.remove if shade_count.zero?

File.write(File.join(ENV.fetch('CASE_DIR', '.'), 'envelope_script_notes.txt'),
           "walls=#{walls.size} shading_surfaces=#{shade_count} shading_area_m2=#{shade_area.round(1)}\n" + log.join("\n") + "\n")
puts "walls=#{walls.size} shading_surfaces=#{shade_count} shading_area_m2=#{shade_area.round(1)}"
model.save(OpenStudio::Path.new(ENV.fetch('OUTPUT_OSM')), true)
