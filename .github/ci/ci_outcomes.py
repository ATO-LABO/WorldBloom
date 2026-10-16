"""Persist exact selected-step outcomes, including unexpected skips."""
import argparse
import json
import os
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('profile',choices=('basic','windows'))
parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
keys=('python','syntax','javascript') if args.profile=='basic' else ('python',)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps({key:os.environ.get('WB_'+key.upper()+'_OUTCOME','missing') for key in keys}),encoding='utf-8')
