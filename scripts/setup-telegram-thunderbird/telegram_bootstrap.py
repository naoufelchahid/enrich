"""Private-chat bootstrap. Read secrets from stdin; print only username or IDs."""
import re
import sys

import requests


def api(token, method, payload):
    try:
        response = requests.post('https://api.telegram.org/bot' + token + '/' + method,
                                 json=payload, timeout=(5, 20), allow_redirects=False)
        body = response.json()
        if response.status_code == 401:
            raise RuntimeError('Telegram refuse le token (HTTP 401 Unauthorized). Recopie le token complet depuis BotFather ; aucun secret affiché.')
        if not response.ok or body.get('ok') is not True:
            raise RuntimeError('Telegram refuse la requête. Vérifie le token et utilise un bot dédié sans autre lecteur actif.')
        return body['result']
    except (requests.RequestException, ValueError, KeyError):
        raise RuntimeError('Connexion Telegram impossible ; aucun secret affiché. Réessaie après vérification.') from None


def identify(updates, nonce):
    candidates = set()
    for update in updates:
        message = update.get('message') or {}
        sender, chat = message.get('from') or {}, message.get('chat') or {}
        if (message.get('text') == nonce and chat.get('type') == 'private'
                and sender.get('is_bot') is False
                and type(sender.get('id')) is int and sender['id'] > 0
                and sender['id'] == chat.get('id')):
            candidates.add((sender['id'], chat['id']))
    if len(candidates) != 1:
        raise RuntimeError('Message de configuration introuvable ou ambigu. Envoie le code affiché au bot, depuis ton compte et en conversation privée, puis relance cette étape.')
    return next(iter(candidates))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('username', 'operator'):
        raise RuntimeError('Commande de configuration invalide.')
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise RuntimeError('Format de token invalide : colle uniquement le token BotFather.')
    if sys.argv[1] == 'username':
        data = api(token, 'getMe', {})
        username = data.get('username')
        if not data.get('is_bot') or not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_]+', username):
            raise RuntimeError('Identité du bot inattendue.')
        print(username)
        return
    nonce = sys.stdin.readline().strip()
    if not re.fullmatch(r'ENRICH_SETUP_[0-9a-f]{32}', nonce):
        raise RuntimeError('Code de configuration invalide.')
    if api(token, 'getWebhookInfo', {}).get('url'):
        raise RuntimeError('Ce bot utilise déjà un webhook. Choisis un bot dédié ; aucun webhook existant n’a été modifié.')
    user_id, chat_id = identify(api(token, 'getUpdates', {'timeout': 0, 'limit': 100}), nonce)
    print(user_id, chat_id)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
