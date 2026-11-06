"""Visual extraction of exact company names from PDF pages with ephemeral Codex calls."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import fitz


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['companies', 'legible'],
    'properties': {
        'legible': {'type': 'boolean'},
        'companies': {
            'type': 'array',
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['name', 'certain'],
                'properties': {'name': {'type': 'string'}, 'certain': {'type': 'boolean'}},
            },
        },
    },
}
PROMPT = '''Lis visuellement les pages PDF jointes. Extrais tous les noms exacts des entreprises
soumissionnaires ou attributaires explicitement écrites dans ces pages, quel que soit leur
statut. N'infère aucun nom. Inclue autant d'entreprises que présentes, sans plafond.
Certain vaut true uniquement si la lecture du nom est sûre. Legible vaut false si les pages
ne permettent aucune lecture fiable. Réponds uniquement suivant le schéma JSON fourni.
N'utilise aucun outil et n'écris aucun autre fichier.'''
NAME = re.compile(r'^[^\x00-\x1f<>]{2,180}$')


def validate_output(raw: str) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'companies', 'legible'}:
        raise ValueError('Invalid vision response')
    if type(data['legible']) is not bool or not isinstance(data['companies'], list):
        raise ValueError('Invalid vision response')
    if not data['legible']:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in data['companies']:
        if not isinstance(item, dict) or set(item) != {'name', 'certain'} or type(item['certain']) is not bool:
            raise ValueError('Invalid company entry')
        name = item['name'].strip() if isinstance(item['name'], str) else ''
        if item['certain'] and NAME.fullmatch(name):
            key = ' '.join(name.casefold().split())
            if key not in seen:
                seen.add(key)
                names.append(name)
    return names


def render_pages(blob: bytes, directory: Path):
    """Render every page, never silently truncate a document."""
    with fitz.open(stream=blob, filetype='pdf') as document:
        if document.page_count < 1 or document.page_count > 150:
            raise ValueError('PDF page count outside supported range')
        for index, page in enumerate(document):
            if page.rect.width * page.rect.height > 4_000_000:
                raise ValueError('PDF page dimensions too large')
            path = directory / f'page-{index + 1:04d}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(path)
            yield path


def codex_batch(images: list[Path], directory: Path) -> list[str]:
    schema_path = directory / 'schema.json'
    output_path = directory / 'response.json'
    schema_path.write_text(json.dumps(SCHEMA), encoding='utf-8')
    command = ['codex', 'exec', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only',
               '-C', str(directory), '--output-schema', str(schema_path),
               '--output-last-message', str(output_path), PROMPT]
    for image in images:
        command.extend(['-i', str(image)])
    result = subprocess.run(command, cwd=directory, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=180, check=False)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError('Codex vision extraction failed')
    return validate_output(output_path.read_text(encoding='utf-8'))


def extract_names(blob: bytes) -> list[str]:
    scratch = Path('.scratch').resolve()
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tender-vision-', dir=scratch) as temp:
        directory = Path(temp)
        names: list[str] = []
        seen: set[str] = set()
        batch: list[Path] = []
        for page in render_pages(blob, directory):
            batch.append(page)
            if len(batch) < 4:
                continue
            for name in codex_batch(batch, directory):
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    names.append(name)
            for image in batch:
                image.unlink()
            batch.clear()
        if batch:
            for name in codex_batch(batch, directory):
                if name.casefold() not in seen:
                    seen.add(name.casefold())
                    names.append(name)
        return names
