"""Add both Laya proposal operations to a NEW copy of the original bridge."""
import argparse
from pathlib import Path
from .integrity import ContractError

def prepare_copy(original,destination):
    text=Path(original).read_text();needle='Some("proposal")=>proposal(v),'
    if text.count(needle)!=1 or 'laya_proposal' in text:
        raise ContractError('NATIVE_DISPATCH_SOURCE_CHANGED')
    root=Path(__file__).parent
    text=text.replace(needle,needle+'Some("laya_proposal")=>laya_proposal(v),Some("laya_resident_proposal")=>laya_resident_proposal(v),')
    text+='\n'+(root/'laya_proposal.rs').read_text()+'\n'+(root/'resident_proposal.rs').read_text()
    with Path(destination).open('x') as output:output.write(text)

def main():
    p=argparse.ArgumentParser();p.add_argument('--original',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();prepare_copy(a.original,a.destination)
if __name__=='__main__':main()
