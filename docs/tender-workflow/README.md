# Tickets locaux — acquisition d'entreprises via les résultats des marchés

Conception confirmée le 15 octobre 2026 : [parcours cible](../TENDER_COMPANY_WORKFLOW_DESIGN.md). Les tickets sont exécutés **séquentiellement** dans l'ordre numérique. Leur statut est mis à jour après vérification de leurs critères. Aucun ticket ne permet d'envoyer un email.

| Ordre | Ticket | Dépend de | Statut |
| --- | --- | --- | --- |
| 01 | [État technique indépendant et contrat de reprise](issues/01-etat-technique.md) | — | terminé |
| 02 | [Découverte du portail et téléchargements éphémères](issues/02-portail-documents.md) | 01 | terminé |
| 03 | [Extraction visuelle Codex sans OCR](issues/03-vision-codex.md) | 02 | terminé |
| 04 | [Identité entreprise via LinkedIn et Kerix](issues/04-identite-entreprise.md) | 03 | terminé |
| 05 | [Création ou mise à jour de l'entreprise Twenty](issues/05-entreprise-crm.md) | 04 | terminé |
| 06 | [Recherche de deux contacts LinkedIn confirmés](issues/06-contacts-linkedin.md) | 05 | terminé |
| 07 | [Email, téléphone et qualification fournisseur](issues/07-coordonnees.md) | 06 | terminé |
| 08 | [Personnes Twenty et orchestration indépendante](issues/08-personnes-orchestration.md) | 07 | terminé |
| 09 | [Vérification, cadence et pilote contrôlé](issues/09-verification-pilote.md) | 08 | terminé : pilote de cinq PDF, cadence installée |

Un ticket fermé indique son implémentation vérifiée. Le ticket 09 distingue les contrôles et le pilote réels de la simulation ; aucun rapprochement d'entreprise ni écriture CRM n'a été exercé par le pilote, car aucune identité n'a été confirmée dans les cinq PDF traités.
