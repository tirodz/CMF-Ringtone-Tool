import sys
from act_splice import deobfuscate, obfuscate, full_frames, MAGIC
target = 15556
S = '/workspace/project/cmf-watch-firmware/sdfs_extract/'
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
open('/tmp/ring1_custom.act', 'wb').write(obfuscate(body))
