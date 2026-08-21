#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send a Telegram notification when the pipeline fails (optional).
Called from the workflow with if: failure(). Requires the Telegram secrets.
"""
import json
import os
import urllib.parse
import urllib.request


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        print('no telegram secrets — skipping failure notification')
        return 0
    run_id = os.environ.get('GITHUB_RUN_ID', '?')
    repo = os.environ.get('GITHUB_REPOSITORY', 'binance-trading-dashboard')
    text = (
        f"⚠️ Pipeline FAILURE\n\n"
        f"Repo: {repo}\nRun: https://github.com/{repo}/actions/runs/{run_id}\n\n"
        f"The analysis workflow failed. The dashboard will keep showing the last "
        f"successful data with a DATA SOURCE ERROR / stale badge until the next "
        f"successful cycle."
    )
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            print('telegram failure notification sent:', r.status == 200)
    except Exception as e:
        print('telegram notification failed:', type(e).__name__)
    return 0


if __name__ == '__main__':
    main()
