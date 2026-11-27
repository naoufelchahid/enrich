import { useEffect, useState } from 'react';
import { RestApiClient } from 'twenty-client-sdk/rest';

const descriptions = {
  probleme: [
    'Problème et impact',
    {
      probleme: 'Problème probable',
      impact: 'Impact',
      prochaine_etape: 'Prochaine étape',
    },
  ],
  preuve: [
    'Commencer par une preuve',
    {
      reference: 'Référence ou résultat',
      source: 'Source',
      pertinence: 'Pertinence pour la cible',
    },
  ],
  choix: [
    'Question à deux options',
    {
      question: 'Question finale',
      option_a: 'Première réponse',
      option_b: 'Deuxième réponse',
    },
  ],
  observation: [
    'Observation liée à l’offre',
    {
      signal: 'Signal à retrouver dans le dossier',
      lien_offre: 'Lien avec l’offre',
    },
  ],
} as const;
type Code = keyof typeof descriptions;
type Technique = { type: Code } & Record<string, string>;
type Angle = {
  cible: string;
  interet: string;
  reponse: string;
  techniques: Technique[];
};
type Brief = { angles: Angle[]; offre?: string; signature?: string };
type Fiche = {
  id: string;
  reference: string;
  briefJson?: string;
  demandeRedaction?: string;
  resultatRedaction?: string;
};
const codes = Object.keys(descriptions) as Code[];
const vide = (): Angle => ({
  cible: '',
  interet: '',
  reponse: '',
  techniques: [],
});
const api = new RestApiClient();
const campagnes = '/rest/prospectingCampaigns';

async function lignes<T>(
  path: string,
  fields: string,
  filter?: string,
): Promise<T[]> {
  const key = path.split('/').pop()!;
  const rows: T[] = [];
  let cursor: string | undefined;
  do {
    const page: {
      data: Record<string, T[]>;
      pageInfo: { hasNextPage: boolean; endCursor: string };
    } = await api.get(path, {
      query: { fields, filter, limit: 60, starting_after: cursor },
    });
    rows.push(...page.data[key]);
    cursor = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : undefined;
  } while (cursor);
  return rows;
}

function monter<T>(liste: T[], i: number) {
  const copie = [...liste];
  [copie[i - 1], copie[i]] = [copie[i], copie[i - 1]];
  return copie;
}

