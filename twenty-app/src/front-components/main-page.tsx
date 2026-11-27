import { defineFrontComponent } from 'twenty-sdk/define';
import { useState, useRef } from 'react';
import * as xlsx from 'xlsx';

import {
  APP_DISPLAY_NAME,
  MAIN_PAGE_FRONT_COMPONENT_UNIVERSAL_IDENTIFIER,
} from 'src/constants/universal-identifiers';

import { CampaignForm } from './campaign-form';

const STANDARD_FIELDS = [
  { key: 'firstName', label: 'First Name' },
  { key: 'lastName', label: 'Last Name' },
  { key: 'email', label: 'Email' },
  { key: 'phone', label: 'Phone' },
  { key: 'companyName', label: 'Company Name' },
  { key: 'companyWebsite', label: 'Company Website' },
  { key: 'linkedInUrl', label: 'LinkedIn URL' },
];

const MainPage = () => {
  const [campagne, setCampagne] = useState(true);
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<any[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [isUploading, setIsUploading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (evt) => {
      const data = evt.target?.result;
      if (!data) return;

      const workbook = xlsx.read(data, { type: 'binary' });
      const firstSheetName = workbook.SheetNames[0];
      const worksheet = workbook.Sheets[firstSheetName];

      const json: any[][] = xlsx.utils.sheet_to_json(worksheet, { header: 1 });
      if (json.length > 0) {
        setHeaders(json[0] || []);
        setRows(json.slice(1).filter(r => r.length > 0)); // skip header and empty rows
      }
    };
    reader.readAsBinaryString(file);
  };

  const handleMappingChange = (standardField: string, excelHeader: string) => {
    setMapping((prev) => ({
      ...prev,
      [standardField]: excelHeader,
    }));
  };

  const handleSubmit = async () => {
    setIsUploading(true);
    setResult(null);

    // Build the mapped JSON
    const mappedJSON = rows.map((row) => {
      const record: any = {};
      Object.entries(mapping).forEach(([stdField, excelHeader]) => {
        const colIndex = headers.indexOf(excelHeader);
        if (colIndex !== -1) {
          record[stdField] = row[colIndex];
        }
      });
      // Always include original row for skipped lead tracking if needed
      record._original = row;
      return record;
    });

    try {
      // In a real Twenty environment, we would use the internal app endpoint for the Logic Function.
      // E.g., /rest/v1/apps/{app-universal-identifier}/functions/ingest-excel
      const response = await fetch('/rest/v1/apps/enrich-twenty-app/functions/ingest-excel', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('access_token') || ''}` // Mocked auth
        },
        body: JSON.stringify({ rows: mappedJSON }),
      });

      if (!response.ok) {
        throw new Error('Failed to ingest Excel data');
      }

      setResult('Successfully ingested ' + mappedJSON.length + ' rows.');
      setHeaders([]);
      setRows([]);
      setMapping({});
    } catch (err: any) {
      setResult('Error: ' + err.message);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div style={{ padding: '40px', fontFamily: 'Inter, sans-serif' }}>
      <nav><button onClick={() => setCampagne(true)}>Campagnes</button> <button onClick={() => setCampagne(false)}>Import Excel</button></nav>
      {campagne ? <CampaignForm/> : <>
      <h1 style={{ fontSize: '24px', marginBottom: '20px' }}>Upload BDD6.xlsx</h1>

      {headers.length === 0 ? (
        <div style={{ border: '2px dashed #ccc', padding: '40px', textAlign: 'center', borderRadius: '8px' }}>
          <input 
            type="file" 
            accept=".xlsx, .xls, .csv" 
            ref={fileInputRef} 
            onChange={handleFileUpload} 
            style={{ display: 'none' }}
          />
          <button 
            onClick={() => fileInputRef.current?.click()}
            style={{ padding: '10px 20px', background: '#333', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
          >
            Select Excel File
          </button>
        </div>
      ) : (
        <div>
          <h2 style={{ fontSize: '18px', marginBottom: '10px' }}>Map Columns</h2>
          <p style={{ marginBottom: '20px', color: '#666' }}>Found {rows.length} rows. Please map your columns below.</p>
          
          <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '20px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #eee' }}>
                <th style={{ padding: '10px' }}>Standard Field</th>
                <th style={{ padding: '10px' }}>Excel Column</th>
              </tr>
            </thead>
            <tbody>
              {STANDARD_FIELDS.map((field) => (
                <tr key={field.key} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '10px' }}>{field.label}</td>
                  <td style={{ padding: '10px' }}>
                    <select
                      value={mapping[field.key] || ''}
                      onChange={(e) => handleMappingChange(field.key, e.target.value)}
                      style={{ padding: '8px', width: '100%', borderRadius: '4px', border: '1px solid #ccc' }}
                    >
                      <option value="">-- Ignore --</option>
                      {headers.map((h, i) => (
                        <option key={i} value={h}>{h}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ display: 'flex', gap: '10px' }}>
            <button 
              onClick={handleSubmit} 
              disabled={isUploading}
              style={{ padding: '10px 20px', background: isUploading ? '#999' : '#007BFF', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
            >
              {isUploading ? 'Ingesting...' : 'Ingest Data'}
            </button>
            <button 
              onClick={() => { setHeaders([]); setRows([]); }}
              style={{ padding: '10px 20px', background: '#f5f5f5', color: '#333', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer' }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {result && (
        <div style={{ marginTop: '20px', padding: '15px', background: result.startsWith('Error') ? '#FEE' : '#EFE', border: `1px solid ${result.startsWith('Error') ? '#FCC' : '#CFC'}`, borderRadius: '4px' }}>
          {result}
        </div>
      )}
      </>}
    </div>
  );
};

export default defineFrontComponent({
  universalIdentifier: MAIN_PAGE_FRONT_COMPONENT_UNIVERSAL_IDENTIFIER,
  name: APP_DISPLAY_NAME,
  description: `${APP_DISPLAY_NAME} Front Component for Excel mapping and data curation`,
  component: MainPage,
});

