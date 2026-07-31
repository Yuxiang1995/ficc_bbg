import {
  CheckOutlined,
  CopyOutlined,
  DownloadOutlined,
  LeftOutlined,
  RightOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Empty,
  Input,
  InputNumber,
  Progress,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  evaluationDownloadUrl,
  getEvaluationDataset,
  saveEvaluationAnnotation,
} from '../api';
import type {
  CriticalField,
  EvaluationAnnotation,
  EvaluationDatasetResponse,
  EvaluationPrediction,
  EvaluationSample,
  FieldAvailability,
  GoldValue,
} from '../types';

const { TextArea } = Input;

const FIELDS: Array<{ key: CriticalField; label: string }> = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'currency', label: '币种' },
  { key: 'maturity_date', label: '到期日' },
  { key: 'coupon_type', label: '票息类型' },
  { key: 'coupon_rate', label: '票面利率' },
];

const AVAILABILITY_OPTIONS = [
  { value: 'pending', label: '待判断' },
  { value: 'present', label: '原文明示' },
  { value: 'inferable', label: '可推断' },
  { value: 'absent', label: '不存在' },
  { value: 'ambiguous', label: '有歧义' },
];

const DIFFICULTY_OPTIONS = [
  { value: 'easy', label: 'Easy' },
  { value: 'hard_extraction', label: '抽取困难' },
  { value: 'hard_context', label: '依赖上下文' },
  { value: 'unanswerable', label: '无法回答' },
  { value: 'ambiguous', label: '存在歧义' },
];

type SampleFilter = 'all' | 'pending' | 'completed';

const cloneAnnotation = (annotation: EvaluationAnnotation): EvaluationAnnotation =>
  JSON.parse(JSON.stringify(annotation)) as EvaluationAnnotation;

const displayValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') {
    return <span className="empty-cell">null</span>;
  }
  return String(value);
};

const statusColor = (sample: EvaluationSample) =>
  sample.annotation.status === 'completed' ? 'green' : 'default';

const annotationError = (annotation: EvaluationAnnotation): string | null => {
  for (const { key, label } of FIELDS) {
    const availability = annotation.field_availability[key];
    const value = annotation.gold[key];
    if (availability === 'pending') return `${label}尚未判断可获得性`;
    if (
      (availability === 'present' || availability === 'inferable')
      && (value === null || value === '')
    ) {
      return `${label}需要填写 Gold`;
    }
    if (availability === 'absent' && value !== null && value !== '') {
      return `${label}标记为不存在时 Gold 必须为空`;
    }
  }
  return null;
};

