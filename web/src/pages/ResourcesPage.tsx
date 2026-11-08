import { useCallback, useEffect, useState } from 'react';
import { api, downloadTbExport, downloadTbExportTbx, downloadTmExport } from '../api/client';
import type { TBEntry, TBInfo, TMEntry, TMInfo } from '../api/types';

type Tab = 'tm' | 'tb';

const PAGE = 100; // размер страницы записей (бэкенд ограничивает limit≤500)

interface TmEdit { id: number; target: string; }
interface TbEdit { id: number; term: string; translation: string; note: string; status: string; }

export default function ResourcesPage() {
  const [tab, setTab] = useState<Tab>('tm');
  const [tms, setTms] = useState<TMInfo[]>([]);
  const [tbs, setTbs] = useState<TBInfo[]>([]);
  const [selectedTm, setSelectedTm] = useState<number | null>(null);
  const [selectedTb, setSelectedTb] = useState<number | null>(null);
  const [tmEntries, setTmEntries] = useState<TMEntry[]>([]);
  const [tbEntries, setTbEntries] = useState<TBEntry[]>([]);
  const [tmTotal, setTmTotal] = useState(0);
  const [tbTotal, setTbTotal] = useState(0);
  const [error, setError] = useState('');
  // правка записи в строке таблицы (maintenance)
  const [editTm, setEditTm] = useState<TmEdit | null>(null);
  const [editTb, setEditTb] = useState<TbEdit | null>(null);
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
      api.tmEntries(selectedTm, search || undefined, 0, PAGE)
        .then((p) => { setTmEntries(p.items); setTmTotal(p.total); })
        .catch(() => { setTmEntries([]); setTmTotal(0); });
    }
    if (tab === 'tb' && selectedTb) {
      api.tbEntries(selectedTb, search || undefined, 0, PAGE)
        .then((p) => { setTbEntries(p.items); setTbTotal(p.total); })
        .catch(() => { setTbEntries([]); setTbTotal(0); });
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

  const saveTmEntry = () => {
    if (!selectedTm || !editTm) return;
    void act(async () => {
      await api.updateTMEntry(selectedTm, editTm.id, editTm.target);
      setEditTm(null);
    });
  };

  const removeTmEntry = (entryId: number) => {
    if (!selectedTm || !window.confirm('Delete this TM entry? It will stop showing as a suggestion.')) return;
    void act(async () => {
      await api.deleteTMEntry(selectedTm, entryId);
      setEditTm(null);
    });
  };

  const saveTbEntry = () => {
    if (!selectedTb || !editTb) return;
    void act(async () => {
      await api.updateTBEntry(selectedTb, editTb.id, {
        term: editTb.term, translation: editTb.translation, note: editTb.note, status: editTb.status,
      });
      setEditTb(null);
    });
  };

  const removeTbEntry = (entryId: number) => {
    if (!selectedTb || !window.confirm('Delete this term?')) return;
    void act(async () => {
      await api.deleteTBEntry(selectedTb, entryId);
      setEditTb(null);
    });
  };

  const loadMoreTm = () => {
    if (!selectedTm) return;
    api.tmEntries(selectedTm, search || undefined, tmEntries.length, PAGE)
      .then((p) => setTmEntries((prev) => [...prev, ...p.items]))
      .catch(() => undefined);
  };

  const loadMoreTb = () => {
    if (!selectedTb) return;
    api.tbEntries(selectedTb, search || undefined, tbEntries.length, PAGE)
      .then((p) => setTbEntries((prev) => [...prev, ...p.items]))
      .catch(() => undefined);
  };

  const exportTm = (tmId: number) => {
    void act(async () => downloadTmExport(tmId));
  };

  const exportTb = (tbId: number) => {
    void act(async () => downloadTbExport(tbId));
  };

  const exportTbTbx = (tbId: number) => {
    void act(async () => downloadTbExportTbx(tbId));
  };

  return (
    <>
      <section className="card row">
        <button className={`btn small ${tab === 'tm' ? 'primary' : ''}`} onClick={() => { setTab('tm'); setEditTm(null); setEditTb(null); }}>Translation memory</button>
        <button className={`btn small ${tab === 'tb' ? 'primary' : ''}`} onClick={() => { setTab('tb'); setEditTm(null); setEditTb(null); }}>Termbase</button>
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
              <>
                <label className="btn small" style={{ cursor: 'pointer' }}>
                  Import TMX…
                  <input type="file" accept=".tmx,.xml" hidden
                         onChange={(e) => {
                           const f = e.target.files?.[0];
                           if (f && selectedTm) void act(() => api.importTMX(selectedTm, f));
                           if (e.target) e.target.value = '';
                         }} />
                </label>
                <label className="btn small" style={{ cursor: 'pointer' }}
                       title="CSV: заголовок source,target — паритет с импортом глоссариев">
                  Import CSV…
                  <input type="file" accept=".csv,text/csv" hidden
                         onChange={(e) => {
                           const f = e.target.files?.[0];
                           if (f && selectedTm) void act(() => api.importTM(selectedTm, f));
                           if (e.target) e.target.value = '';
                         }} />
                </label>
                <button className="btn small" onClick={() => exportTm(selectedTm)}
                        title="Экспорт в TMX 1.4b — обмен памятью с другими CAT-инструментами">
                  Export TMX
                </button>
              </>
            )}
          </section>

          <section className="card row wrap">
            <select value={selectedTm ?? ''} onChange={(e) => { setSelectedTm(Number(e.target.value) || null); setEditTm(null); }}>
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
                <thead><tr><th>Source</th><th>Target</th><th></th></tr></thead>
                <tbody>
                  {tmEntries.map((e) => (
                    <tr key={e.id}>
                      <td>{e.source_text}</td>
                      <td style={{ minWidth: 240 }}>
                        {editTm?.id === e.id ? (
                          <span className="row" style={{ gap: 6 }}>
                            <input
                              value={editTm.target}
                              onChange={(ev) => setEditTm({ ...editTm, target: ev.target.value })}
                              style={{ minWidth: 200 }}
                            />
                            <button className="btn small primary" disabled={!editTm.target.trim()} onClick={saveTmEntry}>Save</button>
                            <button className="btn small" onClick={() => setEditTm(null)}>Cancel</button>
                          </span>
                        ) : e.target_text}
                      </td>
                      <td className="actions-cell">
                        {editTm?.id !== e.id && (
                          <span className="row" style={{ gap: 4 }}>
                            <button
                              className="btn small"
                              title="Maintenance: fix the translation of this entry"
                              onClick={() => setEditTm({ id: e.id, target: e.target_text })}
                            >Edit</button>
                            <button className="btn small" onClick={() => removeTmEntry(e.id)}>Delete</button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {tmEntries.length === 0 && <tr><td colSpan={3} className="muted center">No entries.</td></tr>}
                  {tmEntries.length > 0 && tmEntries.length < tmTotal && (
                    <tr><td colSpan={3} className="center">
                      <button className="btn small" onClick={loadMoreTm}>Load more ({tmEntries.length} of {tmTotal})</button>
                    </td></tr>
                  )}
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
              <>
                <label className="btn small" style={{ cursor: 'pointer' }}>
                  Import CSV…
                  <input type="file" accept=".csv,text/csv" hidden
                         onChange={(e) => {
                           const f = e.target.files?.[0];
                           if (f && selectedTb) void act(() => api.importTB(selectedTb, f));
                           if (e.target) e.target.value = '';
                         }} />
                </label>
                <label className="btn small" style={{ cursor: 'pointer' }}>
                  Import TBX…
                  <input type="file" accept=".tbx,.xml" hidden
                         onChange={(e) => {
                           const f = e.target.files?.[0];
                           if (f && selectedTb) void act(() => api.importTBX(selectedTb, f));
                           if (e.target) e.target.value = '';
                         }} />
                </label>
                <button className="btn small" onClick={() => exportTb(selectedTb)}
                        title="Экспорт в CSV (term,translation,note,status) — тот же шаблон, что и импорт">
                  Export CSV
                </button>
                <button className="btn small" onClick={() => exportTbTbx(selectedTb)}
                        title="Экспорт в TBX-Basic (LISA) — индустриальный стандарт обмена терминологией">
                  Export TBX
                </button>
              </>
            )}
          </section>

          <section className="card row wrap">
            <select value={selectedTb ?? ''} onChange={(e) => { setSelectedTb(Number(e.target.value) || null); setEditTb(null); }}>
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
                <thead><tr><th>Term</th><th>Translation</th><th>Status</th><th>Note</th><th></th></tr></thead>
                <tbody>
                  {tbEntries.map((e) => (
                    <tr key={e.id}>
                      <td style={{ minWidth: 140 }}>
                        {editTb?.id === e.id
                          ? <input value={editTb.term} onChange={(ev) => setEditTb({ ...editTb, term: ev.target.value })} style={{ width: 130 }} />
                          : e.term}
                      </td>
                      <td className={e.status === 'forbidden' ? 'overdue' : ''} style={{ minWidth: 160 }}>
                        {editTb?.id === e.id
                          ? <input value={editTb.translation} onChange={(ev) => setEditTb({ ...editTb, translation: ev.target.value })} style={{ width: 150 }} />
                          : e.translation}
                      </td>
                      <td>
                        {editTb?.id === e.id ? (
                          <select value={editTb.status} onChange={(ev) => setEditTb({ ...editTb, status: ev.target.value })}>
                            <option value="preferred">preferred</option>
                            <option value="admitted">admitted</option>
                            <option value="forbidden">forbidden</option>
                          </select>
                        ) : (
                          <span className={`status-chip ${e.status === 'forbidden' ? 'st-new' : 'st-translated'}`}>{e.status}</span>
                        )}
                      </td>
                      <td className="muted" style={{ minWidth: 180 }}>
                        {editTb?.id === e.id
                          ? <input value={editTb.note} onChange={(ev) => setEditTb({ ...editTb, note: ev.target.value })} style={{ width: 170 }} />
                          : e.note}
                      </td>
                      <td className="actions-cell">
                        {editTb?.id === e.id ? (
                          <span className="row" style={{ gap: 4 }}>
                            <button className="btn small primary" disabled={!editTb.term.trim() || !editTb.translation.trim()} onClick={saveTbEntry}>Save</button>
                            <button className="btn small" onClick={() => setEditTb(null)}>Cancel</button>
                          </span>
                        ) : (
                          <span className="row" style={{ gap: 4 }}>
                            <button className="btn small" onClick={() => setEditTb({
                              id: e.id, term: e.term, translation: e.translation, note: e.note, status: e.status,
                            })}>Edit</button>
                            <button className="btn small" onClick={() => removeTbEntry(e.id)}>Delete</button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {tbEntries.length === 0 && <tr><td colSpan={5} className="muted center">No entries.</td></tr>}
                  {tbEntries.length > 0 && tbEntries.length < tbTotal && (
                    <tr><td colSpan={5} className="center">
                      <button className="btn small" onClick={loadMoreTb}>Load more ({tbEntries.length} of {tbTotal})</button>
                    </td></tr>
                  )}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </>
  );
}
