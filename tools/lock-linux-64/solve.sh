# usage: solve.sh <out.json> <drop-pins (space sep)> <specs...>
out=$1; drop=$2; shift 2
python3 - "$drop" <<'PY'
import sys
excl=set("libintl moltenvk libopenvino-arm-cpu-plugin pyobjc-core pyobjc-framework-cocoa libcxx llvm-openmp".split())|set(sys.argv[1].split())
with open('pinrc.yaml','w') as f:
    f.write('channels: [conda-forge]\nchannel_priority: strict\npinned_packages:\n')
    for l in open('osx_pins.tsv'):
        n,v,b,s=l.rstrip('\n').split('\t')
        if n not in excl: f.write(f'  - "{n}=={v}"\n')
PY
$MM create --rc-file $PWD/pinrc.yaml --platform linux-64 --dry-run --json -y -c conda-forge --override-channels -p $PWD/x "$@" > $out 2>${out%.json}.err
python3 - $out <<'PY'
import json,sys;d=json.load(open(sys.argv[1]))
if 'actions' not in d:
    print([l['message'][:2500] for l in d['log_history'] if l['level']=='error'][0]); raise SystemExit(1)
a=d['actions']['LINK'];print('OK',len(a))
PY
