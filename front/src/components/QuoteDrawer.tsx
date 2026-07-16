import { Descriptions, Drawer, Skeleton, Space, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import type { BondQuote, NumericValue } from '../types';

interface QuoteDrawerProps {
  open: boolean;
  loading: boolean;
  quote: BondQuote | null;
  onClose: () => void;
}

const displayNumber = (value: NumericValue, digits = 4) => {
  if (value === null || value === undefined || value === '') return '-';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits).replace(/\.?0+$/, '') : String(value);
};

const displayDateTime = (value: string | null | undefined) =>
  value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-';

export default function QuoteDrawer({ open, loading, quote, onClose }: QuoteDrawerProps) {
  return (
    <Drawer
      title={quote?.ticker ? `${quote.ticker} 报价详情` : '报价详情'}
      width={720}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {loading || !quote ? (
        <Skeleton active paragraph={{ rows: 12 }} />
      ) : (
        <Space direction="vertical" size={20} className="drawer-content">
          <Descriptions title="债券信息" bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="Ticker">{quote.ticker || '-'}</Descriptions.Item>
            <Descriptions.Item label="ISIN">{quote.isin || '-'}</Descriptions.Item>
            <Descriptions.Item label="发行人" span={2}>{quote.issuer || '-'}</Descriptions.Item>
            <Descriptions.Item label="币种">{quote.currency || '-'}</Descriptions.Item>
            <Descriptions.Item label="区域">{quote.region || '-'}</Descriptions.Item>
            <Descriptions.Item label="票息">
              {quote.coupon_type || '-'} {displayNumber(quote.coupon_rate, 6)}
            </Descriptions.Item>
            <Descriptions.Item label="到期日">{quote.maturity_text || quote.maturity_date || '-'}</Descriptions.Item>
            <Descriptions.Item label="评级 M/S/F" span={2}>
              {[quote.rating_moodys, quote.rating_sp, quote.rating_fitch].map((rating, index) => (
                <Tag key={index}>{rating || '-'}</Tag>
              ))}
            </Descriptions.Item>
          </Descriptions>

          <Descriptions title="报价信息" bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="Bid Price">{displayNumber(quote.bid_price)}</Descriptions.Item>
            <Descriptions.Item label="Ask Price">{displayNumber(quote.ask_price)}</Descriptions.Item>
            <Descriptions.Item label="Bid Spread">{displayNumber(quote.bid_spread)} {quote.spread_unit || ''}</Descriptions.Item>
            <Descriptions.Item label="Ask Spread">{displayNumber(quote.ask_spread)} {quote.spread_unit || ''}</Descriptions.Item>
            <Descriptions.Item label="Bid Size">{displayNumber(quote.bid_size)} {quote.size_unit || ''}</Descriptions.Item>
            <Descriptions.Item label="Ask Size">{displayNumber(quote.ask_size)} {quote.size_unit || ''}</Descriptions.Item>
            <Descriptions.Item label="报价时间">{displayDateTime(quote.quote_time)}</Descriptions.Item>
            <Descriptions.Item label="渠道">{quote.quote_source || '-'}</Descriptions.Item>
            <Descriptions.Item label="报价类型">{quote.quote_type || '-'}</Descriptions.Item>
            <Descriptions.Item label="债券类型">{quote.bond_type || '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={quote.parse_status === 'needs_review' ? 'gold' : 'green'}>{quote.parse_status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="置信度">{displayNumber(quote.confidence, 2)}</Descriptions.Item>
            <Descriptions.Item label="备注" span={2}>{quote.remark || '-'}</Descriptions.Item>
          </Descriptions>

          <div className="detail-block">
            <Typography.Title level={5}>原始报价</Typography.Title>
            <Typography.Paragraph className="raw-text" copyable>{quote.raw_line}</Typography.Paragraph>
          </div>
          {quote.paragraph_text && (
            <div className="detail-block">
              <Typography.Title level={5}>所属消息</Typography.Title>
              <Typography.Paragraph className="context-text" ellipsis={{ rows: 8, expandable: true, symbol: '展开' }}>
                {quote.paragraph_text}
              </Typography.Paragraph>
            </div>
          )}
          <div className="detail-block">
            <Typography.Title level={5}>来源与模型</Typography.Title>
            <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="Quote ID">{quote.quote_id}</Descriptions.Item>
              <Descriptions.Item label="Run ID">{quote.run_id}</Descriptions.Item>
              <Descriptions.Item label="Line ID">{quote.line_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="Speaker">{quote.speaker || '-'}</Descriptions.Item>
              <Descriptions.Item label="Splitter">{quote.splitter_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="Extractor">{quote.extractor_model || '-'}</Descriptions.Item>
            </Descriptions>
          </div>
          {quote.raw_payload && (
            <div className="detail-block">
              <Typography.Title level={5}>LLM 原始结构</Typography.Title>
              <pre className="json-view">{JSON.stringify(quote.raw_payload, null, 2)}</pre>
            </div>
          )}
        </Space>
      )}
    </Drawer>
  );
}
