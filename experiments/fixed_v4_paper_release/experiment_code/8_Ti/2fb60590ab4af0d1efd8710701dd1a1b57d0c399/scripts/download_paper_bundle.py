"""Download the frozen bundle, honoring GitHub rate-limit headers and retrying reads."""
import argparse
from email.utils import parsedate_to_datetime
import io
import json
import os
from pathlib import Path
import random
import time
import urllib.error
import urllib.request
from zipfile import ZipFile


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


def delay(headers, attempt, now=None):
    now = time.time() if now is None else now
    retry = headers.get('Retry-After', '')
    try:
        seconds = float(retry)
    except ValueError:
        try: seconds = parsedate_to_datetime(retry).timestamp() - now
        except (TypeError, ValueError): seconds = 0
    if headers.get('X-RateLimit-Remaining') == '0':
        try: seconds = max(seconds, float(headers.get('X-RateLimit-Reset', '0')) - now)
        except ValueError: pass
    return max(seconds, min(60 * 2**attempt, 900), 0) + random.uniform(1, 20)


def get(url, token):
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(7):
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'User-Agent': 'paper-t678-bundle'})
        try:
            with opener.open(request, timeout=120) as response: return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (403, 429, 500, 502, 503, 504) or attempt == 6: raise
            wait = delay(error.headers, attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 6: raise
            wait = delay({}, attempt)
        print(f'Bundle request failed; retry {attempt+1}/6 in {wait:.0f}s', flush=True)
        time.sleep(wait)


def download(repo, run_id, destination):
    token = os.environ['GH_TOKEN']
    api = f'https://api.github.com/repos/{repo}/actions'
    data = json.loads(get(f'{api}/runs/{int(run_id)}/artifacts?name=paper-t678-bundle&per_page=100', token))
    artifacts = [a for a in data['artifacts'] if a['name'] == 'paper-t678-bundle' and not a['expired']]
    if len(artifacts) != 1: raise RuntimeError('Expected one unexpired paper-t678-bundle')
    archive = get(f"{api}/artifacts/{artifacts[0]['id']}/zip", token)
    destination = Path(destination).resolve()
    with ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.infolist():
            (destination / member.filename).resolve().relative_to(destination)
        zipped.extractall(destination)
    print('Frozen bundle downloaded and extracted.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--setup-run-id', required=True)
    parser.add_argument('--output', default='bundle')
    args = parser.parse_args()
    download(args.repo, args.setup_run_id, args.output)
