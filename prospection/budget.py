"""Réservations transactionnelles des plafonds de facturation Apify."""
import json
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import urlencode

PLAFOND = Decimal("3")


def montant(valeur):
    try:
        chiffre = Decimal(str(valeur))
    except InvalidOperation as erreur:
        raise ValueError("Montant USD invalide") from erreur
    if not chiffre.is_finite() or chiffre < 0:
        raise ValueError("Montant USD invalide")
    return chiffre


class BudgetInsuffisant(RuntimeError):
    pass


class Budget:
    def __init__(self, db, apify, plafond):
        self.db, self.apify, self.plafond = db, apify, montant(plafond)
        if not 0 < self.plafond <= PLAFOND:
            raise ValueError("Le plafond du pilote doit être compris entre 0 et 3 USD")
        db.execute("""CREATE TABLE IF NOT EXISTS budget_runs (
            cle TEXT PRIMARY KEY, acteur TEXT, reserve TEXT, depense TEXT, run TEXT)""")
        db.execute("CREATE TABLE IF NOT EXISTS budget_plafond (valeur TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        with db:
            lignes = db.execute("SELECT valeur FROM budget_plafond").fetchall()
            if lignes and montant(lignes[0][0]) != self.plafond:
                raise ValueError("Plafond différent du plafond enregistré")
            if not lignes:
                db.execute("INSERT INTO budget_plafond VALUES (?)", (str(self.plafond),))

    def bilan(self):
        details = {}
        for acteur, reserve, depense in self.db.execute("SELECT acteur,reserve,depense FROM budget_runs"):
            valide, engage = details.get(acteur, (Decimal(0), Decimal(0)))
            details[acteur] = (valide + montant(depense or 0),
                               engage + (montant(reserve) if depense is None else Decimal(0)))
        return {acteur: {"depense": str(depense), "engage": str(engage)}
                for acteur, (depense, engage) in details.items()}

    def lignes(self, acteur, entree, maximum, *, verification=0, max_items=None):
        maximum, verification = montant(maximum), montant(verification)
        if maximum == 0:
            raise ValueError("Le plafond par lot doit être positif")
        cle = sha256(json.dumps([acteur, entree, str(maximum), max_items], sort_keys=True).encode()).hexdigest()
        precedent = self.reserver(cle, acteur, maximum, verification)
        if precedent:
            run = self.apify.appeler("actor-runs/" + precedent[0])["data"]
        else:
            try:
                data = self.apify.appeler("acts/" + acteur)["data"]
                modeles = data["pricingInfos"]
                modele = modeles[-1]["pricingModel"]
            except (OSError, KeyError, IndexError):
                self.annuler(cle)
                raise
            params = {"timeout": 600}
            if modele == "PAY_PER_EVENT":
                params["maxTotalChargeUsd"] = str(maximum)
            elif modele == "PRICE_PER_DATASET_ITEM" and max_items:
                if montant(modeles[-1]["pricePerUnitUsd"]) * max_items > maximum:
                    self.annuler(cle)
                    raise BudgetInsuffisant(f"Prix par résultat supérieur à la réservation pour {acteur}")
                params["maxItems"] = max_items
            else:
                self.annuler(cle)
                raise BudgetInsuffisant(f"Facturation sans plafond vérifiable pour {acteur}")
            try:
                run = self.apify.appeler(f"acts/{acteur}/runs?{urlencode(params)}", entree)["data"]
            except HTTPError as erreur:
                if erreur.code in {400, 401, 403, 404, 422}:
                    self.annuler(cle)
                raise
            with self.db:
                self.db.execute("UPDATE budget_runs SET run=? WHERE cle=?", (run["id"], cle))
        run = self.apify.attendre(run)
        montant_confirme = run.get("usageTotalUsd")
        if montant_confirme is not None:
            depense = montant(montant_confirme)
            with self.db:
                self.db.execute("UPDATE budget_runs SET depense=? WHERE cle=?", (str(depense), cle))
            if depense > maximum:
                raise BudgetInsuffisant(f"Facturation supérieure au plafond demandé pour {acteur}")
        if run["status"] != "SUCCEEDED":
            raise RuntimeError(f"Lot {acteur} terminé avec le statut {run['status']}")
        dataset = run["defaultDatasetId"]
        return list(self.apify.dataset(dataset)), {"run": run["id"], "dataset": dataset,
                                                   "date": run.get("finishedAt")}

    def solde(self):
        total = sum(montant(reserve) for reserve, in self.db.execute("SELECT reserve FROM budget_runs"))
        return self.plafond - total

    def reserver(self, cle, acteur, maximum, verification=0):
        maximum, verification = montant(maximum), montant(verification)
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            precedent = self.db.execute("SELECT run FROM budget_runs WHERE cle=?", (cle,)).fetchone()
            if precedent:
                if not precedent[0]:
                    raise RuntimeError("Lancement incertain, réservation conservée")
                return precedent
            if maximum + verification > self.solde():
                raise BudgetInsuffisant(f"Solde insuffisant pour {acteur}")
            self.db.execute("INSERT INTO budget_runs VALUES (?,?,?,NULL,NULL)", (cle, acteur, str(maximum)))

    def annuler(self, cle):
        with self.db:
            self.db.execute("DELETE FROM budget_runs WHERE cle=? AND run IS NULL", (cle,))
