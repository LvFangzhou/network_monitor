import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Button, Card, Col, Dropdown, Input, Modal, Row, Segmented, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message, theme } from 'antd'
import { CheckOutlined, DeleteOutlined, EyeInvisibleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  acknowledgeAlert,
  clearAlertHistory,
  getAlertHistory,
  getAlertHistorySummary,
  ignoreAlert,
  quickSilenceAlert,
  resolveAlert,
  type AlertHistory as AlertHistoryItem,
  type AlertHistorySummary,
} from '../../api/alerts'
import { getDatacenters, type Datacenter } from '../../api/devices'
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

const chartColors = ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#14b8a6', '#6366f1', '#84cc16']
const cleanupOptions = [
  { label: '30天前', days: 30 },
  { label: '15天前', days: 15 },
  { label: '7天前', days: 7 },
  { label: '3天前', days: 3 },
  { label: '所有', days: undefined },
]

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

const summaryCardBodyStyle: CSSProperties = {
  height: 320,
  overflowY: 'auto',
  paddingTop: 12,
  paddingBottom: 12,
}

const metricCardBodyStyle: CSSProperties = {
  paddingTop: 18,
  paddingBottom: 18,
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

const getOperationRecordType = (record: AlertHistoryItem) => {
  const text = normalizeTrapText([record.message, record.alert_target_name, record.alert_target_key].filter(Boolean).join(' ')).toLowerCase()
  const info = operationMessageInfo(record)
  const ruleText = `${info.ruleName || ''} ${text}`.toLowerCase()
  if (/登录失败|ssh|login|failed/.test(ruleText)) return '登录失败Trap'
  if (/配置变更|config/.test(ruleText)) return '配置变更Trap'
  return 'P3 Trap日志'
}

type AlertHistoryMode = 'active' | 'audit'

interface AlertHistoryProps {
  mode?: AlertHistoryMode
}

const AlertHistory = ({ mode = 'active' }: AlertHistoryProps) => {
  const { token: themeToken } = theme.useToken()
  const [items, setItems] = useState<AlertHistoryItem[]>([])
  const [summary, setSummary] = useState<AlertHistorySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [quickSilenceLoadingId, setQuickSilenceLoadingId] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>()
  const [severityFilter, setSeverityFilter] = useState<string>()
  const [datacenterFilter, setDatacenterFilter] = useState<string>()
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [searchText, setSearchText] = useState<string>('')
  const [datacenterChartType, setDatacenterChartType] = useState<'pie' | 'bar'>('pie')
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const token = useAuthStore((state) => state.token)
  const currentUser = useAuthStore((state) => state.user)
  const canModify = Boolean(token && !currentUser?.read_only)
  const initialAlertId = new URLSearchParams(window.location.search).get('alert_id')

  const isAuditMode = mode === 'audit'
  const tableTitle = isAuditMode ? '告警日志详情' : '正在触发告警'
  const statusCounts = summary?.statuses || {}
  const severityCounts = summary?.severities || {}

  const datacenterChartData = useMemo(
    () => (summary?.datacenters || []).map((item) => ({ ...item, value: item.count })),
    [summary?.datacenters],
  )
  const dayChartData = useMemo(
    () => (summary?.days || []).map((item) => ({ ...item, label: item.day.slice(5), value: item.count })),
    [summary?.days],
  )
  const maxDayCount = useMemo(
    () => Math.max(1, ...dayChartData.map((item) => item.value)),
    [dayChartData],
  )
  const maxDeviceCount = useMemo(
    () => Math.max(1, ...(summary?.devices || []).map((item) => item.count)),
    [summary?.devices],
  )
  const chartGrid = themeToken.colorBorderSecondary
  const chartAxis = themeToken.colorTextSecondary
  const chartTooltipStyle = {
    background: themeToken.colorBgElevated,
    border: `1px solid ${themeToken.colorBorderSecondary}`,
    borderRadius: 12,
    color: themeToken.colorText,
    boxShadow: themeToken.boxShadowSecondary,
  }

  const buildFilterParams = () => ({
    view: mode,
    status: isAuditMode ? statusFilter : undefined,
    severity: isAuditMode ? severityFilter : undefined,
    datacenter: datacenterFilter,
    alert_id: initialAlertId ? Number(initialAlertId) : undefined,
    search: searchText || undefined,
  })

  const fetchData = async (nextPage = page, nextPageSize = pageSize, silent = false) => {
    if (!silent) {
      setLoading(true)
    }
    try {
      const historyResult = await getAlertHistory({
        skip: (nextPage - 1) * nextPageSize,
        limit: nextPageSize,
        ...buildFilterParams(),
      })
      setItems(historyResult.items)
      setTotal(historyResult.total)
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

  const fetchSummary = async (silent = false) => {
    if (!silent) {
      setSummaryLoading(true)
    }
    try {
      const result = await getAlertHistorySummary({
        ...buildFilterParams(),
        limit: 10,
      })
      setSummary(result)
    } catch {
      if (!silent) {
        message.error('获取告警统计失败')
      }
    } finally {
      if (!silent) {
        setSummaryLoading(false)
      }
    }
  }

  useEffect(() => {
    fetchData()
    fetchSummary()
    getDatacenters().then(setDatacenters).catch(() => undefined)
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1)
      fetchData(1, pageSize)
      fetchSummary()
    }, 300)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, statusFilter, severityFilter, datacenterFilter, searchText])

  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchData(page, pageSize, true)
      fetchSummary(true)
    }, 10000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, page, pageSize, statusFilter, severityFilter, datacenterFilter, searchText])

  const handleResetFilters = () => {
    setStatusFilter(undefined)
    setSeverityFilter(undefined)
    setDatacenterFilter(undefined)
    setSearchText('')
    setPage(1)
  }

  const handleClearHistory = async (olderThanDays?: number, label = '当前筛选') => {
    setClearing(true)
    let previewTotal = total
    let previewProtected = 0
    let previewDeletable = total
    try {
      const preview = await getAlertHistorySummary({
        ...buildFilterParams(),
        older_than_days: olderThanDays,
        limit: 1,
      })
      previewTotal = preview.total || 0
      previewProtected = preview.protected_total || 0
      previewDeletable = preview.deletable_total ?? Math.max(0, previewTotal - previewProtected)
    } catch (error: any) {
      setClearing(false)
      message.error(error?.response?.data?.detail || '获取清理数量失败')
      return
    }
    setClearing(false)

    Modal.confirm({
      title: `确认清除${label}的告警历史？`,
      content: (
        <Space direction="vertical" size={8}>
          <Typography.Text>
            当前选中共 {previewTotal} 条，预计清除 {previewDeletable} 条。
          </Typography.Text>
          <Typography.Text>
            系统默认跳过“触发中 / 已确认 / 暂停复查”的活动告警{previewProtected ? `，本次将跳过 ${previewProtected} 条。` : '。'}
          </Typography.Text>
          <Typography.Text type="secondary">
            如果要缩小范围，请先设置状态、级别或搜索条件后再清除；这些快捷按钮会跟随当前筛选条件。
          </Typography.Text>
        </Space>
      ),
      okText: '确认清除',
      cancelText: '取消',
      okButtonProps: { danger: true, loading: clearing },
      onOk: async () => {
        setClearing(true)
        try {
          const result = await clearAlertHistory({
            ...buildFilterParams(),
            older_than_days: olderThanDays,
            include_active: false,
            confirm_text: 'CLEAR',
            actor_username: currentUser?.username,
          })
          message.success(`已清除 ${result.deleted_count} 条${result.protected_skipped ? `，跳过活动告警 ${result.protected_skipped} 条` : ''}`)
          setPage(1)
          await Promise.all([fetchData(1, pageSize), fetchSummary()])
        } catch (error: any) {
          message.error(error?.response?.data?.detail || '清除失败')
        } finally {
          setClearing(false)
        }
      },
    })
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

  const handleQuickSilence = (record: AlertHistoryItem, durationHours: number) => {
    const durationLabel = durationHours >= 24 && durationHours % 24 === 0
      ? `${durationHours / 24}天`
      : `${durationHours}小时`
    Modal.confirm({
      title: `屏蔽此告警 ${durationLabel}？`,
      content: `仅屏蔽当前设备、当前规则${record.alert_target_name || record.alert_target_key ? '及当前对象' : ''}，到期后自动恢复检查。`,
      okText: '确认屏蔽',
      cancelText: '取消',
      onOk: async () => {
        setQuickSilenceLoadingId(record.id)
        try {
          await quickSilenceAlert(record.id, durationHours, currentUser?.username)
          message.success(`已屏蔽 ${durationLabel}，到期后自动恢复检查`)
          await Promise.all([fetchData(), fetchSummary()])
        } catch (error: any) {
          message.error(error?.response?.data?.detail || '屏蔽失败')
        } finally {
          setQuickSilenceLoadingId(null)
        }
      },
    })
  }

  return (
    <div className="modern-page">
      <Row gutter={[16, 16]}>
        <Col span={8}>
          <Card bodyStyle={metricCardBodyStyle}>
            <Statistic title={isAuditMode ? '日志记录数' : '正在触发'} value={summary?.total ?? total} />
          </Card>
        </Col>
        <Col span={8}>
          <Card bodyStyle={metricCardBodyStyle}>
            <Statistic title={isAuditMode ? '已忽略' : 'P0告警'} value={isAuditMode ? (statusCounts.ignored || 0) : (severityCounts.P0 || 0)} />
          </Card>
        </Col>
        <Col span={8}>
          <Card bodyStyle={metricCardBodyStyle}>
            <Statistic title={isAuditMode ? 'P3 Trap日志' : 'P1/P2告警'} value={isAuditMode ? (severityCounts.P3 || 0) : ((severityCounts.P1 || 0) + (severityCounts.P2 || 0))} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} align="top">
        <Col span={8}>
          <Card
            title="按机房统计"
            loading={summaryLoading}
            bodyStyle={summaryCardBodyStyle}
            extra={
              <Segmented
                size="small"
                value={datacenterChartType}
                onChange={(value) => setDatacenterChartType(value as 'pie' | 'bar')}
                options={[
                  { label: '饼图', value: 'pie' },
                  { label: '柱状图', value: 'bar' },
                ]}
              />
            }
          >
            {datacenterChartType === 'pie' ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={datacenterChartData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={96} paddingAngle={3}>
                    {datacenterChartData.map((item, index) => (
                      <Cell key={item.name} fill={chartColors[index % chartColors.length]} />
                    ))}
                  </Pie>
                  <RechartsTooltip contentStyle={chartTooltipStyle} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={datacenterChartData}
                  layout="vertical"
                  margin={{ top: 8, right: 22, left: 18, bottom: 8 }}
                >
                  <CartesianGrid stroke={chartGrid} horizontal={false} />
                  <XAxis type="number" tick={{ fill: chartAxis, fontSize: 11 }} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={76}
                    tick={{ fill: chartAxis, fontSize: 11 }}
                    tickFormatter={(value) => String(value).length > 5 ? `${String(value).slice(0, 5)}…` : String(value)}
                  />
                  <RechartsTooltip contentStyle={chartTooltipStyle} />
                  <Bar dataKey="value" name="告警数" radius={[0, 8, 8, 0]}>
                    {datacenterChartData.map((item, index) => (
                      <Cell key={item.name} fill={chartColors[index % chartColors.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card title="按日期统计" loading={summaryLoading} bodyStyle={summaryCardBodyStyle}>
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ flex: 1, minHeight: 0 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dayChartData} margin={{ top: 12, right: 18, left: -12, bottom: 4 }}>
                    <defs>
                      <linearGradient id="alertDayTrend" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#2563eb" stopOpacity={0.30} />
                        <stop offset="95%" stopColor="#2563eb" stopOpacity={0.03} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={chartGrid} vertical={false} />
                    <XAxis dataKey="label" minTickGap={18} tick={{ fill: chartAxis, fontSize: 11 }} />
                    <YAxis tick={{ fill: chartAxis, fontSize: 11 }} />
                    <RechartsTooltip contentStyle={chartTooltipStyle} labelFormatter={(_, payload) => payload?.[0]?.payload?.day || ''} />
                    <Area type="monotone" dataKey="value" name="告警数" stroke="#2563eb" strokeWidth={2.4} fill="url(#alertDayTrend)" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(dayChartData.length || 1, 18)}, minmax(8px, 1fr))`, gap: 4 }}>
                {dayChartData.slice(-18).map((item) => {
                  const opacity = 0.18 + (item.value / maxDayCount) * 0.72
                  return (
                    <Tooltip key={item.day} title={`${item.day}：${item.value} 条`}>
                      <div
                        style={{
                          height: 18,
                          borderRadius: 5,
                          background: `rgba(37, 99, 235, ${opacity})`,
                        }}
                      />
                    </Tooltip>
                  )
                })}
              </div>
            </div>
          </Card>
        </Col>
        <Col span={8}>
          <Card title="按设备统计 Top 10" loading={summaryLoading} bodyStyle={summaryCardBodyStyle}>
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {(summary?.devices || []).map((record, index) => {
                const percent = Math.max(4, Math.round((record.count / maxDeviceCount) * 100))
                return (
                  <Tooltip key={record.device_id} title={`${record.device_name}：${record.count} 条`}>
                    <div
                      style={{
                        padding: '10px 12px',
                        borderRadius: 12,
                        background: index === 0 ? 'rgba(37, 99, 235, 0.10)' : themeToken.colorFillQuaternary,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                        <div style={{ minWidth: 0 }}>
                          <Typography.Text style={compactTextStyle}>{record.device_name}</Typography.Text>
                          <span style={{ color: themeToken.colorTextSecondary, fontSize: 12 }}>{record.device_ip || '-'}</span>
                        </div>
                        <Typography.Text strong style={{ color: chartColors[index % chartColors.length] }}>{record.count}</Typography.Text>
                      </div>
                      <div style={{ height: 5, borderRadius: 999, marginTop: 8, background: themeToken.colorFillSecondary, overflow: 'hidden' }}>
                        <div
                          style={{
                            height: '100%',
                            width: `${percent}%`,
                            borderRadius: 999,
                            background: `linear-gradient(90deg, ${chartColors[index % chartColors.length]}, rgba(37, 99, 235, 0.35))`,
                          }}
                        />
                      </div>
                    </div>
                  </Tooltip>
                )
              })}
            </Space>
          </Card>
        </Col>
      </Row>

      <Card
        title={tableTitle}
        extra={
          <Space>
            {isAuditMode ? (
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
            ) : null}
            {isAuditMode ? (
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
            ) : null}
            <Select
              allowClear
              showSearch
              placeholder="机房"
              style={{ width: 160 }}
              value={datacenterFilter}
              onChange={setDatacenterFilter}
              optionFilterProp="label"
              options={[
                ...datacenters.map((item) => ({
                  value: item.name,
                  label: item.name,
                })),
                { value: '__none__', label: '未设置机房' },
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
            {canModify && isAuditMode ? (
              <Space.Compact>
                {cleanupOptions.map((option) => (
                  <Tooltip
                    key={option.label}
                    title={`清除当前筛选下${option.days ? `${option.days}天前` : '所有'}的历史告警，默认跳过活动告警`}
                  >
                    <Button
                      danger
                      icon={option.days === 30 ? <DeleteOutlined /> : undefined}
                      loading={clearing}
                      onClick={() => handleClearHistory(option.days, option.days ? `${option.label}` : '所有')}
                    >
                      清{option.label}
                    </Button>
                  </Tooltip>
                ))}
              </Space.Compact>
            ) : null}
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
                          <span style={operationValueStyle}>{getOperationRecordType(record)}</span>
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
                  ? <Tag color={getOperationRecordType(record).includes('登录失败') ? 'orange' : 'default'}>{getOperationRecordType(record)}</Tag>
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
              width: 245,
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
                  {!isOperationRecord(record) && record.status !== 'resolved' && record.status !== 'ignored' && (
                    <Dropdown
                      trigger={['click']}
                      menu={{
                        items: [
                          { key: '6', label: '屏蔽6小时' },
                          { key: '12', label: '屏蔽12小时' },
                          { key: '24', label: '屏蔽24小时' },
                          { key: '72', label: '屏蔽3天' },
                          { key: '168', label: '屏蔽7天' },
                          { key: '360', label: '屏蔽15天' },
                        ],
                        onClick: ({ key }) => handleQuickSilence(record, Number(key)),
                      }}
                    >
                      <Button
                        size="small"
                        danger
                        icon={<StopOutlined />}
                        loading={quickSilenceLoadingId === record.id}
                      >
                        屏蔽
                      </Button>
                    </Dropdown>
                  )}
                </Space>
              ),
            } : null,
          ].filter(Boolean) as any}
        />
      </Card>
    </div>
  )
}

export default AlertHistory
