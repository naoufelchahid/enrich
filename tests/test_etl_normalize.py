import os
import pytest
from scripts.etl_normalize import (
    is_dirigeant_title,
    normalize_phone,
    split_name,
    fix_mojibake,
    parse_bdd1,
    parse_bdd2,
    parse_bdd3_global,
    parse_bdd3_2023,
    parse_bdd4,
    parse_bdd5,
    parse_bdd6,
    parse_all_sources,
)
from scripts.models import NormalizedContact, EnrichmentStatusEnum, EmailValidationStatusEnum


def test_is_dirigeant_detection():
    # Direct requirements from brief
    assert is_dirigeant_title("Directeur Général") is True
    assert is_dirigeant_title("P.D.G.") is True
    assert is_dirigeant_title("Gérant") is True
    assert is_dirigeant_title("Administrateur Délégué") is True
    assert is_dirigeant_title("Managing Director") is True
    assert is_dirigeant_title("Stagiaire") is False
    assert is_dirigeant_title("Chef de projet informatique") is False
    assert is_dirigeant_title(None) is False

    # Additional executive titles and edge cases
    assert is_dirigeant_title("DG") is True
    assert is_dirigeant_title("CEO") is True
    assert is_dirigeant_title("President") is True
    assert is_dirigeant_title("Présidente") is True
    assert is_dirigeant_title("Fondateur") is True
    assert is_dirigeant_title("Co-fondatrice") is True
    assert is_dirigeant_title("Directeur Associé") is True
    assert is_dirigeant_title("Country Manager") is True
    assert is_dirigeant_title("Chairman") is True
    assert is_dirigeant_title("Dirigeant") is True
    assert is_dirigeant_title("Propriétaire") is True
    assert is_dirigeant_title("Associé gérant") is True
    assert is_dirigeant_title("Administr. Délégué") is True
    assert is_dirigeant_title("GÃƒÂ©rant") is True  # mojibake handling
    assert is_dirigeant_title("President Directeur GÃƒÂ©nÃƒÂ©ral") is True

    # Non-executive titles
    assert is_dirigeant_title("Directeur Commercial") is False
    assert is_dirigeant_title("Directeur Financier") is False
    assert is_dirigeant_title("Responsable Marketing") is False
    assert is_dirigeant_title("Consultant technique") is False
    assert is_dirigeant_title("") is False


def test_normalize_phone():
    # 05 = standard fixe, 06/07 = mobile
    fixe, mobile = normalize_phone("05 22 24 53 29")
    assert fixe == "+212522245329"
    assert mobile is None

    fixe, mobile = normalize_phone("06 61 48 47 22")
    assert fixe is None
    assert mobile == "+212661484722"

    # Mobile with 07
    fixe, mobile = normalize_phone("07 00 11 22 33")
    assert fixe is None
    assert mobile == "+212700112233"

    # International prefixes
    fixe, mobile = normalize_phone("+212 522 24 53 29")
    assert fixe == "+212522245329"
    assert mobile is None

    fixe, mobile = normalize_phone("+212 661 48 47 22")
    assert fixe is None
    assert mobile == "+212661484722"

    # Float representation from Excel
    fixe, mobile = normalize_phone("520484603.0")
    assert fixe == "+212520484603"
    assert mobile is None

    fixe, mobile = normalize_phone(660312424.0)
    assert fixe is None
    assert mobile == "+212660312424"

    # Multi-phone slash strings (takes first valid)
    fixe, mobile = normalize_phone("0661427135/0661168912")
    assert fixe is None
    assert mobile == "+212661427135"

    # Empty / None
    assert normalize_phone(None) == (None, None)
    assert normalize_phone("") == (None, None)


def test_split_name():
    # Direct requirements from brief
    civ, first, last = split_name("M.  Karim  Belaous Abid")
    assert civ == "M."
    assert first == "Karim"
    assert last == "Belaous Abid"

    # Mme civility
    civ, first, last = split_name("Mme. Karima Belaous Abid")
    assert civ == "Mme."
    assert first == "Karima"
    assert last == "Belaous Abid"

    # No civility
    civ, first, last = split_name("Karim Belaous")
    assert civ is None
    assert first == "Karim"
    assert last == "Belaous"

    # Single name
    civ, first, last = split_name("Bennis")
    assert civ is None
    assert first == "Bennis"
    assert last == ""

    # None and empty
    assert split_name(None) == (None, None, None)
    assert split_name("") == (None, None, None)


def test_fix_mojibake():
    assert fix_mojibake("GÃƒÂ©rant") == "Gérant"
    assert fix_mojibake("President Directeur GÃƒÂ©nÃƒÂ©ral") == "President Directeur Général"
    assert fix_mojibake("Simple Text") == "Simple Text"
    assert fix_mojibake(None) is None


@pytest.mark.skipif(not os.path.exists("bdd/BDD1.xlsx"), reason="BDD files not available")
def test_parse_bdd1():
    contacts = parse_bdd1("bdd/BDD1.xlsx")
    assert len(contacts) > 1000
    for c in contacts[:10]:
        assert isinstance(c, NormalizedContact)
        assert c.source_file == "BDD1"
        assert c.company_name != ""


@pytest.mark.skipif(not os.path.exists("bdd/BDD2.xlsx"), reason="BDD files not available")
def test_parse_bdd2():
    contacts = parse_bdd2("bdd/BDD2.xlsx")
    assert len(contacts) > 1000
    # Verify executive flags
    dirigeants = [c for c in contacts if c.is_dirigeant]
    assert len(dirigeants) > 500


@pytest.mark.skipif(not os.path.exists("bdd/BDD3.xls"), reason="BDD files not available")
def test_parse_bdd3():
    contacts_global = parse_bdd3_global("bdd/BDD3.xls")
    assert len(contacts_global) > 500
    contacts_2023 = parse_bdd3_2023("bdd/BDD3.xls")
    assert len(contacts_2023) > 1000


@pytest.mark.skipif(not os.path.exists("bdd/BDD4.xlsx"), reason="BDD files not available")
def test_parse_bdd4():
    contacts = parse_bdd4("bdd/BDD4.xlsx")
    assert len(contacts) > 400
    assert any(c.city == "Tanger" for c in contacts)


@pytest.mark.skipif(not os.path.exists("bdd/BDD5.xlsx"), reason="BDD files not available")
def test_parse_bdd5():
    contacts = parse_bdd5("bdd/BDD5.xlsx")
    assert len(contacts) > 10000
    assert any(c.is_dirigeant for c in contacts)


@pytest.mark.skipif(not os.path.exists("bdd/BDD6.xls"), reason="BDD files not available")
def test_parse_bdd6():
    contacts = parse_bdd6("bdd/BDD6.xls")
    assert len(contacts) > 500


@pytest.mark.skipif(not os.path.exists("bdd"), reason="BDD directory not available")
def test_parse_all_sources_integration():
    all_contacts = parse_all_sources("bdd")
    assert len(all_contacts) > 15000
    # Check that is_dirigeant is identified
    dirigeants = [c for c in all_contacts if c.is_dirigeant]
    assert len(dirigeants) > 3000
    # Check missing fields listing
    incomplete = [c for c in all_contacts if len(c.missing_fields) > 0]
    assert len(incomplete) > 0
