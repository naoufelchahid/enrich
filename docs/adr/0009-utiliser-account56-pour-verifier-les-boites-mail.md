# Utiliser Account56 pour vérifier les boîtes mail

L’Actor Apify `account56/email-verifier` est le seul vérificateur appelé. Les lots dédupliquent les adresses et réservent leur plafond de facturation. Les dossiers conservent le verdict d’origine et la provenance du lot.

L’admission exige une adresse rattachée à l’entreprise et un résultat `ok`, une qualité `good` et un sous-résultat `ok` pour cette boîte. Les résultats catch-all, indéterminés, invalides et les erreurs restent hors envoi. Le graphe relit la preuve dans Twenty avant la transmission. Les anciens résultats restent dans l’historique et chaque boîte auparavant admise doit disposer d’une preuve Account56 pour être envoyée.
