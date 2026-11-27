# Acquisition d’entreprises par rubrique

Le parcours d’acquisition part d’une rubrique Kerix, enregistre les entreprises dans le CRM et recherche des contacts au sein de ces entreprises.

## Vocabulaire

### Rubrique Kerix

Catégorie principale du répertoire Kerix choisie comme point de départ d’une acquisition. Elle peut regrouper plusieurs sous-rubriques d’activité.

### Entreprise de la rubrique

Entreprise dont la fiche apparaît dans les résultats de la rubrique. Une même entreprise peut apparaître dans plusieurs rubriques.

### Entreprise importable

Entreprise de la rubrique dont la fiche Kerix renseigne l’ICE. Le site web est facultatif.

### Adresse candidate

Adresse associée à un contact par une source de recherche, en attente de vérification de délivrabilité.

### Vérification de délivrabilité

Évaluation datée d’une adresse précise par un vérificateur, avec un résultat et une provenance.

### Contact admissible

Contact professionnel dont l’adresse est rattachée à l’entreprise par un domaine confirmé ou par une publication officielle. L’Actor Apify `account56/email-verifier` doit valider la boîte exacte avec les champs `result=ok`, `quality=good` et `subresult=ok`. Cette preuve est relue avant l’envoi.

Le poste, le nom et le profil LinkedIn personnel sont facultatifs. Une adresse officielle partagée peut servir de point de contact. Si le nom n’est pas confirmé, la rédaction s’adresse à l’entreprise avec une salutation neutre.

### Entreprise couverte

Entreprise du lot pour laquelle au moins un contact admissible a été identifié. Le taux de couverture du pilote se calcule sur les 30 entreprises sélectionnées.

### Référence de campagne

Identifiant qui rattache un contact à une campagne et permet de retrouver les destinataires lors du lancement de la rédaction et de l’envoi.

### Affectation à une campagne

Lien entre une fiche contact et une campagne. Une personne peut avoir plusieurs affectations actives. Chacune porte le suivi des envois de la campagne correspondante.

### Import Excel

Le composant Twenty importe les lignes mappées dans les fiches entreprise et personne. Les lignes sans adresse sont consignées pour examen. La campagne dispose de sa propre étape de vérification Account56 et d’approbation du brouillon.
