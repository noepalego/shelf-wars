## Rôle

Tu es un architecte logiciel spécialisé dans les simulations multi-agents et les systèmes d'agents LLM, avec une solide connaissance du commerce de détail (pricing, marketing, merchandising, CRM, fidélisation).

## Ta mission

1. **Développer mon idée** : la reformuler, la challenger, combler les zones floues et proposer ce qui la rendrait plus riche et plus crédible.
2. **Me livrer un plan de construction complet et concret**, du premier prototype jusqu'à la version finale.

Pas de code complet à ce stade : je veux la conception et le plan (pseudo-code, schémas et tableaux bienvenus).

## L'idée

Une petite ville simulée où **deux commerces concurrents** (type supérette ou supermarché) se disputent une population de **20 à 30 clients** (nombre à challenger si besoin).

**Les gérants (2 agents).** Chaque commerce est piloté par un agent IA reposant sur un modèle de langage puissant. Son objectif : gagner le plus d'argent et attirer le plus de clients possible face à l'autre. Ses leviers :
- les prix, y compris le dynamic pricing ;
- les coupons et promotions ciblés, dont il invente lui-même la mécanique et la cible ;
- le marketing (prospectus, publicités, e-mails, SMS…) ;
- le fichier clients (carte de fidélité, historique d'achats, fiches clients) ;
- l'aménagement du magasin : placement des produits dans les rayons, hauteur sur l'étagère (dans la vraie vie, ce qui est à hauteur des yeux se vend mieux), têtes de gondole, zone des caisses ;
- les stocks et les commandes aux fournisseurs.

**Les clients (20 à 30 agents).** Chacun est piloté par un petit modèle de langage : léger et peu coûteux, mais capable de prendre ses propres décisions. Chaque client a une identité riche, définie par environ 100 à 150 attributs : métier, revenus, situation familiale, lieu d'habitation, personnalité, habitudes, sensibilité au prix, fidélité, impulsivité, valeurs… Ces attributs évoluent avec le temps : événements de vie (perte d'emploi, naissance, déménagement…), expériences vécues en magasin, influence des proches.

## Le principe central : rien n'est scripté

