# 09 : Vérification, cadence et pilote contrôlé

**À construire :** fournir aperçu, état, reprise, cadence quotidienne et procédure du pilote limité à vingt entreprises identifiées. Les fichiers source restent éphémères et les résultats réels ne sont jamais ajoutés à Git.

**Dépend de :** 08.

**Statut :** terminé le 15 octobre 2026 ; limites du pilote consignées ci-dessous.

- [x] Tests adaptés et `git diff --check` ; aucune régression du workflow de prospection.
- [x] `readiness` vérifie portail, Codex vision, verrou LinkedIn, FullEnrich, No2Bounce et schéma Twenty sans écriture CRM. Tous les contrôles disponibles passent ; No2Bounce n'a pas de vérification d'authentification gratuite sur son API bulk observée.
- [x] Un aperçu et un run testable sans service réel ; plafonds d'entreprises et de documents configurables.
- [x] Pilote réel : cinq annonces et cinq PDF traités, zéro échec technique et zéro entreprise suffisamment confirmée. Aucun appel payant ni écriture CRM ; le journal d'opérations reste vide. Une pièce PDF réelle avait également été extraite visuellement lors du contrôle de source.
- [x] Cadence quotidienne à 03:27 installée après `readiness.ready=true` ; les quatre lignes cron existantes ont été conservées et une sauvegarde du crontab a été faite sous `bdd/workflow/cron-backups/`. Guide d'exploitation et reprise documenté.

Le pilote ne valide pas encore la chaîne de création Twenty et d'enrichissement fournisseur sur une entreprise réelle. Ces étapes sont couvertes par la simulation et les tests locaux ; elles s'exécuteront lorsqu'une identité suffisamment vérifiée sera trouvée.
