"""Refresh pinned browser assets; no Node or CDN is needed at runtime."""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

PACKAGES = {
    'markdown-it': ('15.0.2', 'q4IGxMv56jCqT4OCRCADBoDP3LO4MhmTXjFbphHPXs4g3j9Xg5RDnxqN8IF/3vIWEU+VCnUq+7JUg/cfy2E6Qw=='),
    'katex': ('0.18.7', 'h+UCwkZ+4Jz8WQ7MLGfj7UVFrRCizGb912fwF4luGdYsC5paYG1vx+jy+KRcC/XkpjGva/P7nAWuxNnPzRvzHw=='),
}
ROOT = Path(__file__).resolve().parents[1] / 'codexremote/web/vendor'

for name, (version, digest) in PACKAGES.items():
    url = f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz'
    data = urllib.request.urlopen(url, timeout=60).read()
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != digest:
        raise ValueError(f'Integrity mismatch: {name}')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            relative = member.name.removeprefix('package/')
            wanted = relative in {'LICENSE', f'dist/{name}.min.js', f'dist/{name}.min.css', 'dist/browser/markdown-it.umd.min.js'}
            wanted |= relative.startswith('dist/fonts/') and relative.endswith(('.woff2', '.woff', '.ttf'))
            if not member.isfile() or not wanted:
                continue
            dest = ROOT / name / ('markdown-it.min.js' if relative == 'dist/browser/markdown-it.umd.min.js' else relative.removeprefix('dist/'))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.extractfile(member).read())
    if not (ROOT / name / f'{name}.min.js').is_file():
        raise ValueError(f'Missing browser bundle: {name}')
    print(f'Bundled {name} {version}')
