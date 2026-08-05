import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from import_clean import group_company_data, sanitize_phone, process_excel_data, main

def test_sanitize_phone():
    assert sanitize_phone("0612345678") == "+212612345678"
    assert sanitize_phone("06 12 34 56 78") == "+212612345678"
    assert sanitize_phone("00212612345678") == "+212612345678"
    assert sanitize_phone("212512345678") == "+212512345678"
    assert sanitize_phone("+212612345678") == "+212612345678"
    assert sanitize_phone(None) == ""
    assert sanitize_phone("") == ""
    assert sanitize_phone("invalid") == ""

def test_group_company_data(tmp_path):
    df = pd.DataFrame({
        "Société": ["Company A ", "COMPANY A", "Company B"],
        "G": ["M.", "Mme.", ""],
        "Prénom et Nom": ["John Doe", "Jane Smith", "Bob"],
        "Fonction": ["CEO", "etudiant", "Manager"],
        "TEL": ["0600000000", "0611111111", "0622222222"],
        "E-mail": ["j@a.com", "jane@a.com", "b@b.com"]
    })
    filepath = tmp_path / "test.xlsx"
    df.to_excel(filepath, index=False)
    
    grouped = group_company_data(str(filepath))
    
    assert len(grouped) == 2
    assert "company a" in grouped
    assert "company b" in grouped
    
    # Check original name preserved
    assert grouped["company a"]["original_name"] == "Company A"
    
    # Check people filtering and assignment
    people_a = grouped["company a"]["people"]
    assert len(people_a) == 1 # Jane (etudiant) is filtered out
    assert people_a[0]["firstName"] == "M. John"
    assert people_a[0]["lastName"] == "Doe"
    assert people_a[0]["phone"] == "+212600000000"
    assert people_a[0]["email"] == "j@a.com"

def test_process_excel_data_alias():
    assert process_excel_data == group_company_data

@patch("import_clean.TwentyCRMClient")
def test_main_import_flow(mock_crm_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "fake_key")
    monkeypatch.setenv("TWENTY_API_BASE_URL", "http://fake-crm/rest")
    
    mock_crm_instance = MagicMock()
    mock_crm_cls.return_value = mock_crm_instance
    mock_crm_instance.create_company.return_value = {"id": "comp_123"}
    
    df = pd.DataFrame({
        "Société": ["ACME Corp"],
        "G": ["M."],
        "Prénom et Nom": ["Alice  Smith "],
        "Fonction": ["Director"],
        "TEL": ["0611223344"],
        "E-mail": ["alice@acme.com"]
    })
    test_file = tmp_path / "BDD6.xls"
    df.to_excel(test_file, index=False)
    
    with patch.object(pd, "read_excel", return_value=df):
        main([])
            
    mock_crm_instance.create_company.assert_called_once_with({"name": "ACME Corp"})
    mock_crm_instance.create_person.assert_called_once_with({
        "name": {"firstName": "M. Alice", "lastName": "Smith"},
        "jobTitle": "Director",
        "companyId": "comp_123",
        "emails": {"primaryEmail": "alice@acme.com"},
        "phones": {"primaryPhoneNumber": "+212611223344"}
    })

@patch("import_clean.TwentyCRMClient")
def test_main_import_flow_missing_comp_id(mock_crm_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "fake_key")
    monkeypatch.setenv("TWENTY_API_BASE_URL", "http://fake-crm/rest")
    
    mock_crm_instance = MagicMock()
    mock_crm_cls.return_value = mock_crm_instance
    # Response without 'id'
    mock_crm_instance.create_company.return_value = {"name": "NoID Corp"}
    
    df = pd.DataFrame({
        "Société": ["NoID Corp"],
        "G": [""],
        "Prénom et Nom": ["Bob Builder"],
        "Fonction": ["Worker"],
        "TEL": ["0611223344"],
        "E-mail": ["bob@noid.com"]
    })
    
    with patch.object(pd, "read_excel", return_value=df):
        main([])
            
    mock_crm_instance.create_company.assert_called_once_with({"name": "NoID Corp"})
    mock_crm_instance.create_person.assert_not_called()

@patch("import_clean.TwentyCRMClient")
def test_main_import_flow_custom_arg(mock_crm_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "fake_key")
    
    mock_crm_instance = MagicMock()
    mock_crm_cls.return_value = mock_crm_instance
    mock_crm_instance.create_company.return_value = {"id": "comp_999"}
    
    custom_file = tmp_path / "custom.xlsx"
    df = pd.DataFrame({
        "Société": ["Custom Corp"],
        "G": [""],
        "Prénom et Nom": ["Charlie Brown"],
        "Fonction": ["Lead"],
        "TEL": ["0655443322"],
        "E-mail": ["charlie@custom.com"]
    })
    df.to_excel(custom_file, index=False)
    
    with patch("import_clean.group_company_data", side_effect=group_company_data) as mock_group:
        main([str(custom_file)])
        mock_group.assert_called_once_with(str(custom_file))


