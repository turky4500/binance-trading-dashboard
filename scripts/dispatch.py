#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trigger the analysis workflow from anywhere (external scheduler, local, CI).

Usage:
    GITHUB_TOKEN=ghp_xxx python3 scripts/dispatch.py
    GITHUB_TOKEN=ghp_xxx python3 scripts/dispatch.py --repo owner/repo --workflow analyze-deploy.yml --ref main

For cron-job.org / any HTTP scheduler, call the endpoint directly:
    POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/<workflow-file>/dispatches
    Headers:  Authorization: Bearer <token>
              Accept: application/vnd.github+json
              X-GitHub-Api-Version: 2022-11-28
              Content-Type: application/json
    Body:     {"ref":"main"}
    Expected response: 204 No Content
"""
import argparse
import json
import os
import sys
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=os.environ.get('DASHBOARD_REPO', 'turky4500/binance-trading-dashboard'))
    ap.add_argument('--workflow', default='analyze-deploy.yml')
    ap.add_argument('--ref', default='main')
    args = ap.parse_args()

    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if not token:
        print('ERROR: set GITHUB_TOKEN (a token with Actions:write permission)', file=sys.stderr)
        return 1

    url = (f'https://api.github.com/repos/{args.repo}/actions/workflows/'
           f'{args.workflow}/dispatches')
    body = json.dumps({'ref': args.ref}).encode()
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
        print('HTTP', e.code, e.read().decode()[:300], file=sys.stderr)
    print(f'dispatch status: {code}  {"OK — workflow triggered" if code == 204 else "check token scopes (needs Actions write)"}')
    return 0 if code == 204 else 1


if __name__ == '__main__':
    sys.exit(main())
