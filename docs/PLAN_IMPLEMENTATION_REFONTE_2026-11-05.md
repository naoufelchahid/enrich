> Référence datée. Pour les règles de mailing applicables depuis le 6 novembre 2026, lire [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md). Les anciennes règles de mailing sont retirées.

# Plan d'implémentation — refonte Enrich

Statut : plan initial issu du cadrage Q30 validé dans la conversation (« je valide »). Son découpage a ensuite été décliné en quinze tickets approuvés. L'exécution par sous-agents est suivie dans le [rapport d'exécution](REFONTE_EXECUTION_2026-11-05.md) : code testé localement pour 01–14, préparation de la bascule pour 15, sans migration réelle. Les dix étapes T01–T10 ci-dessous ne sont pas les identifiants des quinze tickets. Sources : [conception](REFONTE_WORKFLOWS_2026-11-05.md), [ADR-0004](adr/0004-trois-workflows-et-journal-compact.md), [textes validés](COLD_EMAIL_RESEARCH_2026-11-05.md), [exploitation](WORKFLOW_OPERATIONS.md).

## Résultat attendu

Trois parcours autonomes — Apify, LinkedIn, campagnes — partagent un journal technique compact. Twenty reste le référentiel des contacts admis. Les candidats en attente restent récupérables. Telegram pilote briefs, statuts et approbations ; les rendez-vous restent gérés manuellement. Aucun changement de la pause technique, des crons ou des transports ne découle de la validation du cadrage.

Le pilote mailing réutilise jusqu'à 20 contacts admissibles de Twenty, tous secteurs, et compare les messages complets V2/V5. L'acquisition suivante vise 20 nouveaux contacts admissibles au total au Maroc ; ce sont deux lots distincts.

## Ordre et dépendances

T01 → T02 → T03 → T04 → T05 → T06/T07 → T08 → T09 → T10.

T02 prépare l'outil de migration ; T03 prépare le stockage cible et sa reprise. Aucune bascule réelle avant T10. Les travaux locaux utilisent des bases temporaires et des fournisseurs simulés. Une progression plus parallèle des adaptateurs n'est utile qu'après stabilisation des contrats communs.

| Ticket | Livrable | Preuve de fin |
|---|---|---|
| T01 | Arrêt de l'amplification du journal | Initialisations répétées et mises à jour sans changement sans nouveaux événements |
| T02 | Préparation de migration vérifiable | Audit en lecture seule, simulation, sauvegarde restaurée et comparaison exacte |
| T03 | Stockage technique unique et compact | Candidats, opérations, preuves et approbations récupérables après redémarrage |
| T04 | Trois entrées opérationnelles indépendantes | Panne d'un service sans effet sur les parcours qui n'en dépendent pas |
| T05 | Briefs, plafonds et réservation des coûts | Aucun appel payant sans limites ; cumul des réservations respecté |
| T06 | Adaptateur Apify | Soumission unique, reprise et ingestion paginée sans doublons |
| T07 | Acquisition LinkedIn et choix du contact | Découverte selon contrat réel, confirmation nominative et worker unique |
| T08 | Campagnes V2/V5 et spécialistes | Textes exacts, affectations persistantes, revue indépendante, envoi toujours bloqué au test |
| T09 | Pilotage Telegram | Accès autorisé, commandes rejouées sans double opération, silences conservés |
| T10 | Répétition complète et dossier de bascule | Restauration, contrôles de conservation, retour arrière et rapport avant/après |

## T01 — Corriger la croissance avant toute compaction

Points d'entrée : `scripts/direct_enrichment.py`, `scripts/outreach_journal.py`, `tests/test_outreach_journal.py`.

- Restreindre l'initialisation qui remet `is_enriched_linkedin` à zéro aux lignes dont la valeur doit effectivement changer ; définir explicitement le cas NULL.
- Ajouter au trigger UPDATE une comparaison des colonnes avec une sémantique correcte pour NULL. Un vrai changement reste journalisé ; un UPDATE identique ne produit pas de snapshot.
- Conserver l'identité durable, sa règle d'immutabilité, les snapshots de suppression et la compatibilité des schémas larges.
- Ne pas purger l'outbox automatiquement dans ce ticket.

Vérifier : deux initialisations sur une fixture stable ; UPDATE identique ; transition NULL/valeur ; changement réel ; suppression ; rollback ; redémarrage avant export et après commit de l'archive. Rejouer les tests concernés, puis élargir uniquement en cas de dépendance affectée.

## T02 — Préparer la migration et prouver les doublons

Points d'entrée : `scripts/outreach_journal.py`, `scripts/workflow_backup.py` ; outil dédié proposé `scripts/workflow_migration.py`.

