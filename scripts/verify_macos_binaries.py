"""Static Mach-O audit only; this is not a replacement for native execution."""
from pathlib import Path
import argparse
import json
import struct

CPUS={'macos-arm64':0x100000c, 'macos-x64':0x1000007}


def target_slice(data, cpu):
    magic=data[:4]
    if magic==b'\xcf\xfa\xed\xfe':
        if int.from_bytes(data[4:8], 'little')!=cpu: raise ValueError('Wrong Mach-O CPU')
        return data
    if magic in (b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
        count=struct.unpack_from('>I',data,4)[0]; size=32 if magic[-1]==0xbf else 20
        for i in range(count):
            at=8+i*size; found=struct.unpack_from('>I',data,at)[0]
            offset,length=struct.unpack_from('>QQ' if size==32 else '>II',data,at+8)
            if found==cpu: return target_slice(data[offset:offset+length],cpu)
        raise ValueError('Universal binary lacks target architecture')
    return None


def audit(root, platform):
    root=root.resolve()
    count=0; highest=(0,0,0); unused_rpaths=[]
    for path in root.rglob('*'):
        if not path.is_file(): continue
        with path.open('rb') as stream:
            magic=stream.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe',b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
                if path.suffix in ('.so','.dylib'): raise ValueError('Not a Mac binary: '+str(path.relative_to(root)))
                continue
            data=target_slice(magic+stream.read(),CPUS[platform])
        count+=1; at=32; ncmds=struct.unpack_from('<I',data,16)[0]
        loads=[]; rpaths=['@executable_path/../lib']
        for _ in range(ncmds):
            command,length=struct.unpack_from('<II',data,at)
            if length<8 or at+length>len(data): raise ValueError('Invalid Mach-O load command')
            if command in (0xc,0x80000018,0x8000001f,0x20,0x80000023,0x8000001c):
                offset=struct.unpack_from('<I',data,at+8)[0]
                name=data[at+offset:at+length].split(b'\0',1)[0].decode('utf8')
                if command==0x8000001c:
                    rpaths.append(name)
                    if name.startswith('/') and not name.startswith(('/usr/lib/','/System/Library/')):
                        unused_rpaths.append({'file':path.relative_to(root).as_posix(),'rpath':name})
                else:
                    loads.append(name)
            if command in (0x24,0x32):
                version=struct.unpack_from('<I',data,at+(12 if command==0x32 else 8))[0]
                minimum=(version>>16,(version>>8)&255,version&255)
                highest=max(highest,minimum)
                if minimum>(14,0,0): raise ValueError('Binary requires newer than macOS 14: '+str(path.relative_to(root)))
            at+=length
        def expand(value):
            value=value.replace('@loader_path',str(path.parent)).replace('@executable_path',str(root/'python/bin'))
            return Path(value)
        for name in loads:
            if name.startswith(('/usr/lib/','/System/Library/')): continue
            if name.startswith('@rpath/'):
                candidates=[expand(r)/name[len('@rpath/'):] for r in rpaths if r.startswith('@')]
            elif name.startswith(('@loader_path/','@executable_path/')):
                candidates=[expand(name)]
            else:
                raise ValueError('Non-portable library load: '+str(path.relative_to(root))+': '+name)
            if not any(p.resolve().is_relative_to(root) and p.is_file() for p in candidates):
                raise ValueError('Missing bundled library: '+str(path.relative_to(root))+': '+name)
    if not count: raise ValueError('No Mach-O binaries')
    return {'platform':platform,'mach_o_files':count,'highest_minimum_os':'.'.join(map(str,highest)),
            'unresolved_libraries':0,'unused_absolute_rpaths':unused_rpaths,'native_execution':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--output',type=Path);args=parser.parse_args()
    platform=json.loads((args.runtime/'runtime.json').read_text('utf8'))['platform']
    result=audit(args.runtime,platform)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),'utf8')
    print(json.dumps({**result,'unused_absolute_rpaths':len(result['unused_absolute_rpaths'])}))
