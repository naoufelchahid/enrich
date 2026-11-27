"""Exécution isolée des tâches de rédaction et de relecture avec Codex."""
import json
import subprocess
import tempfile
from pathlib import Path

def tour(donnees, prompt, format_sortie, modele=None):
    with tempfile.TemporaryDirectory() as dossier:
        schema = Path(dossier, "schema.json")
        sortie = Path(dossier, "decision.json")
        schema.write_text(json.dumps(format_sortie))
        commande = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                    "--sandbox", "read-only", "-c", 'web_search="disabled"', "--output-schema",
                    str(schema), "-o", str(sortie), "-"]
        if modele:
            commande[2:2] = ["-m", modele]
        resultat = subprocess.run(commande, cwd=dossier, text=True, capture_output=True, timeout=240,
                                  input=prompt + "\n" + json.dumps(donnees, ensure_ascii=False))
        if resultat.returncode:
            raise RuntimeError(f"Qualification Codex interrompue (code {resultat.returncode})")
        return json.loads(sortie.read_text())
