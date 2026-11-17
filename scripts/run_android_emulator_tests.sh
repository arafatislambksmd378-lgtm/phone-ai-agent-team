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
  adb exec-out cat "/sdcard/phase41-${name}.png" > "../emulator-screenshots/${name}.png"
  python3 - "../emulator-screenshots/${name}.png" <<'PY'
import pathlib, struct, sys
p = pathlib.Path(sys.argv[1]); data = p.read_bytes()
if len(data) < 10000 or data[:8] != b'\x89PNG\r\n\x1a\n':
    print(f'::error title=Invalid emulator screenshot::{p.name}: {len(data)} bytes; prefix {data[:80]!r}')
    raise SystemExit(1)
w, h = struct.unpack('>II', data[16:24])
if min(w, h) < 320:
    raise SystemExit(f'{p}: unexpected image dimensions {w}x{h}')
print(f'::notice title=Android screenshot::{p.name} bytes={len(data)} dimensions={w}x{h}')
PY
done
