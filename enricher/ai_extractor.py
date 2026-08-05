import logging
import json
import re
import subprocess

logger = logging.getLogger(__name__)

def extract_company_data(text: str, api_key: str = None) -> dict:
    prompt = f"""
    Extract the following information from the text provided into a JSON object:
    - ice (string)
    - rc (string)
    - address (string)
    - phone (string)
    - capital (string)
    - activity (string)
    
    If a field is not found, return null for it. Output ONLY valid JSON inside a code block.
    
    Text: {text}
    """
    try:
        # Call agy CLI
        result = subprocess.run(["agy", prompt], capture_output=True, text=True, check=True)
        json_str = result.stdout
        # Strip markdown formatting if present
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"LLM Extraction failed: {e}")
        return {}

