import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#0b6b62',
          colorInfo: '#0b6b62',
          borderRadius: 6,
          colorBgLayout: '#f3f5f6',
          colorText: '#1c272b',
          colorTextSecondary: '#637177',
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
        },
        components: {
          Table: { headerBg: '#f6f8f8', headerColor: '#4b5a60', rowHoverBg: '#f0f7f5' },
          Layout: { siderBg: '#162326', headerBg: '#ffffff' },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
