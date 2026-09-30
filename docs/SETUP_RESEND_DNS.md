# Guide de Déploiement & Configuration DNS : `sales.contextelab.ma` (Resend)

Ce guide décrit la configuration DNS requise pour le sous-domaine d'envoi **`sales.contextelab.ma`** utilisé par la plateforme de prospection ContexteLab via **Resend** (infrastructure AWS SES us-east-1), ainsi que les procédures de déploiement, de vérification automatisée et de montée en charge (warm-up).

---

## 1. Principes d'Architecture & Isolation de Réputation

Pour garantir une délivrabilité optimale et protéger la réputation du domaine principal :
- **Domaine principal (`contextelab.ma`)** : Réservé aux communications internes, contractuelles et aux échanges quotidiens de l'équipe (Google Workspace / Microsoft 365).
- **Sous-domaine dédié (`sales.contextelab.ma`)** : Dédié exclusivement à la prospection ciblée et aux campagnes outbound.
- **Sous-domaine technique Return-Path (`send.sales.contextelab.ma`)** : Dédié au traitement technique des rebonds et des enveloppes SMTP (RFC 5321).
- **Isolation DMARC & SPF** : Une éventuelle anomalie ou surchauffe sur le sous-domaine de prospection n'impacte pas la réputation ni la délivrabilité des boîtes nominatives de l'entreprise.

---

## 2. Tableau Récapitulatif des Enregistrements DNS

Voici la liste exacte des enregistrements à déclarer dans votre zone DNS (Cloudflare, OVH, Bind, cPanel, etc.) :

| Type | Nom / Hôte (Host) | Priorité | Cible / Valeur (Value) | TTL | Règle Cloudflare | Rôle & Justification |
| :--- | :--- | :---: | :--- | :---: | :---: | :--- |
| **TXT** | `sales` *(ou `sales.contextelab.ma`)* | - | `v=spf1 include:amazonses.com ~all` | 300 / Auto | **DNS Only** (Gris) | **SPF Principal** : Autorise les serveurs Resend / AWS SES à émettre des emails pour ce sous-domaine. |
| **CNAME** | `resend._domainkey.sales` | - | `resend._domainkey.sales.contextelab.ma.resend.email.` *(ou valeur fournie par Resend)* | 300 / Auto | **DNS Only** (Gris) | **DKIM** : Signature cryptographique asymétrique certifiant l'authenticité et l'intégrité de l'expéditeur. |
| **MX** | `send.sales` *(ou `send.sales.contextelab.ma`)* | `10` | `feedback-smtp.us-east-1.amazonses.com.` | 300 / Auto | **DNS Only** (Gris) | **Return-Path (MX)** : Réception et routage des notifications de rebonds (NDR) vers l'infrastructure Resend. |
| **TXT** | `send.sales` *(ou `send.sales.contextelab.ma`)* | - | `v=spf1 include:amazonses.com ~all` | 300 / Auto | **DNS Only** (Gris) | **Return-Path SPF** : Valide l'alignement SPF strict sur l'enveloppe SMTP Return-Path (`send.sales`). |
| **TXT** | `_dmarc.sales` *(ou `_dmarc.sales.contextelab.ma`)* | - | `v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma; sp=none;` | 300 / Auto | **DNS Only** (Gris) | **DMARC** : Politique d'alignement et collecte des rapports agrégés RUA. Mode d'observation initial `p=none`. |

---

## 3. Règle Impérative Cloudflare : DNS Only (Nuage Gris)

> [!CAUTION]
> **TOUS LES ENREGISTREMENTS CI-DESSUS DOIVENT IMPÉRATIVEMENT ÊTRE EN MODE « DNS ONLY » (NUAGE GRIS).**
> 
> Si un enregistrement est configuré en mode **« Proxied » (Nuage Orange)** :
> 1. Cloudflare interceptera le trafic au niveau applicatif HTTP/HTTPS (ports 80/443).
> 2. Cloudflare masquera les véritables cibles CNAME/MX et substituera ses propres adresses IP Anycast.
> 3. La résolution MX et DKIM deviendra inopérante pour les serveurs de messagerie distants (Gmail, Outlook, Yahoo).
> 4. La vérification Resend échouera systématiquement avec l'erreur `Unverified`.
> 5. Vos emails seront immédiatement classés en Spam ou rejetés pour défaut d'authentification.

---

## 4. Spécifications Détaillées des Enregistrements

