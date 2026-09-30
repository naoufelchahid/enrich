# Guide Complet : Campagne d'Emailing Efficace, Sécurisée & Haute Délivrabilité
*Basé sur les standards de l'industrie et la documentation officielle de [Resend](https://resend.com/docs/knowledge-base/introduction).*

---

## 1. Architecture & Protection du Domaine (*Reputation Isolation*)

L'erreur numéro 1 consiste à envoyer des campagnes depuis votre nom de domaine principal (`monentreprise.com`). Si vos emails sont signalés ou rebondissent en grand nombre, votre domaine tout entier est blacklisté (y compris vos emails professionnels personnels, devis et factures).

### La règle d'or : Isoler les flux via un sous-domaine
* **Domaine principal (`monentreprise.com`)** : Réservé uniquement aux échanges quotidiens en direct (`prenom@monentreprise.com`) et à votre messagerie d'entreprise (Google Workspace, Microsoft 365).
* **Sous-domaine dédié aux campagnes (`news.monentreprise.com` ou `marketing.monentreprise.com`)** : Utilisé pour toutes vos campagnes et diffusions en volume.
* **Sous-domaine transactionnel (`mail.monentreprise.com` ou `notifications.monentreprise.com`)** : Réservé aux réinitialisations de mot de passe, reçus et alertes système.

> **Avantage :** Si votre sous-domaine de campagne subit une vague de plaintes, la réputation de votre domaine principal reste 100% intacte. De plus, cela évite tout conflit avec vos enregistrements MX actuels.

---

## 2. Configuration Technique & Enregistrements DNS

Rendez-vous dans la zone DNS de votre hébergeur (Cloudflare, OVH, Hostinger, Namecheap...) et ajoutez les enregistrements fournis par Resend lors de l'ajout de votre sous-domaine (`news.monentreprise.com`).

| Type | Nom / Hôte | Valeur / Cible | Rôle |
| :--- | :--- | :--- | :--- |
| **TXT** | `news` | `v=spf1 include:amazonses.com ~all` *(fourni par Resend)* | **SPF** : autorise Resend à émettre vos emails. |
| **TXT / CNAME** | `resend._domainkey.news` | *(Clé publique cryptographique fournie par Resend)* | **DKIM** : garantit l'intégrité et l'authenticité de l'email. |
| **MX / CNAME** | `send.news` | `feedback-smtp.us-east-1.amazonses.com` (priorité 10) | **Return-Path** : traite les rebonds et les plaintes. |
| **TXT** | `_dmarc.news` | `v=DMARC1; p=none; rua=mailto:dmarc@monentreprise.com;` | **DMARC** : politique de conformité et de reporting. |

### Les points critiques DNS :
1. **DMARC progressif** :
   * Démarrez avec `p=none;` (mode observation).
   * Après 2 à 3 semaines sans incident, durcissez la politique en `p=quarantine;` (met en spam les usurpations), puis `p=reject;`.
2. **Cloudflare** :
   * Pour les enregistrements `CNAME` et `MX`, désactivez impérativement le proxy orange (sélectionnez **DNS Only / Nuage gris**), sans quoi la validation échouera.
3. **Vérification** :
   * Contrôlez vos en-têtes avec l'outil open-source [dns.email](https://dns.email) pour confirmer que SPF, DKIM et DMARC affichent `PASS`.

---

## 3. Hygiène de Liste & Prévention Anti-Spam

Les algorithmes de Gmail, Outlook et Yahoo évaluent la pertinence de vos emails selon la propreté de vos listes de destinataires.

1. **Nettoyage préalable systématique** :
   * Passez votre base de contacts dans un validateur tiers (*Emailable*, *ZeroBounce*, *Kickbox*) avant le moindre envoi pour éliminer :
     * Les adresses inexistantes ou fermées.
     * Les adresses temporaires / jetables.
     * Les *spam traps* (adresses pièges gérées par les opérateurs pour blacklister les spammeurs).
2. **Bannir les listes achetées ou louées** :
   * Elles contiennent une forte proportion de spam traps et de contacts non consentants. L'envoi vers ces listes ruine la réputation d'un domaine en quelques heures.
3. **Automatiser le traitement des rebonds (Webhooks)** :
   * Configurez un webhook Resend branché sur les événements :
     * `email.bounced` (rebond dur)
     * `email.complained` (signalement spam)
   * Tout contact ayant déclenché un de ces événements doit être **immédiatement radié** de vos bases futures.
4. **La règle du Sunset (Désengagement)** :
   * Excluez systématiquement de vos campagnes marketing les contacts n'ayant ouvert aucun email depuis plus de 6 mois.

---

## 4. Protocole de Warm-Up Progressif (*Calendrier officiel Resend*)

