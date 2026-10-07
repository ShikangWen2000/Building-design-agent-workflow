# Step 3 HVAC script (design agent: claude-opus-5-5), run after the catalog series / tool plan is built.
# 1. Relief fan follows the DCV flow as a variable-volume fan. The constructors make the DOAS supply fan
#    variable volume under DCV but leave the relief (exhaust) fan constant volume. The relief fan is replaced by
#    a variable-volume fan that keeps the relief fan's own pressure rise, total efficiency and motor efficiency
#    and takes the part-load power coefficients of the toolkit's supply fan (cloned from it).
# 2. The disabled Hong Kong DOAS placeholder heating coil gets zero water flow (h03 repair), like the toolkit's
#    placeholder FCU heating coils.
# No setpoint, schedule, coil, chiller, pump, pressure or efficiency value is changed.
require 'openstudio'

model = OpenStudio::OSVersion::VersionTranslator.new.loadModel(OpenStudio::Path.new(ENV.fetch('INPUT_OSM')))
raise "Could not load #{ENV['INPUT_OSM']}" if model.empty?
model = model.get
notes = []

model.getAirLoopHVACs.each do |loop|
  oa = loop.airLoopHVACOutdoorAirSystem
  next if oa.empty?
  oa = oa.get
  supply_fan = loop.supplyComponents.map(&:to_FanVariableVolume).reject(&:empty?).map(&:get).first
  relief_fan = oa.reliefComponents.map(&:to_FanConstantVolume).reject(&:empty?).map(&:get).first
  if supply_fan && relief_fan
    name = relief_fan.nameString
    pressure = relief_fan.pressureRise
    efficiency = relief_fan.fanEfficiency
    motor = relief_fan.motorEfficiency
    in_airstream = relief_fan.motorInAirstreamFraction
    schedule = relief_fan.availabilitySchedule
    relief_fan.remove
    fan = supply_fan.clone(model).to_FanVariableVolume.get
    fan.setName(name)
    fan.setPressureRise(pressure)
    fan.setFanTotalEfficiency(efficiency)
    fan.setMotorEfficiency(motor)
    fan.setMotorInAirstreamFraction(in_airstream)
    fan.setAvailabilitySchedule(schedule)
    fan.autosizeMaximumFlowRate
    node = oa.outboardReliefNode
    raise 'no relief node' if node.empty?
    raise 'could not place the relief fan' unless fan.addToNode(node.get)
    notes << "relief fan #{name}: constant volume -> variable volume (#{pressure} Pa, total efficiency #{efficiency}, motor #{motor})"
  end
  loop.supplyComponents.each do |component|
    coil = component.to_CoilHeatingWater
    next if coil.empty?
    coil.get.setMaximumWaterFlowRate(0.0)
    notes << "zero water flow on placeholder heating coil #{coil.get.nameString}"
  end
end
raise 'nothing changed' if notes.empty?
File.write(File.join(ENV.fetch('CASE_DIR', '.'), 'hvac_script_notes.txt'), notes.join("\n") + "\n")
puts notes.join("\n")
model.save(OpenStudio::Path.new(ENV.fetch('OUTPUT_OSM')), true)
