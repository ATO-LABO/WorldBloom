"""Bounded CPU experiment through the existing WorldBloom GA entry point."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gapengine.evolve import _evolve
from gapengine.festival_ga import ADAPTER


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--population',type=int,default=4)
    p.add_argument('--generations',type=int,default=2)
    p.add_argument('--ga-seed',type=int,default=20261012)
    args=p.parse_args();out=args.out.resolve()
    if out==ROOT or ROOT in out.parents:p.error('Output outside checkout required')
    archive=_evolve({'search_adapter':ADAPTER,'out':str(out),'population':args.population,
        'generations':args.generations,'ga_seed':args.ga_seed,'seeds':[0],'processes':1})
    print({'archive_cells':len(archive.cells),'out':str(out)})


if __name__=='__main__':main()
