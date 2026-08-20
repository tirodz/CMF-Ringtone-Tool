"""verify_full_chain.py - end-to-end verification of a rebuilt AOTA image.

Usage: python3 verify_full_chain.py <original.bin> <modified.bin> <replacement.act>
"""
import os, sys, wave, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decode_test import run
import fwmod

if len(sys.argv) < 4:
    sys.exit(__doc__)
orig_path, mod_path, replacement = sys.argv[1:4]

orig = fwmod.Aota(open(orig_path, 'rb').read())
mod = fwmod.Aota(open(mod_path, 'rb').read())

# 1. AOTA-level CRC validity
mod.verify()
print('1. mod CRCs valid; payload end', hex(mod.payload_end), 'size', hex(len(mod.data)))

# 2. all unchanged entries byte-identical
sdfs_orig = fwmod.Sdfs(fwmod.lzma_unpack(orig.get('sdfs_k.bin')))
sdfs_mod = fwmod.Sdfs(fwmod.lzma_unpack(mod.get('sdfs_k.bin')))
for f in sdfs_mod.files:
    a = sdfs_orig.get(f['name'])
    b = f['data']
    same = (a == b)
    print(f"2. {f['name']:12s} {'identical' if same else 'REPLACED'}")
    if f['name'] == 'ring1.act':
        assert b == open(replacement, 'rb').read(), 'round trip mismatch'

# 3. decode the on-flash ring1 from the repacked image with original decoder
stream = sdfs_mod.get('ring1.act')
res = run(stream, collect_pcm=True)  # auto? decode_test run() without deobfuscate; run() got XORed form
# decode_test.run doesn't deobfuscate; undo step: use act_decode.deobfuscate
from act_decode import deobfuscate
res = run(deobfuscate(stream), collect_pcm=True)
assert res['ok'] and not res['err']
print('3. decoded mod ring1:', res['nframes'], 'frames,', len(res['pcm']), 'bytes PCM')
with wave.open('mod_ring1.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(res['pcm'])

# 4. envelope summary
import struct
s = struct.unpack('<%dh' % (len(res['pcm'])//2), res['pcm'])
env = ['%.0f' % math.sqrt(sum(v*v for v in s[i:i+1600])/1600) for i in range(0, len(s), 1600)]
print('4. envelope:', env)
print('ALL VERIFICATION PASSED')
