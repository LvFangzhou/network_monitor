import { useEffect, useState, type CSSProperties } from 'react'
import { Button, Card, Col, Input, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message } from 'antd'
import { CheckOutlined, EyeInvisibleOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  acknowledgeAlert,
  getAlertHistory,
  getAlertStats,
  ignoreAlert,
  resolveAlert,
  type AlertHistory as AlertHistoryItem,
  type AlertStats,
} from '../../api/alerts'
import { useAuthStore } from '../../store/auth'

const statusColors: Record<string, string> = {
  firing: 'red',
  acknowledged: 'gold',
  resolved: 'green',
  ignored: 'default',
  snoozed: 'blue',
}

const statusLabels: Record<string, string> = {
  firing: '触发中',
  acknowledged: '已确认',
  resolved: '已解决',
  ignored: '已忽略',
  snoozed: '暂停复查',
}

const severityLabels: Record<string, string> = {
  P0: 'P0',
  P1: 'P1',
  P2: 'P2',
  P3: 'P3',
  critical: 'P0',
  warning: 'P1',
  info: 'P2',
}

const severityColors: Record<string, string> = {
  P0: 'red',
  P1: 'gold',
  P2: 'blue',
  P3: 'default',
  critical: 'red',
  warning: 'gold',
  info: 'blue',
}

const compactTextStyle: CSSProperties = {
  display: 'block',
  maxWidth: '100%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const multiLineTextStyle: CSSProperties = {
  display: 'block',
  maxWidth: '100%',
  maxHeight: 60,
  overflow: 'hidden',
  wordBreak: 'break-word',
  overflowWrap: 'anywhere',
  whiteSpace: 'normal',
  lineHeight: '20px',
  fontWeight: 500,
}

const operationMessageStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  maxWidth: '100%',
  fontSize: 12,
  lineHeight: '20px',
  fontWeight: 500,
}

const operationLabelStyle: CSSProperties = {
  color: '#666',
  fontWeight: 600,
}

const operationValueStyle: CSSProperties = {
  display: 'inline',
  wordBreak: 'break-word',
  overflowWrap: 'anywhere',
}

const operationContentStyle: CSSProperties = {
  ...multiLineTextStyle,
  maxHeight: 40,
  fontSize: 12,
  color: '#222',
}

const decodeHexText = (value: string) => {
  const text = value.trim()
  if (!/^0x[0-9a-fA-F]+$/.test(text) || text.length % 2 !== 0) return ''
  try {
    const bytes = new Uint8Array((text.length - 2) / 2)
    for (let i = 2, index = 0; i < text.length; i += 2, index += 1) {
      bytes[index] = parseInt(text.slice(i, i + 2), 16)
    }
    return new TextDecoder('utf-8').decode(bytes).trim()
  } catch {
    return ''
  }
}

const normalizeTrapText = (value?: string | null) => {
  const text = (value || '').trim()
  if (!text) return ''
  return text.replace(/0x[0-9a-fA-F]+/g, (match) => decodeHexText(match) || match)
}

const extractTrapContent = (value?: string | null) => {
  const normalized = normalizeTrapText(value)
  const marker = 'Trap内容:'
  const index = normalized.indexOf(marker)
  if (index >= 0) return normalized.slice(index + marker.length).trim()
  return normalized
}

const cleanOperationContent = (content: string, deviceName?: string | null) => {
  let text = (content || '').trim()
  const name = (deviceName || '').trim()
  if (name && text.startsWith(name)) {
    text = text.slice(name.length).replace(/^[\s/]+/, '')
  }
  if (text.includes(' / ')) {
    const parts = text.split(' / ').map((item) => item.trim()).filter(Boolean)
    text = parts.find((item) => item !== name) || parts[parts.length - 1] || text
  }
  if (name && text === name) {
    text = ''
  }
  return text || '配置变更'
}

const extractTrapField = (text: string, label: string, nextLabels: string[]) => {
  const start = text.indexOf(label)
  if (start < 0) return ''
  const valueStart = start + label.length
  const end = nextLabels
    .map((nextLabel) => text.indexOf(nextLabel, valueStart))
    .filter((index) => index >= 0)
    .sort((a, b) => a - b)[0]
  return text.slice(valueStart, end ?? undefined).trim()
}