export default function AnnotationPage() {
  const [response, setResponse] = useState<EvaluationDatasetResponse | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [draft, setDraft] = useState<EvaluationAnnotation | null>(null);
  const [predictionIndex, setPredictionIndex] = useState(0);
  const [filter, setFilter] = useState<SampleFilter>('all');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const samples = response?.dataset.samples ?? [];
  const sample = samples[selectedIndex] ?? null;
  const prediction = sample?.prediction_snapshot[predictionIndex] ?? {};

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getEvaluationDataset();
      setResponse(next);
      const firstPending = next.dataset.samples.findIndex(
        (item) => item.annotation.status !== 'completed',
      );
      setSelectedIndex(firstPending >= 0 ? firstPending : 0);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '评测集加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!sample) return;
    setDraft(cloneAnnotation(sample.annotation));
    setPredictionIndex(0);
    setDirty(false);
  }, [sample?.sample_id]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [dirty]);

  const filteredIndexes = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return samples.flatMap((item, index) => {
      if (filter !== 'all' && item.annotation.status !== filter) return [];
      const haystack = [
        item.sample_id,
        item.input.quote_line,
        item.source.quote_source,
        item.difficulty_candidate,
      ].join(' ').toLowerCase();
      return normalizedQuery && !haystack.includes(normalizedQuery) ? [] : [index];
    });
  }, [filter, query, samples]);

  const updateDraft = (next: EvaluationAnnotation) => {
    setDraft(next);
    setDirty(true);
  };

  const persist = useCallback(async (
    annotation: EvaluationAnnotation,
    quiet = false,
  ) => {
    if (!sample || !response) return false;
    setSaving(true);
    try {
      const result = await saveEvaluationAnnotation(sample.sample_id, annotation);
      setResponse((current) => {
        if (!current) return current;
        const nextSamples = [...current.dataset.samples];
        nextSamples[selectedIndex] = result.sample;
        return {
          ...current,
          dataset: { ...current.dataset, samples: nextSamples },
          working_file: result.working_file,
          loaded_from: 'working',
          progress: result.progress,
        };
      });
      setDraft(cloneAnnotation(result.sample.annotation));
      setDirty(false);
      if (!quiet) message.success('已写入标注 JSON');
      return true;
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败');
      return false;
    } finally {
      setSaving(false);
    }
  }, [response, sample, selectedIndex]);

  const navigate = async (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= samples.length || nextIndex === selectedIndex) return;
    if (dirty && draft) {
      const saved = await persist(draft, true);
      if (!saved) return;
    }
    setSelectedIndex(nextIndex);
  };

  const saveDraft = async () => {
    if (!draft) return;
    await persist({ ...draft, status: 'pending' });
  };

  const completeAndNext = async () => {
    if (!draft) return;
    const error = annotationError(draft);
    if (error) {
      message.warning(error);
      return;
    }
    const saved = await persist({ ...draft, status: 'completed' });
    if (!saved) return;
    const nextPending = samples.findIndex(
      (item, index) => index > selectedIndex && item.annotation.status !== 'completed',
    );
    const wrappedPending = samples.findIndex(
      (item) => item.annotation.status !== 'completed',
    );
    const nextIndex = nextPending >= 0 ? nextPending : wrappedPending;
    if (nextIndex >= 0 && nextIndex !== selectedIndex) setSelectedIndex(nextIndex);
  };

  const setAvailability = (
    field: CriticalField,
    availability: FieldAvailability,
  ) => {
    if (!draft) return;
    updateDraft({
      ...draft,
      field_availability: {
        ...draft.field_availability,
        [field]: availability,
      },
      gold: {
        ...draft.gold,
        [field]: availability === 'absent' ? null : draft.gold[field],
      },
    });
  };

  const setGold = (field: CriticalField, value: GoldValue) => {
    if (!draft) return;
    updateDraft({ ...draft, gold: { ...draft.gold, [field]: value } });
  };

  const adoptPrediction = (field: CriticalField) => {
    const value = prediction[field];
    setGold(field, value === undefined || value === '' ? null : value as GoldValue);
  };

  const adoptAllPredictions = () => {
    if (!draft) return;
    const nextGold = { ...draft.gold };
    FIELDS.forEach(({ key }) => {
      const value = prediction[key];
      nextGold[key] = value === undefined || value === '' ? null : value as GoldValue;
    });
    updateDraft({ ...draft, gold: nextGold });
  };

  if (loading) {
    return (
      <PageContainer title="评测标注">
        <Skeleton active paragraph={{ rows: 12 }} />
      </PageContainer>
    );
  }

  if (!response || !sample || !draft) {
    return (
      <PageContainer title="评测标注">
        <Empty description="没有可标注的评测样本" />
      </PageContainer>
    );
  }

  const progressPercent = response.progress.total
    ? Math.round(response.progress.completed / response.progress.total * 100)
    : 0;

  return (
    <PageContainer
      title="评测标注"
      subTitle="逐条核对抽取结果并生成 Gold JSON"
      extra={[
        <Tooltip title={response.working_file} key="download">
          <Button
            icon={<DownloadOutlined />}
            href={evaluationDownloadUrl}
            disabled={dirty}
          >
            下载 JSON
          </Button>
        </Tooltip>,
      ]}
    >
      <div className="annotation-progress">
        <div className="annotation-progress-count">
          <span>{response.progress.completed}</span>
          <small>/ {response.progress.total} 已完成</small>
        </div>
        <Progress
          percent={progressPercent}
          showInfo={false}
          strokeColor="#0b6b62"
          trailColor="#e5e9ea"
        />
        <Tag color={response.loaded_from === 'working' ? 'green' : 'default'}>
          {response.loaded_from === 'working' ? '标注副本' : '原始数据集'}
        </Tag>
      </div>

      <div className="annotation-workspace">
        <aside className="sample-navigator">
          <div className="sample-filter">
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索 Line / ID"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Segmented
              block
              size="small"
              value={filter}
              onChange={(value) => setFilter(value as SampleFilter)}
              options={[
                { label: '全部', value: 'all' },
                { label: '待标', value: 'pending' },
                { label: '完成', value: 'completed' },
              ]}
            />
          </div>
          <div className="sample-list">
            {filteredIndexes.map((index) => {
              const item = samples[index];
              return (
                <button
                  type="button"
                  key={item.sample_id}
                  className={`sample-row ${index === selectedIndex ? 'is-active' : ''}`}
                  onClick={() => void navigate(index)}
                >
                  <span className="sample-row-head">
                    <strong>{item.sample_id}</strong>
                    <Tag color={statusColor(item)} bordered={false}>
                      {item.annotation.status === 'completed' ? '完成' : '待标'}
                    </Tag>
                  </span>
                  <span className="sample-row-line">{item.input.quote_line}</span>
                </button>
              );
            })}
          </div>
        </aside>

        <main className="annotation-main">
          <section className="quote-focus">
            <div className="section-heading">
              <div>
                <Typography.Text type="secondary">
                  {sample.sample_id} · 第 {selectedIndex + 1} / {samples.length} 条
                </Typography.Text>
                <Space size={6} wrap>
                  <Tag>{sample.selection_bucket}</Tag>
                  <Tag color="blue">{sample.difficulty_candidate}</Tag>
                  {sample.source.quote_source && <Tag>{sample.source.quote_source}</Tag>}
                </Space>
              </div>
              <Space>
                <Tooltip title="上一条">
                  <Button
                    icon={<LeftOutlined />}
                    disabled={selectedIndex === 0 || saving}
                    onClick={() => void navigate(selectedIndex - 1)}
                  />
                </Tooltip>
                <Tooltip title="下一条">
                  <Button
                    icon={<RightOutlined />}
                    disabled={selectedIndex === samples.length - 1 || saving}
                    onClick={() => void navigate(selectedIndex + 1)}
                  />
                </Tooltip>
              </Space>
            </div>
            <pre className="annotation-quote-line">{sample.input.quote_line}</pre>
          </section>

          <div className="annotation-columns">
            <section className="context-panel">
              <div className="section-heading">
                <Typography.Title level={5}>Paragraph 上下文</Typography.Title>
                <Typography.Text type="secondary">
                  前 {sample.input.paragraph_context_line_limit} 行
                </Typography.Text>
              </div>
              <pre className="annotation-context">{sample.input.paragraph_context}</pre>
              <dl className="source-meta">
                <div><dt>Speaker</dt><dd>{sample.source.speaker || '-'}</dd></div>
                <div><dt>Quote time</dt><dd>{sample.source.quote_time || '-'}</dd></div>
                <div><dt>Paragraph</dt><dd>{sample.source.paragraph_id || '-'}</dd></div>
              </dl>
              {sample.special_format_reasons?.length > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  message="特殊格式"
                  description={sample.special_format_reasons.join(' · ')}
                />
              )}
            </section>

            <section className="gold-panel">
              <div className="section-heading">
                <div>
                  <Typography.Title level={5}>预测与 Gold</Typography.Title>
                  <Typography.Text type="secondary">
                    {sample.prediction_snapshot.length || 0} 条当前预测
                  </Typography.Text>
                </div>
                <Space>
                  {sample.prediction_snapshot.length > 1 && (
                    <Select
                      size="small"
                      value={predictionIndex}
                      onChange={setPredictionIndex}
                      options={sample.prediction_snapshot.map((_, index) => ({
                        value: index,
                        label: `预测 ${index + 1}`,
                      }))}
                    />
                  )}
                  <Button
                    size="small"
                    icon={<CopyOutlined />}
                    disabled={!sample.prediction_snapshot.length}
                    onClick={adoptAllPredictions}
                  >
                    采用全部预测
                  </Button>
                </Space>
              </div>

              <div className="gold-grid gold-grid-head">
                <span>字段</span>
                <span>当前预测</span>
                <span>可获得性</span>
                <span>Gold</span>
                <span />
              </div>
              {FIELDS.map(({ key, label }) => (
                <div className="gold-grid gold-grid-row" key={key}>
                  <label htmlFor={`gold-${key}`}>{label}</label>
                  <div className="prediction-value">{displayValue(prediction[key])}</div>
                  <Select
                    value={draft.field_availability[key]}
                    options={AVAILABILITY_OPTIONS}
                    onChange={(value) => setAvailability(key, value)}
                  />
                  {key === 'coupon_rate' ? (
                    <InputNumber
                      id={`gold-${key}`}
                      value={draft.gold[key] as number | null}
                      controls={false}
                      disabled={draft.field_availability[key] === 'absent'}
                      onChange={(value) => setGold(key, value)}
                    />
                  ) : key === 'coupon_type' ? (
                    <Select
                      id={`gold-${key}`}
                      allowClear
                      value={draft.gold[key] as string | null}
                      disabled={draft.field_availability[key] === 'absent'}
                      options={[
                        { value: 'Fixed', label: 'Fixed' },
                        { value: 'FRN', label: 'FRN' },
                      ]}
                      onChange={(value) => setGold(key, value ?? null)}
                    />
                  ) : (
                    <Input
                      id={`gold-${key}`}
                      allowClear
                      value={draft.gold[key] as string | null ?? ''}
                      disabled={draft.field_availability[key] === 'absent'}
                      onChange={(event) => setGold(key, event.target.value || null)}
                    />
                  )}
                  <Tooltip title="采用当前预测">
                    <Button
                      type="text"
                      icon={<CopyOutlined />}
                      disabled={
                        prediction[key] === undefined
                        || draft.field_availability[key] === 'absent'
                      }
                      onClick={() => adoptPrediction(key)}
                    />
                  </Tooltip>
                </div>
              ))}

              <div className="annotation-meta">
                <label>
                  <span>Gold 难度</span>
                  <Select
                    allowClear
                    value={draft.difficulty_gold}
                    options={DIFFICULTY_OPTIONS}
                    onChange={(value) => updateDraft({ ...draft, difficulty_gold: value ?? null })}
                  />
                </label>
                <label>
                  <span>标注人</span>
                  <Input
                    value={draft.annotator ?? ''}
                    onChange={(event) => updateDraft({ ...draft, annotator: event.target.value || null })}
                  />
                </label>
                <label className="annotation-remark">
                  <span>备注</span>
                  <TextArea
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    value={draft.remark ?? ''}
                    onChange={(event) => updateDraft({ ...draft, remark: event.target.value || null })}
                  />
                </label>
              </div>
            </section>
          </div>

          <footer className="annotation-actions">
            <Typography.Text type={dirty ? 'warning' : 'secondary'}>
              {dirty
                ? '有未保存修改'
                : response.loaded_from === 'working'
                  ? `已同步至 ${response.working_file}`
                  : '首次保存后将生成独立标注 JSON'}
            </Typography.Text>
            <Space>
              <Button
                icon={<SaveOutlined />}
                loading={saving}
                disabled={!dirty}
                onClick={() => void saveDraft()}
              >
                保存草稿
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                loading={saving}
                onClick={() => void completeAndNext()}
              >
                完成并下一条
              </Button>
            </Space>
          </footer>
        </main>
      </div>
    </PageContainer>
  );
}
