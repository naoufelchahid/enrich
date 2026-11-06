> Guide historique/pilotes. Pour le workflow actif implémenté le 7 octobre 2026, ses règles d’admission et ses cadences, lire [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md).

# Ordre du pipeline : vérification → LinkedIn → FullEnrich

Décision utilisateur du 7 octobre 2026 : rechercher et confirmer l’URL LinkedIn personnelle avant toute nouvelle demande FullEnrich. L’identité de la nouvelle demande est exclusivement cette URL ; aucun fallback prénom/nom/entreprise/domaine.

## Parcours

1. Email présent : vérification No2Bounce ou réutilisation d’une preuve exacte encore valable (90 jours). Délivrable : pas de recherche email FullEnrich. Accept-all/inconnu/erreur : attente, pas de FullEnrich.
2. Email absent ou définitivement INVALID pour l’adresse actuelle : URL LinkedIn obligatoire. Le worker MCP confirme le profil et fournit un JSON importé dans le SAS. Une simple URL saisie ou un ancien drapeau ne suffit pas.
3. Le journal d’import doit attester CONFIRMED pour cette URL et l’identité source actuelle (lead, nom, prénom, société, source), avec provenance linkedin_mcp ou preuve historique codex_chrome et date correspondante. Toute identité modifiée impose une nouvelle revue.
4. FullEnrich reçoit seulement linkedin_url, enrich_fields=[contact.work_emails] et custom.request_key. Demande nommée, lots plafonnés à 100, réservations avant POST et quotas existants.
5. Email obtenu : seul DELIVERABLE plus hygiène locale PASS donne une preuve positive. HIGH_PROBABILITY/catch-all restent WAITING.

Contrat fournisseur vérifié : [FullEnrich Start Bulk Enrichment](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/post), rubrique Input needed : linkedin_url peut constituer à elle seule l’identité requise. Cela ne garantit ni l’identité du profil ni la délivrabilité de l’email ; ces deux validations restent distinctes.

## Attente et reprise

Sans URL confirmée, contact_discovery_status=WAITING_LINKEDIN. Aucun POST ni GET de solde FullEnrich pour ces contacts. La phase enrich retourne les tâches MCP disponibles ; elle ne lance pas l'extraction automatiquement. Après import, relancer manuellement enrich avec le budget explicitement choisi. Le mode manuel, un contact à la fois, reste actif.

Les NO_MATCH antérieurs restent NO_MATCH : un changement de méthode ne repaye pas automatiquement des recherches vides. Réconcilier explicitement les cas historiques avant nouvelle tentative. Les jobs déjà SUBMITTED/UNKNOWN conservent journal et identité d’origine ; récupérer leur résultat ne justifie jamais une seconde soumission.

Les données de domaines restent dans le SAS pour d’autres usages. Elles ne sont plus envoyées dans les nouvelles demandes email par URL. Le helper contact_input conserve les anciennes identités seulement pour la compatibilité des caches/jobs historiques.

## Validation

Tests offline : blocage URL seule non confirmée/attestation manquante/identité modifiée, libération après import valide, charge utile URL-only, maintien des règles email, absence de relance, rejet d’un résultat après changement d’URL, récupération d’un ancien job nom/entreprise sans resoumission. Aucun appel fournisseur réel pour ce changement.
