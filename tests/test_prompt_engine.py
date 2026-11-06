"""The initial copy is frozen V2/V5; the free writer handles replies only."""
import pytest
from scripts.prompt_engine import draft_workflow_email, validate_email_copy, workflow_writing_instructions
from scripts.email_patterns import formal_salutation, PILOT_PATTERNS


def test_initial_cannot_use_the_free_reply_writer(tmp_path):
    with pytest.raises(ValueError,match='INITIAL_REQUIRES_REVIEWED_V2_V5'):
        draft_workflow_email({'last_name':'Tazi','civility':'Mme'},runner=lambda *a,**kw:pytest.fail('no worker'),output_dir=tmp_path)
    assert set(PILOT_PATTERNS)=={'V2','V5'}


@pytest.mark.parametrize('subject,body',[('Re: précision','Merci.'),('Re: une question qui nécessite un objet plus long','mot '*120)])
def test_reply_has_no_retired_editorial_word_limits(subject,body):
    assert validate_email_copy(subject,body)==(True,[])


@pytest.mark.parametrize('subject,body',[('Re: test','<p>Texte</p>'),('objet\ninjecté','Texte'),('','Texte'),('Objet','')])
def test_reply_format_rejects_html_empty_content_and_header_injection(subject,body):
    assert not validate_email_copy(subject,body)[0]


def test_reply_writer_requires_formal_salutation(tmp_path):
    with pytest.raises(ValueError,match='FORMAL_SALUTATION_REQUIRED'):
        draft_workflow_email({'last_name':'Tazi','civility':'Mme'},reply={'subject':'Précision'},
            runner=lambda *a,**kw:{'subject':'Re: Précision','body':'Bonjour Alice,\nMerci.'},output_dir=tmp_path)


def test_reply_writer_uses_skills_and_preserves_operator_input(tmp_path):
    def runner(instructions,data,schema,**kwargs):
        assert 'Cold Email Writing' in instructions and 'Marketing Psychology' in instructions
        assert 'five' not in instructions.lower().split('project decisions override general writing advice.')[1]
        assert data['operator_directive']=='Préciser le format'
        return {'subject':'Re: Précision','body':data['required_salutation']+'\n\nMerci pour votre retour.'}
    result=draft_workflow_email({'last_name':'Tazi','civility':'Mme'},reply={'subject':'Précision'},
        directive='Préciser le format',runner=runner,output_dir=tmp_path)
    assert result['body'].startswith('Bonjour Madame Tazi,')


def test_formal_salutation_uses_a_confirmed_identity():
    assert formal_salutation(last_name='Tazi',civility='Mme')=='Bonjour Madame Tazi,'
    with pytest.raises(ValueError,match='CIVILITY_REVIEW_REQUIRED'):
        formal_salutation(last_name='Tazi')
