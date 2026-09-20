"""Explicit DEV-only deployment of an unmerged Hosting PR using Factory V3.

No catalog writes, Git ref updates, merges, production or foreign resources.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from scripts.advanced_product_contract import validate_manifest
from scripts.reconcile_advanced_product import Coolify, ReconcileError, reconcile


def github(path):
    return json.loads(subprocess.check_output(['gh','api',path],text=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sha',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    repository='goodmorning-mx/hosting'
    source=github(f'repos/{repository}/commits/feature/hosting-mvp')
    if source['sha'] != args.sha:
        raise ReconcileError('Requested source SHA does not match the feature branch.')
    prs=github(f'repos/{repository}/pulls?state=open&base=dev&head=goodmorning-mx:feature/hosting-mvp')
    if len(prs)!=1 or prs[0]['head']['sha'] != args.sha:
        raise ReconcileError('An open PR against dev at this exact SHA is required.')
    checks=github(f'repos/{repository}/commits/{args.sha}/check-runs')['check_runs']
    expected={'Frontend checks','Backend checks','Docker build validation'}
    if not expected.issubset({c['name'] for c in checks if c['conclusion']=='success'}):
        raise ReconcileError('All product CI checks must pass on this SHA before preview.')
    raw=json.loads(Path('previews/hosting.json').read_text())
    raw['backup']['storage_uuid']=os.environ['COOLIFY_BACKUP_STORAGE_UUID']
    manifest=validate_manifest(raw)
    client=Coolify(os.environ['COOLIFY_BASE_URL'],os.environ['COOLIFY_API_TOKEN'],apply=args.apply)
    result=reconcile(manifest,client=client,server_name=os.environ.get('COOLIFY_SERVER_NAME','localhost'),
        destination_uuid=os.environ['COOLIFY_DESTINATION_UUID'],confirm_product='hosting',confirm_environment='dev',
        deploy=args.apply,preview_source={'branch':'feature/hosting-mvp','sha':args.sha})
    # Report only public names/actions; no infrastructure identifiers.
    print(json.dumps({'status':result['status'],'product':'hosting','source_sha':args.sha,
        'actions':[{k:v for k,v in a.items() if k in {'resource','kind','action','keys'}} for a in result['actions']]}))


if __name__=='__main__':
    main()
