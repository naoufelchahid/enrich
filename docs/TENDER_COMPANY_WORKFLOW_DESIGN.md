# Acquisition d'entreprises depuis les résultats des marchés publics marocains

Statut : conception confirmée par l'opérateur le 15 octobre 2026. L'exécution suit les tickets locaux de [tender-workflow](tender-workflow/README.md), dans leur ordre numérique.

## Objectif et choix confirmés

- Repérer toutes les entreprises mentionnées comme soumissionnaires dans les résultats, y compris les attributaires et les entreprises écartées. La sélection ne dépend pas du résultat de leur offre.
- L'acquisition globale n'a pas vocation à répertorier exhaustivement toutes les entreprises du portail : une entreprise sans correspondance fiable est ignorée et le parcours continue.
- Rechercher les résultats directement sur le portail marocain des marchés publics, sans limiter la découverte aux appels d'offres déjà présents dans la base de `tender_scrape`.
- Parcourir initialement douze mois d'historique, puis rechercher les nouvelles annonces chaque jour. Commencer par un pilote de vingt entreprises identifiées ; le plafond quotidien ultérieur sera configurable après mesure du pilote.
- Lire les procès-verbaux et les autres documents de résultat disponibles lorsque ceux-ci contiennent les informations d'entreprise recherchées ; le type de document n'est pas une fin en soi.
- Créer un workflow indépendant, avec son propre état et ses propres exécutions, qui réutilise les clients Twenty, LinkedIn et FullEnrich existants et respecte l'unique worker LinkedIn MCP.
- Ne pas créer dans le CRM d'historique de participation, de lot, de montant ou de statut d'offre. Ne conserver après traitement ni PDF, ni copie de document, ni identifiant, empreinte, date ou état de traitement du document dans le nouveau workflow. Cette exception aux règles de conservation du workflow de prospection est décrite dans [ADR-0003](adr/0003-acquisition-sans-trace-de-participation.md).
- Créer ou rapprocher automatiquement une entreprise seulement lorsque son identité est suffisamment fiable. Ignorer toute entreprise sans correspondance fiable, y compris les homonymies non résolues, et passer à la suivante sans file de revue métier. Créer les contacts après confirmation de leur lien professionnel et qualification des coordonnées.
- Rechercher au maximum deux contacts par entreprise : un dirigeant et un responsable commercial ou des appels d'offres.
- Faire extraire les noms et données du document par un agent Codex utilisant sa vision, sans moteur OCR. Les pages du PDF peuvent être rendues temporairement en images pour l'agent. Traiter les PDF directs et les PDF contenus dans des ZIP ; les autres formats ne sont pas inclus dans le premier périmètre. Supprimer les téléchargements et images temporaires dès la fin de leur traitement. Si la lecture visuelle échoue, ignorer ce document, supprimer les fichiers et continuer.
- Une personne confirmée peut entrer dans Twenty avec seulement un email professionnel qualifié ou un téléphone qualifié ; l'autre coordonnée peut manquer. Ce parcours s'arrête à l'enregistrement CRM et n'alimente pas automatiquement le workflow d'emails.
- Conserver un journal technique minimal pour les opérations fournisseurs et CRM afin de réconcilier les résultats incertains, sans référence de marché, participation, empreinte ou métadonnée de document.
- Confirmer l'entreprise par un domaine ou identifiant fiable, ou par deux indices indépendants concordants. Un nom seul ou des homonymes non résolus entraînent l'abandon de cette entreprise.
- Lorsqu'une entreprise ou personne existe déjà dans Twenty, compléter seulement ses champs vides et relire son identité et ses liens après écriture. Ne pas écraser ses données fiables.
- Vérifier chaque email professionnel retenu avec No2Bounce. Ne retenir un téléphone FullEnrich que depuis `most_probable_phone` après contrôle de son état de ligne. Une seule de ces coordonnées qualifiées suffit pour admettre la personne dans Twenty.
- Le passage quotidien relit les annonces publiées pendant les trois derniers jours. Il peut retraiter des PDF déjà lus ; le rapprochement CRM et le journal technique évitent les nouvelles créations ou soumissions payantes en double.
- Renseigner dans Twenty les données fiables disponibles de l'entreprise : nom, site ou domaine, ville, secteur, description, page LinkedIn et téléphone général, seulement si le schéma du workspace possède le champ correspondant. Aucun champ relatif à un marché ou statut d'envoi automatique n'est ajouté.

## Parcours cible

