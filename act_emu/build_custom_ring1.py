"""build_custom_ring1.py - legacy splicer: build a ringtone from stock segments.

NOTE: this is the segment splicer, NOT the ACT encoder (act_encode.py).  It is
kept as a utility for assembling ring1.act from existing stock segments.

Usage: CMF_SDFS_DIR=<dir with stock .act files> python3 build_custom_ring1.py [out.act]
"""
import os, sys
from act_splice import deobfuscate, obfuscate, full_frames, MAGIC
target = 15556
S = os.environ.get('CMF_SDFS_DIR', 'sdfs_extract')
S = S.rstrip('/') + '/'
OUT = sys.argv[1] if len(sys.argv) > 1 else 'ring1_custom.act'
segs = ['di', 'welcome', 'di', 'welcome', 'poweroff', 'di', 'welcome', 'di', 'welcome', 'find']
out = bytearray(MAGIC)
for nm in segs:
    d = deobfuscate(open(S + nm + '.act', 'rb').read())
    out += b''.join(full_frames(d))
print('spliced raw length:', len(out))
n_frames = (target - 16) // 20
body = bytes(out[:2 + n_frames * 20])
if len(body) < target:
    body += bytes(target - len(body))  # zero tail -> decoder EOF
print('frames:', n_frames, 'final size:', len(body))
open(OUT, 'wb').write(obfuscate(body))
print('wrote', OUT)
