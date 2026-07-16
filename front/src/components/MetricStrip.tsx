import { AlertOutlined, CheckCircleOutlined, ClockCircleOutlined, DatabaseOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import type { QuoteSummary } from '../types';

interface MetricStripProps {
  summary: QuoteSummary | null;
}

const metrics = [
  { key: 'total_quotes', label: '累计报价', icon: DatabaseOutlined },
  { key: 'review_quotes', label: '待复核', icon: CheckCircleOutlined },
  { key: 'extraction_failures', label: '抽取失败', icon: AlertOutlined },
] as const;

export default function MetricStrip({ summary }: MetricStripProps) {
  return (
    <div className="metric-strip" aria-label="报价概览">
      {metrics.map(({ key, label, icon: Icon }) => (
        <div className="metric-item" key={key}>
          <Icon className="metric-icon" />
          <div>
            <div className="metric-label">{label}</div>
            <div className="metric-value">{summary ? summary[key].toLocaleString() : '--'}</div>
          </div>
        </div>
      ))}
      <div className="metric-item metric-latest">
        <ClockCircleOutlined className="metric-icon" />
        <div>
          <div className="metric-label">最近报价时间</div>
          <div className="metric-time">
            {summary?.latest_quote_time
              ? dayjs(summary.latest_quote_time).format('YYYY-MM-DD HH:mm:ss')
              : '--'}
          </div>
        </div>
      </div>
    </div>
  );
}
