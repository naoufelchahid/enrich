# Utiliser f0rty7even pour le contrôle DNS

L’acquisition utilise l’Actor Apify `f0rty7even/email-verifier` pour contrôler la syntaxe, les domaines et les enregistrements MX des adresses déjà rattachées aux entreprises. Les lots dédupliquent les adresses et réservent leur dépense maximale dans le journal du pilote. Les réponses d’origine et leur provenance sont conservées.

Le résultat `valid` de cet Actor correspond à un contrôle DNS positif. Le graphe le journalise sous `dns_ok` et garde le dossier en attente de preuve SMTP. L’envoi continue de vérifier une preuve concernant la boîte exacte, y compris pour les contacts dont la preuve SMTP est antérieure à cette décision.
