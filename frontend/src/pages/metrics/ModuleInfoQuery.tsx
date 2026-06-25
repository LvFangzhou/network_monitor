import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  getControllerOptions,
  getControllerOpticals,
  type ControllerOption,
} from '../../api/controller'

const { Text } = Typography

const levelOptions = [
  { value: 0, label: '全部健康等级' },
  { value: 1, label: '差' },
  { value: 2, label: '一般' },
  { value: 3, label: '好' },
  { value: 4, label: '其他' },
]

const hourOptions = [
  { value: 3, label: '最近3小时' },
  { value: 24, label: '最近24小时' },
  { value: 72, label: '最近3天' },
  { value: 168, label: '最近7天' },
]

const levelLabel = (value?: number | string) => {
  const normalized = Number(value)
  if (normalized === 1) return <Tag color="red">差</Tag>
  if (normalized === 2) return <Tag color="orange">一般</Tag>
  if (normalized === 3) return <Tag color="green">好</Tag>
  if (normalized === 4) return <Tag>其他</Tag>
  return <Text type="secondary">-</Text>
}

const formatOpticalPower = (value?: number | null) => {
  if (value === undefined || value === null) return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '-'
  return `${(numeric * 0.01).toFixed(2)} dBm`
}

const formatTemperature = (value?: number | null) => {
  if (value === undefined || value === null) return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '-'
  const display = Math.abs(numeric) > 1000 ? numeric / 1000 : numeric
  return `${display.toFixed(1)} ℃`
}

const formatVoltage = (value?: number | null) => {
  if (value === undefined || value === null) return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '-'
  return `${(numeric * 0.01).toFixed(2)} V`
}

const ModuleInfoQuery = () => {
  const [controllers, setControllers] = useState<ControllerOption[]>([])
  const [controllerId, setControllerId] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [filters, setFilters] = useState({
    search: '',
    device_ip: '',
    interface_name: '',
    vendor_name: '',
    level: 0,
    hours: 3,
  })

  const controllerOptions = useMemo(
    () => controllers.map((item) => ({ value: item.id, label: `${item.name}（${item.base_url}）` })),
    [controllers],
  )

  const loadControllers = async () => {
    try {
      const result = await getControllerOptions()
      setControllers(result.items || [])
      setControllerId((current) => current || result.items?.[0]?.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取控制器列表失败')
    }
  }

  const loadData = async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true)
    try {
      const result = await getControllerOpticals({
        controller_id: controllerId,
        page: nextPage,
        page_size: nextPageSize,
        search: filters.search?.trim() || undefined,
        device_ip: filters.device_ip?.trim() || undefined,
        interface_name: filters.interface_name?.trim() || undefined,
        vendor_name: filters.vendor_name?.trim() || undefined,
        level: filters.level,
        hours: filters.hours,
      })
      setItems(result.items || [])
      setTotal(result.total || 0)
      setPage(nextPage)
      setPageSize(nextPageSize)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '查询模块信息失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadControllers()
  }, [])

  useEffect(() => {
    if (controllerId) loadData(1, pageSize)
  }, [controllerId])

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space wrap>
          <Select
            style={{ minWidth: 320 }}
            placeholder="选择控制器"
            value={controllerId}
            options={controllerOptions}
            onChange={setControllerId}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="设备/IP/接口模糊搜索"
            style={{ width: 240 }}
            value={filters.search}
            onChange={(event) => setFilters((prev) => ({ ...prev, search: event.target.value }))}
            onPressEnter={() => loadData(1, pageSize)}
          />
          <Input
            allowClear
            placeholder="设备IP精确筛选"
            style={{ width: 180 }}
            value={filters.device_ip}
            onChange={(event) => setFilters((prev) => ({ ...prev, device_ip: event.target.value }))}
            onPressEnter={() => loadData(1, pageSize)}
          />
          <Input
            allowClear
            placeholder="接口精确筛选"
            style={{ width: 180 }}
            value={filters.interface_name}
            onChange={(event) => setFilters((prev) => ({ ...prev, interface_name: event.target.value }))}
            onPressEnter={() => loadData(1, pageSize)}
          />
          <Input
            allowClear
            placeholder="厂商筛选"
            style={{ width: 160 }}
            value={filters.vendor_name}
            onChange={(event) => setFilters((prev) => ({ ...prev, vendor_name: event.target.value }))}
            onPressEnter={() => loadData(1, pageSize)}
          />
          <Select
            style={{ width: 140 }}
            value={filters.level}
            options={levelOptions}
            onChange={(value) => setFilters((prev) => ({ ...prev, level: value }))}
          />
          <Select
            style={{ width: 130 }}
            value={filters.hours}
            options={hourOptions}
            onChange={(value) => setFilters((prev) => ({ ...prev, hours: value }))}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={() => loadData(1, pageSize)} disabled={!controllerId}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadData(page, pageSize)} disabled={!controllerId}>
            刷新
          </Button>
        </Space>
      </Card>

      <Card title={`模块信息查询${total ? `（共 ${total} 条）` : ''}`}>
        <Table
          rowKey={(record) => `${record.assetId || record.deviceIp}-${record.ifIndex || record.ifDesc}-${record.serialNumber || ''}`}
          loading={loading}
          dataSource={items}
          scroll={{ x: 1800 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
            onChange: loadData,
          }}
          columns={[
            { title: '设备', dataIndex: 'deviceName', width: 260, fixed: 'left', ellipsis: true },
            { title: 'IP', dataIndex: 'deviceIp', width: 130 },
            { title: '接口', dataIndex: 'ifDesc', width: 150, ellipsis: true },
            { title: '运行状态', dataIndex: 'ifOperStatus', width: 100, render: (value) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag>DOWN</Tag> },
            { title: '管理状态', dataIndex: 'adminStatus', width: 100, render: (value) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag>DOWN</Tag> },
            { title: '健康等级', dataIndex: 'level', width: 100, render: levelLabel },
            { title: '评分', dataIndex: 'opticalGrade', width: 90, render: (value) => value ?? '-' },
            { title: '厂商', dataIndex: 'vendorName', width: 150, ellipsis: true },
            { title: '序列号', dataIndex: 'serialNumber', width: 180, ellipsis: true },
            { title: '类型', dataIndex: 'transceiveType', width: 160, ellipsis: true },
            { title: '速率', dataIndex: 'transceiverSpeed', width: 120, render: (value) => value || '-' },
            { title: '收光', dataIndex: 'curRxPower', width: 120, render: formatOpticalPower },
            { title: '发光', dataIndex: 'curTxPower', width: 120, render: formatOpticalPower },
            { title: '温度', dataIndex: 'curTemperature', width: 110, render: formatTemperature },
            { title: '电压', dataIndex: 'curVoltage', width: 110, render: formatVoltage },
            { title: '生产日期', dataIndex: 'mfgDate', width: 130, render: (value) => value || '-' },
            { title: '采集时间', dataIndex: 'time', width: 170, render: (value) => value ? new Date(Number(value)).toLocaleString() : '-' },
          ]}
        />
      </Card>
    </Space>
  )
}

export default ModuleInfoQuery