export function CampaignForm() {
  const [refs, setRefs] = useState<string[]>([]);
  const [ref, setRef] = useState('');
  const [fiche, setFiche] = useState<Fiche>();
  const [brief, setBrief] = useState<Brief>({ angles: [vide()] });
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [pret, setPret] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    Promise.all([
      lignes<Fiche>(campagnes, 'reference'),
      lignes<{ campaignRef: string }>(
        '/rest/prospectingAssignments',
        'campaignRef',
      ),
    ])
      .then(([cs, as]) =>
        setRefs(
          [
            ...new Set([
              ...cs.map((c) => c.reference),
              ...as.map((a) => a.campaignRef),
            ]),
          ].sort(),
        ),
      )
      .catch((e) => setMessage(String(e)));
  }, []);
  useEffect(() => {
    let actif = true;
    if (!ref) return;
    setPret(false);
    api
      .get<{ data: { prospectingCampaigns: Fiche[] } }>(campagnes, {
        query: { filter: 'reference[eq]:' + ref },
      })
      .then(async ({ data }) => {
        if (!actif) return;
        const f = data.prospectingCampaigns[0];
        const b = f?.briefJson ? JSON.parse(f.briefJson) : {};
        setFiche(f);
        setBrief({ ...b, angles: b.angles?.length ? b.angles : [vide()] });
        const statut = JSON.parse(f?.resultatRedaction || '{}');
        const drafts = await lignes<{ draftJson?: string }>(
          '/rest/prospectingAssignments',
          'draftJson',
          'campaignRef[eq]:' + ref,
        );
        if (!actif) return;
        const ancien = drafts.some(
          (row) =>
            row.draftJson &&
            !JSON.parse(row.draftJson).version_brief &&
            JSON.parse(row.draftJson).brouillon,
        );
        if (f?.demandeRedaction && statut.demande === f.demandeRedaction) {
          setMessage(
            statut.motif ||
              [
                ...(statut.brouillons || []).map(
                  (x: {
                    adresse: string;
                    angle: string;
                    technique: Code;
                    revue: string;
                  }) =>
                    [
                      x.adresse,
                      x.angle,
                      descriptions[x.technique][0],
                      x.revue,
                    ].join(' · '),
                ),
                ...(statut.ecartes || []).map(
                  (x: { affectation: string; motif: string }) =>
                    x.affectation + ' · ' + x.motif,
                ),
              ].join('\n') ||
              'Aucun contact éligible.',
          );
        } else
          setMessage(
            f?.demandeRedaction
              ? 'Préparation demandée.'
              : ancien || f?.resultatRedaction
                ? 'À régénérer'
                : '',
          );
        setPret(true);
      })
      .catch((e) => {
        if (actif) setMessage(String(e));
      });
    return () => {
      actif = false;
    };
  }, [ref, reload]);
  const angles = brief.angles;
  function modifier(i: number, a: Angle) {
    setBrief({ ...brief, angles: angles.map((v, n) => (n === i ? a : v)) });
  }
  const complet =
    angles.length > 0 &&
    angles.every(
      (a) =>
        a.cible.trim() &&
        a.interet.trim() &&
        a.reponse.trim() &&
        a.techniques.length &&
        a.techniques.every(
          (t) =>
            Object.keys(descriptions[t.type][1]).every((k) => t[k]?.trim()) &&
            (t.type !== 'choix' ||
              t.option_a.trim().toLowerCase() !==
                t.option_b.trim().toLowerCase()),
        ),
    );
  async function sauver(lancer: boolean) {
    setBusy(true);
    try {
      const data = {
        reference: ref,
        name: ref,
        briefJson: JSON.stringify(brief),
        demandeRedaction: lancer ? (Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)) : '',
      };
      if (fiche) await api.patch(campagnes + '/' + fiche.id, data);
      else await api.post(campagnes, data);
      setReload((n) => n + 1);
      setMessage(lancer ? 'Préparation demandée.' : 'Brief enregistré.');
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      style={{
        maxWidth: 760,
        margin: '24px auto',
        fontFamily: 'Inter, sans-serif',
      }}
    >
      <h1>Préparer la campagne</h1>
      <label>
        Campagne{' '}
        <select
          disabled={busy}
          value={ref}
          onChange={(e) => {
            setPret(false);
            setRef(e.target.value);
          }}
        >
          <option value="">Choisir une campagne</option>
          {refs.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      </label>
      {ref && pret && (
        <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
          {(['offre', 'signature'] as const).map((k) => (
            <label key={k} style={{ display: 'block', margin: '12px 0' }}>
              {k === 'offre' ? 'Offre' : 'Signature'}{' '}
              <input
                value={brief[k] || ''}
                onChange={(e) => setBrief({ ...brief, [k]: e.target.value })}
              />
            </label>
          ))}
          {angles.map((a, i) => (
            <fieldset
              key={i}
              style={{
                padding: 18,
                margin: '18px 0',
                border: '1px solid #aebdbc',
                borderRadius: 8,
              }}
            >
              <legend>Angle {i + 1}</legend>
              {(['cible', 'interet', 'reponse'] as const).map((k, n) => (
                <label key={k} style={{ display: 'grid', marginBottom: 8 }}>
                  {
                    ['Cible', 'Intérêt pour cette cible', 'Réponse recherchée'][
                      n
                    ]
                  }
                  <input
                    value={a[k]}
                    onChange={(e) => modifier(i, { ...a, [k]: e.target.value })}
                  />
                </label>
              ))}
              <p>Techniques autorisées, par ordre de préférence</p>
              {codes.map((code) => (
                <label key={code} style={{ display: 'block' }}>
                  <input
                    type="checkbox"
                    checked={a.techniques.some((t) => t.type === code)}
                    onChange={(e) =>
                      modifier(i, {
                        ...a,
                        techniques: e.target.checked
                          ? [...a.techniques, { type: code }]
                          : a.techniques.filter((t) => t.type !== code),
                      })
                    }
                  />{' '}
                  {descriptions[code][0]}
                </label>
              ))}
              {a.techniques.map((t, j) => (
                <fieldset key={t.type} style={{ margin: '12px 0' }}>
                  <legend>
                    {j + 1}. {descriptions[t.type][0]}
                  </legend>
                  {Object.entries(descriptions[t.type][1]).map(([k, label]) => (
                    <label key={k} style={{ display: 'grid', marginBottom: 6 }}>
                      {label}
                      <input
                        value={t[k] || ''}
                        onChange={(e) =>
                          modifier(i, {
                            ...a,
                            techniques: a.techniques.map((v, n) =>
                              n === j ? { ...v, [k]: e.target.value } : v,
                            ),
                          })
                        }
                      />
                    </label>
                  ))}
                  {j > 0 && (
                    <button
                      onClick={() =>
                        modifier(i, {
                          ...a,
                          techniques: monter(a.techniques, j),
                        })
                      }
                    >
                      Prioriser cette technique
                    </button>
                  )}
                </fieldset>
              ))}
              {i > 0 && (
                <button
                  onClick={() =>
                    setBrief({ ...brief, angles: monter(angles, i) })
                  }
                >
                  Prioriser cet angle
                </button>
              )}
              {angles.length > 1 && (
                <button
                  onClick={() =>
                    setBrief({
                      ...brief,
                      angles: angles.filter((_, n) => n !== i),
                    })
                  }
                >
                  Retirer cet angle
                </button>
              )}
            </fieldset>
          ))}
          <button
            onClick={() => setBrief({ ...brief, angles: [...angles, vide()] })}
          >
            Ajouter un angle
          </button>
          <p>
            <button onClick={() => sauver(false)}>Enregistrer le brief</button>{' '}
            <button disabled={!complet} onClick={() => sauver(true)}>
              Valider et préparer les brouillons
            </button>
          </p>
          {!complet && (
            <p>
              Renseignez la cible, son intérêt, la réponse recherchée et chaque
              champ des techniques sélectionnées.
            </p>
          )}
        </fieldset>
      )}
      <p role="status" style={{ whiteSpace: 'pre-line' }}>
        {message}
      </p>
      {ref && (
        <button disabled={busy} onClick={() => setReload((n) => n + 1)}>
          Recharger le brief et son état
        </button>
      )}
    </section>
  );
}
