import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { TBEntry, TBInfo, TMEntry, TMInfo } from '../api/types';

type Tab = 'tm' | 'tb';

export default function ResourcesPage() {
  const [tab, setTab] = useState<Tab>('tm');
  const [tms, setTms] = useState<TMInfo[]>([]);
  const [tbs, setTbs] = useState<TBInfo[]>([]);
  const [selectedTm, setSelectedTm] = useState<number | null>(null);
  const [selectedTb, setSelectedTb] = useState<number | null>(null);
  const [tmEntries, setTmEntries] = useState<TMEntry[]>([]);
  const [tbEntries, setTbEntries] = useState<TBEntry[]>([]);
  const [error, setError] = useState('');
  // формы
  const [tmName, setTmName] = useState('');
  const [tmSource, setTmSource] = useState('en');
  const [tmTarget, setTmTarget] = useState('ru');
  const [tbName, setTbName] = useState('');
  const [tbSource, setTbSource] = useState('en');
  const [tbTarget, setTbTarget] = useState('ru');
  const [srcText, setSrcText] = useState('');
  const [tgtText, setTgtText] = useState('');
  const [term, setTerm] = useState('');
  const [termTr, setTermTr] = useState('');
  const [termNote, setTermNote] = useState('');
  const [termStatus, setTermStatus] = useState('preferred');
  const [search, setSearch] = useState('');

  const loadLists = useCallback(() => {
    api.tms().then(setTms).catch((e) => setError(e instanceof Error ? e.message : 'Load failed'));
    api.tbs().then(setTbs).catch(() => undefined);
  }, []);

  useEffect(loadLists, [loadLists]);

  const loadEntries = useCallback(() => {
    if (tab === 'tm' && selectedTm) {
      api.tmEntries(selectedTm, search || undefined).then(setTmEntries).catch(() => setTmEntries([]));
    }
    if (tab === 'tb' && selectedTb) {
      api.tbEntries(selectedTb, search || undefined).then(setTbEntries).catch(() => setTbEntries([]));
    }
  }, [tab, selectedTm, selectedTb, search]);

  useEffect(loadEntries, [loadEntries]);

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      loadLists();
      loadEntries();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    }
  };

  return (
    <>
      <section className="card row">
        <button className={`btn small ${tab === 'tm' ? 'primary' : ''}`} onClick={() => setTab('tm')}>Translation memory</button>
        <button className={`btn small ${tab === 'tb' ? 'primary' : ''}`} onClick={() => setTab('tb')}>Termbase</button>
        <span className="spacer" />
        <input placeholder="Search entries…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </section>

      {error && <div className="alert">{error}</div>}

      {tab === 'tm' && (
        <>
          <section className="card row wrap">
            <input placeholder="New TM name…" value={tmName} onChange={(e) => setTmName(e.target.value)} />
            <input value={tmSource} onChange={(e) => setTmSource(e.target.value)} style={{ width: 60 }} title="source lang" />
            →
            <input value={tmTarget} onChange={(e) => setTmTarget(e.target.value)} style={{ width: 60 }} title="target lang" />
            <button className="btn small primary" disabled={!tmName.trim()}
                    onClick={() => act(() => api.createTM({ name: tmName, source_lang: tmSource, target_lang: tmTarget })).then(() => setTmName(''))}>
              Create TM
            </button>
            {selectedTm && (
              <label className="btn small" style={{ cursor: 'pointer' }}>
                Import TMX…
                <input type="file" accept=".tmx,.xml" hidden
                       onChange={(e) => {
                         const f = e.target.files?.[0];
                         if (f && selectedTm) void act(() => api.importTMX(selectedTm, f));
                         if (e.target) e.target.value = '';
                       }} />
              </label>
            )}
          </section>

          <section className="card row wrap">
            <select value={selectedTm ?? ''} onChange={(e) => setSelectedTm(Number(e.target.value) || null)}>
              <option value="">select TM…</option>
              {tms.map((t) => (
                <option key={t.id} value={t.id}>{t.name} ({t.source_lang}→{t.target_lang}, {t.entry_count})</option>
              ))}
            </select>
            {selectedTm !== null && (
              <span className="row" style={{ gap: 6 }}>
                <input placeholder="Source…" value={srcText} onChange={(e) => setSrcText(e.target.value)} style={{ minWidth: 220 }} />
                <input placeholder="Target…" value={tgtText} onChange={(e) => setTgtText(e.target.value)} style={{ minWidth: 220 }} />
                <button className="btn small primary" disabled={!srcText.trim() || !tgtText.trim()}
                        onClick={() => act(() => api.upsertTMEntry(selectedTm, { source_text: srcText, target_text: tgtText })).then(() => { setSrcText(''); setTgtText(''); })}>
                  Add / update
                </button>
              </span>
            )}
          </section>

          {selectedTm !== null && (
            <section className="card">
              <table className="jobs-table">
                <thead><tr><th>Source</th><th>Target</th></tr></thead>
                <tbody>
                  {tmEntries.map((e) => (
                    <tr key={e.id}><td>{e.source_text}</td><td>{e.target_text}</td></tr>
                  ))}
                  {tmEntries.length === 0 && <tr><td colSpan={2} className="muted center">No entries.</td></tr>}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}

      {tab === 'tb' && (
        <>
          <section className="card row wrap">
            <input placeholder="New TB name…" value={tbName} onChange={(e) => setTbName(e.target.value)} />
            <input value={tbSource} onChange={(e) => setTbSource(e.target.value)} style={{ width: 60 }} title="source lang" />
            →
            <input value={tbTarget} onChange={(e) => setTbTarget(e.target.value)} style={{ width: 60 }} title="target lang" />
            <button className="btn small primary" disabled={!tbName.trim()}
                    onClick={() => act(() => api.createTB({ name: tbName, source_lang: tbSource, target_lang: tbTarget })).then(() => setTbName(''))}>
              Create TB
            </button>
            {selectedTb && (
              <label className="btn small" style={{ cursor: 'pointer' }}>
                Import CSV…
                <input type="file" accept=".csv,text/csv" hidden
                       onChange={(e) => {
                         const f = e.target.files?.[0];
                         if (f && selectedTb) void act(() => api.importTB(selectedTb, f));
                         if (e.target) e.target.value = '';
                       }} />
              </label>
            )}
          </section>

          <section className="card row wrap">
            <select value={selectedTb ?? ''} onChange={(e) => setSelectedTb(Number(e.target.value) || null)}>
              <option value="">select TB…</option>
              {tbs.map((t) => (
                <option key={t.id} value={t.id}>{t.name} ({t.source_lang}→{t.target_lang}, {t.entry_count})</option>
              ))}
            </select>
            {selectedTb !== null && (
              <span className="row" style={{ gap: 6 }}>
                <input placeholder="Term…" value={term} onChange={(e) => setTerm(e.target.value)} style={{ width: 140 }} />
                <input placeholder="Translation…" value={termTr} onChange={(e) => setTermTr(e.target.value)} style={{ width: 160 }} />
                <select value={termStatus} onChange={(e) => setTermStatus(e.target.value)}>
                  <option value="preferred">preferred</option>
                  <option value="admitted">admitted</option>
                  <option value="forbidden">forbidden</option>
                </select>
                <input placeholder="Note…" value={termNote} onChange={(e) => setTermNote(e.target.value)} style={{ width: 180 }} />
                <button className="btn small primary" disabled={!term.trim() || !termTr.trim()}
                        onClick={() => act(() => api.addTBEntry(selectedTb, { term, translation: termTr, note: termNote, status: termStatus })).then(() => { setTerm(''); setTermTr(''); setTermNote(''); })}>
                  Add
                </button>
              </span>
            )}
          </section>

          {selectedTb !== null && (
            <section className="card">
              <table className="jobs-table">
                <thead><tr><th>Term</th><th>Translation</th><th>Status</th><th>Note</th></tr></thead>
                <tbody>
                  {tbEntries.map((e) => (
                    <tr key={e.id}>
                      <td>{e.term}</td>
                      <td className={e.status === 'forbidden' ? 'overdue' : ''}>{e.translation}</td>
                      <td><span className={`status-chip ${e.status === 'forbidden' ? 'st-new' : 'st-translated'}`}>{e.status}</span></td>
                      <td className="muted">{e.note}</td>
                    </tr>
                  ))}
                  {tbEntries.length === 0 && <tr><td colSpan={4} className="muted center">No entries.</td></tr>}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </>
  );
}
