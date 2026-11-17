#!/usr/bin/env bash
# Runs inside the CI Android 14 emulator. Screenshots are actual emulator pixels.
set -euo pipefail
cd android
gradle connectedDebugAndroidTest --no-daemon
python3 - <<'PY'
import glob, xml.etree.ElementTree as ET
files = glob.glob('app/build/outputs/androidTest-results/connected/**/*.xml', recursive=True)
tests = sum(int(ET.parse(path).getroot().get('tests', 0)) for path in files)
failures = sum(int(ET.parse(path).getroot().get('failures', 0)) + int(ET.parse(path).getroot().get('errors', 0)) for path in files)
print(f'Instrumentation XML reports: {len(files)}; tests: {tests}; failures: {failures}')
if tests < 2 or failures:
    raise SystemExit('Missing or failed instrumentation tests; refusing a false green')
print(f'::notice title=Android instrumentation::tests={tests} failures={failures}')
PY
mkdir -p ../emulator-screenshots
for name in home settings projects; do
  adb exec-out run-as com.phoneagent.team cat "files/phase41-screenshots/${name}.png" > "../emulator-screenshots/${name}.png"
  test -s "../emulator-screenshots/${name}.png"
done