- Inspection des schémas et opérations en lecture seule, par lots ; éviter de charger les historiques de plusieurs Go en mémoire.
- Produire un manifeste : chemins, version de schéma, identités sources, comptes par état, jobs soumis/inconnus, exclusions, approbations, artefacts et espace disponible.
- Comparer chaque événement exporté à son homologue par `source_id/event_id`, type, contact, contenu exact et date. Un drapeau exporté ou un nombre égal ne justifie aucune suppression.
- Préparer une sauvegarde cohérente des deux bases sous arrêt des écritures. Restaurer vers une copie distincte, vérifier intégrité SQLite et contenu.
- Prévoir simulation sans mutation ; toute divergence bloque la purge. Une archive déjà présente ne constitue pas à elle seule une sauvegarde fraîche valide.
- Préserver les snapshots historiques exacts, compressés si nécessaire ; ne pas dédupliquer des faits temporels distincts au seul motif que leur contenu se ressemble.

Preuves : événement absent ou divergent bloque ; panne entre étapes et reprise ; restauration vérifiée. Le potentiel d'environ 4,59 Go est une mesure historique, pas un volume garanti à libérer.

## T03 — Un seul stockage opérationnel

Points d'entrée : `workflow_store.py`, `staging_db.py`, `direct_enrichment.py`, `linkedin_queue.py`, `workflow_files.py`, `crm_transfer.py`.

- S'appuyer sur le journal existant et ses tables `workflow_*` plutôt que créer une troisième copie permanente.
- Introduire une représentation minimale des candidats et des tâches. Conserver la correspondance des anciens identifiants et des identités durables.
- Garder opérations, résultats exacts, exclusions, approbations, réservations d'envoi et références aux preuves ; stocker les blobs identiques une seule fois.
- Identifier tous les lecteurs/écrivains de l'ancien sas, y compris les imports et tâches de maintenance. Fournir une transition testable avec une seule autorité d'écriture à chaque étape.
- Préserver les URI d'artefacts et leur SHA-256. Les profils CRM ne sont pas recopiés intégralement à chaque transition.
- Répéter sur fixtures et copie contrôlée avant toute bascule. Aucun retrait du sas tant qu'un lecteur actif ou une opération non réconciliée dépend encore de lui.

Preuves : reprise d'un job SUBMITTED sans nouvelle soumission ; résultat inconnu conservé ; identité et exclusion retrouvées ; brouillon approuvé inchangé ; preuve restaurée ; rollback vers l'état cohérent pré-bascule. Après reprise d'écritures nouvelles, interdire un retour vers une sauvegarde ancienne sans réconciliation de ces écritures.

## T04 — Séparer les trois workflows

Points d'entrée : `workflow.py`, `cron_agent_dispatcher.sh`, imports, file LinkedIn et sender existants.

- Trois services d'orchestration et trois entrées explicites ; conserver un adaptateur de compatibilité pour les commandes existantes pendant la transition.
- Initialiser les clients à la demande. La disponibilité est évaluée pour l'action demandée, pas globalement sur tous les fournisseurs.
- Réutiliser qualification, dédoublonnage, import Twenty et exclusions.
- Préserver les verrous nécessaires aux mutations, au worker LinkedIn et aux créneaux globaux d'envoi, tout en laissant les tâches indépendantes avancer.

Preuves : Thunderbird absent et acquisition fonctionnelle ; LinkedIn bloqué et brouillons de contacts admis toujours possibles ; arrêt/reprise d'un seul parcours ; doubles consommateurs sans double application.

## T05 — Briefs et plafonds

Un brief versionné porte cible, zone, recherches proposées, objectif admissible, quantités maximales, plafonds monétaires par campagne/fournisseur, devise, base de calcul et conditions d'arrêt.

- Objectif atteint, quota ou budget atteint : arrêt explicite, sans assouplir l'admission.
- Coût non évaluable ou plafond absent : aucun appel payant ; afficher le renseignement manquant.
- Réserver le coût avant soumission et rapprocher le coût réel ensuite. Conserver la réserve d'une opération incertaine jusqu'à réconciliation.
- Distinguer crédits fournisseurs, argent et quantités ; aucun taux de conversion inventé.
- Cumuler tous les runs d'une campagne, pas seulement le plafond Apify d'un run.

Preuves : deux tâches simultanées ne dépensent pas le même solde ; relance d'une commande sans nouvelle réservation ; échec certain et résultat ambigu traités distinctement.

## T06 — Acquisition Apify

Réutiliser l'import local comme chemin d'ingestion et ajouter l'orchestration de `compass/crawler-google-places` derrière un adaptateur.

- Préparer une liste finie de recherches métier pour le Maroc ; « tous secteurs » ne promet pas un inventaire exhaustif.
- Vérifier le contrat et les prix du compte lors de la préparation du brief.
- Conserver run/dataset, entrée versionnée et curseur de pagination. Réconcilier une soumission ambiguë ; ne jamais relancer aveuglément.
- Une entreprise trouvée ne constitue pas une personne contactable. Diriger les comptes sans interlocuteur identifié vers une tâche de découverte.

Preuves : pages répétées ou notification dupliquée sans doublons ; interruption au milieu du dataset ; plafond par terme et plafond global ; résultat partiel conservé.

## T07 — Acquisition LinkedIn

