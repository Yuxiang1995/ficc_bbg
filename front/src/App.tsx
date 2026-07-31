import { AlertOutlined, DatabaseOutlined, FormOutlined, LineChartOutlined } from '@ant-design/icons';
import { ProLayout } from '@ant-design/pro-components';
import { Badge, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';
import FailuresPage from './pages/FailuresPage';
import AnnotationPage from './pages/AnnotationPage';
import QuotesPage from './pages/QuotesPage';

type ViewKey = '/quotes' | '/failures' | '/annotation';

const currentView = (): ViewKey => {
  if (window.location.hash === '#/failures') return '/failures';
  if (window.location.hash === '#/annotation') return '/annotation';
  return '/quotes';
};

export default function App() {
  const [view, setView] = useState<ViewKey>(currentView());

  useEffect(() => {
    const onHashChange = () => setView(currentView());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = (path: string) => {
    window.location.hash = path;
  };

  return (
    <ProLayout
      title="FICC Quote Console"
      logo={<div className="brand-mark"><LineChartOutlined /></div>}
      layout="mix"
      fixedHeader
      fixSiderbar
      breakpoint="lg"
      siderWidth={216}
      contentWidth="Fluid"
      location={{ pathname: view }}
      route={{
        path: '/',
        routes: [
          { path: '/quotes', name: '债券报价', icon: <DatabaseOutlined /> },
          { path: '/failures', name: '抽取失败', icon: <AlertOutlined /> },
          { path: '/annotation', name: '评测标注', icon: <FormOutlined /> },
        ],
      }}
      menuItemRender={(item, dom) => (
        <a onClick={(event) => { event.preventDefault(); navigate(item.path || '/quotes'); }} href={`#${item.path}`}>
          {dom}
        </a>
      )}
      avatarProps={false}
      actionsRender={() => [
        <Space key="database" className="database-state">
          <Badge status="success" />
          <Typography.Text>PostgreSQL</Typography.Text>
        </Space>,
      ]}
      token={{
        header: { colorBgHeader: '#ffffff', colorHeaderTitle: '#1c272b' },
        sider: { colorMenuBackground: '#162326', colorTextMenu: '#b9c6c8', colorTextMenuSelected: '#ffffff' },
      }}
      contentStyle={{ padding: 0 }}
    >
      {view === '/failures' && <FailuresPage />}
      {view === '/annotation' && <AnnotationPage />}
      {view === '/quotes' && <QuotesPage />}
    </ProLayout>
  );
}