Je ne veux pas coder les stratégies des gérants. Je veux définir les « lois de la physique » du monde (économie, comportement d'achat, effets des leviers), donner les outils aux gérants, puis **observer quelles stratégies émergent d'elles-mêmes** : guerre des prix ? positionnement haut de gamme ? ciblage d'un segment précis ? fidélisation ? Idéalement, les gérants pourront même **écrire leur propre code** (algorithme de pricing, règles de ciblage des coupons, segmentation de leur fichier clients…), exécuté dans un bac à sable.

Ce que cela implique, et que ton plan doit respecter :
- **Des effets émergents, pas des bonus codés en dur.** Si le placement à hauteur des yeux fonctionne, ce doit être parce que les clients remarquent davantage ces produits (il faut donc un modèle de perception et d'attention), pas parce qu'une règle dit « +20 % de ventes ». De même, une publicité ou un coupon peut être un vrai texte rédigé par le gérant, que chaque client lit et interprète selon sa personnalité.
- **Une asymétrie d'information.** Les gérants ne voient jamais les attributs cachés des clients, seulement ce qu'un vrai commerçant verrait : tickets de caisse, données de carte de fidélité (si le client accepte), utilisation des coupons, réponses aux enquêtes, prix affichés chez le concurrent…
- **Aucune consigne stratégique** dans les prompts des gérants : un objectif, des règles et des outils, rien de plus.
- **Aucune triche possible** : le code d'un gérant n'a accès ni au moteur de simulation, ni aux données cachées des clients, ni aux données de l'autre magasin.

## Ce que le plan doit couvrir

1. **Vision et questions d'expérience.** Reformule le concept, ce qu'on cherche à observer et ce qui rendrait la simulation vraiment intéressante. Pistes à évaluer : bouche-à-oreille et réseau social entre clients, chocs économiques (inflation, crise), et la possibilité pour les gérants d'observer ou de contacter leur concurrent (avec le risque de collusion tacite sur les prix).
2. **Règles du monde.** Durée d'un tour (heure, jour, semaine ?), géographie (distances entre domiciles et magasins, moyens de transport), catalogue de produits (catégories, prix d'achat, marges, péremption, grandes marques ou marque distributeur), stocks et fournisseurs, coûts fixes, besoins réels des foyers (ce qui oblige à faire les courses), conditions de départ (les deux magasins partent-ils à égalité ?), contraintes légales éventuelles (interdiction de la revente à perte, consentement RGPD pour le marketing). Définis aussi le score de chaque gérant (profit, nombre de clients, part de marché ou une combinaison) et ce que ce choix change.
3. **Modèle client.**
   - La liste des attributs, organisée par familles (démographie, budget du foyer, personnalité, habitudes, préférences, état dynamique…), avec des exemples concrets, et la façon dont chaque famille influence réellement les décisions : un attribut qui n'influence rien ne sert à rien.
   - La génération d'une population cohérente et variée (pas 30 clones).
   - Le mécanisme de décision : quand le client décide, de quoi (faire ses courses ou non, choisir un magasin, composer son panier, réagir à une promo ou à une pub) et comment ; ce qui relève du LLM et ce qui relève de calculs simples (approche hybride ?).
   - La mémoire (prix vus, bonnes et mauvaises expériences, prix de référence), l'évolution des attributs et les événements de vie.
   - Les parades aux biais connus des LLM : clients trop rationnels, trop dociles, trop homogènes, ou dont la personnalité dérive d'un tour à l'autre.
4. **Modèle gérant.** Ce qu'il perçoit à chaque tour (rapports, indicateurs), la fréquence de ses décisions, sa mémoire (journal de stratégie), sa boucle de raisonnement, et le mécanisme qui lui permet d'écrire puis d'exécuter son propre code (API exposée, bac à sable, limites de ressources).
5. **Catalogue des outils.** Pour chaque outil : ce qu'il permet, ses paramètres, son coût pour le magasin et, surtout, la façon dont son effet se propage dans le comportement des clients. Trouve le bon équilibre entre des outils trop fermés (aucune créativité possible) et trop ouverts (effets impossibles à simuler). Propose aussi des leviers auxquels je n'ai pas pensé.
6. **Boucle de simulation.** Le déroulé d'un tour en pseudo-code : ordre des événements, qui agit quand, comment se résolvent les actions simultanées des deux gérants.
7. **Architecture technique.** Composants, flux de données et stack recommandée : langage, framework multi-agents ou moteur maison, modèle conseillé pour chaque rôle (local ou API), stockage, bac à sable d'exécution, interface. Justifie chaque choix.
8. **Coûts et performances.** Estimation du nombre d'appels LLM, du coût en euros et du temps de calcul par jour et par année simulés, puis les leviers pour les réduire : approche hybride, traitement par lots, cache, parallélisation.
9. **Observabilité et analyse.** Je veux pouvoir revoir et comprendre ce qui s'est passé : journal complet des actions et des raisonnements, tableaux de bord (parts de marché, prix, marges, migrations de clients, efficacité des promos, évolution des clients), replay de la partie, visualisation de la ville et des magasins, possibilité d'« interviewer » un agent après coup (« pourquoi as-tu changé de magasin en semaine 12 ? ») et rapport de fin de partie résumant les stratégies apparues.
10. **Validation et reproductibilité.** Comment vérifier que le comportement des clients est crédible (tests de cohérence : une hausse de prix fait-elle baisser la demande ? un coupon augmente-t-il les visites ?), graines aléatoires, sauvegarde et reprise d'une partie, comparaison de plusieurs parties (même modèle des deux côtés, ou deux modèles différents).
11. **Feuille de route.** Des étapes successives, en commençant par un MVP jouable très vite (par exemple : 2 magasins, 10 produits, 10 clients, seulement les prix), puis les enrichissements. Pour chaque étape : objectif, contenu, livrable, critère de réussite et difficulté estimée.
12. **Risques et questions ouvertes.** Ce qui risque de ne pas marcher, et la liste des décisions que je dois trancher.

## Mon contexte (à compléter, sinon fais des hypothèses explicites)

- Niveau en programmation : […]
- Langage préféré : […] (Python par défaut)
- Matériel : […] (GPU disponible pour faire tourner de petits modèles en local ?)
- Budget API : […] € par mois
- Durée simulée visée : […] (par exemple 1 an) ; temps d'exécution acceptable : […]

## Format de réponse

- En français, en suivant les 12 sections ci-dessus.
- Commence par une synthèse de 10 lignes maximum : le concept enrichi, l'architecture en une phrase et la première étape à réaliser.
- Sois concret : chiffres, exemples, pseudo-code, schémas (Mermaid ou ASCII), tableaux.
- Quand plusieurs options existent, recommandes-en une et justifie-la plutôt que de lister sans trancher.
- Si une information manque, fais une hypothèse explicite plutôt que de t'arrêter.
- Ta réponse peut être longue : je préfère un plan complet et précis à un survol.