1. Lire directement les deux listes publiques du portail, « Tous les extraits de PV » et « Tous les résultats définitifs ». Pour le premier parcours, regarder les douze derniers mois et s'arrêter après vingt entreprises identifiées ; pour les passages suivants, utiliser la fenêtre glissante de trois jours. Paginer par date décroissante avec une borne configurable de documents par exécution.
2. Ouvrir chaque annonce et récupérer tous ses PDF accessibles directement ou contenus dans une archive ZIP. Les fichiers DOCX sont hors du premier périmètre. Télécharger les pièces dans un répertoire temporaire réservé au run.
3. Rendre les pages PDF en images temporaires et les transmettre à un agent Codex doté de vision, sans OCR. Le contrat de sortie contient les noms exacts des entreprises mentionnées, sans montant, lot ni statut conservé après l'appel. Contrôler la structure de la sortie et parcourir toutes les pages du document traité. Si la sortie n'est pas exploitable, ignorer le document. Supprimer téléchargements, images et sortie locale à la fin du traitement, succès ou échec.
4. Rechercher chaque entreprise candidate avec les outils société du MCP LinkedIn ; utiliser Kerix lorsque son accès et sa réponse sont disponibles. Le worker LinkedIn de ce parcours partage le verrou unique avec celui du workflow existant et lance ses appels en séquence. N'utiliser les données de ces services que si le rapprochement répond à la règle d'identité confirmée ; sinon passer à la société suivante.
5. Relire Twenty avant toute création. Rapprocher l'entreprise par domaine, identifiant ou autre identité fiable ; ajouter la fiche si absente, compléter seulement les champs vides si elle existe. Journaliser les soumissions CRM et réconcilier tout résultat incertain avant une nouvelle tentative. Relire la fiche après écriture.
6. Rechercher au plus deux personnes travaillant actuellement dans l'entreprise, en priorité un dirigeant puis un responsable commercial ou des appels d'offres. Lire leurs profils personnels via le MCP LinkedIn et confirmer individuellement nom, entreprise actuelle et profil unique. Ignorer les profils ambigus ou non confirmés.
7. Soumettre à FullEnrich l'URL LinkedIn personnelle confirmée pour demander email professionnel et téléphone. Vérifier l'email professionnel retenu avec No2Bounce ; ne conserver comme téléphone de personne que le numéro probable accepté par FullEnrich et dont l'état de ligne est admissible. Journaliser les opérations payantes et leurs identifiants sans données du marché. Un résultat inconnu est réconcilié avant resoumission.
8. Créer ou compléter chaque personne admissible dans Twenty, même si une seule des deux coordonnées est disponible. Vérifier par relecture son identité, son entreprise et son lien. Ne pas inscrire cette personne dans la réserve d'envoi ni déclencher un brouillon ou un envoi.
9. Finir le run sans conserver de PDF, page rendue, texte, sortie d'extraction, référence de marché, participation ou état de document. Seul le journal technique des opérations fournisseurs et CRM, ainsi que les fiches entreprises et personnes dans Twenty, persistent.

## Cas concrets qui fixent les limites

- Deux sociétés homonymes trouvées pour un nom du PV, sans domaine ou autre indice discriminant : aucune fiche créée et aucun contact cherché pour cette occurrence.
- Un PDF illisible pour Codex : fichiers supprimés, annonce ignorée, parcours continué.
- Une entreprise identifiée sans personne confirmée : fiche entreprise conservée dans Twenty, sans création de personne fictive.
- Une personne confirmée avec téléphone qualifié mais sans email professionnel : fiche personne admise dans Twenty, sans statut prêt à envoyer.
- Une réponse FullEnrich ou Twenty incertaine : opération technique conservée pour réconciliation, aucune nouvelle soumission aveugle.

## Faits vérifiés dans le code existant

- `tender_scrape/7_Result_scrape/Result_update/results_scraper.py` part d'appels d'offres déjà enregistrés dans sa table `Tender`, puis recherche sur le portail une annonce d'extrait de PV par référence. Il ne parcourt pas directement tous les résultats du portail. Sa déduplication de fichiers ne persiste pas entre les exécutions.
- Le portail propose publiquement les listes [« Tous les extraits de PV »](https://www.marchespublics.gov.ma/index.php5?AvisExtraitPV=&page=entreprise.EntrepriseAdvancedSearch) et [« Tous les résultats définitifs »](https://www.marchespublics.gov.ma/index.php5?AvisAttribution=&page=entreprise.EntrepriseAdvancedSearch). La première affichait 95 648 résultats le 15 octobre 2026 ; le parcours direct exige donc une fenêtre temporelle et une pagination bornées.
- `tender_scrape/7_Result_scrape/results_ocr_container/data_extractor.py` reçoit du texte déjà extrait ; il ne télécharge pas les PDF. Son format large pour base limite les autres soumissionnaires à quinze par lot et ne conserve pas tous les champs structurés.
- `scripts/crm_transfer.py` exige actuellement, pour les personnes du workflow de prospection, une identité LinkedIn confirmée et un email admissible. Le nouveau workflow devra préciser ses propres règles d'admission au CRM.
- `scripts/enrichment_clients.py` soumet actuellement à FullEnrich uniquement `contact.work_emails`. La [documentation officielle de FullEnrich](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/post) accepte aussi `contact.phones` ; le client, le traitement des résultats et le mapping CRM restent à adapter.
- `codex exec --help` accepte des fichiers image avec `--image` et le mode `--ephemeral`. `pdftoppm`, `mutool` et `pdfinfo` sont disponibles localement pour rendre temporairement les pages des PDF ; ce rendu n'est pas une étape OCR.

## Points à vérifier pendant l'implémentation

- Schéma réel du workspace Twenty et possibilités de rapprochement des entreprises par URL LinkedIn ou identifiant légal.
- Accès et contrat actuels de Kerix ; une indisponibilité n'empêche pas le parcours LinkedIn.
- Pagination, filtres de dates, disponibilité des PDF et éventuelles limites d'accès du portail sur un petit échantillon en lecture seule.
- Format de sortie Codex, nombre de pages raisonnable par document et nettoyage des fichiers temporaires même lors d'une interruption.
- Champs et verdicts réels de FullEnrich et No2Bounce dans le workspace, sans appels payants avant les tests simulés.

Le choix métier est confirmé. Aucun run réel, création CRM ou appel payant n'a été effectué pendant l'entretien de conception.
