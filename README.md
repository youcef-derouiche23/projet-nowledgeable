# Pipeline de traitement de transactions financières

Pipeline de données complète : des fichiers de transactions (CSV et **images d'avis de virement**) sont
déposés dans un stockage **S3 local**, récupérés, validés, traités, puis enregistrés dans une base
**PostgreSQL**. Le tout est testé automatiquement (CI) et déployé automatiquement avec Docker (CD).

## Architecture

```mermaid
flowchart LR
    SRC["Sources<br/>CSV + images"] -->|upload + vérification| S3[("S3 local<br/>SeaweedFS")]
    PLAN["Planificateur<br/>(toutes les N secondes)"] --> PIPE
    S3 -->|"récupération (retry)"| PIPE["Pipeline"]
    PIPE -->|"image : OCR Tesseract"| PIPE
    PIPE -->|"insertion atomique (retry)<br/>+ anti-doublon par hash"| DB[("PostgreSQL")]
```

Pour chaque fichier : récupération S3, détection d'un doublon (hash SHA-256), OCR si c'est une image,
validation, traitements (fonctions pures), puis insertion en **une seule transaction SQL**.

## Ce qui a été réalisé

| Niveau | Contenu |
|---|---|
| **1** | Génération de CSV déterministes, 4 traitements (somme envoyée par IBAN, par banque, somme reçue par IBAN, flag des montants > 5000), base de données, pipeline CI avec tests d'intégration, simulation d'un échec de pipeline, retry à l'insertion en base, anti-doublon |
| **2** | Service S3 local (SeaweedFS) en Docker, upload automatique des fichiers avec vérification, lecture depuis S3 (plus de dépendance au disque local), retry sur la récupération S3 |
| **3** | **OCR** : extraction de transactions depuis des images (Tesseract). **CD** : image Docker, déploiement automatique de l'environnement (base + S3 + traitement) et planificateur de traitement régulier |

## Structure du dépôt

```
scripts/generate_csv.py      génère 3 CSV déterministes (16 transactions, 5 flaggées)
scripts/generate_images.py   génère 3 images d'avis de virement (pour l'OCR)
scripts/upload_to_s3.py      crée le bucket, envoie les fichiers, vérifie leur présence
src/loader.py                chargement / validation des CSV, hash des fichiers
src/processing.py            traitements : fonctions pures
src/ocr.py                   image -> OCR -> validation -> CSV au format standard
src/storage.py               accès S3 (bucket, upload, liste, téléchargement avec retry)
src/database.py + schema.sql PostgreSQL : schéma et insertion atomique
src/retry.py                 retry générique (attente croissante, nombre maximal de tentatives)
src/pipeline.py              pipeline complète
src/scheduler.py             planificateur : relance la pipeline à intervalle régulier
tests/                       40 tests (unitaires et intégration)
Dockerfile                   image du système de traitement (Python + Tesseract)
docker-compose.yml           développement : PostgreSQL + S3
docker-compose.prod.yml      production : PostgreSQL + S3 + système de traitement planifié
.github/workflows/ci.yml     CI : tests + pipeline complète
.github/workflows/cd.yml     CD : construction de l'image, déploiement, vérification
```

## Lancer le projet et faire la démonstration, pas à pas

Ce guide est le déroulé exact de la démonstration. **Copiez uniquement le bloc de commandes** (le bloc gris, sans texte autour), collez-le dans le Terminal, puis lisez l'explication en dessous : elle suit l'ordre des lignes. Prérequis : Docker Desktop lancé, Python 3.13, Tesseract (`brew install tesseract` sur macOS), et les dépendances installées une fois avec `pip install -r requirements.txt` dans l'environnement virtuel (`python3 -m venv .venv`).

### Étape 1 : Préparer le Terminal

```bash
cd ~/projet-nowledgeable
source .venv/bin/activate
```

1. Se placer dans le dossier du projet.
2. Activer l'environnement virtuel Python : pytest, psycopg, boto3, pytesseract et pillow deviennent utilisables. Le Terminal affiche `(.venv)` au début de la ligne.

### Étape 2 : Repartir d'un environnement vide

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose down -v
docker compose up -d --wait
rm -f data/incoming/transactions_004.csv
```

1. Arrête et supprime l'environnement de production s'il tourne, avec ses données. Libère le port 8333. Sans effet s'il n'est pas lancé.
2. Supprime l'environnement de développement précédent avec ses données : base et stockage S3 repartent vides.
3. Démarre PostgreSQL et le stockage S3 (SeaweedFS) en arrière-plan, et attend qu'ils soient prêts. Doit afficher `Healthy` pour `db` et `s3`.
4. Supprime un éventuel fichier de test laissé par une démonstration précédente, pour obtenir des chiffres exacts. Sans effet s'il n'existe pas.

### Étape 3 : Lancer les tests automatiques

```bash
python -m pytest -q
```

Lance les 40 tests (traitements, base de données, intégration, retry, S3, OCR, planificateur) sur la vraie base et le vrai S3. **Résultat attendu :** `40 passed`. C'est ce que la CI exécute à chaque push.

### Étape 4 : Générer les données

```bash
python scripts/generate_csv.py
python -m scripts.generate_images
```

1. Crée 3 fichiers CSV fixes dans `data/incoming/` (16 transactions, dont les cas pièges 5000.00 et 5000.01). **Résultat attendu :** 3 lignes `OK`.
2. Dessine 3 images d'avis de virement (6200.00, 5000.00, 480.50) pour l'OCR. **Résultat attendu :** 3 lignes `OK`.

### Étape 5 : Envoyer les fichiers dans le stockage S3

```bash
python -m scripts.upload_to_s3
```

Crée le bucket `transactions`, y envoie les 6 fichiers, puis relit le bucket pour vérifier qu'ils y sont tous. **Résultat attendu :** 6 lignes `envoyé`, puis `vérification : 6/6 fichier(s) présent(s) dans le bucket`.

### Étape 6 : Traiter les fichiers, avec une panne simulée de la base (retry)

```bash
SIMULATE_DB_FAILURES=2 DB_RETRY_DELAY=1 python -m src.pipeline
```

Lance la pipeline complète : téléchargement depuis S3, OCR des images, validation, calculs, insertion dans PostgreSQL. `SIMULATE_DB_FAILURES=2` fait échouer volontairement les 2 premières insertions, et `DB_RETRY_DELAY=1` règle la première attente à 1 s (puis 2 s, elle double). **Résultat attendu :** 2 lignes `[RETRY]` avec leur attente, puis `[OCR]` et `[OK]` pour chaque fichier, et `Pipeline terminée : 6/6 fichier(s) traité(s)`.

### Étape 7 : Relancer la pipeline (anti-doublon) et compter les données

```bash
python -m src.pipeline
docker compose exec db psql -U pipeline -c "SELECT count(*), count(*) FILTER (WHERE flag) FROM transactions;"
```

1. Relance la pipeline sans panne. Chaque fichier a une empreinte (hash SHA-256) déjà enregistrée en base : il est reconnu et ignoré. **Résultat attendu :** `[SKIP] ... déjà traité` pour les 6 fichiers, sans nouvel `[OCR]`, et `0/6 fichier(s) traité(s)`.
2. Interroge PostgreSQL : nombre total de transactions, puis nombre de celles dont le flag est vrai (montant strictement supérieur à 5000). **Résultat attendu :** `19 | 6` (16 transactions CSV dont 5 flaggées, plus 3 images dont 1 flaggée). Les chiffres n'ont pas bougé malgré la relance : pas de doublon.

### Étape 8 : Panne permanente de la base : abandon propre

```bash
docker compose down -v
docker compose up -d --wait
python -m scripts.upload_to_s3
```

1. Détruit la base et le S3 pour repartir de zéro.
2. Les redémarre, vides.
3. Renvoie les 6 fichiers (toujours présents sur le disque) dans le nouveau bucket.

```bash
SIMULATE_DB_FAILURES=99 DB_MAX_ATTEMPTS=3 DB_RETRY_DELAY=1 python -m src.pipeline; echo "code de sortie : $?"
```

Simule une panne qui ne finit jamais (`99`), avec 3 tentatives maximum. La pipeline s'arrête avec un message clair, et `echo` affiche le code de sortie. **Résultat attendu :** `tentative 1/3`, `2/3`, `3/3` échouées, puis `abandon après 3 tentatives` et `code de sortie : 1` (le code 1 signifie « terminé en erreur » : c'est ce qui fait échouer la CI).

### Étape 9 : Panne temporaire du S3 : retry à la récupération

```bash
SIMULATE_S3_FAILURES=2 S3_RETRY_DELAY=1 python -m src.pipeline
```

Simule 2 échecs de téléchargement depuis le S3. La base est vide à ce stade (l'étape précédente n'a rien inséré), donc les fichiers sont traités. **Résultat attendu :** 2 lignes `[RETRY] récupération de ... depuis S3`, puis `[OK]` pour chaque fichier et `6/6 fichier(s) traité(s)`.

### Étape 10 : Vraie panne du S3 : le service est arrêté

```bash
docker compose stop s3
S3_MAX_ATTEMPTS=3 S3_RETRY_DELAY=1 python -m src.pipeline; echo "code de sortie : $?"
docker compose start s3
```

1. Arrête réellement le conteneur du stockage S3.
2. La pipeline essaie de lister le bucket, échoue 3 fois, puis s'arrête proprement. **Résultat attendu :** 3 lignes `[RETRY] liste des fichiers du bucket transactions`, `abandon après 3 tentatives`, `code de sortie : 1`.
3. Redémarre le stockage S3.

### Étape 11 : Voir le contenu de la base de données

La base contient les données insérées à l'étape 9 (les pannes des étapes 8 et 10 n'ont rien inséré ni supprimé).

```bash
docker compose exec db psql -U pipeline -c "SELECT f.filename, count(*) AS transactions, count(*) FILTER (WHERE t.flag) AS flaggees FROM transactions t JOIN processed_files f ON f.id = t.file_id GROUP BY f.filename ORDER BY f.filename;"
docker compose exec db psql -U pipeline -c "SELECT datetime_transaction, banque_source, iban_origine, iban_destinataire, montant FROM transactions WHERE flag ORDER BY montant DESC;"
```

1. Une ligne par fichier traité, avec son nombre de transactions et son nombre de transactions flaggées. **Résultat attendu :** 6 lignes (3 CSV et 3 images), 19 transactions au total, 6 flaggées.
2. Le détail des transactions flaggées (montant strictement supérieur à 5000), de la plus grosse à la plus petite. **Résultat attendu :** 6 lignes, de 10000.00 jusqu'à 5000.01. Le montant 5000.00 n'y est pas.

### Étape 12 (optionnelle, 2 minutes) : la production et le planificateur

```bash
docker compose down -v
SCHEDULER_INTERVAL=10 docker compose -f docker-compose.prod.yml up -d --build --wait
```

1. Supprime l'environnement de développement pour libérer le port 8333.
2. Construit l'image Docker du système de traitement, puis démarre 3 conteneurs : base de données, S3 et traitement. `SCHEDULER_INTERVAL=10` règle le planificateur sur un cycle toutes les 10 secondes (60 par défaut). Prend 1 à 2 minutes.

```bash
docker compose -f docker-compose.prod.yml ps
python -m scripts.upload_to_s3
sleep 25
```

1. Liste les services de production. **Résultat attendu :** `db`, `s3` et `processor` démarrés.
2. Dépose les 6 fichiers dans le S3 de production (port 8333).
3. Laisse au planificateur le temps d'effectuer plusieurs cycles.

```bash
docker compose -f docker-compose.prod.yml logs processor | grep -E "OCR|\[OK\]|SKIP|cycle"
docker compose -f docker-compose.prod.yml down -v
```

1. Affiche le journal du planificateur. **Résultat attendu :** des cycles sans fichier, puis `[OCR]` et `[OK]` au cycle qui trouve les fichiers, puis uniquement des `[SKIP]` aux cycles suivants (aucun OCR refait).
2. Arrête et supprime la production.

### Étape 13 : les preuves sur GitHub

Dans l'onglet **Actions** du dépôt : la CI n°3 rouge (bug volontaire `>=`) puis la n°4 verte (correction), et le workflow **CD** vert avec ses deux jobs (construction de l'image Docker, déploiement de l'environnement de production).

## Choix techniques

- **Données déterministes** : chaque traitement est vérifiable avec des résultats connus à l'avance
  (cas limites : `5000.00` n'est pas flaggé, `5000.01` l'est).
- **Fonctions pures** pour les traitements, séparées des entrées/sorties. Montants en `Decimal`.
- **Insertion atomique** : un fichier est enregistré en une seule transaction SQL. En cas d'erreur, tout est
  annulé : aucun fichier n'est jamais traité à moitié.
- **Anti-doublon** : le hash SHA-256 du contenu identifie un fichier déjà traité, même renommé. Contrôle effectué
  avant l'OCR et à chaque tentative d'insertion (y compris après un retry).
- **Retry** : seules les pannes temporaires sont réessayées (base indisponible, connexion S3 perdue, erreur HTTP 5xx),
  avec une attente croissante. Un fichier absent, un CSV invalide ou une image illisible échouent immédiatement.
  Au-delà du maximum de tentatives, la pipeline s'arrête avec un message clair et un code de sortie non nul.
- **OCR** : une image est un avis de virement. Les champs sont extraits puis nettoyés (espaces parasites dans un IBAN,
  virgule décimale, « EUR ») et validés, puis convertis en CSV : l'image suit ensuite exactement le même flux qu'un CSV.
- **Planificateur** : un cycle toutes les N secondes ; un cycle en erreur n'arrête pas le service.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `DATABASE_URL` | `postgresql://pipeline:pipeline@localhost:5432/pipeline` | connexion à PostgreSQL |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `http://localhost:8333` / `admin` / `secret` | accès S3 |
| `S3_BUCKET` | `transactions` | bucket des fichiers à traiter |
| `DB_MAX_ATTEMPTS` / `DB_RETRY_DELAY` | `3` / `2` | retry de l'insertion en base |
| `S3_MAX_ATTEMPTS` / `S3_RETRY_DELAY` | `3` / `2` | retry de la récupération S3 |
| `SCHEDULER_INTERVAL` | `60` | secondes entre deux cycles du planificateur |
| `SIMULATE_DB_FAILURES` / `SIMULATE_S3_FAILURES` | `0` | simule N pannes (démonstration) |

## CI / CD

**CI** (`ci.yml`, à chaque push) : démarre PostgreSQL et S3 (`docker compose`), installe Tesseract, lance les 40 tests,
génère les CSV et les images, crée le bucket et envoie les fichiers (avec vérification de leur présence), puis lance la
pipeline. En cas d'échec, les logs des conteneurs sont affichés.

**CD** (`cd.yml`, déclenché automatiquement quand la CI est verte sur `main`) :
1. construit l'image Docker du système de traitement et la publie sur GitHub Container Registry ;
2. déploie l'environnement de production (`docker-compose.prod.yml` : base de données, S3, planificateur) ;
3. dépose des fichiers dans le S3 de production et vérifie que le traitement planifié les a traités
   (3 fichiers, 16 transactions, 5 flaggées).
4. si la variable de dépôt `AZURE_VM_HOST` est définie, déploie le même environnement par SSH sur une machine
   virtuelle Azure (job `deploy-vm`, voir [docs/DEPLOIEMENT_AZURE.md](docs/DEPLOIEMENT_AZURE.md)).

## Captures d'écran

### 1. Simulation d'un échec de pipeline (CI)

Un bug est introduit volontairement dans le seuil de flag (`>=` au lieu de `>`). Les tests le détectent en local, la CI
échoue sur l'étape « Lancer les tests » et **les étapes suivantes ne s'exécutent pas** : aucune donnée fausse n'atteint la
base. Un second commit corrige le seuil et la CI repasse au vert.

**Cause du problème** : le commit « Bug volontaire » remplace `>` par `>=` dans `flag_large_transactions`.
![Cause du bug](docs/screenshots/01_cause_bug.png)

**Message d'erreur** : 3 tests en échec (`5000.00` est flaggé à tort : `6 == 5`, `3 == 2`).
![Tests en échec](docs/screenshots/02_tests_en_echec.png)

**Échec de la pipeline** (run #3) : « Lancer les tests » en rouge, étapes suivantes ignorées.
![CI rouge](docs/screenshots/03_ci_rouge.png)

**Correction apportée** : retour à `>` (seuil strictement supérieur à 5000).
![Correction](docs/screenshots/04_correction.png)

**Réussite après correction** (run #4).
![CI verte après correction](docs/screenshots/05_ci_verte_apres_correction.png)

**Historique** : succès, échec volontaire (#3), puis correction (#4).
![Historique CI](docs/screenshots/06_historique_ci.png)

### 2. Retry à l'insertion en base de données

**Panne temporaire** (`SIMULATE_DB_FAILURES=2`) : deux échecs, attente croissante (1 s, 2 s), puis réussite.
![Retry DB réussi](docs/screenshots/07_retry_db_reussi.png)

**Panne permanente** (`SIMULATE_DB_FAILURES=99`) : abandon après 3 tentatives, message clair, code de sortie 1.
![Retry DB abandon](docs/screenshots/08_retry_db_abandon.png)

### 3. Stockage S3 local

**Upload et vérification** : 3/3 fichiers présents dans le bucket, traités ; une seconde exécution ignore les fichiers
déjà traités (`[SKIP]`, anti-doublon).
![Upload S3 et anti-doublon](docs/screenshots/09_upload_s3_et_anti_doublon.png)

**Retry S3** (`SIMULATE_S3_FAILURES=2`) : deux échecs de récupération, puis réussite.
![Retry S3](docs/screenshots/10_retry_s3_reussi.png)

**Vraie panne** (service S3 arrêté) : abandon propre après 3 tentatives, code de sortie 1, puis redémarrage du service.
![Panne S3](docs/screenshots/11_panne_s3_abandon.png)

### 4. OCR : images d'avis de virement

**Exemple d'image** lue par l'OCR (`avis_virement_001.png`, montant 6200.00 EUR, donc flaggé).
![Avis de virement](docs/screenshots/12_avis_virement.png)

**Pipeline complète** : 6/6 fichiers envoyés puis traités (3 images via OCR, 3 CSV).
![Pipeline CSV et images](docs/screenshots/13_pipeline_csv_et_images.png)

**Suite de tests** : 40 tests passent.
![40 tests](docs/screenshots/14_tests_40_passes.png)

### 5. CD et planificateur

**Environnement de production** : base de données, S3 et système de traitement tournent en conteneurs Docker.
![Services de production](docs/screenshots/15_services_prod.png)

**Planificateur** : les fichiers déposés sont traités au cycle suivant (`[OCR]` puis `[OK]`), puis ignorés aux cycles
suivants (`[SKIP]`), sans refaire d'OCR.
![Planificateur](docs/screenshots/16_planificateur.png)

**Nouveau fichier** : `transactions_004.csv` est déposé dans le bucket ; seul ce fichier est traité, les autres sont ignorés.
![Nouveau fichier](docs/screenshots/17_nouveau_fichier.png)

**Workflow CD** : construction de l'image Docker publiée sur GHCR, puis déploiement de l'environnement de production,
déclenché automatiquement par la CI verte.
![CD vert](docs/screenshots/18_cd_vert.png)

**CI verte sur le dernier commit** du dépôt.
![CI verte sur le dernier commit](docs/screenshots/19_ci_verte_dernier_commit.png)

## Limites connues

- Les identifiants (`admin` / `secret`, `pipeline` / `pipeline`) sont des valeurs de démonstration : en production réelle,
  ils viendraient de secrets.
- Le déploiement sur une machine virtuelle persistante est écrit (job `deploy-vm`) mais n'a pas pu être exécuté :
  sur l'abonnement Azure for Students utilisé, aucune taille de VM n'est à la fois proposée dans les régions autorisées
  et couverte par un quota. En attendant, le CD prouve le déploiement sur un runner GitHub (machine virtuelle éphémère).
- L'OCR attend un format d'avis de virement fixe (libellés « IBAN origine », « Montant », etc.). Les IBAN sont validés
  par leur format, pas par leur clé de contrôle.
