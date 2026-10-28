"""Build the exact canonical artifact owner bridge in an isolated workspace.

No Agentd source rewrite and no hand-written signature substitute. Native code
bytes are verified; only copied Cargo workspace membership is narrowed.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,tomllib
PIN='108bc534d33c7f9e48caa569c254253f6c356271'
PACKAGES=('hepta-types','hepta-learning-artifacts')
def toml(v):
 if isinstance(v,bool):return 'true' if v else 'false'
 if isinstance(v,str):return json.dumps(v)
 if isinstance(v,list):return '['+', '.join(toml(x) for x in v)+']'
 if isinstance(v,dict):return '{'+', '.join(k+' = '+toml(x) for k,x in v.items())+'}'
 return str(v)
def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--build',type=Path,required=True);a=p.parse_args()
 source=a.source.resolve();build=a.build.resolve();adapter=Path(__file__).parent/'financial_owner_verify.rs'
 if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=PIN:raise RuntimeError('CANONICAL_SOURCE_PIN')
 if build.exists():raise RuntimeError('FRESH_BUILD_PATH_REQUIRED')
 build.mkdir(mode=0o700,parents=True);hashes={}
 for name in PACKAGES:
  subprocess.run(['git','-C',str(source),'diff','--exit-code',PIN,'--','codex-rs/'+name],check=True,stdout=subprocess.DEVNULL)
  src=source/'codex-rs'/name;dst=build/name;shutil.copytree(src,dst)
  for f in src.rglob('*.rs'):
   rel=str(f.relative_to(source/'codex-rs'));hashes[rel]=hashlib.sha256(f.read_bytes()).hexdigest()
   assert hashes[rel]==hashlib.sha256((build/rel).read_bytes()).hexdigest()
 origin=tomllib.loads((source/'codex-rs/Cargo.toml').read_text());deps=origin['workspace']['dependencies']
 text='[workspace]\nresolver="3"\nmembers='+toml([*PACKAGES,'bridge'])+'\n[workspace.package]\nedition="2024"\nlicense="Apache-2.0"\nversion="0.0.0"\n[workspace.lints]\n[workspace.dependencies]\n'
 for k in ('ed25519-dalek','serde','serde_json','sha2','zeroize','pretty_assertions','tempfile'):text+=k+' = '+toml(deps[k])+'\n'
 (build/'Cargo.toml').write_text(text);bridge=build/'bridge';(bridge/'src').mkdir(parents=True)
 shutil.copyfile(adapter,bridge/'src/main.rs')
 shutil.copyfile(adapter.with_name('financial_owner_laya_tests.rs'),bridge/'src/financial_owner_laya_tests.rs')
 (bridge/'Cargo.toml').write_text('''[package]
name="hepta-financial-owner-verify"
version="0.1.0"
edition="2024"
[dependencies]
codex-hepta-learning-artifacts={path="../hepta-learning-artifacts"}
codex-hepta-types={path="../hepta-types"}
serde_json={workspace=true}
[dev-dependencies]
ed25519-dalek={workspace=true}
tempfile={workspace=true}
''')
 env={**os.environ,'CARGO_BUILD_JOBS':'2'}
 subprocess.run(['cargo','generate-lockfile','--offline','--manifest-path',str(build/'Cargo.toml')],check=True,env=env)
 subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',str(build/'Cargo.toml'),'-p','hepta-financial-owner-verify'],check=True,env=env)
 binary=build/'target/release/hepta-financial-owner-verify'
 r={'schema':'heptatrader.canonical-artifact-verifier-build.v1','native_source_sha':PIN,'unmodified_rust_sources':hashes,
    'binary':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
    'adapter_sha256':hashlib.sha256(adapter.read_bytes()).hexdigest(),'lock_sha256':hashlib.sha256((build/'Cargo.lock').read_bytes()).hexdigest(),
    'canonical_agentd_service_deployed':False,'owner_credentials_minted':False,'order_authority':False}
 (build/'build-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='unmodified_rust_sources'}))
if __name__=='__main__':main()
