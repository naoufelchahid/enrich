# Configuration DNS Resend : `sales.contextelab.ma`

Ce document récapitule les 5 enregistrements DNS exacts à ajouter dans votre **cPanel Nindohost (Zone Editor / Éditeur de zone)** pour valider le sous-domaine d'expédition sur **Resend**.

---

## 📋 Tableau Récapitulatif

| # | Type | Nom d'hôte (*Name*) | Valeur / Cible (*Record*) | Priorité | TTL |
|---|---|---|---|---|---|
| **1** | `TXT` | `sales.contextelab.ma.` *(ou `sales`)* | `v=spf1 include:amazonses.com ~all` | - | 14400 |
| **2** | `CNAME` | `resend._domainkey.sales.contextelab.ma.` *(ou `resend._domainkey.sales`)* | `resend._domainkey.sales.contextelab.ma.resend.email.` *(ou valeur Resend)* | - | 14400 |
| **3** | `MX` | `send.sales.contextelab.ma.` *(ou `send.sales`)* | `feedback-smtp.us-east-1.amazonses.com.` | `10` | 14400 |
| **4** | `TXT` | `send.sales.contextelab.ma.` *(ou `send.sales`)* | `v=spf1 include:amazonses.com ~all` | - | 14400 |
| **5** | `TXT` | `_dmarc.sales.contextelab.ma.` *(ou `_dmarc.sales`)* | `v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma; sp=none;` | - | 14400 |

> **Astuce cPanel** : Selon la configuration de votre cPanel, si vous saisissez uniquement le préfixe (ex: `sales` ou `send.sales`), cPanel ajoute automatiquement `.contextelab.ma.`. Vérifiez simplement que le nom final affiché correspond bien à `...sales.contextelab.ma`.

---

## 📑 Valeurs Individuelles Prêtes à Copier-Coller

### 1. Enregistrement SPF Principal (Envoi)
- **Type** : `TXT`
- **Nom d'hôte** :
  ```text
  sales.contextelab.ma.
  ```
- **Valeur / Données** :
  ```text
  v=spf1 include:amazonses.com ~all
  ```

---

### 2. Enregistrement DKIM (Signature cryptographique)
- **Type** : `CNAME`
- **Nom d'hôte** :
  ```text
  resend._domainkey.sales.contextelab.ma.
  ```
- **Valeur / Cible** *(à vérifier avec celle affichée sur votre page Resend)* :
  ```text
  resend._domainkey.sales.contextelab.ma.resend.email.
  ```

---

### 3. Enregistrement Return-Path MX (Gestion des rebonds)
- **Type** : `MX`
- **Nom d'hôte** :
  ```text
  send.sales.contextelab.ma.
  ```
- **Priorité** :
  ```text
  10
  ```
- **Destination** :
  ```text
  feedback-smtp.us-east-1.amazonses.com.
  ```

---

### 4. Enregistrement Return-Path SPF (Validation d'enveloppe)
- **Type** : `TXT`
- **Nom d'hôte** :
  ```text
  send.sales.contextelab.ma.
  ```
- **Valeur / Données** :
  ```text
  v=spf1 include:amazonses.com ~all
  ```

---

### 5. Enregistrement DMARC (Protection & Délivrabilité)
- **Type** : `TXT`
- **Nom d'hôte** :
  ```text
  _dmarc.sales.contextelab.ma.
  ```
- **Valeur / Données** :
  ```text
  v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma; sp=none;
  ```

---

## 🔍 Commande de Vérification Automatique

Une fois les enregistrements enregistrés dans cPanel, vous pouvez lancer à tout moment la vérification en direct avec la commande :

```bash
python3 scripts/verify_dns_setup.py --domain sales.contextelab.ma
```
