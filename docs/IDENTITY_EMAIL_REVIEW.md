# Identité et anti-rebond — entretien de conception

Date : 6 octobre 2026. Statut : Q1 à Q7 confirmées ; étape 1 validée ; étape 2 de correction hors ligne autorisée et implémentée, bilan soumis à validation avant les appels réels. Aucun changement du pipeline ni appel fournisseur payant pendant cette revue.

## Contrat FullEnrich vérifié

L'API v2 accepte une URL LinkedIn personnelle, ou prénom + nom + entreprise identifiée par son nom ou son domaine. Le domaine n'est pas systématiquement obligatoire. Le centre d'aide confirme ce fonctionnement pour l'API, les CSV et la plateforme ; le fournisseur recherche le domaine manquant.

Sources : [Bulk enrichment](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/post), [données nécessaires](https://help.fullenrich.com/en/articles/9688091-data-provided-by-fullenrich).

Cette acceptation technique ne prouve pas que le découpage du nom ou le rattachement à l'entreprise est correct. `split_name` prend actuellement le premier mot comme prénom et le reste comme nom, sans score de confiance ni conservation dédiée du nom complet source. `contact_input` utilise le nom d'entreprise mais ignore le champ `website` disponible.

Précision de l'utilisateur au premier tour : les données concernées comportent déjà des colonnes prénom et nom distinctes. Ces colonnes doivent être utilisées directement, sans reconstruire puis redécouper un nom complet. Le constat sur `split_name` concerne les parseurs qui l'utilisent, pas une obligation de retraiter les colonnes déjà séparées.

Certains parseurs BDD importent des sites web. Le parseur BDD5 dérive même l'entreprise et le site d'un domaine d'email : cette inférence ne doit pas devenir une preuve d'identité. BDD2 peut associer l'email d'entreprise à plusieurs dirigeants : une adresse présente n'est donc pas nécessairement personnelle.

## État observé et limites de l'échantillon

Lecture seule du staging actuel : 20 lignes, 20 prénoms/noms/entreprises renseignés, 20 emails sources et nettoyés, aucune URL LinkedIn. Les 20 anciens statuts VALID/READY_FOR_CRM n'ont pas de preuve de validation dans ce schéma antérieur à la migration. Le dispatcher refuse correctement de les considérer comme éligibles. Ces comptes portent uniquement sur le staging, pas sur l'ensemble des BDD.

## Constats initiaux anti-rebond (avant l’étape 2)

Le nettoyage a retiré l'ancien module d'hygiène sans rétablir son rôle dans le pipeline direct. Les tests de non-régression exécutés après nettoyage ne démontraient pas la couverture de cette exigence métier.

| Contrôle | État constaté |
| --- | --- |
| Statut DELIVERABLE FullEnrich, adresse exacte, provenance et date | Présent pour les emails nouvellement trouvés |
| Catch-all ou HIGH_PROBABILITY autorisés à l'envoi | Non |
| Hygiène syntaxique robuste, domaines jetables, DNS/MX | Absente ou insuffisante |
| Vérification autonome des emails déjà présents | Absente |
| Conservation séparée de l'email original et du résultat trouvé | Absente : un résultat peut écraser raw_email |
| Durée de validité de la preuve de vérification | Non définie ; date présente mais ancienneté non contrôlée |
| Déduplication de la file et blocage des désinscriptions reconnues | Présents |
| Arrêt sur rebond détecté | Présent |
| Blocage si lecture de boîte impossible | Insuffisant : une erreur peut devenir une liste vide |
| Blocage si état du coupe-circuit corrompu | Insuffisant : état par défaut non déclenché |
| Exclusion durable de l'adresse ayant rebondi | Incomplète : arrêt global sans statut d'exclusion propre à l'adresse |

Ne pas restaurer l'ancien module tel quel : il marquait une adresse délivrable après les seuls contrôles syntaxe/domaine/MX lorsque sa deuxième passe API était absente. Un domaine capable de recevoir des emails ne prouve pas l'existence d'une boîte particulière. Une erreur DNS temporaire ne prouve pas non plus l'invalidité d'une adresse.

## Vérification et enrichissement sont distincts

FullEnrich documente la vérification des emails qu'il découvre. Aucun endpoint public de vérification autonome d'une liste d'emails sources n'a été trouvé dans l'index v2 et l'OpenAPI consultés. Le reverse email lookup identifie la personne derrière une adresse ; il ne constitue pas un contrat de vérification de délivrabilité. Il reste à choisir la méthode applicable aux emails existants avant de prétendre couvrir ce besoin.

Sources : [statuts email](https://docs.fullenrich.com/api/v2/general/email-status), [index API](https://docs.fullenrich.com/llms.txt), [OpenAPI](https://docs.fullenrich.com/api/v2/reference/openapi.yaml).

## Arbre des décisions

Premier tour, décisions acceptées :

1. Vérifier l'adresse existante. Si elle est délivrable, la conserver sans enrichissement email. Déclencher l'enrichissement seulement si l'adresse est absente ou non délivrable. Les résultats incertains restent en attente conformément à Q4 ; ils ne sont pas assimilés à des verdicts négatifs.
2. Pour tous les contacts nécessitant un enrichissement au sens de Q1, utiliser prénom + nom + entreprise. Ne pas imposer une recherche de domaine ou de LinkedIn ni une revue d'ambiguïté systématique avant l'appel. Utiliser les colonnes prénom et nom séparées indiquées par l'utilisateur. La validation technique des champs requis reste nécessaire ; ne pas inventer une valeur manquante.

Deuxième tour, décisions acceptées :

3. Utiliser No2Bounce pour vérifier les adresses existantes, en complément de FullEnrich et Harvest.
4. Les verdicts catch-all/unknown et les erreurs techniques mettent le contact en attente, sans envoi ni enrichissement automatique. Ils ne prouvent pas que l’adresse est non délivrable.

Troisième tour, décisions acceptées :

5. Accepter les nouveaux emails FullEnrich DELIVERABLE sans seconde vérification immédiate No2Bounce. Conserver la preuve, le fournisseur et la date pour l’adresse exacte. Les autres résultats FullEnrich n’autorisent pas l’envoi.
6. Réutiliser un verdict positif pendant 90 jours à compter de la vérification, pour la même adresse. Après expiration, une nouvelle vérification No2Bounce est nécessaire avant envoi, soumise au quota. Cette durée est une règle métier, pas une garantie contre les rebonds ; elle ne lève jamais une exclusion ou un coupe-circuit.
7. Pilote de 3 contacts : au plus 3 adresses distinctes soumises à No2Bounce et au plus 3 contacts enrichis avec FullEnrich ; Harvest désactivé pendant cette validation. Chaque étape réelle attend la validation de la précédente. Ce plafond compte des opérations, pas un montant monétaire garanti. La facturation du compte doit être rapprochée avant tout passage à l’échelle.

Le schéma précis des verdicts terminaux No2Bounce, les règles de normalisation des catégories et le coût réel restent à vérifier avant intégration réelle. Ils sont à rechercher dans les sources/compte fournisseur, pas à inventer.

Flux retenu : inventaire des sources → conservation de l'identité et de l'email d'origine → hygiène et exclusions → vérification de l'email existant → si absent/non délivrable, enrichissement prénom + nom + entreprise → contrôle du résultat → critères d'envoi → surveillance et exclusions durables. Les branches incertaines mènent à une mise en attente sans dépense automatique.

La première étape d'exécution proposée reste un inventaire sans dépense. Les tests manquants devront couvrir les lacunes ci-dessus avant toute reprise des essais d'envoi. La frontière entre vérification et enrichissement est consignée dans [ADR-0001](adr/0001-verifier-avant-enrichissement.md).

## Contrat No2Bounce : première vérification documentaire

La [documentation officielle](https://www.no2bounce.com/api-documentation) décrit `POST https://connect.no2bounce.com/v2/n2b_validate_bulk`, header `apitoken`, corps `emailList`, puis `GET` sur le même chemin avec `trackingId`. L’identifiant de suivi ne vaut jamais verdict de délivrabilité. Les compteurs de progression ne suffisent pas non plus ; il faut un résultat final associé à chaque adresse.

La page mentionne `apiResponse` mais n’en donne pas le schéma détaillé par adresse. Les gros résultats peuvent être téléchargés via une URL signée. Elle évoque un `hashkey` valable trois minutes sans placement clair dans l’exemple : ne pas inventer son utilisation ni lui confier seul la déduplication. Une limite d’emails peut être fixée sur la clé ; les crédits sont consommés à l’usage, sans tarif unitaire précis établi par cette page. Aucun appel réel n’a été effectué.

## Cadrage consolidé confirmé

- Email présent : hygiène puis vérification No2Bounce, ou réutilisation d’une preuve positive de moins de 90 jours pour cette adresse.
- Email délivrable : conserver l’adresse, sans enrichissement email.
- Email absent ou explicitement non délivrable : enrichir via FullEnrich avec les colonnes prénom, nom et entreprise.
- Résultat incertain ou erreur : mise en attente, sans envoi, sans relance payante automatique, sans déclenchement FullEnrich.
- Nouvelle adresse FullEnrich DELIVERABLE : admissible côté vérification sans second contrôle No2Bounce immédiat. Les autres garde-fous d’envoi restent applicables.
- Email original conservé séparément du résultat fournisseur. Déduplication avant facturation et avant envoi.
- Pilote isolé : 3 contacts, au plus 3 adresses distinctes soumises à No2Bounce et 3 contacts enrichis via FullEnrich sur l’ensemble du pilote. Aucun reset quotidien ne doit étendre cette enveloppe. Harvest reste désactivé durant cette validation.

Ordre d’exécution : (1) inventaire en lecture seule et sélection des trois contacts ; (2) correction et tests hors ligne du routage/anti-rebond ; (3) pilote No2Bounce et validation des résultats/coûts ; (4) FullEnrich uniquement pour les cibles admissibles, puis validation ; (5) reprise du parcours Harvest/CRM/envoi selon les étapes convenues. L’inventaire et les tests ne consomment aucun crédit ; chaque étape réelle est validée avant la suivante. La forme des verdicts No2Bounce et les règles tarifaires du compte restent des vérifications techniques requises, pas des décisions métier laissées à deviner.

## Réalisation de l’étape 2

Le routage, les 90 jours, la conservation de l’email original, les plafonds cumulés, l’hygiène locale et les protections de boîte/coupe-circuit sont implémentés et testés hors ligne. Une base pilote isolée a été créée sans appeler les fournisseurs. Le contrat terminal No2Bounce reste explicitement non confirmé et empêche les soumissions réelles. Voir [le guide](EMAIL_VERIFICATION.md) et [le bilan](../archives/essais/pilot-step2-2026-10-06/VALIDATION.md).
