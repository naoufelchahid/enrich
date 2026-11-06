"""Read public result listings. URLs and downloaded documents live in memory only."""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = 'https://www.marchespublics.gov.ma/'
LISTS = ('AvisExtraitPV', 'AvisAttribution')
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
DATE = re.compile(r'\b(\d{2}/\d{2}/\d{4})\b')
MAX_DOWNLOAD = 35 * 1024 * 1024
MAX_PDF = 30 * 1024 * 1024


@dataclass(frozen=True)
class Announcement:
    url: str
    published: date
    kind: str


def parse_listing(html: str, kind: str) -> tuple[list[Announcement], bool]:
    soup = BeautifulSoup(html, 'lxml')
    found: list[Announcement] = []
    for row in soup.select('table.table-results tr'):
        link = row.select_one('a[href*="EntrepriseDetailConsultation"]')
        cells = row.find_all('td', recursive=False)
        if not link or len(cells) < 2:
            continue
        match = DATE.search(cells[1].get_text(' ', strip=True))
        if not match:
            continue
        try:
            published = datetime.strptime(match.group(1), '%d/%m/%Y').date()
        except ValueError:
            continue
        url = urljoin(BASE, link.get('href', ''))
        if urlparse(url).netloc == urlparse(BASE).netloc:
            found.append(Announcement(url, published, kind))
    more = bool(soup.select_one('a[id$="PagerTop_ctl2"]'))
    return found, more


def postback_fields(html: str) -> dict[str, str]:
    form = BeautifulSoup(html, 'lxml').find('form', id='ctl0_ctl4')
    if form is None:
        raise ValueError('Portal postback form absent')
    fields = {element['name']: element.get('value', '')
              for element in form.select('input[name]')
              if element.get('type', '').lower() in ('hidden', 'text')}
    if 'PRADO_PAGESTATE' not in fields:
        raise ValueError('Portal page state absent')
    fields['PRADO_POSTBACK_TARGET'] = 'ctl0$CONTENU_PAGE$resultSearch$PagerTop$ctl2'
    fields['PRADO_POSTBACK_PARAMETER'] = ''
    return fields


def attachment_urls(html: str, page_url: str, kind: str) -> list[str]:
    soup = BeautifulSoup(html, 'lxml')
    label = soup.select_one('[id$="EntrepriseConsultationSummary_annonce"]')
    expected = {'AvisExtraitPV': 'extrait de pv', 'AvisAttribution': 'résultat définitif'}[kind]
    if not label or expected not in label.get_text(' ', strip=True).casefold():
        return []
    urls: list[str] = []
    for link in soup.select('a[href*="EntrepriseDownloadAvisJAL"]'):
        url = urljoin(page_url, link['href'])
        parsed = urlparse(url)
        if parsed.netloc == urlparse(BASE).netloc and 'idAvis=' in parsed.query and not parsed.query.endswith('idAvis='):
            if url not in urls:
                urls.append(url)
    return urls


def pdf_payloads(blob: bytes, content_type: str = '') -> list[bytes]:
    if len(blob) > MAX_DOWNLOAD:
        raise ValueError('Attachment too large')
    if blob.startswith(b'%PDF-'):
        return [blob]
    if not zipfile.is_zipfile(io.BytesIO(blob)):
        return []
    result: list[bytes] = []
    expanded = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            if item.is_dir() or path.is_absolute() or '..' in path.parts or path.suffix.casefold() != '.pdf':
                continue
            if item.file_size > MAX_PDF or item.compress_size == 0:
                continue
            expanded += item.file_size
            if expanded > 100 * 1024 * 1024 or len(result) >= 100:
                raise ValueError('ZIP expansion limit exceeded')
            with archive.open(item) as source:
                data = source.read(MAX_PDF + 1)
            if len(data) <= MAX_PDF and data.startswith(b'%PDF-'):
                result.append(data)
    return result


class PortalClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers['User-Agent'] = USER_AGENT

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response

    def iter_announcements(self, *, since: date, max_announcements: int):
        if max_announcements < 1:
            raise ValueError('max_announcements must be positive')
        count = 0
        seen: set[tuple[str, str]] = set()  # run-local only
        for kind in LISTS:
            url = f'{BASE}index.php5?page=entreprise.EntrepriseAdvancedSearch&{kind}'
            html = self._get(url).text
            while count < max_announcements:
                rows, more = parse_listing(html, kind)
                if not rows:
                    break
                for row in rows:
                    if row.published < since:
                        continue
                    if (kind, row.url) in seen:
                        continue
                    seen.add((kind, row.url))
                    yield row
                    count += 1
                    if count >= max_announcements:
                        return
                if not more or all(row.published < since for row in rows):
                    break
                fields = postback_fields(html)
                response = self.session.post(url, data=fields, headers={'Referer': url}, timeout=30)
                response.raise_for_status()
                if response.text == html:
                    break
                html = response.text

    def download_pdfs(self, announcement: Announcement) -> list[bytes]:
        response = self._get(announcement.url)
        result: list[bytes] = []
        for url in attachment_urls(response.text, response.url, announcement.kind):
            try:
                attachment = self._get(url)
                if len(attachment.content) <= MAX_DOWNLOAD:
                    result.extend(pdf_payloads(attachment.content, attachment.headers.get('Content-Type', '')))
            except (requests.RequestException, ValueError, zipfile.BadZipFile):
                continue
        return result


@contextmanager
def temporary_pdf(blob: bytes):
    """Only use when a filesystem PDF is required by the vision step."""
    scratch = Path('.scratch')
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tender-pdf-', dir=scratch) as directory:
        path = Path(directory) / 'document.pdf'
        path.write_bytes(blob)
        yield path
