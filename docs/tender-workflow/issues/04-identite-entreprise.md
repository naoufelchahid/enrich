# 04 : Identité entreprise via LinkedIn et Kerix

**À construire :** rechercher chaque nom extrait avec les outils société du MCP LinkedIn, puis Kerix si accessible. Une identité unique exige un domaine ou identifiant fiable, ou deux indices indépendants concordants.

**Dépend de :** 03.

**Statut :** terminé.

- [x] Appels LinkedIn séquentiels sous le verrou partagé avec le worker existant ; seuls les outils de lecture nécessaires sont autorisés.
- [x] Nom seul, homonymies et données contradictoires entraînent l'abandon sans file de revue métier.
- [x] Kerix indisponible n'empêche pas un résultat LinkedIn suffisamment confirmé.
- [x] Tests de concordance, ambiguïté et indisponibilité, sans appels réels payants.
