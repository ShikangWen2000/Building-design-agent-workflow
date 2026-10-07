# Frame of a Step 3 HVAC script. The workflow runs it with the configured OpenStudio CLI:
#   openstudio execute_ruby_script <script>
# ENV['INPUT_OSM'] is the Step 2 model, or the catalog series or tool plan built on it when the spec also
# names a series_id or tool_plan; ENV['OUTPUT_OSM'] is the path to save the changed model to; ENV['CASE_DIR']
# the case's iteration folder.
#
# The script may build, replace or change HVAC systems. Envelope, loads, outdoor-air requirement, thermostats
# and simulation settings stay as they are, and equipment may not perform better than the catalog of the
# active climate. The model-level rules in steps/shared/model_rules.py judge the saved model; their limits are
# listed in steps/step3_hvac/README.md. Coil sizing passes and the simulation follow the script.
require 'openstudio'

model = OpenStudio::OSVersion::VersionTranslator.new.loadModel(OpenStudio::Path.new(ENV.fetch('INPUT_OSM')))
raise "Could not load #{ENV['INPUT_OSM']}" if model.empty?
model = model.get

# ... design changes ...

model.save(OpenStudio::Path.new(ENV.fetch('OUTPUT_OSM')), true)
