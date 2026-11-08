import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { RATE_CATEGORY_CODES, RATE_CATEGORY_LABELS } from '../api/types';
import type { RateCard } from '../api/types';

/** Прайс-листы: ставка за слово по категориям совпадений + минимальная сумма.
 *  Ставки правятся инлайн (PATCH при потере фокуса), как в TM/TB. */
export default function RatesPage() {
  const [cards, setCards] = useState<RateCard[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // форма создания
  const [name, setName] = useState('');
  const [source, setSource] = useState('en');
  const [target, setTarget] = useState('ru');
  const [currency, setCurrency] = useState('EUR');
  const [minCharge, setMinCharge] = useState('0');

  const load = useCallback(() => {
    api.rateCards().then(setCards).catch((e) => setError(e instanceof Error ? e.message : 'Не удалось загрузить'));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setError('');
    setNotice('');
    try {
      await fn();
      load();
      if (ok) setNotice(ok);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Действие не удалось');
    }
  };

  const create = () => {
    if (!name.trim()) return;
    void act(
      () => api.createRateCard({
        name: name.trim(), source_lang: source, target_lang: target,
        currency, min_charge: Number(minCharge) || 0,
      }),
      `Прайс-лист «${name.trim()}» создан со ставками по умолчанию — правьте инлайн.`,
    ).then(() => setName(''));
  };

  /** Правка ставки/минимальной суммы при потере фокуса, если значение изменилось. */
  const patchOnBlur = (card: RateCard, field: 'min_charge' | string, raw: string, prev: number) => {
    const value = Number(raw.replace(',', '.'));
    if (!Number.isFinite(value) || value === prev || value < 0) return;
    if (field === 'min_charge') {
      void act(() => api.updateRateCard(card.id, { min_charge: value }), `Обновлено ${card.name}: мин. сумма ${value}`);
    } else {
      void act(
        () => api.updateRateCard(card.id, { rates: { [field]: value } }),
        `Обновлено ${card.name}: ${RATE_CATEGORY_LABELS[field] ?? field} = ${value}`,
      );
    }
  };

  return (
    <>
      <section className="card row wrap">
        <input placeholder="Название нового прайс-листа…" value={name} onChange={(e) => setName(e.target.value)} />
        <input value={source} onChange={(e) => setSource(e.target.value)} style={{ width: 60 }} title="исходный язык" />
        <span className="muted">→</span>
        <input value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 60 }} title="целевой язык" />
        <input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} style={{ width: 70 }} title="валюта" />
        <input value={minCharge} onChange={(e) => setMinCharge(e.target.value)} style={{ width: 110 }} title="минимальная сумма" placeholder="мин. сумма" />
        <button className="btn small primary" disabled={!name.trim()} onClick={create}>Создать</button>
      </section>

      {error && <div className="alert">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      <section className="card">
        <h3 className="panel-title">Прайс-листы — {cards.length}</h3>
        <p className="muted small-text">
          Ставки за слово по категориям анализа (как в Trados: скидка зашита в ставку категории).
          Котировка = Σ слов × ставка; итог задания не опускается ниже минимальной суммы.
        </p>
        {cards.map((card) => (
          <div key={card.id} className="rate-card-block">
            <div className="row wrap" style={{ alignItems: 'baseline' }}>
              <strong>{card.name}</strong>
              <span className="muted">{card.source_lang}→{card.target_lang}</span>
              <span className="status-chip st-translated">{card.currency}</span>
              <span className="muted small-text">
                мин. сумма:&nbsp;
                <input
                  className="rate-input" defaultValue={card.min_charge}
                  onBlur={(e) => patchOnBlur(card, 'min_charge', e.target.value, card.min_charge)}
                  title="Минимальная сумма задания"
                />
              </span>
              <span className="spacer" />
              <button
                className="btn small"
                onClick={() => {
                  if (window.confirm(`Удалить прайс-лист «${card.name}»?`)) {
                    void act(() => api.deleteRateCard(card.id), `Удалено ${card.name}`);
                  }
                }}
              >
                Удалить
              </button>
            </div>
            <div className="row wrap" style={{ marginTop: 6 }}>
              {RATE_CATEGORY_CODES.map((code) => (
                <label key={code} className="rate-cell" title={`${RATE_CATEGORY_LABELS[code]} — ставка за слово`}>
                  <span className="muted small-text">{RATE_CATEGORY_LABELS[code]}</span>
                  <input
                    className="rate-input"
                    defaultValue={card.rates[code] ?? 0}
                    onBlur={(e) => patchOnBlur(card, code, e.target.value, card.rates[code] ?? 0)}
                  />
                </label>
              ))}
            </div>
          </div>
        ))}
        {cards.length === 0 && (
          <div className="muted center" style={{ padding: 20 }}>
            No rate cards yet — create one for a language pair to enable quoting.
          </div>
        )}
      </section>
    </>
  );
}
