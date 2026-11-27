import pytest
from prospection import angles


def test_ordre_et_signal_manquant(monkeypatch):
    premiers = {"cible":"Cabinet","interet":"Délai","reponse":"Répondre",
                "techniques":[{"type":"observation","signal":"Ouverture","lien_offre":"Installation"},
                              {"type":"choix","question":"Quand ?","option_a":"Mars","option_b":"Avril"}]}
    seconds = {**premiers,"cible":"Toutes entreprises"}
    angles.valider({"angles":[premiers, seconds]})
    possibles = [{"angle":1,"technique":1,"fait":-1},{"angle":0,"technique":1,"fait":-1},
                 {"angle":0,"technique":0,"fait":0}]
    monkeypatch.setattr(angles,"decision",lambda *args: {"possibles":possibles,"motif":"Signal absent"})
    choix, _ = angles.choisir([premiers, seconds],{})
    assert (choix["priorite"], choix["technique"]["type"]) == (1,"choix")
    del possibles[:2]
    assert angles.choisir([premiers, seconds],{}) == (None,"Signal absent")


@pytest.mark.parametrize("brief",[{},{"angles":[]},{"angles":[{"cible":"Entreprise"}]}])
def test_brief_incomplet(brief):
    with pytest.raises(ValueError):
        angles.valider(brief)
