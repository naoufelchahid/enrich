# Consignes pour les prochains runs

Lire [agents.md](agents.md) avant toute intervention dans ce dépôt.
Pour créer ou nettoyer des fichiers, appliquer obligatoirement la section
« Classement et nettoyage » de [WORKFLOW_OPERATIONS.md](docs/WORKFLOW_OPERATIONS.md).

Les preuves d'un contact intégré au CRM sont conservées dans le journal SQLite ;
leurs copies de contrôle et les rapports des runs entièrement intégrés sont
supprimés par `scripts.workflow_files`, après vérification. Les contacts en attente
gardent leurs fichiers. Sources et exports humains sont conservés.

Les tests restent arrêtés au brouillon. LinkedIn utilise un seul worker MCP à la fois. Respecter les consignes de mémoire et coordination fournies par
l'opérateur lorsqu'elles sont présentes dans la session.
