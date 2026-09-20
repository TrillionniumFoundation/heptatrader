"""Synthetic public fixtures; no upstream data, accounts, SDK or credentials."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

START = int(datetime(2024, 1, 2, 9, tzinfo=timezone.utc).timestamp()) * 1000000
PRICES = [10, 11, 12, 9, 8, 7]


def catalog(extra: str = '', name: str = 'SYNTH') -> str:
    return ('<Catalog><Instrument InstrumentID="'+name+'" ExchangeID="TEST" ProductID="SY" '
            'ProductClass="1" PriceTick="0.5" VolumeMultiple="3" '+extra+'/></Catalog>')


def csv_row(i: int, *, price=None, volume=None, layout='immsg35') -> str:
    fields = ['0'] * (35 if layout == 'immsg35' else 32)
    if layout == 'immsg35':
        fields[1:9] = ['IMMSG', 'SYNTH', '20240102', '20240102', f'09:{i:02d}:00', '0',
                       str(PRICES[i] if price is None else price), str(10*(i+1) if volume is None else volume)]
    else:
        fields[0:6] = ['SYNTH', '20240102', f'09:{i:02d}:00', '0',
                       str(PRICES[i] if price is None else price), str(10*(i+1) if volume is None else volume)]
    return ','.join(fields)+'\n'


def binary_row(i: int) -> bytes:
    # Independent explicit packing of the reviewed native layout, not a vendor object.
    data = bytearray(424)
    for offset, text in ((0,'TEST'), (11,'20240102'), (20,'20240102'),
                         (29,f'09:{i:02d}:00'), (44,'SYNTH')):
        data[offset:offset+len(text)] = text.encode('ascii')
    struct.pack_into('<d', data, 288, float(PRICES[i]))
    struct.pack_into('<q', data, 328, (i+1)*10)
    return bytes(data)


def fixture(directory: Path, *, binary=False, cut=3, single=False) -> dict:
    config, instruments, bindings, sessions, files = (directory/name for name in
        ('config.xml','instruments.xml','bindings.json','sessions.csv','files.xml'))
    kind = (1 if binary else 0) + (0 if single else 2)
    front = 'old-file.bin' if single and binary else 'old-file.csv' if single else 'old-list.xml'
    config.write_text(f'<Configuration><User type="{kind}"><SimulatorServer Front="{front}" '
        'Instrument="old-instruments.xml" Interval="9"/><Account PreBalance="99999"/></User>'
        '<Subscription><Instrument ID="SYNTH"/></Subscription>'
        '<System><Cache Need="true" Path="/untrusted/not-created" Instrument="SYNTH"/></System>'
        '<Result><TotalResult bSave="true" Interval="10"/></Result></Configuration>', encoding='utf-8')
    instruments.write_text(catalog(), encoding='utf-8')
    sessions.write_text(f'begin_us,end_us,trading_day\n{START},{START+3600000000},20240102\n')
    rows = [binary_row(i) if binary else csv_row(i).encode() for i in range(6)]
    groups = [rows] if single else [rows[:cut], rows[cut:]]
    sources = []
    for j, group in enumerate(groups):
        index = 1 if single else [20, 90][j]
        reference = front if single else f'legacy-part-{j}'
        path = directory/f'data-{j}'
        data = b''.join(group)
        path.write_bytes(data)
        sources.append(dict(index=index, reference=reference, path=str(path.resolve()),
                            sha256=hashlib.sha256(data).hexdigest(),
                            layout='hepta-depth82-le-a8-v1' if binary else 'immsg35'))
    # Intentionally reverse both XML and bindings. Processing must use numeric indices.
    files.write_text('<Files>'+''.join(f'<MDFile DateIndexId="{s["index"]}" '
        f'FilePath="{s["reference"]}"/>' for s in reversed(sources))+'</Files>')
    binding = dict(schema='hepta.research.xml-bindings.v1', instrument_reference='old-instruments.xml',
                   front_reference=front, sources=list(reversed(sources)))
    bindings.write_text(json.dumps(binding), encoding='utf-8')
    return dict(config=config, instruments=instruments, bindings=bindings, sessions=sessions,
                file_list_path=None if single else files, source_paths=[Path(s['path']) for s in sources])


def update_digest(bundle: dict, index: int = 0) -> None:
    data = json.loads(bundle['bindings'].read_text())
    p = bundle['source_paths'][index]
    for item in data['sources']:
        if item['path'] == str(p):
            item['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    bundle['bindings'].write_text(json.dumps(data))


def cli_args(bundle: dict, output: Path) -> list[str]:
    args = []
    for name in ('config','instruments','bindings','sessions'):
        args += ['--'+name, str(bundle[name])]
    if bundle['file_list_path'] is not None:
        args += ['--file-list', str(bundle['file_list_path'])]
    return args+['--output',str(output),'--instrument','SYNTH','--clock-zone','UTC',
                 '--period-us','60000000','--first-volume','include','--capital','1000',
                 '--quantity','2','--fast','1','--slow','2','--slippage','0.5','--fee-per-unit','0.25']
