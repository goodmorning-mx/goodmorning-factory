"""Explicit DEV-only deployment of an unmerged Hosting PR using Factory V3.

No catalog writes, Git ref updates, merges, production or foreign resources.
"""
import argparse
import json
import os
import re
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
    # Factory's existing App has contents/actions access, not pull_requests/checks.
    # Verify the authoritative CI push run for the exact feature source instead
    # of widening credentials. The operator verifies the open PR separately.
    runs=github(f'repos/{repository}/actions/runs?branch=feature/hosting-mvp&event=push&head_sha={args.sha}')['workflow_runs']
    if not any(r['name']=='CI' and r['head_sha']==args.sha and r['conclusion']=='success' for r in runs):
        raise ReconcileError('A successful product CI push run is required on this exact SHA.')
    raw=json.loads(Path('previews/hosting.json').read_text())
    raw['backup']['storage_uuid']=os.environ.get('COOLIFY_BACKUP_STORAGE_UUID') or json.loads(Path('products/workspace.json').read_text())['backup']['storage_uuid']
    destination=os.environ.get('COOLIFY_DESTINATION_UUID')
    if not destination:
        # Reuse the already reviewed Factory-wide default; never invent an ID.
        workflow=Path('.github/workflows/create-product.yml').read_text()
        match=re.search(r"vars\.COOLIFY_RESOURCES_DESTINATION_UUID \|\| '([a-z0-9]+)'",workflow)
        if not match:
            raise ReconcileError('No reviewed Factory destination is configured.')
        destination=match.group(1)
    manifest=validate_manifest(raw)
    client=Coolify(os.environ['COOLIFY_BASE_URL'],os.environ['COOLIFY_API_TOKEN'],apply=args.apply)
    result=reconcile(manifest,client=client,server_name=os.environ.get('COOLIFY_SERVER_NAME','localhost'),
        destination_uuid=destination,confirm_product='hosting',confirm_environment='dev',
        deploy=args.apply,preview_source={'branch':'feature/hosting-mvp','sha':args.sha})
    # Report only public names/actions; no infrastructure identifiers.
    print(json.dumps({'status':result['status'],'product':'hosting','source_sha':args.sha,
        'actions':[{k:v for k,v in a.items() if k in {'resource','kind','action','keys'}} for a in result['actions']]}))


if __name__=='__main__':
    main()
