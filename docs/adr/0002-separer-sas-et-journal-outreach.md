---
status: accepted
---

# Séparer le sas de qualification du journal durable de prospection

Le 7 octobre 2026, l'utilisateur choisit de retirer du sas les contacts après confirmation de leur transfert CRM. Comme l'envoi actuel dépend du sas pour retrouver les preuves et statuts, la cible de refonte conserve ces informations dans un journal indépendant et un JSON de contrôle par contact, avant toute suppression. Le CRM porte les personnes, entreprises et leurs relations ; le journal conserve les identifiants, preuves et opérations nécessaires à la reprise sans doublon. Les archives sont conservées 12 mois, les exclusions d'envoi séparément tant que la prospection reste active.

Ce choix accepte une conservation supplémentaire plutôt que de garder indéfiniment les contacts transférés dans le sas ou de perdre les traces au retrait. La refonte vérifie par relecture la personne, l'entreprise et leur lien avant de supprimer une ligne du sas.

## Politique de qualification associée

La cible est définie dans [le cahier de conception](../../archives/conception/WORKFLOW_REFACTOR_DESIGN.md). LinkedIn confirmé précède FullEnrich ; les nouvelles adresses sont vérifiées par No2Bounce ; délivrable et accept-all/catch-all sont admis avec verdict réel conservé ; preuves valables 90 jours ; aucun plafond applicatif de crédits. Ces choix remplacent les règles du pilote consignées dans ADR-0001. Cette décision décrit la cible acceptée, pas une implémentation déjà active.
