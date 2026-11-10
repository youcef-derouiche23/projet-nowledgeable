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

## Lancer le projet en local

Prérequis : Docker Desktop, Python 3.13, et Tesseract pour l'OCR (`brew install tesseract` sur macOS).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d --wait                  # PostgreSQL + S3 (SeaweedFS, port 8333)
python -m pytest -v                          # 40 tests

python scripts/generate_csv.py               # 3 CSV dans data/incoming
python -m scripts.generate_images            # 3 images dans data/incoming
python -m scripts.upload_to_s3               # bucket + upload + vérification
python -m src.pipeline                       # traitement S3 -> base de données
```

Relancer la pipeline n'a aucun effet sur les fichiers déjà traités (`[SKIP]`).

### Environnement de production (local)

```bash
docker compose down                          # libère le port 8333
SCHEDULER_INTERVAL=10 docker compose -f docker-compose.prod.yml up -d --build --wait
docker compose -f docker-compose.prod.yml logs -f processor
```

Tout fichier déposé dans le bucket (`python -m scripts.upload_to_s3`) est traité au cycle suivant.

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

## Démonstrations de pannes

```bash
# Panne temporaire de la base : 2 échecs, puis réussite
SIMULATE_DB_FAILURES=2 DB_RETRY_DELAY=1 python -m src.pipeline
# Panne permanente : abandon après 3 tentatives, code de sortie 1
SIMULATE_DB_FAILURES=99 DB_MAX_ATTEMPTS=3 DB_RETRY_DELAY=1 python -m src.pipeline
# Panne temporaire du S3
SIMULATE_S3_FAILURES=2 S3_RETRY_DELAY=1 python -m src.pipeline
# Vraie panne : service S3 arrêté
docker compose stop s3 && S3_MAX_ATTEMPTS=3 S3_RETRY_DELAY=1 python -m src.pipeline; docker compose start s3
```

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
- Le CD déploie sur un runner GitHub (machine virtuelle éphémère) pour prouver le déploiement, pas sur un serveur
  persistant.
- L'OCR attend un format d'avis de virement fixe (libellés « IBAN origine », « Montant », etc.). Les IBAN sont validés
  par leur format, pas par leur clé de contrôle.
