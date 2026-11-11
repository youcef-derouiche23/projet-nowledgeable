# Déploiement sur une VM Azure

Le workflow `cd.yml` contient un job `deploy-vm` qui déploie l'environnement de production
(PostgreSQL + S3 + système de traitement planifié) sur une machine virtuelle Azure, par SSH.
Il ne s'exécute que si la variable de dépôt `AZURE_VM_HOST` est définie.

Enchaînement du CD : construction de l'image -> test du déploiement sur un environnement
éphémère -> déploiement sur la VM.

## 1. Créer la VM (une seule fois)

Depuis le portail Azure, ouvrir **Cloud Shell** (icône `>_` en haut, mode Bash) :

```bash
# Clé SSH réservée au déploiement
ssh-keygen -t ed25519 -f ~/.ssh/pipeline_deploy -N "" -C "github-actions-deploy"

# Groupe de ressources + VM Ubuntu
az group create -n rg-pipeline -l <region-autorisee>
az vm create -g rg-pipeline -n vm-pipeline \
  --image Ubuntu2404 --size <taille-avec-quota> \
  --admin-username azureuser \
  --ssh-key-values ~/.ssh/pipeline_deploy.pub \
  --public-ip-sku Standard

# Installer Docker sur la VM
az vm run-command invoke -g rg-pipeline -n vm-pipeline \
  --command-id RunShellScript \
  --scripts "curl -fsSL https://get.docker.com | sh && usermod -aG docker azureuser"

# Noter l'adresse IP publique et la clé privée
az vm show -d -g rg-pipeline -n vm-pipeline --query publicIps -o tsv
cat ~/.ssh/pipeline_deploy
```

`az vm create` n'ouvre que le port 22 (SSH). Ne pas ouvrir les ports 5432 et 8333 : la base et
le S3 doivent rester inaccessibles depuis Internet.

### Limite rencontrée avec Azure for Students (novembre 2026)

La VM n'a pas pu être créée sur l'abonnement étudiant utilisé pour le projet :

- la stratégie de l'abonnement n'autorise que cinq régions (`germanywestcentral`, `polandcentral`,
  `norwayeast`, `austriaeast`, `belgiumcentral`) : `francecentral` est refusée ;
- dans ces régions, aucune taille n'est à la fois proposée et couverte par un quota : par exemple
  `Standard_D2ls_v7` est proposée en `germanywestcentral` mais son quota est de 0 vCPU, et les
  familles qui ont un quota (série B, D v3/v4...) n'y sont pas proposées.

Le job `deploy-vm` n'a donc pas encore été exécuté. Il est prévu pour n'importe quelle VM Linux
joignable en SSH avec Docker installé : sur un abonnement sans ces restrictions, adapter `-l` et
`--size` dans la commande ci-dessus. Pour vérifier ce qui est disponible :

```bash
az policy assignment list --query "[].parameters.listOfAllowedLocations.value" -o tsv   # régions autorisées
az vm list-usage -l <région> -o table                                                   # quotas par famille
az vm list-skus -l <région> --resource-type virtualMachines -o table                    # tailles proposées
```

## 2. Configurer GitHub (une seule fois)

Dans le dépôt : **Settings > Secrets and variables > Actions**.

| Type | Nom | Valeur |
|---|---|---|
| Variable | `AZURE_VM_HOST` | adresse IP publique de la VM |
| Variable | `AZURE_VM_USER` | `azureuser` (facultatif, valeur par défaut) |
| Secret | `AZURE_VM_SSH_KEY` | contenu complet de `~/.ssh/pipeline_deploy` |
| Secret | `PROD_POSTGRES_PASSWORD` | mot de passe de la base (facultatif) |
| Secret | `PROD_S3_ACCESS_KEY` / `PROD_S3_SECRET_KEY` | identifiants S3 (facultatif) |

Définir les trois secrets `PROD_*` **avant le premier déploiement** : le mot de passe PostgreSQL
est fixé à la création du volume et ne change plus ensuite.

## 3. Déployer

Pousser sur `main` : la CI tourne, puis le CD. Ou lancer le CD à la main (Actions > CD > Run workflow).

## 4. Vérifier sur la VM

```bash
ssh -i ~/.ssh/pipeline_deploy azureuser@<IP>
cd ~/pipeline
docker compose ps                       # 3 conteneurs
docker compose logs --tail 30 processor # cycles du planificateur : [OK] puis [SKIP]
docker compose exec db psql -U pipeline -c "SELECT filename, processed_at FROM processed_files"
```

## 5. Arrêter les frais

```bash
az vm deallocate -g rg-pipeline -n vm-pipeline   # arrête la VM (plus de facturation du calcul)
az group delete -n rg-pipeline --yes             # supprime tout après la soutenance
```
