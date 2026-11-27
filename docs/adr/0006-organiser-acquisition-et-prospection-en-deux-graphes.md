# Organiser l’acquisition et la prospection en deux graphes LangGraph

Kerix est la source unique de sélection des entreprises. Le graphe d’acquisition orchestre le tirage, l’import des entreprises dans Twenty, la recherche des dirigeants, les vérifications par lots et l’admission des contacts. Le deuxième graphe porte la rédaction et l’envoi des emails.

Chaque fournisseur possède un adaptateur qui expose ses capacités et normalise ses réponses. Les acteurs de recherche produisent des candidats et les vérificateurs produisent des résultats de délivrabilité. Chaque donnée conserve sa provenance et son lien avec le résultat d’origine pour que l’agent puisse examiner le dossier complet.

Un agent Codex App Server reçoit le dossier complet de la personne dans un contexte isolé par contact. LangGraph traite les appels à Kerix, aux acteurs Apify, aux vérificateurs et à Twenty par lots. Codex peut consulter le web directement avec Browsentic lorsqu’il lui manque une preuve sur le contact ou l’entreprise. Le dossier conserve la source consultée et le fait qu’elle établit. Les acteurs d’enrichissement sont la source des adresses candidates. Une adresse découverte pendant une recherche Browsentic est exclue de la liste à vérifier.

Les deux passages partagent les instructions système d’admission. Le premier prompt demande de qualifier la personne, son poste actuel et la filiale puis de choisir l’adresse candidate ou de préciser la preuve manquante. Le second prompt soumet le dossier complété par le résultat de délivrabilité pour cette adresse. Codex rend alors une décision d’admission motivée. LangGraph utilise les réponses structurées pour choisir l’étape suivante.

À l’admission, la fiche contact Twenty contient le dossier complet de la personne, dont les données fournisseurs, le profil, le poste, le rattachement à l’entreprise, les adresses et leurs statuts, les deux décisions de qualification, les résultats datés des vérificateurs et les sources consultées. L’adresse admise pour l’envoi y est identifiée explicitement.

L’opérateur fournit la référence de campagne au lancement de l’acquisition. Une personne possède une fiche unique dans Twenty et peut participer à plusieurs campagnes actives. À l’admission, le graphe crée ou enrichit cette fiche et lui associe la référence de campagne en préservant ses autres affectations. Chaque couple contact–campagne possède une seule affectation et son propre historique d’envoi. Réimporter le contact pour la même campagne conserve cet historique.

Chaque campagne est indépendante. Twenty est la source de vérité du graphe de rédaction et d’envoi. L’opérateur lance ce graphe avec la seule référence de campagne. Le graphe parcourt via l’API de Twenty les contacts portant cette référence, fige la liste de leurs identifiants pour ce lancement et charge leurs dossiers complets pour les agents rédacteurs. Les ajouts ultérieurs entrent dans le prochain lancement. Avant chaque envoi, le graphe relit la fiche dans Twenty et vérifie l’affectation courante, l’éligibilité et les désinscriptions.

Chaque pilote possède une configuration pour la rubrique, la taille du tirage, la graine, le plafond de dépenses et l’ordre des fournisseurs. Les variantes de titres sont versionnées dans une liste commune fournie à l’agent.

Le traitement des pannes et la reprise automatique sont différés. Les contraintes budgétaires s’appliquent à chaque lot pendant son exécution. Le second vérificateur reste prévu pour les timeouts signalés sur des adresses individuelles.
