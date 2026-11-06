import io
import zipfile
from datetime import date

from scripts.tender_acquisition.portal import (
    PortalClient, attachment_urls, parse_listing, pdf_payloads, postback_fields,
    temporary_pdf,
)


LIST = '''<form id="ctl0_ctl4"><input name="PRADO_PAGESTATE" value="state" type="hidden"></form>
<table class="table-results"><tr><td></td><td>AOO 14/10/2026</td><td>
<a href="?page=entreprise.EntrepriseDetailConsultation&refConsultation=3">open</a>
</td></tr></table><a id="ctl0_CONTENU_PAGE_resultSearch_PagerTop_ctl2" href="#">next</a>'''


def test_listing_and_pagination_fields():
    rows, more = parse_listing(LIST, 'AvisExtraitPV')
    assert len(rows) == 1 and rows[0].published == date(2026, 10, 14) and more
    fields = postback_fields(LIST)
    assert fields['PRADO_POSTBACK_TARGET'].endswith('PagerTop$ctl2')


def test_only_expected_announcement_attachments():
    html = '''<span id="ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_annonce">Annonce d'extrait de PV</span>
    <a href="index.php?page=entreprise.EntrepriseDownloadAvisJAL&idAvis=12">PDF</a>
    <a href="index.php?page=entreprise.EntrepriseDownloadAvisJAL&idAvis=">empty</a>'''
    assert len(attachment_urls(html, 'https://www.marchespublics.gov.ma/index.php', 'AvisExtraitPV')) == 1
    assert attachment_urls(html, 'https://www.marchespublics.gov.ma/index.php', 'AvisAttribution') == []


def test_zip_extracts_only_safe_pdfs_and_temp_file_is_deleted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('a.pdf', b'%PDF-1.4\nhello')
        archive.writestr('../escape.pdf', b'%PDF-1.4\nsecret')
        archive.writestr('note.docx', b'docx')
    assert pdf_payloads(stream.getvalue()) == [b'%PDF-1.4\nhello']
    with temporary_pdf(b'%PDF-1.4\nhello') as path:
        assert path.exists()
    assert not path.exists()
    assert list((tmp_path / '.scratch').iterdir()) == []


def test_date_boundary_and_document_budget():
    class Response:
        text = LIST
        def raise_for_status(self): pass

    class Session:
        def __init__(self): self.headers = {}; self.calls = 0
        def get(self, *args, **kwargs): self.calls += 1; return Response()
        def post(self, *args, **kwargs): self.calls += 1; return Response()

    session = Session()
    rows = list(PortalClient(session).iter_announcements(since=date(2026, 10, 15), max_announcements=2))
    assert rows == [] and session.calls == 2
