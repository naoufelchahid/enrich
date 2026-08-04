import google.generativeai as genai
import json
import re

def extract_company_data(text: str, api_key: str) -> dict:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash") # Use standard fast model
    
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
        response = model.generate_content(prompt)
        json_str = response.text
        # Strip markdown formatting if present
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        return json.loads(json_str)
    except Exception as e:
        print(f"LLM Extraction failed: {e}")
        return {}
