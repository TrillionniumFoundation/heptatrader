#!/usr/bin/env python3
"""Build an isolated adapter using exact, unmodified native Hepta Rust sources.

Only the copied Cargo workspace is narrowed. Core .rs bytes are hash-verified;
no native implementation is vendored into or rewritten by HeptaTrader.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,tomllib
PACKAGES=('hepta-types','hepta-infer-core','hepta-contracts','hepta-neuron','hepta-ndu','hepta-plasticity')
PIN='78fdb0cf8537e3a84fc6e0a849707559c80881e8'

def tv(v):
    if isinstance(v,bool):return 'true' if v else 'false'
    if isinstance(v,str):return json.dumps(v)
    if isinstance(v,list):return '['+', '.join(tv(x) for x in v)+']'
    if isinstance(v,dict):return '{'+', '.join(k+' = '+tv(x) for k,x in v.items())+'}'
    return str(v)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--build',type=Path,required=True);ap.add_argument('--adapter',type=Path,required=True)
    a=ap.parse_args();source=a.source.resolve();build=a.build.resolve()
    sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if sha!=PIN:raise RuntimeError('NATIVE_SOURCE_PIN_CHANGED')
    subprocess.run(['git','-C',str(source),'diff','--exit-code',sha,'--',*[f'codex-rs/{n}' for n in PACKAGES]],check=True,stdout=subprocess.DEVNULL)
    original=tomllib.loads((source/'codex-rs/Cargo.toml').read_text())
    if build==source or build in source.parents or source in build.parents:
        raise RuntimeError('BUILD_MUST_BE_SEPARATE_FROM_SOURCE')
    build.mkdir(parents=True,exist_ok=True)
    hashes={}
    for name in PACKAGES:
        src=source/'codex-rs'/name;dst=build/name
        if dst.exists():shutil.rmtree(dst) # confined disposable build copy only
        shutil.copytree(src,dst)
        for p in sorted(src.rglob('*.rs')):
            rel=str(p.relative_to(source/'codex-rs'))
            hashes[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
            assert hashes[rel]==hashlib.sha256((build/rel).read_bytes()).hexdigest()
        if name=='hepta-contracts':
            # Not testing contracts here; omit its broad workspace-only dev graph.
            p=dst/'Cargo.toml'
            p.write_text(re.sub(r'\n\[dev-dependencies\][\s\S]*?(?=\n\[|\Z)','\n',p.read_text()))
    deps=original['workspace']['dependencies']
    selected=('ed25519-dalek','rustix','serde','serde_json','sha2','zeroize','pretty_assertions','tempfile')
    toml='[workspace]\nresolver = "3"\nmembers = '+tv([*PACKAGES,'bridge'])+'\n'
    toml+='[workspace.package]\nedition = "2024"\nlicense = "Apache-2.0"\nversion = "0.0.0"\n'
    toml+='[workspace.lints]\n[workspace.dependencies]\n'
    toml+='\n'.join(k+' = '+tv(deps[k]) for k in selected)+'\n'
    (build/'Cargo.toml').write_text(toml)
    bridge=build/'bridge';(bridge/'src').mkdir(parents=True,exist_ok=True)
    shutil.copyfile(a.adapter,bridge/'src/main.rs')
    (bridge/'Cargo.toml').write_text('''[package]
name = "hepta-trader-neuron-native"
version = "0.1.0"
edition = "2024"
[dependencies]
codex-hepta-neuron = { path = "../hepta-neuron" }
codex-hepta-ndu = { path = "../hepta-ndu" }
codex-hepta-plasticity = { path = "../hepta-plasticity" }
codex-hepta-types = { path = "../hepta-types" }
serde_json = { workspace = true }
''')
    pinned_lock=Path(__file__).parent/'native/Cargo.lock'
    if pinned_lock.exists():shutil.copyfile(pinned_lock,build/'Cargo.lock')
    env={**os.environ,'HEPTA_NATIVE_SOURCE_SHA':sha,'CARGO_BUILD_JOBS':'2'}
    subprocess.run(['cargo','build','--release','--locked','--manifest-path',str(build/'Cargo.toml'),'-p','hepta-trader-neuron-native'],env=env,check=True)
    binary=build/'target/release/hepta-trader-neuron-native'
    receipt={'source_repository':'TrillionniumFoundation/hepta-private-ci','source_sha':sha,
             'native_source_sha256':hashes,'core_rust_sources_unmodified':True,
             'workspace_change':'standalone member set; contracts dev-dependencies omitted',
             'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
             'adapter_sha256':hashlib.sha256(a.adapter.read_bytes()).hexdigest(),
             'cargo_lock_sha256':hashlib.sha256((build/'Cargo.lock').read_bytes()).hexdigest(),
             'effect_authority':False,'full_agentd_owner_activation_claimed':False}
    (build/'native-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'binary':str(binary),'source_sha':sha,'core_source_files':len(hashes)}))
if __name__=='__main__':main()
