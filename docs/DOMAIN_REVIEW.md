# Revue gratuite d’un domaine candidat

Le module `scripts/domain_review.py` consulte les informations d’enregistrement d’un domaine connu. Il ne cherche pas de domaine à partir d’un nom d’entreprise, ne vérifie pas une boîte email et ne lance jamais FullEnrich, No2Bounce, Harvest, CRM ou envoi.

## Utilisation

```bash
python3 -m scripts.domain_review --domain accessenergy.ma --company 'ACCESS ENERGY' --dry-run
python3 -m scripts.domain_review --domain accessenergy.ma --company 'ACCESS ENERGY'
```

`--dry-run` valide seulement la forme de l’entrée, sans connexion ni fichier créé. Les consultations réelles utilisent WHOIS `whois.registre.ma:43` pour `.ma`, conformément à l’IANA ; pour les autres extensions, le module cherche un endpoint HTTPS dans `https://data.iana.org/rdap/dns.json`. Aucune URL de service fournie par le contact, aucun suivi des liens/referrals du résultat, aucune redirection HTTP. Réponses limitées à 2 Mo et délais réseau bornés. Aucun compte ou token requis.

## Interprétation

- `FOUND` : réponse d’enregistrement pour le domaine demandé ; ce n’est pas une preuve de délivrabilité.
- `NAME_MATCH` : nom d’entreprise concordant avec un nom de titulaire publié, après normalisation de casse et ponctuation. Indice utile, pas validation automatique.
- `NEEDS_REVIEW` : titulaire absent/masqué/différent, service non couvert ou erreur. Un résultat différent peut correspondre à une société mère ou à un prestataire.
- `UNKNOWN` / `UNSUPPORTED` : aucune conclusion sur l’enregistrement ou la disponibilité du domaine. Pas de rejet automatique.

`requires_human_review` reste vrai et `email_deliverability_proven` reste faux. La réponse fournit la source, la date d’observation et une empreinte des données reçues. Les adresses email et numéros de téléphone WHOIS ne sont pas conservés dans le cache. Le registre fournit ses données telles quelles : le contrôle ne certifie pas l’appartenance juridique d’un domaine.

## Cache et coût

Cache local dédié `bdd/auxiliary/domain-review.sqlite`, indépendant du staging. Un domaine déjà consulté est réutilisé pour plusieurs entreprises/contacts ; la comparaison au nom est recalculée à chaque fois. Réponses positives et annuaire IANA conservés sept jours ; erreurs et absence de couverture cinq minutes. Cette durée concerne les informations d’enregistrement, pas les 90 jours des preuves email. La date d’observation n’est pas renouvelée lors d’une lecture du cache. Le module ne promet pas une seule requête sous plusieurs exécutions concurrentes : utiliser une séquence de consultations, sans lancement massif parallèle.

Aucun abonnement ajouté. Respecter les limites des registres ; le module traite un domaine par invocation et ne lance pas de balayage de la base. Un domaine invalide (URL, email, IP, localhost) est refusé avant création du cache.

## Place dans le pipeline

1. Relever un domaine candidat dans une source disponible (site officiel ou domaine professionnel déjà connu), sans le deviner.
2. Exécuter cette revue et examiner le titulaire lorsqu’il est publié. Vérifier séparément site officiel et DNS/MX si nécessaire ; ce module ne fait pas ces contrôles supplémentaires.
3. Après revue, renseigner `company_domain` et `company_domain_source` avec la provenance et la date. Le module produit la preuve mais n’écrit pas ces champs automatiquement.
4. Le pipeline FullEnrich utilise alors le domaine selon les règles existantes : adresse absente ou invalide uniquement, budgets persistants, aucun ancien NO_MATCH automatiquement relancé.

Les anciens domaines déjà validés manuellement restent utilisables : cette revue est un outil préparatoire, pas un appel réseau ajouté à chaque contact. Aucun changement des règles AcceptAll/HIGH_PROBABILITY ou du coupe-circuit.

Sources : [IANA .ma](https://www.iana.org/domains/root/db/ma.html), [annuaire RDAP IANA](https://data.iana.org/rdap/dns.json), [WHOIS ANRT](https://www.registre.ma/ressources/servicewhois).

## Renseigner le domaine dans la base sas

Après revue, utiliser la commande locale suivante :

```bash
python3 -m scripts.assign_company_domain --db-path archives/essais/ancien-sas/staging_leads.sqlite --domain accessenergy.ma --company 'ACCESS ENERGY' --dry-run
python3 -m scripts.assign_company_domain --db-path archives/essais/ancien-sas/staging_leads.sqlite --domain accessenergy.ma --company 'ACCESS ENERGY'
```

La seconde commande matérialise l’approbation de l’association entreprise/domaine. Elle lit uniquement le cache local existant, exige une preuve FOUND de moins de sept jours et un titulaire dont le nom concorde. Elle ne contacte aucun service. Les cas masqués, périmés ou ambigus sont refusés ; ils nécessitent une revue distincte, pas un domaine inventé.

Les contacts dont le nom d’entreprise concorde reçoivent company_domain et company_domain_source. La provenance contient le protocole, la source, la date d’observation et l’empreinte. Le journal company_domain_assignments conserve le détail et les anciennes valeurs. Un domaine différent déjà renseigné fait échouer toute l’opération ; une répétition identique est sans effet.

Une sauvegarde `.before-domain-assignment.bak` est créée avant la première écriture. Si les deux colonnes manquent dans une ancienne base, seules ces colonnes sont ajoutées : pas de migration générale ni de remise à zéro des statuts. Le prévisualisateur ne crée aucun fichier. Le verrou d’enrichissement et une transaction SQLite protègent l’écriture.

Les emails, preuves de délivrabilité, statuts, identités et liens CRM restent inchangés. La commande ne relance pas les enrichissements, même après ajout d’un domaine.
