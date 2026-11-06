---
status: superseded by ADR-0002
---

# Vérifier l'email existant avant de rechercher un remplacement

Le 6 octobre 2026, l'utilisateur décide de vérifier les adresses existantes et de conserver celles déclarées délivrables ; l'enrichissement email est réservé aux adresses absentes ou non délivrables, afin de ne pas payer une redécouverte inutile. Pour ces enrichissements, prénom, nom et entreprise sont utilisés directement, sans recherche préalable obligatoire de domaine ou de LinkedIn ; les colonnes prénom et nom sont déjà séparées dans les données concernées. La vérification d'une adresse existante constitue donc une étape distincte de FullEnrich ; No2Bounce est choisi pour cette vérification. Les résultats catch-all/unknown et les erreurs techniques restent en attente sans envoi ni appel automatique FullEnrich ; les nouveaux résultats FullEnrich DELIVERABLE ne sont pas revérifiés immédiatement par No2Bounce. Un verdict positif est réutilisable pendant 90 jours pour la même adresse, puis une vérification No2Bounce est requise avant envoi sous réserve du quota. Le pilote est limité à trois contacts, trois adresses vérifiées par No2Bounce et trois enrichissements FullEnrich au maximum ; Harvest est désactivé pendant ce pilote et chaque étape est validée avant la suivante.