const operationMessageInfo = (record: AlertHistoryItem) => {
  const text = normalizeTrapText(record.message || '')
  const rawContent = extractTrapContent(text)
  return {
    fullText: text,
    ruleName: extractTrapField(text, '规则:', ['Trap OID:', 'Trap级别:', '设备时间:', 'Trap内容:']),
    trapTime: extractTrapField(text, '设备时间:', ['Trap内容:']),
    content: cleanOperationContent(rawContent, record.device_name),
  }
}

const displayTargetName = (record: AlertHistoryItem) => {
  if (record.severity === 'P3' || record.alert_target_type === 'snmp_trap') {
    return ''
  }
  return record.alert_target_name || ''
}

const isOperationRecord = (record: AlertHistoryItem) => record.severity === 'P3'

const AlertHistory = () => {
  const [items, setItems] = useState<AlertHistoryItem[]>([])
  const [stats, setStats] = useState<AlertStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>()
  const [severityFilter, setSeverityFilter] = useState<string>()
  const [searchText, setSearchText] = useState<string>('')
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const token = useAuthStore((state) => state.token)
  const currentUser = useAuthStore((state) => state.user)
  const canModify = Boolean(token && !currentUser?.read_only)
  const initialAlertId = new URLSearchParams(window.location.search).get('alert_id')

  const fetchData = async (nextPage = page, nextPageSize = pageSize, silent = false) => {
    if (!silent) {
      setLoading(true)
    }
    try {
      const [historyResult, statsResult] = await Promise.all([
        getAlertHistory({
          skip: (nextPage - 1) * nextPageSize,
          limit: nextPageSize,
          status: statusFilter,
          severity: severityFilter,
          alert_id: initialAlertId ? Number(initialAlertId) : undefined,
          search: searchText || undefined,
        }),
        getAlertStats(),
      ])
      setItems(historyResult.items)
      setTotal(historyResult.total)
      setStats(statsResult)
    } catch (error) {
      if (!silent) {
        message.error('获取告警历史失败')
      }
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1)
      fetchData(1, pageSize)
    }, 300)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, severityFilter, searchText])

  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchData(page, pageSize, true)
    }, 10000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, statusFilter, severityFilter, searchText])

  const handleResetFilters = () => {
    setStatusFilter(undefined)
    setSeverityFilter(undefined)
    setSearchText('')
    setPage(1)
  }

  const handleAcknowledge = async (id: number) => {
    try {
      await acknowledgeAlert(id, '', currentUser?.username)
      message.success('告警已确认')
      fetchData()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '确认失败')
    }
  }

  const handleIgnore = async (id: number) => {
    try {
      await ignoreAlert(id, '由前端页面手动忽略', currentUser?.username)
      message.success('告警已忽略')
      fetchData()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '忽略失败')
    }
  }

  const handleResolve = async (id: number) => {
    try {
      await resolveAlert(id, '由前端页面手动解决', currentUser?.username)
      message.success('已暂停复查，1小时后如故障仍存在会重新触发')
      fetchData()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '解决失败')
    }
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card>
            <Statistic title="正在触发" value={stats?.total_firing || 0} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="已解决" value={stats?.total_resolved || 0} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="历史记录数" value={total} />
          </Card>
        </Col>
      </Row>

      <Card
        title="告警历史"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 120 }}
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: 'firing', label: '触发中' },
                { value: 'acknowledged', label: '已确认' },
                { value: 'resolved', label: '已解决' },
                { value: 'ignored', label: '已忽略' },
                { value: 'snoozed', label: '暂停复查' },
              ]}
            />
            <Select
              allowClear
              placeholder="级别"
              style={{ width: 120 }}
              value={severityFilter}
              onChange={setSeverityFilter}
              options={[
                { value: 'P0', label: 'P0' },
                { value: 'P1', label: 'P1' },
                { value: 'P2', label: 'P2' },
                { value: 'P3', label: 'P3' },
              ]}
            />
            <Input
              placeholder="搜索 AlarmID / 设备 / 端口 / IP / 消息"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ width: 320 }}
            />
            <Tooltip title="重置筛选">
              <Button icon={<ReloadOutlined />} onClick={handleResetFilters}>
                重置
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <Table
          rowKey="id"
          tableLayout="fixed"
          scroll={{ x: 1180 }}
          loading={loading}
          dataSource={items}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100, 200],
            showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPage)
              setPageSize(nextPageSize)
              fetchData(nextPage, nextPageSize)
            },
          }}
          columns={[
            {
              title: 'Alarm ID',
              dataIndex: 'alarm_id',
              width: 92,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (value?: string | null) => value || '-',
            },
            {
              title: '设备',
              width: 300,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (_: unknown, record: AlertHistoryItem) => (
                <Space direction="vertical" size={0}>
                  <Tooltip title={record.device_name || `设备 ${record.device_id}`}>
                    <Typography.Text style={compactTextStyle}>{record.device_name || `设备 ${record.device_id}`}</Typography.Text>
                  </Tooltip>
                  <span style={{ color: '#666', fontSize: 12 }}>{record.device_ip || '-'}</span>
                  {displayTargetName(record) ? (
                    <Tooltip title={displayTargetName(record)}>
                      <Typography.Text style={{ ...compactTextStyle, color: '#999', fontSize: 12 }}>
                        {displayTargetName(record)}
                      </Typography.Text>
                    </Tooltip>
                  ) : null}
                </Space>
              ),
            },
            {
              title: '消息',
              dataIndex: 'message',
              width: 520,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (value?: string | null, record?: AlertHistoryItem) => {
                if (record && isOperationRecord(record)) {
                  const info = operationMessageInfo(record)
                  return (
                    <Tooltip title={<span style={{ whiteSpace: 'pre-wrap' }}>{info.fullText}</span>}>
                      <div style={operationMessageStyle}>
                        <div style={compactTextStyle}>
                          <span style={operationLabelStyle}>记录类型：</span>
                          <span style={operationValueStyle}>{info.ruleName || '山石配置变更Trap'}</span>
                        </div>
                        {info.trapTime ? (
                          <div style={compactTextStyle}>
                            <span style={operationLabelStyle}>设备时间：</span>
                            <span style={operationValueStyle}>{info.trapTime}</span>
                          </div>
                        ) : null}
                        <div style={operationContentStyle}>
                          <span style={operationLabelStyle}>变更内容：</span>
                          <span>{info.content || '-'}</span>
                        </div>
                      </div>
                    </Tooltip>
                  )
                }
                const text = value || '-'
                return (
                  <Tooltip title={<span style={{ whiteSpace: 'pre-wrap' }}>{text}</span>}>
                    <Typography.Text style={multiLineTextStyle}>{text}</Typography.Text>
                  </Tooltip>
                )
              },
            },
            {
              title: '级别',
              dataIndex: 'severity',
              width: 76,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (value?: string | null) => <Tag color={value ? severityColors[value] || 'default' : 'default'}>{value ? severityLabels[value] || value : '-'}</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 88,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (value: string, record: AlertHistoryItem) => (
                isOperationRecord(record)
                  ? <Tag color="default">变更记录</Tag>
                  : <Tag color={statusColors[value] || 'default'}>{statusLabels[value] || value}</Tag>
              ),
            },
            {
              title: '处理人',
              width: 90,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (_: unknown, record: AlertHistoryItem) => record.current_handler || '-',
            },
            {
              title: '开始时间',
              dataIndex: 'started_at',
              width: 170,
              onCell: () => ({ style: { verticalAlign: 'top' } }),
              render: (value?: string | null) => value ? new Date(value).toLocaleString() : '-',
            },
            canModify ? {
              title: '操作',
              width: 160,
              render: (_: unknown, record: AlertHistoryItem) => (
                <Space>
                  {!isOperationRecord(record) && record.status === 'firing' && (
                    <Tooltip title="确认告警">
                      <Button size="small" icon={<CheckOutlined />} onClick={() => handleAcknowledge(record.id)}>
                      确认
                      </Button>
                    </Tooltip>
                  )}
                  {!isOperationRecord(record) && record.status === 'firing' && (
                    <Tooltip title="忽略告警">
                      <Button size="small" icon={<EyeInvisibleOutlined />} onClick={() => handleIgnore(record.id)}>
                      忽略
                      </Button>
                    </Tooltip>
                  )}
                  {!isOperationRecord(record) && record.status !== 'resolved' && record.status !== 'snoozed' && (
                    <Tooltip title="暂停复查一小时">
                      <Button size="small" type="primary" onClick={() => handleResolve(record.id)}>
                      解决
                      </Button>
                    </Tooltip>
                  )}
                </Space>
              ),
            } : null,
          ].filter(Boolean) as any}
        />
      </Card>
    </Space>
  )
}

export default AlertHistory
