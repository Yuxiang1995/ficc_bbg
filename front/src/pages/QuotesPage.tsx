import { EyeOutlined } from '@ant-design/icons';
import { PageContainer, ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components';
import { Button, Tag, Tooltip, Typography, message } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getOptions, getQuote, getQuotes, getSummary } from '../api';
import MetricStrip from '../components/MetricStrip';
import QuoteDrawer from '../components/QuoteDrawer';
import type { BondQuote, FilterOptions, NumericValue, QuoteSummary } from '../types';

const EMPTY_OPTIONS: FilterOptions = {
  currencies: [], quote_sources: [], quote_types: [], bond_types: [], parse_statuses: [], regions: [],
};

const numberText = (value: NumericValue, digits = 4) => {
  if (value === null || value === undefined || value === '') return <span className="empty-cell">-</span>;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits).replace(/\.?0+$/, '') : String(value);
};

const valueEnum = (values: string[]) =>
  Object.fromEntries(values.map((value) => [value, { text: value }]));

export default function QuotesPage() {
  const actionRef = useRef<ActionType>();
  const [summary, setSummary] = useState<QuoteSummary | null>(null);
  const [options, setOptions] = useState<FilterOptions>(EMPTY_OPTIONS);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedQuote, setSelectedQuote] = useState<BondQuote | null>(null);

  const loadOverview = async () => {
    try {
      const [nextSummary, nextOptions] = await Promise.all([getSummary(), getOptions()]);
      setSummary(nextSummary);
      setOptions(nextOptions);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '概览加载失败');
    }
  };

  useEffect(() => { void loadOverview(); }, []);

  const openDetail = async (quoteId: number) => {
    setDrawerOpen(true);
    setDetailLoading(true);
    try {
      setSelectedQuote(await getQuote(quoteId));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '报价详情加载失败');
      setDrawerOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  const columns = useMemo<ProColumns<BondQuote>[]>(() => [
    { title: 'Keyword', dataIndex: 'q', hideInTable: true, fieldProps: { placeholder: 'Ticker / ISIN / Issuer' } },
    {
      title: 'Quote Date', dataIndex: 'date_range', valueType: 'dateRange', hideInTable: true,
      fieldProps: { allowClear: true },
    },
    {
      title: 'Currency', dataIndex: 'currency', hideInTable: true, valueType: 'select',
      valueEnum: valueEnum(options.currencies), fieldProps: { mode: 'multiple' },
    },
    {
      title: 'Quote Type', dataIndex: 'quote_type', hideInTable: true, valueType: 'select',
      valueEnum: valueEnum(options.quote_types), fieldProps: { mode: 'multiple' },
    },
    {
      title: 'Bond Type', dataIndex: 'bond_type', hideInTable: true, valueType: 'select',
      valueEnum: valueEnum(options.bond_types), fieldProps: { mode: 'multiple' },
    },
    {
      title: 'Quote Source', dataIndex: 'quote_source', hideInTable: true, valueType: 'select',
      valueEnum: valueEnum(options.quote_sources), fieldProps: { showSearch: true },
    },
    {
      title: 'Parse Status', dataIndex: 'parse_status', hideInTable: true, valueType: 'select',
      valueEnum: valueEnum(options.parse_statuses),
    },
    {
      title: 'Quote Time', dataIndex: 'quote_time', width: 158, fixed: 'left', sorter: true, hideInSearch: true,
      render: (_, row) => row.quote_time ? dayjs(row.quote_time).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: 'Ticker', dataIndex: 'ticker', width: 108, fixed: 'left', sorter: true, hideInSearch: true,
      render: (_, row) => <Typography.Text strong>{row.ticker || '-'}</Typography.Text>,
    },
    {
      title: 'Coupon Type', dataIndex: 'coupon_type', width: 100, hideInSearch: true,
      render: (_, row) => row.coupon_type || '-',
    },
    {
      title: 'Coupon Rate', dataIndex: 'coupon_rate', width: 100, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => numberText(row.coupon_rate, 5),
    },
    {
      title: 'ISIN', dataIndex: 'isin', width: 126, ellipsis: true,
      render: (_, row) => row.isin || '-',
    },
    {
      title: 'Maturity Date', dataIndex: 'maturity_date', width: 116, sorter: true, hideInSearch: true,
      render: (_, row) => row.maturity_text || row.maturity_date || '-',
    },
    {
      title: 'Bid Px', dataIndex: 'bid_price', width: 88, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => <span className="bid-value">{numberText(row.bid_price)}</span>,
    },
    {
      title: 'Ask Px', dataIndex: 'ask_price', width: 88, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => <span className="ask-value">{numberText(row.ask_price)}</span>,
    },
    {
      title: 'Bid Sprd', dataIndex: 'bid_spread', width: 88, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => numberText(row.bid_spread, 2),
    },
    {
      title: 'Ask Sprd', dataIndex: 'ask_spread', width: 88, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => numberText(row.ask_spread, 2),
    },
    {
      title: 'Bid Size', dataIndex: 'bid_size', width: 88, align: 'right', hideInSearch: true,
      render: (_, row) => numberText(row.bid_size, 2),
    },
    {
      title: 'Ask Size', dataIndex: 'ask_size', width: 88, align: 'right', hideInSearch: true,
      render: (_, row) => numberText(row.ask_size, 2),
    },
    { title: 'Currency', dataIndex: 'currency', width: 76, hideInSearch: true },
    {
      title: 'Quote Source', dataIndex: 'quote_source', width: 130, ellipsis: true, hideInSearch: true,
      render: (_, row) => row.quote_source || '-',
    },
    {
      title: 'Parse Status', dataIndex: 'parse_status', width: 104, hideInSearch: true,
      render: (_, row) => <Tag color={row.parse_status === 'needs_review' ? 'gold' : 'green'}>{row.parse_status}</Tag>,
    },
    {
      title: 'Confidence', dataIndex: 'confidence', width: 88, align: 'right', sorter: true, hideInSearch: true,
      render: (_, row) => numberText(row.confidence, 2),
    },
    {
      title: '', valueType: 'option', width: 48, fixed: 'right',
      render: (_, row) => (
        <Tooltip title="查看详情">
          <Button type="text" icon={<EyeOutlined />} onClick={() => void openDetail(row.quote_id)} />
        </Tooltip>
      ),
    },
  ], [options]);

  return (
    <PageContainer
      title="债券报价"
      subTitle="Bloomberg 机构询报价结构化结果"
      extra={<span className="table-caption">时间显示：HK</span>}
    >
      <MetricStrip summary={summary} />
      <ProTable<BondQuote>
        actionRef={actionRef}
        rowKey="quote_id"
        columns={columns}
        cardBordered={false}
        scroll={{ x: 1730 }}
        search={{ labelWidth: 96, defaultCollapsed: false }}
        form={{ syncToUrl: false }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        options={{ density: true, fullScreen: true, reload: true, setting: true }}
        dateFormatter="string"
        request={async (params, sort) => {
          try {
            const [sortField, sortOrder] = Object.entries(sort)[0] ?? ['quote_time', 'descend'];
            const dateRange = params.date_range as unknown as Array<Dayjs | string> | undefined;
            const response = await getQuotes({
              page: params.current,
              page_size: params.pageSize,
              q: params.q,
              isin: params.isin,
              currency: Array.isArray(params.currency) ? params.currency.join(',') : params.currency,
              quote_type: Array.isArray(params.quote_type) ? params.quote_type.join(',') : params.quote_type,
              bond_type: Array.isArray(params.bond_type) ? params.bond_type.join(',') : params.bond_type,
              quote_source: params.quote_source,
              parse_status: params.parse_status,
              date_from: dateRange?.[0] ? dayjs(dateRange[0]).format('YYYY-MM-DD') : undefined,
              date_to: dateRange?.[1] ? dayjs(dateRange[1]).format('YYYY-MM-DD') : undefined,
              sort_field: sortField,
              sort_order: sortOrder as string,
            });
            return { data: response.data, total: response.total, success: true };
          } catch (error) {
            message.error(error instanceof Error ? error.message : '报价列表加载失败');
            return { data: [], total: 0, success: false };
          }
        }}
        onRow={(row) => ({
          onDoubleClick: () => void openDetail(row.quote_id),
          className: 'clickable-row',
        })}
      />
      <QuoteDrawer
        open={drawerOpen}
        loading={detailLoading}
        quote={selectedQuote}
        onClose={() => { setDrawerOpen(false); setSelectedQuote(null); }}
      />
    </PageContainer>
  );
}
