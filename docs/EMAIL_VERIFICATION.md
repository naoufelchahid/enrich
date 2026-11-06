> Guide historique/pilotes. Pour le workflow actif implémenté le 7 octobre 2026, ses règles d’admission et ses cadences, lire [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md).

# Vérification avant enrichissement — étape 2

Le routage validé dans [ADR-0001](adr/0001-verifier-avant-enrichissement.md) est implémenté et testé hors ligne. Aucun appel payant n'est nécessaire à ces tests. Le format terminal CSV a été confirmé sur deux jobs existants du compte le 6 octobre 2026 ; les quotas de nouvelles soumissions restent à zéro par défaut.

## Règles

| Situation | Action |
| --- | --- |
| Adresse présente sans preuve positive récente | Hygiène puis No2Bounce |
| Verdict positif pour la même adresse, datant d'au plus 90 jours | Réutilisation ; aucun enrichissement email |
| Adresse absente ou explicitement invalide | FullEnrich avec prénom, nom et entreprise |
| Catch-all, inconnu, erreur technique, DNS temporairement indisponible | Attente sans envoi ni enrichissement automatique |
| Nouveau résultat FullEnrich DELIVERABLE et hygiène favorable | Validation datée, sans seconde vérification immédiate |
| Preuve expirée | Nouvelle vérification No2Bounce sous quota, jamais une nouvelle date sur l'ancien cache |
| Désinscription ou rebond enregistré | Exclusion durable même après réimport ou reset du coupe-circuit |

L'hygiène vérifie une syntaxe ASCII conservatrice, une liste locale limitée de domaines jetables et les MX. Un résultat favorable ne prouve jamais la délivrabilité. Les syntaxes non prises en charge, les domaines jetables et les erreurs DNS sont mis en attente. Un domaine NXDOMAIN ou un MX nul explicite est invalide ; l'absence de MX seule reste indéterminée. Les réponses DNS ne sont réutilisées que pendant une invocation.

`original_email` conserve l'adresse observée au premier import ou à la migration. `raw_email` reste l'adresse courante pour compatibilité ; un remplacement FullEnrich ne détruit plus la copie originale. Le journal conserve les réponses et leurs liens avec les contacts. Les dates de preuve proviennent du job d'origine, y compris lors d'une réutilisation du cache ; cette datation conservatrice n'allonge pas la validité après un polling tardif.

Une adresse partagée par plusieurs lignes est vérifiée une seule fois par génération de cache. Son verdict est rattaché aux lignes dont l'adresse n'a pas changé. Cela ne prouve pas que la boîte appartient personnellement à chaque contact ; la file d'envoi continue de dédupliquer les destinataires.

## No2Bounce : contrat observé et limites

La [documentation publique](https://www.no2bounce.com/api-documentation) décrit POST `emailList`, header `apitoken`, puis GET avec `trackingId`. Le GET réel du compte diffère : objet racine avec `trackingId`, `overallStatus: Completed`, `percent: 100`, `totalRecord` et `result.downloadFile`, même pour vingt lignes. Le CSV contient `email,finalScore,finalScoreValue,catchall`.

Le lecteur vérifie le job, la fin du traitement, le nombre de lignes et l'unicité des adresses. Il télécharge uniquement depuis l'hôte HTTPS S3 observé, sans redirection ni clé API, avec un plafond de 2 Mo. Le journal conserve les lignes et leur empreinte ; la signature temporaire est masquée. Les clés de résultat doivent ensuite correspondre exactement aux adresses soumises. Aucune preuve positive ne vient des compteurs ou du score numérique seul.

| CSV exact | Verdict interne |
| --- | --- |
| `Deliverable` et `catchall=false` | `VALID` |
| `UnDeliverable` et `catchall=false` | `INVALID` |
| Toute mention AcceptAll, valeur inconnue ou contradiction | `WAITING` |

Les anciens résultats présentent des doublons : ils sont refusés par le lecteur automatique. Leur éventuelle reprise exige une importation locale contrôlée, avec concordance des répétitions, adresse exacte et date du job conservée. Les trois preuves du pilote ont été importées localement après validation utilisateur ; voir [le compte rendu](../archives/essais/pilot-proof-import-2026-10-06/VALIDATION.md).

Les fixtures `tests/fixtures/no2bounce/observed-duplicates.*` reproduisent les réponses observées après anonymisation. `completed.*` est un cas dérivé, dédupliqué pour tester les nouveaux lots. Le transport conserve la lecture des compteurs de progression documentés, mais tout autre format non reconnu reste bloquant ; pas de relance payante. Voir [l'investigation](../archives/essais/no2bounce-contract-2026-10-06/INVESTIGATION.md).

## Quotas et pilote

`NO2BOUNCE_DAILY_ADDRESSES` vaut zéro par défaut. Il compte les adresses soumises, pas un montant en euros ni un tarif supposé en crédits. FullEnrich conserve son quota de crédits et Harvest son quota de requêtes.

La table `enrichment_limits` impose en plus les plafonds cumulés du pilote : trois adresses No2Bounce, trois contacts FullEnrich, zéro requête Harvest. Ils persistent dans la base, même sans réutiliser le flag `--pilot`, après minuit et après redémarrage. Une erreur ambiguë conserve sa réservation et n'est jamais resoumise automatiquement. Un résultat FullEnrich gratuit ne restitue pas une place du pilote.

Préparer une base isolée depuis la sélection validée :

```bash
python3 -m scripts.prepare_pilot --selection bdd/pilot/selection.json --db-path bdd/pilot/staging.sqlite
```

Cette commande refuse une destination existante, copie uniquement les champs utiles, n'importe aucun ID CRM ni preuve ancienne et ne contacte aucun service. La création des plafonds précède toute commande d'enrichissement.

Aperçus, sans réseau ni modification du staging :

```bash
bash scripts/cron_agent_dispatcher.sh --task enrich --db-path bdd/pilot/staging.sqlite --pilot --phase verify --dry-run
bash scripts/cron_agent_dispatcher.sh --task enrich --db-path bdd/pilot/staging.sqlite --pilot --phase enrich --dry-run
```

Sur une base pilote, la phase par défaut est `verify` ; `all` est refusée. La phase `enrich` est exécutée séparément après validation des résultats No2Bounce. Retirer `--dry-run` peut dépenser une fois les quotas configurés : cette action appartient à l'étape suivante, pas à la validation hors ligne.

Le coupe-circuit bloque désormais un état illisible. Une erreur de lecture de boîte bloque également l'envoi. Les suppressions sont enregistrées avant archivage du message, pour rester effectives même si son déplacement échoue. L'expiration de la preuve et l'hygiène sont contrôlées à nouveau avant envoi.

## Tests

```bash
python3 -m pytest tests/test_verification_pipeline.py tests/test_email_hygiene.py tests/test_direct_outreach_safety.py -q
python3 -m pytest tests -q
```

Le réseau réel est bloqué dans la suite de tests ; les échanges fournisseurs et DNS sont simulés. Les tests DNS vérifient séparément les erreurs temporaires, NXDOMAIN et MX nul.

## Reprise locale de preuves historiques

`scripts/import_verification_proof.py` importe un manifeste revu et ses fichiers locaux dans une base pilote existante. Aucune connexion réseau ni migration de base principale. Le journal `local_email_proofs` conserve la provenance séparément des jobs facturables : trois preuves importées ne créent pas trois nouvelles soumissions et ne consomment pas de quota. Les statuts du contact et leur date initiale gouvernent ensuite la réutilisation et l’expiration. Les anciens CSV doivent être archivés localement avant expiration des liens du fournisseur.
