#!/usr/bin/env python3
"""Build the reviewed, pinned Linux x64 Lite release. Python is build-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def acquire(url, path, expected, size=None):
    if not path.exists() or sha(path) != expected:
        tmp = path.with_suffix('.download')
        req = urllib.request.Request(url, headers={'User-Agent': 'omp-portable-builder'})
        with urllib.request.urlopen(req, timeout=120) as src, open(tmp, 'wb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if sha(tmp) != expected:
            tmp.unlink()
            raise RuntimeError(f'Hash mismatch: {url}')
        tmp.replace(path)
    if size is not None and path.stat().st_size != size:
        raise RuntimeError(f'Size mismatch: {path}')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-compile', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        parser.error('Only native Linux x64 is implemented and reviewed')
    lock = json.loads((ROOT / 'config/upstream-lock.json').read_text())
    policy = tomllib.loads((ROOT / 'config/editions.toml').read_text())['lite']
    if any(policy[k] for k in ('browser','speech_default','mnemopi','yt_dlp','trafilatura')) or policy['local_models'] != 'none':
        raise RuntimeError('This builder supports only Lite without optional components')
    cache = ROOT / 'build/upstream'
    cache.mkdir(parents=True, exist_ok=True)
    for item in [lock['asset'], lock['license'], *lock['reviewed_sources']]:
        acquire(item['url'], cache / item['name'], item['sha256'], item.get('size'))
    if not args.skip_compile:
        subprocess.run(['cargo', 'build', '--locked', '--release'], cwd=ROOT, check=True)
    stage = ROOT / 'build/stage'
    if stage.exists():
        shutil.rmtree(stage)
    for folder in ('bin', 'launcher-bin', 'licenses'):
        (stage / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache / lock['asset']['name'], stage / 'bin/omp.real')
    shutil.copyfile(ROOT / 'target/release/sfx-stub', stage / 'launcher-bin/omp')
    shutil.copyfile(cache / 'LICENSE', stage / 'licenses/OMP-LICENSE')
    shutil.copyfile(ROOT / 'LICENSE', stage / 'licenses/LAUNCHER-LICENSE')
    for path in ('bin/omp.real', 'launcher-bin/omp'):
        (stage / path).chmod(0o755)
    files = {}
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            files[p.relative_to(stage).as_posix()] = {'sha256': sha(p), 'size': p.stat().st_size, 'executable': bool(p.stat().st_mode & 0o111)}
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    manifest = {'schema': 1, 'edition': 'lite', 'target': 'linux-x64',
                'upstream': {'repo': lock['repo'], 'tag': lock['tag'], 'release_id': lock['release_id'],
                             'asset': lock['asset']['name'], 'sha256': lock['asset']['sha256'],
                             'reviewed_sources': lock['reviewed_sources']},
                'builder': {'repo_commit': commit, 'dirty': dirty, 'workflow_run': os.getenv('GITHUB_RUN_ID'),
                            'rustc': subprocess.check_output(['rustc', '--version'], text=True).strip()},
                'files': files}
    (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    out = dist / f"omp-lite-{lock['tag'].removeprefix('v')}-linux-x64"
    if out.exists():
        out.unlink()
    subprocess.run([str(ROOT / 'target/release/sfx-pack'), str(ROOT / 'target/release/sfx-stub'), str(stage), str(out), str(policy['max_sfx_bytes'])], check=True)
    (dist / 'SHA256SUMS').write_text(f'{sha(out)}  {out.name}\n')
    shutil.copyfile(stage / 'manifest.json', dist / 'manifest.json')
    print(out)

if __name__ == '__main__':
    main()
