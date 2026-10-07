"""Add one operation to a COPY of the existing native adapter, never the selected binary."""
from pathlib import Path
from .integrity import ContractError

def prepare_copy(original:Path,destination:Path):
 text=original.read_text();old='Some("proposal")=>proposal(v),'
 if text.count(old)!=1 or 'laya_proposal' in text:raise ContractError('UNREVIEWED_NATIVE_DISPATCH_SOURCE')
 addition=(Path(__file__).parent/'laya_proposal.rs').read_text()
 text=text.replace(old,old+'Some("laya_proposal")=>laya_proposal(v),',1)
 with destination.open('x') as out:out.write(text+'\n'+addition)

def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--original',required=True,type=Path);p.add_argument('--destination',required=True,type=Path);a=p.parse_args();prepare_copy(a.original,a.destination)
if __name__=='__main__':main()
