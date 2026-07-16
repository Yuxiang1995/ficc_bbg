import type {
  BondQuote,
  ExtractionFailure,
  FilterOptions,
  PageResult,
  QuoteSummary,
} from './types';

type QueryValue = string | number | boolean | null | undefined;

async function request<T>(path: string, params?: Record<string, QueryValue>): Promise<T> {
  const url = new URL(path, window.location.origin);
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value));
    }
  });
  const response = await fetch(url.toString(), { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const getSummary = () => request<QuoteSummary>('/api/summary');
export const getOptions = () => request<FilterOptions>('/api/options');
export const getQuotes = (params: Record<string, QueryValue>) =>
  request<PageResult<BondQuote>>('/api/bond-quotes', params);
export const getQuote = (quoteId: number) =>
  request<BondQuote>(`/api/bond-quotes/${quoteId}`);
export const getFailures = (params: Record<string, QueryValue>) =>
  request<PageResult<ExtractionFailure>>('/api/extraction-failures', params);
