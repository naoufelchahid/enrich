# Intégrer les entreprises avant la recherche de dirigeants

Décision sur le vérificateur remplacée par la [fiche 0009](0009-utiliser-account56-pour-verifier-les-boites-mail.md).

L’opérateur indique que la base Twenty est vide au départ du pilote de la rubrique Kerix 21300. L’ICE est obligatoire pour l’import. Les fiches Kerix sans ICE sont exclues de l’import et de la recherche de contacts. Le traitement rattache les contacts à l’identifiant Twenty de l’entreprise identifiée par son ICE. Les correspondances ambiguës passent en revue. Une entreprise identifiée par son ICE peut entrer dans Twenty sans site web et attendre la confirmation de sa page LinkedIn pour la recherche de contacts.

Le tirage reproductible sélectionne une seule fois 30 fiches Kerix uniques de la rubrique, avant le contrôle de l’ICE. La composition du lot est alors définitive. Chaque fiche sans ICE compte parmi les 30 et figure au bilan des exclusions. Le nombre d’entreprises importées peut donc être inférieur à 30. La liste source et le paramètre de tirage sont conservés pour pouvoir reproduire le lot.

Les recherches et les vérifications sont traitées par lots via API. Les fonctions ciblées sont, par ordre de préférence, gérant, directeur général et directeur commercial. Si un lot fournit plusieurs contacts admissibles, le pilote conserve au plus une personne par fonction et trois personnes distinctes par entreprise. Chaque contact est rattaché à l’identifiant Twenty de son entreprise. Dès le premier contact admissible, l’entreprise sort des lots transmis aux fournisseurs plus chers.

Le plafond cumulé du pilote est de **3 USD**, recherche Apify et vérification des emails comprises. Avant chaque appel payant, le traitement réserve son coût maximal dans le solde disponible, en tenant compte des lots en cours et des vérifications restant à financer. Lorsque ce solde est insuffisant, le pilote s’arrête et le bilan indique la couverture obtenue sur les 30 entreprises initiales.

## Recherche et admission des contacts

PipelineLabs recherche les dirigeants en premier. Une seule source suffit pour établir le rattachement si elle identifie le profil LinkedIn personnel et le poste actuel et que le domaine confirmé ou la page LinkedIn de l’employeur correspond à l’entreprise ciblée. Une adresse professionnelle nominative passe en vérification uniquement si son domaine correspond exactement au domaine confirmé de l’entreprise ciblée. Pour une filiale, le comparateur utilise le domaine propre de cette filiale. Si ce domaine reste inconnu, l’adresse reste en attente.

Pour un dirigeant identifié dont l’adresse manque ou a été rejetée, les URL personnelles sont regroupées pour HarvestAPI LinkedIn Profile Scraper avec recherche d’email. Pour une entreprise encore sans dirigeant identifié et dont la page LinkedIn est confirmée, HarvestAPI LinkedIn Company Employees reçoit les entreprises par lot en mode Full avec recherche d’email.

La recherche couvre les variantes féminines et masculines, les abréviations, les graphies avec ou sans accents, les titres composés et les équivalents linguistiques des trois fonctions. Elle inclut par exemple « co-gérante », « directrice générale », « PDG », « CEO », « managing director », « directrice commerciale », « sales director », « المدير العام » et « المديرة التجارية ». L’admission exige une correspondance directe entre le titre actuel et une variante des trois fonctions. « Associé-gérant » contient explicitement la fonction de gérant. Les titres voisins tels que « Managing Partner » et « Head of Sales », les postes anciens et les fonctions d’adjoint ou d’assistant sont exclus. La description des responsabilités ne permet pas de reclasser un titre.

BounceVerify traite les adresses candidates par lot. Un délai d’attente explicitement signalé pour une adresse déclenche son transfert en lot à l’Actor zaiq/email-verifier. Son verdict deliverable permet l’admission si l’adresse remplit les critères d’identité et de domaine et si les indicateurs de risque sont écartés. Un second verdict unknown laisse le contact en attente et clôt les dépenses sur cette adresse. Les deux vérifications entrent dans le plafond cumulé de 3 USD.
