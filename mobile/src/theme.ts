/** Общие цвета и стили мобильного клиента. */
import { StyleSheet } from 'react-native';

export const colors = {
  bg: '#F0F4F8',
  card: '#FFFFFF',
  border: '#DFE5EC',
  text: '#1F2937',
  muted: '#6B7280',
  accent: '#2563EB',
  accentDark: '#1D4ED8',
  green: '#16A34A',
  greenBg: '#DCFCE7',
  orange: '#D97706',
  orangeBg: '#FEF3C7',
  red: '#DC2626',
  redBg: '#FEF2F2',
  gray: '#475569',
  grayBg: '#E2E8F0',
  blueBg: '#DBEAFE',
};

export const common = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    elevation: 1,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  wrap: { flexWrap: 'wrap' },
  spacer: { flex: 1 },
  muted: { color: colors.muted },
  small: { fontSize: 12, color: colors.muted },
  button: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
    alignItems: 'center',
  },
  buttonText: { color: colors.text, fontSize: 14 },
  primaryButton: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  primaryButtonText: { color: '#FFFFFF', fontWeight: 'bold' },
  disabled: { opacity: 0.5 },
  input: {
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 15,
  },
  chip: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    backgroundColor: colors.grayBg,
  },
  chipText: { fontSize: 11, color: colors.gray },
});

export const statusColors: Record<string, { bg: string; fg: string }> = {
  new: { bg: colors.grayBg, fg: colors.gray },
  untranslated: { bg: colors.grayBg, fg: colors.gray },
  drafted: { bg: colors.orangeBg, fg: '#B45309' },
  translated: { bg: colors.greenBg, fg: '#15803D' },
  confirmed: { bg: colors.greenBg, fg: '#15803D' },
  unassigned: { bg: colors.grayBg, fg: colors.gray },
  assigned: { bg: colors.blueBg, fg: '#1D4ED8' },
  declined: { bg: colors.redBg, fg: colors.red },
  accepted: { bg: colors.blueBg, fg: '#1D4ED8' },
  in_progress: { bg: colors.blueBg, fg: '#1D4ED8' },
  submitted: { bg: colors.orangeBg, fg: '#B45309' },
  rework: { bg: colors.redBg, fg: colors.red },
  completed: { bg: colors.greenBg, fg: '#15803D' },
  canceled: { bg: colors.grayBg, fg: colors.gray },
  processing: { bg: colors.orangeBg, fg: '#B45309' },
  ready: { bg: colors.greenBg, fg: '#15803D' },
  failed: { bg: colors.redBg, fg: colors.red },
  preferred: { bg: colors.greenBg, fg: '#15803D' },
  forbidden: { bg: colors.redBg, fg: colors.red },
  admitted: { bg: colors.blueBg, fg: '#1D4ED8' },
  error: { bg: colors.redBg, fg: colors.red },
  warning: { bg: colors.orangeBg, fg: '#B45309' },
};

export function fmtDateTime(value: string | null): string {
  if (!value) return '—';
  const iso = value.endsWith('Z') || value.includes('+') ? value : `${value}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
