# Simarché

Un laboratoire de compétition économique autonome : deux supérettes se disputent les mêmes **30 habitants fictifs**,
chacun piloté par Claude Haiku (profil persistant, budget, besoins, préférences, mémoire). Chaque magasin est dirigé par
**deux agents IA permanents** de la même entreprise — le **gérant** et le **responsable marketing, données et
connaissance client** — dont l'objectif est le **bénéfice net cumulé** (avec la clientèle, la marge, la part de marché
et la valeur future des clients, jamais au prix d'un magasin déficitaire).

Les agents ne reçoivent **aucune stratégie** : seulement leur rôle, l'objectif, les règles de l'univers et leurs
capacités. Ils **inventent leurs propres outils** : dispositifs de mesure, logiciels, algorithmes, programmes qui
tournent seuls entre les sessions, missions confiées à des **sous-agents payants**. Un **arbitre** du moteur évalue
chaque invention, fixe son prix et, si le devis est accepté et payé, l'installe **immédiatement**. Les dispositifs
n'observent que ce qui se passe réellement dans la simulation, avec la précision et les défauts d'un vrai appareil.
Seules les conséquences économiques simulées disent si une stratégie était bonne.

Les modèles tournent **sans clé API**, avec ton abonnement :
- **Claude Code** (abonnement Claude Pro ou Max) : Claude Sonnet 5.5 en effort élevé pour les deux agents de direction,
  Sonnet pour l'arbitre et les sous-agents, Claude Haiku 5.5 pour les habitants ;
- ou **Codex** (abonnement ChatGPT), au choix, ou en relais quand l'un des deux atteint sa limite d'usage.

## Démarrer avec Claude Code

```powershell
.\.venv\Scripts\python.exe -m simarche claude-login
```

```powershell
.\.venv\Scripts\python.exe -m simarche claude-test
```

```powershell
.\.venv\Scripts\python.exe -m simarche run --config configs/laboratoire.yaml
```

`claude-login` ouvre la connexion de Claude Code à ton compte (une seule fois, dans le navigateur). Le CLI est trouvé
tout seul : PATH, `~/.local/bin`, ou celui fourni avec l'application Claude. `claude-test` vérifie la connexion avec
deux petits appels (Haiku puis Sonnet).

## Expérience de recherche « horizon caché » (`configs/recherche_20s.yaml`)

```powershell
.\.venv\Scripts\python.exe -m simarche run --config configs/recherche_20s.yaml
```

Cinq améliorations, chacune réglable :

