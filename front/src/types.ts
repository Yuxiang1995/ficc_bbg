export type NumericValue = number | string | null;

export interface QuoteSummary {
  total_quotes: number;
  review_quotes: number;
  quotes_with_bid: number;
  quotes_with_ask: number;
  latest_quote_time: string | null;
  extraction_failures: number;
}

export interface FilterOptions {
  currencies: string[];
  quote_sources: string[];
  quote_types: string[];
  bond_types: string[];
  parse_statuses: string[];
  regions: string[];
}

export interface BondQuote {
  quote_id: number;
  run_id: number;
  ticker: string | null;
  isin: string | null;
  issuer: string | null;
  currency: string | null;
  maturity_text: string | null;
  maturity_date: string | null;
  coupon_type: string | null;
  coupon_rate: NumericValue;
  rating_raw: string | null;
  rating_moodys: string | null;
  rating_sp: string | null;
  rating_fitch: string | null;
  bid_price: NumericValue;
  ask_price: NumericValue;
  bid_spread: NumericValue;
  ask_spread: NumericValue;
  spread_bid_ask: NumericValue;
  spread_unit: string | null;
  bid_size: NumericValue;
  ask_size: NumericValue;
  size_unit: string | null;
  quote_time: string | null;
  quote_source: string | null;
  quote_type: string | null;
  bond_type: string | null;
  region: string | null;
  remark: string | null;
  parse_status: string;
  confidence: NumericValue;
  raw_line: string;
  created_at: string;
  raw_payload?: Record<string, unknown>;
  paragraph_text?: string;
  speaker?: string | null;
  line_id?: string;
  paragraph_id?: string;
  source_mode?: string;
  splitter_model?: string | null;
  extractor_model?: string | null;
}

export interface ExtractionFailure {
  failure_id: number;
  run_id: number;
  model: string;
  attempts: number;
  error_type: string;
  error_message: string;
  failure_payload: Record<string, unknown>;
  created_at: string;
  line_id: string;
  quote_line: string;
  quote_time: string | null;
  quote_source: string | null;
  speaker: string | null;
  source_date: string | null;
  paragraph_id: string;
  paragraph_type: string;
  paragraph_text: string;
}

export interface PageResult<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
}

export type CriticalField =
  | 'ticker'
  | 'currency'
  | 'maturity_date'
  | 'coupon_type'
  | 'coupon_rate';

export type FieldAvailability =
  | 'pending'
  | 'present'
  | 'inferable'
  | 'absent'
  | 'ambiguous';

export type GoldValue = string | number | null;

export interface EvaluationAnnotation {
  status: 'pending' | 'completed';
  difficulty_gold:
    | 'easy'
    | 'hard_extraction'
    | 'hard_context'
    | 'unanswerable'
    | 'ambiguous'
    | null;
  field_availability: Record<CriticalField, FieldAvailability>;
  gold: Record<CriticalField, GoldValue>;
  annotator: string | null;
  remark: string | null;
}

export interface EvaluationPrediction {
  ticker?: string | null;
  issuer?: string | null;
  currency?: string | null;
  maturity_date?: string | null;
  coupon_type?: string | null;
  coupon_rate?: NumericValue;
  [key: string]: unknown;
}

export interface EvaluationSample {
  sample_id: string;
  selection_bucket: string;
  selection_reason: string;
  difficulty_candidate: string;
  missing_fields_current: CriticalField[];
  missing_field_count: number;
  special_format_reasons: string[];
  source: {
    run_id: number | null;
    paragraph_id: string | null;
    quote_time: string | null;
    quote_source: string | null;
    speaker: string | null;
    source_date: string | null;
    [key: string]: unknown;
  };
  input: {
    quote_line: string;
    paragraph_context: string;
    paragraph_context_line_limit: number;
    paragraph_line_count: number;
  };
  prediction_snapshot: EvaluationPrediction[];
  extraction_failure: Record<string, unknown> | null;
  annotation: EvaluationAnnotation;
  inference_status?: string;
  inference_failure?: Record<string, unknown>[] | null;
}

export interface EvaluationDataset {
  schema_version?: string;
  dataset_name?: string;
  metadata: Record<string, unknown>;
  samples: EvaluationSample[];
}

export interface AnnotationProgress {
  total: number;
  completed: number;
  pending: number;
}

export interface EvaluationDatasetResponse {
  dataset: EvaluationDataset;
  source_file: string;
  working_file: string;
  loaded_from: 'source' | 'working';
  progress: AnnotationProgress;
}

export interface EvaluationSaveResponse {
  sample: EvaluationSample;
  working_file: string;
  progress: AnnotationProgress;
}