> ⚠️ **Avertissement officiel de Resend :**  
> N'utilisez pas de services d'auto-warmup artificiels (robots qui s'échangent des faux emails en boucle). Google et Microsoft détectent ces réseaux et appliquent des pénalités sévères. La seule méthode durable est un **ramp-up progressif et organique**.

Si votre sous-domaine est neuf ou inactif depuis longtemps, respectez les paliers suivants :

| Jour | Volume max / jour | Débit max / heure | Type de destinataires prioritaires |
| :--- | :--- | :--- | :--- |
| **Jour 1** | 150 emails | Étalé sur la journée | Vos contacts les plus fidèles / engagés |
| **Jour 2** | 250 emails | Étalé | Contacts très récents |
| **Jour 3** | 400 emails | Étalé | Contacts qualifiés |
| **Jour 4** | 700 emails | 50 emails / h | Base engagée |
| **Jour 5** | 1 000 emails | 75 emails / h | Base générale nettoyée |
| **Jour 6** | 1 500 emails | 100 emails / h | Base générale |
| **Jour 7** | 2 000 emails | 150 emails / h | Base générale |
| **Semaine 2+** | +40% à +50% par jour | Réparti sur la journée | Ajuster selon les métriques |

> **Stratégie clé :** Envoyez toujours les premiers jours aux contacts les plus susceptibles d'ouvrir et d'interagir. Ce signal initial d'ouverture et de réponse forge une réputation d'expéditeur exemplaire.

---

## 5. Rédaction & Contenu : Maximiser les Retours et Éviter le Spam

Le mot d'ordre résumé par Resend : **« Pensez à ce que ferait un spammeur… et faites l'exact inverse ! »**

### Règles de conception :
* **Format épuré** : Privilégiez le texte brut ou un HTML minimaliste (ex: via [React Email](https://react.email)). Bannissez les emails "tout image" ou les mises en page complexes et lourdes.
* **Taille de l'email** : Maintenez le corps du message sous les **100 Ko** pour éviter la troncation de Gmail (*Message clipped*).
* **Alignement des liens** :
  * Tous les liens hypertextes doivent pointer vers votre domaine ou sous-domaine officiel.
  * **Interdiction stricte des réducteurs de liens** (type `bit.ly`, `tinyurl`) : ils déclenchent instantanément les filtres de sécurité.
* **Désinscription One-Click fluide** :
  * Resend inclut automatiquement les en-têtes RFC `List-Unsubscribe` exigés par Google et Yahoo.
  * Laissez un lien de désinscription visible et clair en pied de page. Il est infiniment préférable qu'un contact se désinscrive plutôt qu'il clique sur *« Signaler comme spam »*.
* **Le levier ultime : Inciter à la réponse (Reply)** :
  * Le signal d'engagement le plus fort aux yeux des algorithmes est une **réponse directe** au message.
  * Terminez par une question pertinente invitant à répondre par email plutôt que de pousser uniquement des liens externes.

---

## 6. Exemple d'Intégration avec Resend (Node.js / TypeScript)

```typescript
import { Resend } from 'resend';

const resend = new Resend(process.env.RESEND_API_KEY);

async function envoyerEmailCampagne() {
  const data = await resend.emails.send({
    // Expéditeur : utilise le sous-domaine isolé
    from: 'Alexandre <contact@news.monentreprise.com>',
    
    // Destinataire
    to: ['prospect@exemple.com'],
    
    // Objet clair, sans majuscules excessives ni ponctuation abusive
    subject: 'Optimisation de vos processus : notre retour d’expérience',
    
    // Contenu HTML épuré
    html: `
      <p>Bonjour,</p>
      <p>Je me permets de vous contacter suite à nos récentes analyses sur votre secteur...</p>
      <p>Seriez-vous disponible pour un échange rapide de 10 minutes cette semaine ? Répondez simplement à cet email pour me dire si cela vous intéresse.</p>
      <p>Bien cordialement,<br>Alexandre</p>
      <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;" />
      <p style="font-size: 11px; color: #888;">
        Pour ne plus recevoir nos actualités, <a href="{{{RESEND_UNSUBSCRIBE_URL}}}">cliquez ici</a>.
      </p>
    `,
    
    // ASTUCE : Les réponses arrivent directement sur votre boîte principale de travail
    replyTo: 'alexandre@monentreprise.com',
  });

  console.log('Email envoyé avec succès :', data);
}

envoyerEmailCampagne();
```

---

## 7. Tableau de Bord : Métriques & Seuils d'Alerte Vitaux

Surveillez quotidiennement vos métriques sur Resend et sur [Google Postmaster Tools](https://postmaster.google.com) :

| Indicateur | Objectif idéal | Seuil d'alerte critique | Conséquence si franchi |
| :--- | :--- | :--- | :--- |
| **Taux de rebond (Bounce rate)** | `< 1%` | **> 4%** | Arrêt immédiat de la campagne. Re-vérifier l'ensemble de la liste. |
| **Taux de plainte spam** | `< 0,02%` | **> 0,08%** *(seuil de blocage à 0,10%)* | Mise en quarantaine par Gmail / Yahoo. Réduction du volume et révision du ciblage. |
| **Taux d'ouverture** | `> 25%` | `< 12%` | Indique souvent que les messages commencent à glisser dans les onglets *Promotions* ou *Spam*. |
| **Réputation Google Postmaster** | Élevée (*High*) | Moyenne (*Medium*) ou Faible (*Low*) | Réduire le volume d'envoi de moitié et envoyer uniquement aux utilisateurs ayant interagi récemment. |
