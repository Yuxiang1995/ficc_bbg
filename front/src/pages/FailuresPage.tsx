import { EyeOutlined } from '@ant-design/icons';
import { PageContainer, ProTable, type ProColumns } from '@ant-design/pro-components';
import { Button, Descriptions, Drawer, Tag, Tooltip, Typography } from 'antd';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';
import { getFailures } from '../api';
import type { ExtractionFailure } from '../types';

export default function FailuresPage() {
  const [selected, setSelected] = useState<ExtractionFailure | null>(null);
  const columns = useMemo<ProColumns<ExtractionFailure>[]>(() => [
    { title: '关键字', dataIndex: 'q', hideInTable: true, fieldProps: { placeholder: '原始报价 / Line ID / 错误' } },
    { title: '错误类型', dataIndex: 'error_type', hideInTable: true },
    { title: '模型', dataIndex: 'model', hideInTable: true },
    { title: '发生日期', dataIndex: 'date_range', valueType: 'dateRange', hideInTable: true },
    {
      title: '发生时间', dataIndex: 'created_at', width: 170, sorter: true,
      render: (_, row) => dayjs(row.created_at).format('YYYY-MM-DD HH:mm:ss'),
    },
    { title: 'Line ID', dataIndex: 'line_id', width: 150, ellipsis: true, hideInSearch: true },
    {
      title: '原始报价', dataIndex: 'quote_line', width: 360, ellipsis: true, hideInSearch: true,
      render: (_, row) => <Typography.Text className="quote-line-cell">{row.quote_line}</Typography.Text>,
    },
    {
      title: '错误类型', dataIndex: 'error_type', width: 160, sorter: true, hideInSearch: true,
      render: (_, row) => <Tag color="red">{row.error_type}</Tag>,
    },
    { title: '模型', dataIndex: 'model', width: 150, sorter: true, ellipsis: true, hideInSearch: true },
    { title: '尝试次数', dataIndex: 'attempts', width: 92, align: 'right', sorter: true, hideInSearch: true },
    { title: '错误信息', dataIndex: 'error_message', width: 280, ellipsis: true, hideInSearch: true },
    {
      title: '', valueType: 'option', width: 48, fixed: 'right',
      render: (_, row) => (
        <Tooltip title="查看详情">
          <Button type="text" icon={<EyeOutlined />} onClick={() => setSelected(row)} />
        </Tooltip>
      ),
    },
  ], []);

  return (
    <PageContainer title="抽取失败" subTitle="超过三次尝试后跳过并保留的 quote line">
      <ProTable<ExtractionFailure>
        rowKey="failure_id"
        columns={columns}
        cardBordered={false}
        scroll={{ x: 1450 }}
        search={{ labelWidth: 76, defaultCollapsed: false }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        options={{ density: true, fullScreen: true, reload: true, setting: true }}
        dateFormatter="string"
        request={async (params, sort) => {
          const [sortField, sortOrder] = Object.entries(sort)[0] ?? ['created_at', 'descend'];
          const dateRange = params.date_range as string[] | undefined;
          const response = await getFailures({
            page: params.current,
            page_size: params.pageSize,
            q: params.q,
            model: params.model,
            error_type: params.error_type,
            date_from: dateRange?.[0],
            date_to: dateRange?.[1],
            sort_field: sortField,
            sort_order: sortOrder as string,
          });
          return { data: response.data, total: response.total, success: true };
        }}
      />
      <Drawer title="失败详情" width={680} open={Boolean(selected)} onClose={() => setSelected(null)}>
        {selected && (
          <div className="drawer-content">
            <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="Failure ID">{selected.failure_id}</Descriptions.Item>
              <Descriptions.Item label="Run ID">{selected.run_id}</Descriptions.Item>
              <Descriptions.Item label="模型">{selected.model}</Descriptions.Item>
              <Descriptions.Item label="尝试次数">{selected.attempts}</Descriptions.Item>
              <Descriptions.Item label="错误类型" span={2}>{selected.error_type}</Descriptions.Item>
              <Descriptions.Item label="错误信息" span={2}>{selected.error_message}</Descriptions.Item>
            </Descriptions>
            <div className="detail-block">
              <Typography.Title level={5}>原始报价</Typography.Title>
              <Typography.Paragraph className="raw-text" copyable>{selected.quote_line}</Typography.Paragraph>
            </div>
            <div className="detail-block">
              <Typography.Title level={5}>所属消息</Typography.Title>
              <Typography.Paragraph className="context-text">{selected.paragraph_text}</Typography.Paragraph>
            </div>
            <div className="detail-block">
              <Typography.Title level={5}>失败记录</Typography.Title>
              <pre className="json-view">{JSON.stringify(selected.failure_payload, null, 2)}</pre>
            </div>
          </div>
        )}
      </Drawer>
    </PageContainer>
  );
}
