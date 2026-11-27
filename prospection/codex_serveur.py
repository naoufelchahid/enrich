"""Client JSON-RPC stdio du serveur Codex pour les décisions d’admission."""
import json
import os
import subprocess
import tempfile

def decision(instructions, prompt, dossier, schema):
    with tempfile.TemporaryDirectory() as cwd:
        commande = ["codex", "app-server", "-c", 'web_search="disabled"']
        config = {"mcp_servers": {},
                  "model_reasoning_effort": "low", "shell_environment_policy": {"inherit": "none"}}
        with subprocess.Popen(commande, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True) as serveur:
            try:
                return _tour(serveur, cwd, config, instructions, prompt, dossier, schema)
            finally:
                if serveur.poll() is None:
                    serveur.kill()


def _tour(serveur, cwd, config, instructions, prompt, dossier, schema):
    from selectors import EVENT_READ, DefaultSelector
    from time import monotonic
    # Lire les octets sur le descripteur évite le tampon de TextIO avec select().
    desc = serveur.stdout.fileno()
    echeance = monotonic() + 300
    tampon = b""

    def envoyer(methode, parametres=None, identifiant=None):
        message = {"method": methode}
        if parametres is not None:
            message["params"] = parametres
        if identifiant is not None:
            message["id"] = identifiant
        serveur.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        serveur.stdin.flush()

    with DefaultSelector() as selecteur:
        selecteur.register(desc, EVENT_READ)

        def lecture():
            nonlocal tampon
            while b"\n" not in tampon:
                restant = echeance - monotonic()
                if restant <= 0 or not selecteur.select(restant):
                    raise TimeoutError("Délai dépassé pour la décision Codex")
                bloc = os.read(desc, 65536)
                if not bloc:
                    raise RuntimeError("Connexion au serveur Codex interrompue")
                tampon += bloc
            ligne, tampon = tampon.split(b"\n", 1)
            return json.loads(ligne)

        def reponse(identifiant):
            while True:
                ligne = lecture()
                if ligne.get("id") == identifiant and "method" not in ligne:
                    if "error" in ligne:
                        raise RuntimeError(ligne["error"].get("message", "Erreur du serveur Codex"))
                    return ligne["result"]

        envoyer("initialize", {"clientInfo": {"name": "enrich", "version": "1.0"}}, 1)
        reponse(1)
        envoyer("initialized")
        envoyer("thread/start", {"cwd": cwd, "ephemeral": True, "sandbox": "read-only",
                                "approvalPolicy": "never", "config": config,
                                "baseInstructions": instructions}, 2)
        thread = reponse(2)["thread"]["id"]
        envoyer("turn/start", {"threadId": thread, "outputSchema": schema,
                               "input": [{"type": "text", "text": prompt + "\n" +
                                          json.dumps(dossier, ensure_ascii=False), "text_elements": []}]}, 3)
        tourne = reponse(3)["turn"]["id"]
        texte = None
        while True:
            ligne = lecture()
            params = ligne.get("params", {})
            if params.get("threadId") != thread:
                continue
            if ligne.get("method") == "item/completed" and params["turnId"] == tourne:
                item = params["item"]
                if item["type"] == "agentMessage":
                    texte = item["text"]
            if ligne.get("method") == "turn/completed" and params["turn"]["id"] == tourne:
                if params["turn"]["status"] != "completed" or not texte:
                    raise RuntimeError("Aucune décision structurée renvoyée par Codex")
                return json.loads(texte)
