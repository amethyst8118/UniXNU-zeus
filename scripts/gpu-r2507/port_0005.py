#!/usr/bin/env python3
"""0005 (client cache, display link, zeus-prime) on AdrenoMetal R2507. usage: port_0005.py AdrenoMetal 0005.patch"""
import re,sys,subprocess
from pathlib import Path
r=Path(sys.argv[1])
if not (r/'ZeusClientQuirks.h').exists(): subprocess.run(['git','apply','--include=ZeusClientQuirks.h',sys.argv[2]],cwd=r,check=True)
p=r/'NativeAdrenoMetalPlugin.mm';s=p.read_text()
def sub(a,b):
    global s
    if s.count(a)!=1:raise SystemExit(f'{s.count(a)}: {a[:80]}')
    s=s.replace(a,b)
sub('static id clientDevice(unsigned port) {','#include "ZeusClientQuirks.h"\nstatic id clientDevice(unsigned port) {')
i=s.index('static id clientDevice(unsigned port) {');j=s.index(chr(10)+'}'+chr(10),i)
c=s[i:j]
def cs(a,b):
    global c
    if c.count(a)!=1:raise SystemExit(f'client {c.count(a)}: {a[:80]}')
    c=c.replace(a,b)
cs('char candidates[3][512] = {};','char candidates[4][512] = {};')
cs('    id device = nil;','    zeusPersistentClientCache(candidates[3], sizeof candidates[3] - 16);\n    zeusSystemLayer();\n    id device = nil;')
m=re.search(r'for \(unsigned i : \{([^}]*)\}\)',c);print('order',m and m[0]) 
cs(m[0],'for (unsigned i : {2u, 3u, 0u, 1u})')
s=s[:i]+c+s[j:]
m=re.search(r'\n( *)native_bridge_metal_system_cache\([^\n]*i == 6\);\n',s)
print('server',m and m[0].strip())
if m:s=s[:m.end()]+m[1]+'zeusSystemLayer();\n'+s[m.end():]
sub('if (!bootArgSet("gpu-prime=0")) pthread_create','if (!bootArgSet("gpu-prime=0") && !zeusdl::bootArg("zeus-prime=0")) pthread_create')
h=(r/'ZeusClientQuirks.h').read_text()
print([x for x in re.findall(r'static (?:bool|void|int) (\w+)\(',h)])
p.write_text(s)