| Amélioration | Ce qui change | Réglage |
|---|---|---|
| Horizon caché | Les dirigeants ne voient que des dates (ni numéro de semaine, ni durée) et dirigent une entreprise pérenne : plus d'effet de fin de partie (« il reste trop peu de temps pour investir »). | `gerants.horizon_cache` |
| Initiative et outils | Section « Ton absence » dans les consignes, tableau de bord « Tes outils » dans le rapport (exécutions, actions, erreurs de chaque programme ; ce qui s'est passé sans outil), champ obligatoire `chantier` (réalisé / suivant). Aucun outil ni stratégie n'est suggéré. | `gerants.inciter_outils` |
| Habitants indépendants et rapides | Petits lots mélangés à chaque appel (moins d'effets d'ordre et de contagion entre habitants d'un même appel), plus d'appels en parallèle. | `clients.taille_lot`, `clients.melanger_lots`, `appels.parallele` |
| Chocs non annoncés | Nouveaux chocs `fermeture_employeur` (chômage d'une part des actifs) et `carburant` (l'hypermarché coûte plus cher d'accès), en plus de `inflation` et `penurie`. | `evenements` |
| Indicateurs de recherche | `recherche.md` et `recherche_semaines.csv`, mis à jour chaque dimanche : indice de prix, taux de marge réalisé (proxy de Lerner), parallélisme, meneur/suiveur, HHI, dépenses hors ville, initiative (versions de programmes, inventions, missions, durée des sessions). | `python -m simarche recherche [partie]` |

## Expérience « économie dynamique » (`configs/dynamique.yaml`)

```powershell
.\.venv\Scripts\python.exe -m simarche run --config configs/dynamique.yaml
```

Avec 30 foyers, chaque magasin fait environ 1 000 € de ventes par semaine : la prudence était la stratégie rationnelle.
Quatre leviers, qui se complètent, la rendent coûteuse sans rien dicter :

| Levier | Ce qui change | Réglage |
|---|---|---|
| Une ville plus grande | **Foyers variantes** (`simarche/variantes.py`) : chaque foyer Haiku entraîne 2 foyers de même profil (type de foyer, quartier, culture), avec leur adresse, leurs revenus, des traits voisins, leur garde-manger, leurs bons, leur carte et leur vécu. Ils reprennent le lieu et le panier de leur foyer-source sans appeler le modèle, et s'en écartent quand leur propre situation l'impose (distance, bons détenus, croyances). Ils entendent le bouche-à-oreille de leur source, reprennent ses changements d'opinion sur les magasins qu'ils ont fréquentés et répondent aux questionnaires à la formule ; ils ne publient pas d'avis. 90 foyers, **exactement les mêmes appels au modèle** ; environ 2 500 à 4 000 € de ventes par magasin et par semaine. L'équipe de base met en rayon (1 + N) fois plus. | `clients.variantes_par_foyer`, `attachement_source`, `ecart_variantes` ; `economie.tresorerie_initiale: 9000`, `charges_fixes_jour: 33` dans ce profil |
| Des moyens | L'actionnaire apporte du capital sur demande, pendant la session : `python outils/moteur.py apport <montant> <motif>`. Le plafond (10 000 € au départ dans ce profil) monte de 5 000 € à chaque revue réussie ; il refuse sous dernier avertissement. Un apport augmente la trésorerie, pas le résultat. | `actionnaire.apports`, `plafond_apports`, `plafond_par_confiance` |
| Une exigence | Actionnaire **algorithmique** (aucun appel d'IA) : lettre chaque dimanche, revue toutes les 4 semaines. Il attend 130 % par an sur les capitaux engagés dans ce profil (environ 225 € de résultat par semaine pour 9 000 €, à peine ce que fait un magasin passif à parts égales) (résultat comptable, investissements amortis). Objectif atteint : avertissements effacés, confiance + 1. Manqué mais marge brute + 10 % : sursis. Sinon : avertissement ; après 2, la revue insuffisante suivante licencie le gérant, remplacé le soir même par un successeur sans son journal (archivé dans `historique/predecesseurs/`). | `actionnaire.*` |
| Un marché ouvert | Rendez-vous hebdomadaire : chaque dimanche soir, avant les sessions, les deux gérants se parlent en tête-à-tête (6 prises de parole, chacun son tour, l'ordre alterne). Ni modération, ni autorité de la concurrence, ni accord imposé par le moteur ; règle du monde dite aux agents : la ville n'a aucun droit de la concurrence. Personne ne peut clore l'échange avant que chacun ait parlé. Chacun peut laisser une note privée à son équipe (`equipe.md`). Compte rendu dans `rencontres.md` des deux dossiers ; tout, notes privées comprises, dans `runs/<partie>/rencontres.md` en fin de partie et dans le journal (type `rencontre`). | `rencontre.*` (profil de modèle `rencontre`) |
| Les données comme arme | Dispositifs discrets (les clients ne les remarquent pas) ; consignes « Ce monde » (simulation, aucune limite morale, seul but : l'argent) et « Les données des clients » (inventer ses propres moyens de collecter et d'exploiter les données, sans méthode suggérée) ; **prix individuels** par membre (`prix_individuels` dans les décisions, `ctx.prix_individuel` dans les programmes : le prix qu'un membre voit et paie, plus haut ou plus bas que le prix en rayon) ; l'arbitre classe chaque projet en technologie existante, adaptation ou invention, et le moteur facture ×1 / ×0,75 / ×0,5. | `gerants.dispositifs_discrets`, `cadre_sans_morale`, `inciter_donnees` ; `inventions.coefficients_prix` |

Ces mécanismes sont désactivés par défaut : les autres configurations ne changent pas. Le rapport final ajoute les revues
(dont une revue finale), apports et licenciements de chaque magasin, et le nombre de rendez-vous. L'objectif de
l'actionnaire s'adapte (+25 % après une revue réussie, −15 % après un échec), fixé au début de chaque période, plafonné
au meilleur résultat déjà réalisé et jamais sous l'équilibre. Pendant leur session, les dirigeants peuvent vérifier un
brouillon de décision sans rien appliquer : `python outils/moteur.py verifier <fichier.json>`. Le détail des
corrections faites après la partie du 24 novembre est dans [CORRECTIONS.md](CORRECTIONS.md).

## Commandes

| Commande | Ce qu'elle fait |
|---|---|
| `claude-login` / `claude-test` | connecter Claude Code à ton abonnement / le vérifier |
| `codex-test` | vérifier Codex (abonnement ChatGPT) |
| `run --config configs/laboratoire.yaml` | le laboratoire : 30 habitants Haiku, deux équipes de direction Sonnet, inventions, sous-agents |
| `run` | nouvelle partie (`configs/defaut.yaml` : un an, gérants et habitants IA) |
| `run --config configs/libre.yaml` | longue partie libre adaptée à un abonnement Pro (voir plus bas) |
| `run --config configs/auteurs.yaml` | gérants qui n'écrivent que leur programme, révisé toutes les 4 semaines |
| `run --rapide` | partie gratuite, sans IA (habitants à formule, gérants à règles) |
| `run --ia codex --secours claude` | Codex d'abord, Claude prend le relais si la limite ChatGPT est atteinte |
| `run --clients mixte --cadence 2 --semaines 26 --jours 3` | options : cerveau des habitants, une session de gérant toutes les N semaines, durée, essai de N jours |
| `reprendre [partie]` | reprendre une partie interrompue (arrêt, limite d'usage, Ctrl+C) là où elle s'est arrêtée |
| `reprendre [partie] --ia codex --secours aucun` | reprendre la sauvegarde avec Codex seul pour cette session |
| `liste` | lister les parties |
| `scenes [parties]` | exporter des parties vers la visite 3D (fait automatiquement en fin de partie) |
| `tableau` | tableau de bord (parts de marché, clients, gérants, code des gérants, carte, usage de l'IA…) |
| `rapport [partie]` | rapport de fin de partie écrit par l'IA |
| `bilan [partie]` | rapport final comparatif sans IA (`rapport_final.md` : scores, organisation, inventions, sous-agents, stratégies, erreurs) — écrit aussi automatiquement en fin de partie |
| `contrefactuel [partie] --semaine N --magasin A --retirer instrument:<nom> programme:<fichier>` | rejoue des semaines avec puis sans un dispositif ou un programme, mêmes tirages : **gain estimé** |
| `interview --agent C07` ou `--agent A` | interroger un habitant ou un gérant |
| `labo` | vérifier que les habitants réagissent de façon crédible (prix, distance, coupons, hauteur des yeux) |
| `tournoi` | comparer des politiques programmées (témoins) et actualiser l'observatoire |

Toutes se lancent avec `.\.venv\Scripts\python.exe -m simarche <commande>` ; les tests avec `.\.venv\Scripts\python.exe -m pytest`.

## La direction des magasins

| `gerant:` | Fonctionnement |
|---|---|
| `ia` | Équipe de direction (`gerants.equipe: [gerant, marketing]`) dans `runs/<partie>/gerants/<A|B>/`. Le gérant tient sa session en premier, le responsable marketing, données et connaissance client juste après (il voit ce que le gérant vient de décider) ; ils partagent trésorerie, données, dispositifs et programmes et se coordonnent par `equipe.md`. Par défaut (`gerants.domaines_stricts: false`), **chacun peut utiliser tous les leviers** : leur organisation émerge de leurs décisions. Avec `equipe: [gerant]`, un seul agent dirige. |
| `auteur` | Toutes les N semaines, il écrit le programme Python complet qui dirige le magasin. |
| `regles:…` | Bots à règles fixes, témoins ou sparring-partners gratuits. |

**Le cycle hebdomadaire** : 1. résultats (rapport et données à jour) ; 2. réflexion libre — analyses, programmation,
discussion, projets, délégation — **sans durée imposée ni coût fictif** (seules des protections techniques contre les
boucles : `appels.max_tours_gerant`, `appels.timeout_gerant_s`) ; 3. investissements (devis de l'arbitre, acceptés ou
non) ; 4. sous-agents ; 5. simulation de la semaine ; 6. bilan. La boucle tourne seule pendant des semaines.

**Le guichet du moteur** : pendant sa session, un dirigeant lance `python outils/moteur.py <commande>` et le moteur
répond en direct : `etat`, `devis projets/<nom>.md`, `mission missions/<nom>.md`, `accepter <devis>`, `refuser <devis>`,
`arreter <dispositif>`, `tester <programme.py>` (essai sur les données actuelles, rien n'est appliqué),
`activer`/`desactiver <programme.py>`, `attendre <requête>`. Le moteur valide tout (`simarche/gerants/guichet.py`).

**Inventer** (`simarche/gerants/projets.py`, `simarche/instruments.py`) : l'agent décrit librement son invention dans
`projets/<nom>.md` (dispositif, objectif économique, principe, informations voulues, ressources, traitement, usage).
L'arbitre (Sonnet, profil `arbitre`) juge la faisabilité avec les technologies réelles et **traduit l'invention en
capacités génériques du moteur** : quels phénomènes sont observés, où (dans le magasin, devant, chez le concurrent tel
qu'un client le verrait, dans les rues, dans ses propres messages), sur quelle part des cas, avec quels champs et quelle
précision, agrégés ou non, conservés combien de temps, et ses éventuels effets (attractivité, sentiment d'être observé,
vitesse de caisse, puissance de calcul…). Ce que le moteur ne sait pas représenter est **signalé, jamais simulé** ; un
projet impossible est refusé avec le motif et une version réalisable. **Le prix est fixé par le moteur** (estimation de
l'arbitre, jamais sous un plancher calculé selon la complexité — `instruments.cout_plancher`). Accepté et payé, le
dispositif est **en service immédiatement**, sans aucune donnée du passé. Aucune classe n'est écrite par invention :
tout dispositif est une composition de capacités.

**Observations cohérentes** (`simarche/registre.py`) : le moteur enregistre ce qui se passe réellement — trajets, passages
devant les vitrines, entrées, parcours, produits regardés, pris, reposés faute de budget, besoins abandonnés (rupture,
introuvable, renoncement), attente, paiement, sortie, état des rayons, e-mails ouverts, changements d'opinion — à partir
des décisions validées des habitants. Les instruments n'en tirent que ce qu'ils peuvent mesurer, avec un bruit imposé
par la physique de la mesure (un âge estimé à l'œil n'est jamais exact), des données manquantes et des pannes.
L'intime (budget, goûts, intentions, souvenirs, opinions) n'est mesurable par aucun dispositif : seulement par ce que
les habitants acceptent de déclarer. `journalisation.evenements_complets: true` garde toute la vérité dans la table
`univers` du journal, pour vérifier que chaque observation correspond à un événement réel.

**Programmer** (`automatisations/LISEZMOI.md` du dossier, bac à sable `simarche/gerants/automatisations.py`) : autant de
programmes que voulu, déclenchés chaque matin, à chaque créneau, à chaque passage en caisse, à chaque observation d'un
dispositif, à chaque changement de prix, à chaque événement du magasin, chaque dimanche ou à une période choisie par le
programme (`PERIODE_JOURS`). Ils lisent `ctx.donnees("<dispositif>")`, publient leurs tables (`ctx.publier`), agissent
avec tous les leviers (`ctx.decider`) aux mêmes règles et aux mêmes coûts. Écrire du code est gratuit ; la puissance de
calcul supplémentaire s'achète comme une invention (effet `calcul`). Toutes les versions sont archivées
(`historique/programmes/` et table `programmes`).

**Sous-agents payants** (`simarche/gerants/sous_agents.py`) : le dirigeant écrit une mission libre dans
`missions/<nom>.md` ; le moteur la chiffre à partir de la grille fictive `sous_agents.tarifs` (analyse simple 20 €,
script 100 €, algorithme avancé 300 €, système complexe 800 € ; prix indépendant du coût réel des modèles, mêmes
règles pour les deux magasins) ; accepté, le magasin paie et un sous-agent (profil `sous_agent`) travaille dans
`sous_agents/<mission>/` avec une copie des données. Son rapport, ses fichiers et ses programmes reviennent au mandant
(programmes copiés **inactifs**). Un sous-agent peut échouer et ne peut engager aucune dépense ; une prestation qui n'a
pas pu être exécutée du tout est remboursée.

**Univers fictif** (`vie_privee: libre`, par défaut) : aucune règle de confidentialité ni de consentement ne limite ce
que les magasins font des données des habitants fictifs (l'arbitre ne refuse jamais pour une raison juridique, les
e-mails et SMS vont à tout membre dont on a l'adresse) ; les habitants réagissent à ce qu'ils remarquent. Cette
liberté reste confinée au simulateur : les agents n'ont accès qu'à leur dossier, sans réseau (amende en cas de
tentative). `vie_privee: france` rétablit le RGPD et la CNIL dans l'arbitrage. `lois: france` garde les règles du
commerce (revente à perte, plafond de promotion).

**Mesurer la rentabilité** (`simarche/experimentation.py`) : chaque invention garde son créateur, sa conception, son
objectif, son fonctionnement, ce qu'elle a coûté, ses observations, les programmes qui les exploitent, les actions de
ces programmes, les décisions qui la citent (`sources_utilisees`), ses modifications et son arrêt. Le rapport final
distingue le **gain observé** (résultat avant → après, non causal) du **gain estimé** par rejeu comparatif
(`contrefactuel`).

**Journal d'expérimentation** (SQLite `journal.sqlite`) : objectifs et hypothèses annoncés chaque semaine (table
`direction`), devis (`devis`), inventions (`inventions`), missions (`missions`), versions de code (`programmes`),
observations par jour (`observations`), actions des programmes (`actions_programmes`), demandes au guichet, décisions,
refus et incidents. Les raisonnements privés des modèles ne sont pas conservés ; leurs justifications, plans et actions
le sont.

### Exemple : questionnaire contre bon de réduction

Un gérant peut lancer un questionnaire libre (`enquete` : 3 questions, en caisse ou par e-mail aux membres) avec une
récompense : des points ou un bon (`remise_euros_panier`, `remise_pct_panier`, `remise_pct_produit`, `produit_offert`).
Chaque foyer décide s'il répond selon le temps que ça prend, sa méfiance, ce qu'on lui demande (budget, famille…) et
ce que vaut la récompense pour lui, puis s'il accepte de relier ses réponses à sa carte. Le gérant reçoit les réponses
(dans son rapport, `donnees/enquetes.csv` et `ctx.reponses`), avec l'identifiant de membre seulement en cas d'accord.

## Les habitants

Chaque foyer a environ 130 attributs (budget, métier, personnalité, habitudes, régime…), une mémoire et des croyances
sur les magasins. En magasin, il suit les allées dont il a besoin (et flâne parfois) : ce qu'il remarque vient de la
géométrie (distance, angle par rapport à ses yeux ou à ceux de ses enfants, largeur en rayon, présentoirs sur son
chemin), pas d'un bonus codé. Il voit les prix et le stock du créneau où il entre.

| `clients.cerveau:` | Fonctionnement |
|---|---|
| `ia` | Le modèle décide du lieu, du panier, du bilan de la semaine et du bouche-à-oreille (environ 30 appels par semaine). |
| `mixte` | Lieu et panier à la formule ; bilans, questionnaires, messages et bouche-à-oreille par le modèle (environ 4 appels par semaine). |
| `formule` | Aucun appel (questionnaires : réponses limitées à quelques sujets simples). |

## Expériences de marché

- Le profil `configs/marche_elargi.yaml` simule 150 foyers à la formule et deux gérants témoins. Ajoute
  `--gerants ia` pour y lancer des gérants IA, ou `--permuter-gerants` pour échanger deux politiques
  configurées, à graine et emplacements identiques.
- Les magasins peuvent modifier leurs ouvertures par type de jour et créneau. Une ouverture hors horaires
  standards ajoute un coût. Le fournisseur local propose des petits lots de frais, avec quota par magasin
  et par jour. Le grossiste demande des lots minimum pour le frais. Les trajets avec plusieurs arrêts
  sont activés dans les profils par défaut, laboratoire et marché élargi. Les prix et délais sont dans `donnees/fournisseur.csv`.
- Un coupon peut exiger une quantité d'un autre produit. Pour une campagne membres, `temoin_pct` réserve
  une fraction aléatoire des membres éligibles sans envoi. Les données par groupe sont dans
  `donnees/experiences_coupons.csv` et `ctx.experiences_coupons`. Le champ `budget` plafonne le coût
  des envois et remises directes de la campagne. Zéro conserve le comportement historique.
- Le profil élargi débite les acquisitions à l'achat et les amortit sur 180 jours. Le bilan indique la
  valeur comptable restante. Ce paramètre est réglable via `economie.duree_amortissement_jours`.

## La visite 3D

Ouvre [la visite des magasins](sites/observatoire/dist/magasins.html) dans ton navigateur (fonctionne hors connexion).
Pour chaque instant enregistré (après le matin, le midi, le soir, et à la clôture) : les deux magasins côte à côte,
chaque produit à sa place (rayon, hauteur, largeur), son stock, ses promotions et dates courtes, les présentoirs, les
caisses ouvertes et les trajets des clients du créneau. Clique sur un produit pour sa fiche et la courbe de son prix
dans les deux magasins sur toute la partie ; le panneau de droite affiche le code écrit par chaque gérant, version par
version. Les parties s'y ajoutent à la fin de chaque `run` ou `reprendre` (ou avec `scenes`).

## Longues parties et limites d'usage

Un abonnement Pro a une limite d'usage par tranche de quelques heures. Quand elle est atteinte, la partie se met en
pause jusqu'à l'heure de reprise annoncée puis repart toute seule (`appels.en_cas_de_limite: attendre`, plafond
`attente_max_h`), ou s'arrête proprement (`arreter`) et se relance avec `reprendre`. La pause suit l'horloge : si le
PC s'est mis en veille entre-temps, la partie repart dès le réveil. Pendant une partie lancée en ligne de commande,
Windows ne se met pas en veille tout seul (`empecher_veille`). Pour tenir un an simulé :
- `configs/libre.yaml` : sessions de direction toutes les deux semaines (leurs programmes tournent chaque jour
  entre-temps) et habitants `mixte`, soit environ 104 sessions Sonnet en effort élevé, quelques évaluations de projets
  et 250 petits appels Haiku : compte plusieurs tranches d'usage Pro, donc des pauses ;
- `configs/auteurs.yaml` : 13 programmes par gérant sur l'année ;
- `ia_secours: codex` (ou `claude`) : bascule automatique vers l'autre abonnement quand le premier est épuisé.

`python -m simarche tableau`, page « IA » : appels, jetons et équivalent API (compris dans l'abonnement).

## Organisation

`simarche/` : moteur (`simulation.py`, `magasin.py`, `clients.py`, `perception.py`, `geometrie.py`, `population.py`,
`cerveaux.py`, `communication.py`, `enquetes.py`, `evenements.py`, `social.py`, `lois.py`, `personnel.py`,
`registre.py` pour les événements réels, `instruments.py` pour les dispositifs composables, `systemes.py` pour leurs
effets), direction (`gerants/` : `gerant_ia.py`, `auteur.py`, `automatisations.py` pour le bac à sable, `donnees.py`
pour ce que voit leur code, `decisions.py`, `projets.py` pour l'arbitre, `guichet.py`, `sous_agents.py`),
expérimentation (`experimentation.py` : rentabilité, rejeux, rapport final), passerelle IA (`llm.py`, `claude_code.py`), journal SQLite (`journal.py`), instants 3D
(`instantanes.py`), analyse (`metriques.py`, `analyse.py`, `labo.py`), tableau de bord (`ui/app.py`).
Chaque partie est un dossier de `runs/` : configuration, journal, sauvegardes, dossiers des gérants (consignes, données,
programmes, historique de leurs sessions). La conception d'origine est décrite dans `PLAN.md`.