- Distinguer découverte d'interlocuteurs et confirmation d'une identité nominative.
- Inspecter le contrat réel de `search_people` et `get_person_profile` avant de concevoir les requêtes de découverte. S'il ne permet pas le besoin, conserver une attente explicite sans contournement silencieux.
- Un seul worker MCP et appels séquentiels ; aucune interaction sociale.
- Choisir le contact le mieux placé parmi les disponibles, en motivant le choix par des faits. L'homonymie ou l'identité incertaine restent en revue.
- Vérifier l'adresse source avant enrichissement payant. FullEnrich selon règles actives, puis No2Bounce sur la même adresse ; admission mailing seulement avec identité confirmée et verdict VALID récent.

Preuves : aucune recherche inventée à partir d'un nom absent ; homonymie ; contact sans email ; budget épuisé ; profil déjà confirmé réutilisé ; exclusion durable respectée.

## T08 — Campagnes et sous-agents

Points d'entrée : `email_patterns.py`, `prompt_engine.py`, `structured_agent.py`, `telegram_approval.py`, `workflow_sender.py`.

- Ajouter V2/V5 comme modèles identifiés et versionnés, sans altérer le contenu validé ni remplacer silencieusement les anciens modèles.
- Le validateur historique impose 35–85 mots et une question. Pour V2/V5, valider l'égalité au modèle rendu, les substitutions autorisées et le lien exact ; ne pas réécrire le texte pour satisfaire l'ancien validateur ni désactiver globalement les contrôles.
- Le point d'interrogation de l'URL n'est pas une question rédactionnelle. Conserver la conclusion et une seule signature HTML.
- Affectation persistante avant résultats, aussi proche de 10/10 que possible ; chaque prospect reçoit un seul email initial. Si plusieurs contacts d'une entreprise sont retenus, préserver le groupement et signaler la dépendance des observations.
- Critique indépendante avec sortie structurée, sans droit de mutation CRM ni d'envoi. Pour ce pilote figé, le rédacteur remplit le modèle ; il ne produit pas de paraphrase.
- Les spécialistes qualification, stratégie, recherche, rédaction, critique, réponses et analyse reçoivent seulement le dossier nécessaire. L'orchestrateur applique les actions validées ; une erreur de schéma devient une attente explicite.
- Toute réécriture révoque l'approbation. Brouillon, destinataire, objet, corps et signature/version composent l'identité approuvée.
- Suivi manuel des rendez-vous et bilan à 14 jours par envoi ; pas de synchronisation Cal.com. Résultats descriptifs, aucune promotion automatique d'un gagnant.

Preuves : corps V2/V5 exacts ; modification non permise rejetée ; civilité inconnue en revue ; callback périmé inactif ; signature unique ; exclusion ou fraîcheur expirée après approbation empêche l'envoi. Les essais restent arrêtés au brouillon.

## T09 — Pilotage Telegram

- Réutiliser le traitement des updates et le réveil d'agent ; ajouter des commandes pour créer/inspecter un brief et connaître l'état de chaque parcours.
- Présenter recherche, budget et limites du brief avant son lancement explicite.
- Conserver les identifiants de commande, l'autorisation du compte/chat et la version du brief pour qu'un replay ne crée pas deux acquisitions.
- Un brouillon sans réponse reste en attente ; les tâches indépendantes continuent.
- Aucune commande de statut ne modifie la disponibilité, le budget ou la pause des envois.

Preuves : utilisateur non autorisé refusé ; update rejoué ; deux versions d'un brief ; budget absent ; bref état d'un seul parcours en panne ; aucun envoi déclenché par validation du cadrage.

## T10 — Répétition et bascule

1. Exécuter les tests ciblés, puis la suite complète après intégration ; vérifier `git diff --check` et les aperçus sans appels externes.
2. Répéter la migration sur une copie contrôlée et produire le manifeste de conservation.
3. Préparer le dossier de bascule : durée mesurée, espace temporaire, sauvegardes, verrous, opérations en attente, contrôles, commandes et retour arrière.
4. Planifier la pause validée ; confirmer qu'aucun écrivain ne reste actif et sauvegarder l'état final cohérent.
5. Basculer vers le journal unique, comparer comptes et contenus, vérifier les preuves et reprises ; retirer l'ancien sas seulement après ces contrôles.
6. Mesurer le volume réellement récupéré et la croissance après reprises. Conserver les sources, exports humains et exclusions ; rétention via les règles existantes.

Les étapes réelles sont distinctes de la préparation locale. Les montants de brief et la fenêtre de bascule doivent être renseignés avant leurs opérations. Les tests ne démontrent pas à eux seuls une intégration réelle. La campagne reste en pause jusqu'à instruction explicite de reprise et validations normales.

## Contrôle du tour de préparation initial

Le tour initial avait livré uniquement ce plan et la clôture documentaire du grilling. Ce constat historique ne décrit pas l'exécution ultérieure : consulter le [rapport d'exécution](REFONTE_EXECUTION_2026-11-05.md). Les montants des budgets restent requis avant les acquisitions payantes, et la fenêtre ainsi que les contrôles réels restent requis avant une bascule.
