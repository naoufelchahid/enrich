# Rattacher les adresses à l’entreprise et vérifier leur délivrabilité

Décision sur le vérificateur remplacée par la [fiche 0009](0009-utiliser-account56-pour-verifier-les-boites-mail.md).

Le pilote élargi reprend le tirage Kerix déjà archivé et conserve le rapprochement des entreprises dans Twenty par ICE. Le graphe conserve les profils rattachés à l’entreprise quel que soit leur poste. La présence d’un LinkedIn personnel et la certitude du nom servent à documenter le dossier et à choisir la rédaction.

Pour les entreprises sans site ni page LinkedIn, un Actor de recherche SERP interroge le Web. Codex examine ses résultats au regard de la fiche Kerix et conserve la source qui corrobore le rattachement. PipelineLabs recherche sans fonction imposée, d’abord par domaine strict, puis par nom d’entreprise au Maroc pour les entreprises encore sans profil PipelineLabs. Harvest utilise les profils et les pages d’entreprise disponibles sans filtre d’intitulé.

Une adresse candidate doit relever du domaine confirmé ou être explicitement publiée sur le site officiel de l’entreprise. Les adresses génériques peuvent être vérifiées. Une boîte sans personne identifiée produit une fiche Twenty indexée par adresse et une rédaction à salutation neutre. Les profils et preuves restent dans le bilan du tirage. Pour la recette, une seule adresse vérifiée par entreprise entre dans la campagne.

BounceVerify valide l’adresse exacte. Zaiq intervient lorsque BounceVerify signale un timeout explicite. L’admission exige un résultat positif sans indicateur de risque. L’envoi en production relit la preuve depuis Twenty.
