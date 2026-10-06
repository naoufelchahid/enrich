import json
import urllib.request
import urllib.error
import sys

def verify_connection():
    connection_file = "/home/naoufel/Downloads/thunderbird.tmp/thunderbird-mcp/connection.json"
    
    try:
        with open(connection_file, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Erreur : Le fichier {connection_file} n'existe pas.")
        sys.exit(1)
        
    port = config.get("port")
    token = config.get("token")
    
    if not port or not token:
        print("Erreur : Port ou token manquant dans le fichier de connexion.")
        sys.exit(1)
        
    url = f"http://127.0.0.1:{port}/api/accounts"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    
    try:
        print(f"Connexion à l'API Thunderbird sur le port {port}...")
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            print("Connexion réussie ! Comptes trouvés :")
            print(json.dumps(data, indent=2))
    except urllib.error.URLError as e:
        print(f"Échec de la connexion : {e}")
        sys.exit(1)

if __name__ == "__main__":
    verify_connection()
