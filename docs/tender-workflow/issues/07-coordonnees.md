# 07 : Email, téléphone et qualification fournisseur

**À construire :** adapter FullEnrich pour demander `contact.work_emails` et `contact.phones` depuis l'URL LinkedIn personnelle confirmée. Vérifier l'email professionnel retenu avec No2Bounce ; ne retenir que le téléphone le plus probable dont l'état de ligne est admissible.

**Dépend de :** 06.

**Statut :** terminé.

- [x] Le client de prospection existant conserve son contrat ; la nouvelle variante n'accepte pas d'URL LinkedIn non confirmée.
- [x] Soumissions et identifiants FullEnrich/No2Bounce journalisés ; statut inconnu réconcilié avant toute nouvelle dépense.
- [x] Email personnel ou générique exclu ; un email ou un téléphone qualifié suffit.
- [x] Tests de résultats email seul, téléphone seul, deux, aucun, candidat non probable et appel inconnu.