### 4.1. SPF (Sender Policy Framework)
- **Hôte** : `sales.contextelab.ma` (ou `sales` selon l'interface de votre gestionnaire DNS).
- **Type** : `TXT`
- **Valeur** :
  ```text
  v=spf1 include:amazonses.com ~all
  ```
- **Précisions** :
  - Ne jamais créer plusieurs enregistrements TXT SPF sur le même hôte (violation stricte de la RFC 7208 qui invalide le SPF).
  - L'utilisation du tilde `~all` (SoftFail) est indispensable pendant la phase initiale de montée en charge. Après stabilisation complète de la chaîne d'envoi et confirmation de l'alignement DMARC à 100%, il pourra être basculé vers `-all` (HardFail).

### 4.2. DKIM (DomainKeys Identified Mail)
- **Hôte** : `resend._domainkey.sales.contextelab.ma` (ou `resend._domainkey.sales`)
- **Type** : `CNAME` (par défaut avec Resend) ou `TXT` (si clé publique brute)
- **Valeur CNAME** :
  ```text
  resend._domainkey.sales.contextelab.ma.resend.email.
  ```
- **Précisions** :
  - Le sélecteur standard utilisé par Resend est `resend`.
  - Attention aux gestionnaires DNS (comme cPanel ou OVH) qui ajoutent automatiquement le domaine principal à la fin du champ Hôte. Si vous saisissez `resend._domainkey.sales.contextelab.ma`, le nom résultant peut devenir `resend._domainkey.sales.contextelab.ma.contextelab.ma`, provoquant une erreur NXDOMAIN.

### 4.3. Return-Path / Mail From (MX et SPF)
Resend utilise une sous-zone technique (`send.sales.contextelab.ma`) pour gérer les messages non distribués (RFC 5321 MAIL FROM) :
- **Hôte MX** : `send.sales.contextelab.ma` (ou `send.sales`)
- **Priorité MX** : `10`
- **Cible MX** :
  ```text
  feedback-smtp.us-east-1.amazonses.com.
  ```
- **Hôte SPF** : `send.sales.contextelab.ma` (ou `send.sales`)
- **Type** : `TXT`
- **Valeur SPF** :
  ```text
  v=spf1 include:amazonses.com ~all
  ```

### 4.4. DMARC (Domain-based Message Authentication, Reporting and Conformance)
- **Hôte** : `_dmarc.sales.contextelab.ma` (ou `_dmarc.sales`)
- **Type** : `TXT`
- **Valeur** :
  ```text
  v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma; sp=none;
  ```
- **Précisions** :
  - `p=none` : Politique de surveillance passive. Aucun message légitime n'est bloqué lors de la mise en service.
  - `rua=mailto:dmarc@contextelab.ma` : Réception quotidienne des rapports d'alignement XML émis par Google, Microsoft, Yahoo.
  - `sp=none` : Politique applicable aux sous-domaines éventuels.
  - Progression recommandée : Après 14 jours de warm-up sans anomalie d'alignement, passer à `p=quarantine; pct=25`, puis `p=quarantine; pct=100`, et enfin `p=reject; pct=100`.

---

## 5. Procédure de Déploiement Pas-à-Pas

### Étape 1 : Ajout du Domaine dans Resend
1. Connectez-vous sur le tableau de bord [Resend Dashboard](https://resend.com/domains).
2. Cliquez sur **Add Domain**.
3. Saisissez : `sales.contextelab.ma`.
4. Sélectionnez la région : **us-east-1 (N. Virginia)**.
5. Notez les valeurs exactes affichées par Resend pour SPF, DKIM et Return-Path.

### Étape 2 : Déclaration dans le Gestionnaire DNS
1. Accédez à la console de gestion DNS de `contextelab.ma` (ex: Cloudflare).
2. Ajoutez un à un les 5 enregistrements répertoriés dans le Tableau §2.
3. Vérifiez pour chaque enregistrement :
   - Nom d'hôte relatif ou absolu conforme à la syntaxe de votre registrar.
   - TTL réglé au minimum (ex: `2 min` ou `5 min`, ou `Auto`) pour faciliter les tests.
   - Icône Cloudflare en **Nuage Gris (DNS Only)**.

### Étape 3 : Attente de Propagation
- Selon le TTL et votre registrar, la propagation prend entre 2 minutes et 60 minutes.

### Étape 4 : Exécution du Script de Vérification Automatisée
Depuis la racine du projet `Enrich`, lancez l'outil de diagnostic intégré :

```bash
# Vérification complète avec affichage interactif
python scripts/verify_dns_setup.py

# Vérification en interrogeant directement un résolveur neutre (Google DNS ou Cloudflare)
python scripts/verify_dns_setup.py --nameserver 1.1.1.1

# Format JSON pour intégration CI/CD ou pipeline automatisé
python scripts/verify_dns_setup.py --json
```

Le script contrôle automatiquement :
- L'existence et la validité du SPF (`sales.contextelab.ma`)
- La résolution du CNAME / TXT DKIM (`resend._domainkey.sales.contextelab.ma`)
- L'enregistrement MX Return-Path (`send.sales.contextelab.ma`)
- L'alignement et la syntaxe de la politique DMARC (`_dmarc.sales.contextelab.ma`)
- L'absence de proxy Cloudflare masquant les enregistrements

### Étape 5 : Validation Définitive dans Resend
1. Retournez dans l'interface Resend sur la fiche du domaine `sales.contextelab.ma`.
2. Cliquez sur le bouton **Verify Records**.
3. Le statut doit basculer de `Pending` à **`Verified`** (badge vert pour SPF, DKIM et MX).

### Étape 6 : Test de Délivrabilité End-to-End
1. Envoyez un email de test vers un inspecteur de réputation (ex: [mail-tester.com](https://www.mail-tester.com)).
2. Vérifiez le score obtenu (score cible : **10/10**).
3. Inspectez les en-têtes bruts du message reçu :
   - `spf=pass`
   - `dkim=pass`
   - `dmarc=pass`

---

## 6. Protocole de Warm-up & Montée en Charge

La réputation d'une nouvelle adresse IP / domaine se construit progressivement auprès des filtres antispam (Gmail, Microsoft SmartScreen). L'application d'un calendrier strict de montée en charge est obligatoire.

### 6.1. Calendrier d'Envoi Recommandé

| Période | Volume Quotidien Max | Intervalle entre Envois | Rythme & Surveillance |
| :--- | :---: | :---: | :--- |
| **Jours 1 à 3** | **50 à 100 emails / j** | 30 à 60 secondes | Test initial de la boucle webhook et de l'ingestion Twenty CRM. |
| **Jours 4 à 7** | **150 à 250 emails / j** | 20 à 45 secondes | Vérification du taux d'ouverture et absence de bounce technique. |
| **Semaine 2** | **350 à 500 emails / j** | 15 à 30 secondes | Montée en charge progressive sur les contacts `Ready to Mail`. |
| **Semaine 3+** | **750 à 1000 emails / j** | 10 à 20 secondes | Vitesse de croisière nominale. |

### 6.2. Seuils Critiques & Circuit Breaker Automatique

Le dispatcher (`scripts/campaign_dispatcher.py`) et le récepteur webhook (`scripts/webhook_listener.py`) surveillent en temps réel les seuils suivants :

| Métrique | Seuil Normal | Seuil d'Alerte | Seuil de Coupure Immédiate |
| :--- | :---: | :---: | :---: |
| **Taux de Rebond Dur (Hard Bounce)** | `< 0.8%` | `> 1.0%` | **`>= 2.0%`** (Arrêt de la campagne) |
| **Taux de Plainte Spam (Complaints)** | `< 0.01%` | `> 0.02%` | **`>= 0.05%`** (Arrêt de la campagne) |
| **Taux d'Ouverture Unique** | `> 30%` | `< 20%` | `< 12%` (Suspicion de classement Spam) |

En cas de dépassement d'un seuil critique, le dispatcher interrompt immédiatement le lot en cours pour préserver le domaine.

---

## 7. Guide de Dépannage & FAQ Technique

### Q1. Resend affiche toujours "Unverified" après 30 minutes.
- **Cause 1** : Proxy Cloudflare activé. Rendez-vous dans Cloudflare > DNS et passez l'enregistrement en **DNS Only (Nuage Gris)**.
- **Cause 2** : Nom d'hôte dupliqué. Vérifiez si votre registrar n'a pas enregistré `resend._domainkey.sales.contextelab.ma.contextelab.ma`. Si c'est le cas, remplacez le nom par `resend._domainkey.sales`.
- **Vérification CLI** :
  ```bash
  python scripts/verify_dns_setup.py --nameserver 1.1.1.1
  ```

### Q2. Erreur `PermError: Multiple SPF records found`.
- **Cause** : Deux enregistrements TXT commençant par `v=spf1` sont présents sur `sales.contextelab.ma`.
- **Correction** : Fusionnez-les en un seul enregistrement ou supprimez l'ancien enregistrement obsolète.

### Q3. Les rapports DMARC indiquent un échec d'alignement SPF.
- **Cause** : L'adresse `Return-Path` n'utilise pas le sous-domaine `send.sales.contextelab.ma`.
- **Correction** : Assurez-vous que l'enregistrement MX sur `send.sales.contextelab.ma` pointe bien vers `feedback-smtp.us-east-1.amazonses.com`.

### Q4. Comment tester avant l'envoi réel ?
- Utilisez toujours l'option `--dry-run` du dispatcher :
  ```bash
  python scripts/campaign_dispatcher.py --dry-run --batch 100
  ```
- Cela vérifie les règles d'exclusion, valide l'hygiène des emails et inspecte le formatage sans envoyer de requête réseau à Resend.
