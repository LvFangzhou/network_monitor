import { Card, Empty, Space, Typography } from 'antd'
import { LineChartOutlined } from '@ant-design/icons'

const { Text, Title } = Typography

const QualityQuery = () => {
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 质量查询</div>
      <Card>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Title level={4} style={{ margin: 0 }}>公网质量探测</Title>
          <Text type="secondary">
            这里用于后续维护公网质量探测目标，持续采集延迟、丢包、抖动和 SLA 曲线。
          </Text>
          <Empty
            image={<LineChartOutlined style={{ fontSize: 42, color: '#8c8c8c' }} />}
            description="质量探测目标和 SLA 曲线功能待接入"
          />
        </Space>
      </Card>
    </Space>
  )
}

export default QualityQuery
