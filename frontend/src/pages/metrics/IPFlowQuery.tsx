import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Empty, Input, Select, Space, Spin, Table, Tag, Typography, message, theme } from 'antd'
import { LineChartOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RechartsTooltip, XAxis, YAxis } from 'recharts'
import { useSearchParams } from 'react-router-dom'
import {
  getInterfaceIpSeries,
  getIpFlowTraffic,
  getSflowAgents,
  getSflowInterfaces,
  type InterfaceTopIpItem,
  type IpFlowPoint,
  type SflowAgentOption,
  type SflowInterfaceOption,
} from '../../api/metrics'

const { Text } = Typography

type ChartPoint = {
  timestamp: number
  in_bps?: number | null
  out_bps?: number | null
}

type AnalyzerChartPoint = {
  timestamp: number
  [ip: string]: number
}

type DisplayScale = {
  divisor: number
  suffix: string
}

const RANGE_OPTIONS = [
  { value: '-10m', label: '过去10分钟', interval: '10s' },
  { value: '-30m', label: '过去30分钟', interval: '30s' },
  { value: '-1h', label: '过去1小时', interval: '1m' },
  { value: '-6h', label: '过去6小时', interval: '5m' },
  { value: '-24h', label: '过去24小时', interval: '5m' },
  { value: '-3d', label: '过去3天', interval: '5m' },
  { value: '-7d', label: '过去7天', interval: '5m' },
]

const REFRESH_OPTIONS = [
  { value: 10, label: '每10秒刷新' },
  { value: 30, label: '每30秒刷新' },
  { value: 60, label: '每60秒刷新' },
]

const isIpLike = (value: string) =>
  /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(value.trim())

const getDisplayScale = (value?: number | null): DisplayScale => {
  const safeValue = Math.abs(value ?? 0)
  if (safeValue >= 1_000_000_000) return { divisor: 1_000_000_000, suffix: 'Gbps' }
  if (safeValue >= 1_000_000) return { divisor: 1_000_000, suffix: 'Mbps' }
  if (safeValue >= 1_000) return { divisor: 1_000, suffix: 'Kbps' }
  return { divisor: 1, suffix: 'bps' }
}

const formatBps = (value?: number | null, scale?: DisplayScale) => {
  if (value === null || value === undefined) return '-'
  const resolvedScale = scale || getDisplayScale(value)
  const scaled = value / resolvedScale.divisor
  const precision = scaled >= 100 || resolvedScale.suffix === 'bps' ? 0 : scaled >= 10 ? 1 : 2
  return `${scaled.toFixed(precision)} ${resolvedScale.suffix}`
}

const niceCeil = (value: number) => {
  if (value <= 0) return 1
  const exponent = Math.floor(Math.log10(value))
  const base = 10 ** exponent
  const normalized = value / base
  if (normalized <= 1) return base
  if (normalized <= 2) return 2 * base
  if (normalized <= 5) return 5 * base
  return 10 * base
}

const buildTicks = (start: number, end: number, count: number) => {
  if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) return []
  const steps = Math.max(count - 1, 1)
  return Array.from({ length: count }, (_, index) => Math.round(start + ((end - start) * index) / steps))
}

const getXAxisTickCount = (rangeValue: string) => {
  if (rangeValue === '-10m') return 7
  if (rangeValue === '-30m') return 8
  if (rangeValue === '-1h') return 8
  if (rangeValue === '-6h') return 9
  return 8
}

const formatXAxisTick = (timestamp: number, rangeValue: string) => {
  const time = dayjs(timestamp)
  if (rangeValue === '-10m') return time.format('HH:mm:ss')
  if (rangeValue === '-30m' || rangeValue === '-1h' || rangeValue === '-6h' || rangeValue === '-24h') return time.format('HH:mm')
  return time.format('MM-DD HH:mm')
}

const normalizePoint = (item: IpFlowPoint): ChartPoint => {
  const rawTime = item._time || item.time || new Date().toISOString()
  const normalizedTime =
    typeof rawTime === 'string' && rawTime.includes('T') && !/[zZ]|[+\-]\d{2}:\d{2}$/.test(rawTime)
      ? `${rawTime}Z`
      : rawTime
  return {
    timestamp: dayjs(normalizedTime).valueOf(),
    in_bps: item.in_bps ?? null,
    out_bps: item.out_bps ?? null,
  }
}

const normalizeTimestamp = (rawTime?: string) => {
  const value = rawTime || new Date().toISOString()
  const normalizedTime =
    typeof value === 'string' && value.includes('T') && !/[zZ]|[+\-]\d{2}:\d{2}$/.test(value)
      ? `${value}Z`
      : value
  return dayjs(normalizedTime).valueOf()
}

const getAnalyzerDataKey = (ip: string) => `ip_${ip.replace(/[^a-zA-Z0-9_]/g, '_')}`

const getSeriesStats = (values: Array<number | null | undefined>) => {
  const numericValues = values.filter((value): value is number => typeof value === 'number')
  return {
    min: numericValues.length ? Math.min(...numericValues) : null,
    max: numericValues.length ? Math.max(...numericValues) : null,
  }
}

export default function IPFlowQuery() {
  const { token } = theme.useToken()
  const [searchParams, setSearchParams] = useSearchParams()
  const [ipInput, setIpInput] = useState(searchParams.get('ip') || '')
  const [currentIp, setCurrentIp] = useState(searchParams.get('ip') || '')
  const [rangeValue, setRangeValue] = useState(searchParams.get('range') || '-30m')
  const [refreshSeconds, setRefreshSeconds] = useState(10)
  const [loading, setLoading] = useState(false)
  const [points, setPoints] = useState<ChartPoint[]>([])
  const [customers, setCustomers] = useState<Array<{ id: number; name: string }>>([])
  const [flowSource, setFlowSource] = useState<string>()
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string>()
  const [analyzerDeviceIp, setAnalyzerDeviceIp] = useState(searchParams.get('agent_ip') || '')
  const [analyzerInterfaceIndex, setAnalyzerInterfaceIndex] = useState<number | null>(
    searchParams.get('interface_index') ? Number(searchParams.get('interface_index')) : null
  )
  const [analyzerLoading, setAnalyzerLoading] = useState(false)
  const [sflowAgentsLoading, setSflowAgentsLoading] = useState(false)
  const [sflowAgents, setSflowAgents] = useState<SflowAgentOption[]>([])
  const [sflowInterfacesLoading, setSflowInterfacesLoading] = useState(false)
  const [sflowInterfaces, setSflowInterfaces] = useState<SflowInterfaceOption[]>([])
  const [topIps, setTopIps] = useState<InterfaceTopIpItem[]>([])
  const [analyzerSeries, setAnalyzerSeries] = useState<AnalyzerChartPoint[]>([])
  const [analyzerFilterIp, setAnalyzerFilterIp] = useState('')
  const [selectedRank, setSelectedRank] = useState<number | null>(null)
  const [visibleTopIps, setVisibleTopIps] = useState<string[]>([])

  const selectedRange = RANGE_OPTIONS.find((item) => item.value === rangeValue) || RANGE_OPTIONS[1]

  const selectedSflowAgent = useMemo(
    () => sflowAgents.find((item) => item.agent_ip === analyzerDeviceIp),
    [sflowAgents, analyzerDeviceIp]
  )

  const selectedSflowInterface = useMemo(
    () => sflowInterfaces.find((item) => Number(item.interface_index) === Number(analyzerInterfaceIndex)),
    [sflowInterfaces, analyzerInterfaceIndex]
  )

  const fetchTraffic = async (ipValue = currentIp, range = rangeValue, silent = false) => {
    const trimmedIp = ipValue.trim()
    if (!trimmedIp) {
      setPoints([])
      setCustomers([])
      return
    }
    if (!isIpLike(trimmedIp)) {
      if (!silent) message.warning('请输入正确的 IPv4 地址')
      return
    }

    setLoading(!silent)
    try {
      const selected = RANGE_OPTIONS.find((item) => item.value === range) || RANGE_OPTIONS[1]
      const result = await getIpFlowTraffic({
        ip: trimmedIp,
        range: selected.value,
        interval: selected.interval,
      })
      setCurrentIp(result.ip)
      setIpInput(result.ip)
      setCustomers(result.customers || [])
      setFlowSource(result.source)
      setPoints((result.data || []).map(normalizePoint).filter((item) => Number.isFinite(item.timestamp)))
      setLastUpdatedAt(dayjs().format('YYYY-MM-DD HH:mm:ss'))
      setSearchParams({ ip: result.ip, range: selected.value })
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取IP流量失败')
      setPoints([])
      setCustomers([])
      setFlowSource(undefined)
    } finally {
      setLoading(false)
    }
  }

  const fetchSflowAgents = async (silent = false) => {
    setSflowAgentsLoading(true)
    try {
      const result = await getSflowAgents({ range: selectedRange.value })
      const items = result.items || []
      setSflowAgents(items)
      if (!analyzerDeviceIp && items.length) {
        setAnalyzerDeviceIp(items[0].agent_ip)
        void fetchSflowInterfaces(items[0].agent_ip, true)
      }
      if (!silent && !items.length) {
        message.info('当前时间范围内暂未发现 sFlow Agent 数据')
      }
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取sFlow Agent失败')
      setSflowAgents([])
    } finally {
      setSflowAgentsLoading(false)
    }
  }

  const fetchSflowInterfaces = async (agentIpValue = analyzerDeviceIp, silent = false) => {
    const trimmedIp = agentIpValue.trim()
    if (!trimmedIp) {
      setSflowInterfaces([])
      return
    }
    if (!isIpLike(trimmedIp)) {
      if (!silent) message.warning('请输入正确的设备 IP')
      return
    }
    setSflowInterfacesLoading(true)
    try {
      const result = await getSflowInterfaces({
        agent_ip: trimmedIp,
        range: selectedRange.value,
      })
      const items = result.items || []
      setAnalyzerDeviceIp(result.agent_ip)
      setSflowInterfaces(items)
      setAnalyzerInterfaceIndex((currentValue) => {
        if (currentValue && items.some((item) => Number(item.interface_index) === Number(currentValue))) {
          return currentValue
        }
        const firstIndex = items.length ? Number(items[0].interface_index) : null
        return firstIndex && Number.isFinite(firstIndex) ? firstIndex : null
      })
      if (!silent && !items.length) {
        message.info('当前时间范围内没有查询到该设备的 sFlow 接口数据')
      }
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取sFlow接口失败')
      setSflowInterfaces([])
    } finally {
      setSflowInterfacesLoading(false)
    }
  }

  const fetchInterfaceAnalysis = async (ipValue = analyzerFilterIp) => {
    const trimmedIp = analyzerDeviceIp.trim()
    if (!isIpLike(trimmedIp)) {
      message.warning('请输入正确的设备 IP')
      return
    }
    if (!analyzerInterfaceIndex) {
      message.warning('请输入接口 ifIndex')
      return
    }
    const targetIp = ipValue.trim()
    if (targetIp && !isIpLike(targetIp)) {
      message.warning('请输入正确的分析 IP')
      return
    }
    setAnalyzerLoading(true)
    try {
      const result = await getInterfaceIpSeries({
        agent_ip: trimmedIp,
        interface_index: analyzerInterfaceIndex,
        range: selectedRange.value,
        interval: selectedRange.interval,
        limit: 20,
        ip: targetIp || undefined,
      })
      setTopIps(result.top_ips || [])
      setVisibleTopIps([])
      setSelectedRank(result.selected_rank ?? null)
      const pointMap = new Map<number, AnalyzerChartPoint>()
      for (const item of result.series || []) {
        const timestamp = normalizeTimestamp(item._time || item.time)
        const point = pointMap.get(timestamp) || { timestamp }
        point[getAnalyzerDataKey(item.ip)] = Number(item._value ?? item.value ?? 0)
        pointMap.set(timestamp, point)
      }
      setAnalyzerSeries(Array.from(pointMap.values()).sort((a, b) => a.timestamp - b.timestamp))
      setSearchParams({
        ...(currentIp ? { ip: currentIp } : {}),
        range: selectedRange.value,
        agent_ip: result.agent_ip,
        interface_index: String(result.interface_index),
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取接口IP分析失败')
      setTopIps([])
      setAnalyzerSeries([])
      setSelectedRank(null)
    } finally {
      setAnalyzerLoading(false)
    }
  }

  useEffect(() => {
    if (!currentIp) return
    const timer = window.setInterval(() => {
      fetchTraffic(currentIp, rangeValue, true)
    }, refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [currentIp, rangeValue, refreshSeconds])

  useEffect(() => {
    const initialIp = searchParams.get('ip')
    if (initialIp) {
      fetchTraffic(initialIp, rangeValue)
    }
    void fetchSflowAgents(true)
    const initialAgentIp = searchParams.get('agent_ip')
    if (initialAgentIp) {
      void fetchSflowInterfaces(initialAgentIp, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (analyzerDeviceIp && isIpLike(analyzerDeviceIp)) {
      void fetchSflowInterfaces(analyzerDeviceIp, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeValue])

  const chartMeta = useMemo(() => {
    const validPoints = points.filter((point) => typeof point.in_bps === 'number' || typeof point.out_bps === 'number')
    const values = validPoints.flatMap((point) => [point.in_bps, point.out_bps]).filter((value): value is number => typeof value === 'number')
    const maxValue = Math.max(...values, 0)
    const yMax = niceCeil(maxValue * 1.12)
    const yTicks = Array.from({ length: 5 }, (_, index) => (yMax * index) / 4)
    const scale = getDisplayScale(yMax)
    const xStart = validPoints.length ? Math.min(...validPoints.map((item) => item.timestamp)) : Date.now() - 10 * 60 * 1000
    const xEnd = validPoints.length ? Math.max(...validPoints.map((item) => item.timestamp)) : Date.now()
    const adjustedStart = xStart === xEnd ? xStart - 30_000 : xStart
    const adjustedEnd = xStart === xEnd ? xEnd + 30_000 : xEnd
    const xTicks = buildTicks(adjustedStart, adjustedEnd, getXAxisTickCount(rangeValue))
    const inStats = getSeriesStats(validPoints.map((item) => item.in_bps))
    const outStats = getSeriesStats(validPoints.map((item) => item.out_bps))
    return {
      data: validPoints,
      xDomain: [adjustedStart, adjustedEnd] as [number, number],
      xTicks,
      yDomain: [0, yMax] as [number, number],
      yTicks,
      scale,
      maxValue,
      lastIn: validPoints.length ? validPoints[validPoints.length - 1].in_bps : null,
      lastOut: validPoints.length ? validPoints[validPoints.length - 1].out_bps : null,
      minIn: inStats.min,
      minOut: outStats.min,
      maxIn: inStats.max,
      maxOut: outStats.max,
    }
  }, [points, rangeValue])

  const analyzerChartMeta = useMemo(() => {
    const chartIps = visibleTopIps.length ? topIps.filter((item) => visibleTopIps.includes(item.ip)) : topIps
    const values = analyzerSeries.flatMap((point) =>
      chartIps.map((item) => Number(point[getAnalyzerDataKey(item.ip)] || 0))
    )
    const maxValue = Math.max(...values, 0)
    const yMax = niceCeil(maxValue * 1.12)
    const yTicks = Array.from({ length: 5 }, (_, index) => (yMax * index) / 4)
    const scale = getDisplayScale(yMax)
    const xStart = analyzerSeries.length ? Math.min(...analyzerSeries.map((item) => item.timestamp)) : Date.now() - 10 * 60 * 1000
    const xEnd = analyzerSeries.length ? Math.max(...analyzerSeries.map((item) => item.timestamp)) : Date.now()
    const adjustedStart = xStart === xEnd ? xStart - 30_000 : xStart
    const adjustedEnd = xStart === xEnd ? xEnd + 30_000 : xEnd
    return {
      data: analyzerSeries,
      yDomain: [0, yMax] as [number, number],
      yTicks,
      xDomain: [adjustedStart, adjustedEnd] as [number, number],
      xTicks: buildTicks(adjustedStart, adjustedEnd, getXAxisTickCount(rangeValue)),
      scale,
      chartIps,
    }
  }, [analyzerSeries, topIps, visibleTopIps, rangeValue])

  const inboundTopIps = useMemo(
    () => [...topIps]
      .filter((item) => Number(item.in_bps || 0) > 0)
      .sort((a, b) => Number(b.in_bps || 0) - Number(a.in_bps || 0))
      .slice(0, 10)
      .map((item, index) => ({ ...item, direction_rank: index + 1 })),
    [topIps]
  )

  const outboundTopIps = useMemo(
    () => [...topIps]
      .filter((item) => Number(item.out_bps || 0) > 0)
      .sort((a, b) => Number(b.out_bps || 0) - Number(a.out_bps || 0))
      .slice(0, 10)
      .map((item, index) => ({ ...item, direction_rank: index + 1 })),
    [topIps]
  )

  const lineColors = ['#52c41a', '#f4d000', '#1677ff', '#fa8c16', '#722ed1', '#13c2c2', '#eb2f96', '#a0d911', '#fa541c', '#2f54eb']

  const getOperatorColor = (operator?: string | null) => {
    const value = operator || ''
    if (value.includes('电信') || value.toLowerCase().includes('ctcc')) return 'blue'
    if (value.includes('联通') || value.toLowerCase().includes('cucc')) return 'magenta'
    if (value.includes('移动') || value.toLowerCase().includes('cmcc')) return 'green'
    if (value.toLowerCase().includes('bgp')) return 'purple'
    return 'default'
  }

  const renderIpTrafficChart = () => (
    <Spin spinning={loading}>
      {chartMeta.data.length ? (
        <>
          <div
            style={{
              width: '100%',
              height: 360,
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: 8,
              padding: '16px 14px 6px 8px',
              background: token.colorBgContainer,
            }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartMeta.data} margin={{ top: 12, right: 24, left: 20, bottom: 18 }}>
                <CartesianGrid stroke={token.colorBorderSecondary} horizontal vertical />
                <XAxis
                  type="number"
                  dataKey="timestamp"
                  domain={chartMeta.xDomain}
                  ticks={chartMeta.xTicks}
                  tickFormatter={(value) => formatXAxisTick(Number(value), rangeValue)}
                  tick={{ fill: token.colorTextSecondary, fontSize: 12 }}
                  axisLine={{ stroke: token.colorBorder }}
                  tickLine={{ stroke: token.colorBorder }}
                  allowDataOverflow
                />
                <YAxis
                  type="number"
                  domain={chartMeta.yDomain}
                  ticks={chartMeta.yTicks}
                  tickFormatter={(value) => formatBps(Number(value), chartMeta.scale)}
                  tick={{ fill: token.colorTextSecondary, fontSize: 12 }}
                  axisLine={{ stroke: token.colorBorder }}
                  tickLine={{ stroke: token.colorBorder }}
                  width={82}
                />
                <RechartsTooltip
                  labelFormatter={(value) => dayjs(Number(value)).format('YYYY-MM-DD HH:mm:ss')}
                  formatter={(value: any, name: any) => [formatBps(Number(value)), name]}
                />
                <Line
                  type="linear"
                  dataKey="in_bps"
                  name="IP入向流量"
                  stroke="#70d34f"
                  strokeWidth={1.5}
                  dot={{ r: 2, strokeWidth: 1.4, fill: '#fff' }}
                  activeDot={{ r: 4 }}
                  connectNulls={false}
                />
                <Line
                  type="linear"
                  dataKey="out_bps"
                  name="IP出向流量"
                  stroke="#f4d000"
                  strokeWidth={1.5}
                  dot={{ r: 2, strokeWidth: 1.4, fill: '#fff' }}
                  activeDot={{ r: 4 }}
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 110px 110px 110px', gap: 8, marginTop: 12, paddingInline: 6, fontSize: 13 }}>
            <Text type="secondary">Name</Text>
            <Text strong style={{ textAlign: 'right' }}>Last</Text>
            <Text strong style={{ textAlign: 'right' }}>Min</Text>
            <Text strong style={{ textAlign: 'right' }}>Max</Text>
            <Space size={8}><span style={{ width: 14, height: 3, background: '#70d34f', display: 'inline-block' }} />IP入向流量</Space>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.lastIn)}</Text>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.minIn)}</Text>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.maxIn)}</Text>
            <Space size={8}><span style={{ width: 14, height: 3, background: '#f4d000', display: 'inline-block' }} />IP出向流量</Space>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.lastOut)}</Text>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.minOut)}</Text>
            <Text style={{ textAlign: 'right' }}>{formatBps(chartMeta.maxOut)}</Text>
          </div>
        </>
      ) : (
        <Empty description={currentIp ? '当前时间范围内暂无IP流量数据' : '请输入IP地址查询流量'} style={{ padding: '72px 0' }} />
      )}
    </Spin>
  )

  const renderTopIpTable = (
    title: string,
    dataSource: InterfaceTopIpItem[],
    valueKey: 'in_bps' | 'out_bps',
    color: string
  ) => (
    <Table
      size="small"
      rowKey={(record) => `${valueKey}-${record.ip}`}
      dataSource={dataSource}
      pagination={false}
      scroll={{ y: 168 }}
      title={() => (
        <Space size={8} wrap>
          <span style={{ width: 10, height: 10, borderRadius: 10, background: color, display: 'inline-block' }} />
          <Text strong>{title}</Text>
          <Text type="secondary">点击 IP 可筛选折线</Text>
        </Space>
      )}
      onRow={(record) => ({
        onClick: () => {
          setVisibleTopIps((prev) =>
            prev.includes(record.ip)
              ? prev.filter((item) => item !== record.ip)
              : [...prev, record.ip]
          )
        },
        style: {
          cursor: 'pointer',
          background: visibleTopIps.includes(record.ip) ? token.colorPrimaryBg : undefined,
        },
      })}
      columns={[
        { title: '排名', key: 'rank', width: 54, render: (_: unknown, record: InterfaceTopIpItem) => record.direction_rank || '-' },
        {
          title: 'IP地址',
          dataIndex: 'ip',
          key: 'ip',
          width: 134,
          render: (value: string) => (
            <Text strong={visibleTopIps.includes(value)} style={{ color: visibleTopIps.includes(value) ? token.colorPrimary : undefined }}>
              {value}
            </Text>
          ),
        },
        { title: title.replace('Top10', ''), dataIndex: valueKey, key: valueKey, align: 'right', render: (value: number) => <Text strong>{formatBps(value)}</Text> },
      ]}
    />
  )

  return (
    <div style={{ padding: 24, background: token.colorBgLayout, minHeight: '100%' }}>
      <Card
        style={{ marginBottom: 16, borderRadius: 12 }}
        bodyStyle={{ padding: 18 }}
        title={
          <Space size={8}>
            <LineChartOutlined style={{ color: '#52c41a' }} />
            <span>单 IP 流量分析</span>
            {currentIp && <Tag color="blue">{currentIp}</Tag>}
          </Space>
        }
        extra={
          <Space size={8} wrap>
            <Tag>单位 {chartMeta.scale.suffix}</Tag>
            <Tag>粒度 {selectedRange.interval}</Tag>
            {flowSource === 'sflow_interface' && <Tag color="green">sFlow接口样本</Tag>}
            {customers.length ? customers.map((item) => <Tag color="blue" key={item.id}>{item.name}</Tag>) : <Tag>未匹配客户</Tag>}
          </Space>
        }
      >
        <Space wrap size={10} align="center" style={{ marginBottom: 14 }}>
          <Input
            value={ipInput}
            onChange={(event) => setIpInput(event.target.value)}
            onPressEnter={() => fetchTraffic(ipInput, rangeValue)}
            placeholder="输入要分析的 IP，例如 119.167.167.59"
            prefix={<SearchOutlined />}
            style={{ width: 320 }}
          />
          <Select
            value={rangeValue}
            options={RANGE_OPTIONS.map(({ value, label }) => ({ value, label }))}
            style={{ width: 170 }}
            onChange={(value) => {
              setRangeValue(value)
              if (currentIp) fetchTraffic(currentIp, value)
            }}
          />
          <Select
            value={refreshSeconds}
            options={REFRESH_OPTIONS}
            style={{ width: 150 }}
            onChange={setRefreshSeconds}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={() => fetchTraffic(ipInput, rangeValue)}>
            查询IP流量
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => fetchTraffic(currentIp || ipInput, rangeValue)} />
          {lastUpdatedAt && <Text type="secondary">更新时间 {lastUpdatedAt}</Text>}
        </Space>
        {renderIpTrafficChart()}
      </Card>

      <Card
        style={{ borderRadius: 12 }}
        bodyStyle={{ padding: 18 }}
        title="sFlow 接口 Top10 IP 分析"
        extra={
          <Space size={8}>
            <Tag>按平均带宽排序</Tag>
            <Button size="small" icon={<ReloadOutlined />} loading={sflowAgentsLoading} onClick={() => fetchSflowAgents()}>
              刷新Agent
            </Button>
          </Space>
        }
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 520px', gap: 14, marginBottom: 14, alignItems: 'start' }}>
          <Space wrap size={10} align="center">
            <Select
              showSearch
              allowClear
              value={analyzerDeviceIp || undefined}
              loading={sflowAgentsLoading}
              placeholder="选择 sFlow Agent / 设备"
              style={{ width: 360 }}
              optionFilterProp="label"
              onChange={(value) => {
                const nextIp = value || ''
                setAnalyzerDeviceIp(nextIp)
                setSflowInterfaces([])
                setAnalyzerInterfaceIndex(null)
                if (nextIp) void fetchSflowInterfaces(nextIp, true)
              }}
              options={sflowAgents.map((item) => {
                const device = item.device
                const dcName = device?.datacenter?.name
                const label = `${item.agent_ip}${device?.name ? ` / ${device.name}` : ''}${dcName ? ` / ${dcName}` : ''}`
                return {
                  value: item.agent_ip,
                  label,
                }
              })}
            />
            <Button icon={<ReloadOutlined />} loading={sflowInterfacesLoading} onClick={() => fetchSflowInterfaces(analyzerDeviceIp)}>
              读取接口
            </Button>
            <Select
              showSearch
              allowClear
              value={analyzerInterfaceIndex}
              onChange={(value) => setAnalyzerInterfaceIndex(typeof value === 'number' ? value : null)}
              placeholder="选择接口"
              loading={sflowInterfacesLoading}
              style={{ width: 260 }}
              optionFilterProp="label"
              options={sflowInterfaces.map((item) => {
                const numericValue = Number(item.interface_index)
                const value = Number.isFinite(numericValue) ? numericValue : -1
                return {
                  value,
                  label: `${item.label || `ifIndex ${item.interface_index}`} · ${formatBps(item.total_bps || 0)}`,
                  disabled: value < 1,
                }
              })}
            />
            <Input
              value={analyzerFilterIp}
              onChange={(event) => setAnalyzerFilterIp(event.target.value)}
              onPressEnter={() => fetchInterfaceAnalysis(analyzerFilterIp)}
              placeholder="指定IP，可留空看Top10"
              style={{ width: 220 }}
            />
            <Button type="primary" icon={<SearchOutlined />} onClick={() => fetchInterfaceAnalysis(analyzerFilterIp)}>
              分析Top10
            </Button>
            {selectedRank && <Tag color="blue">当前IP排名 {selectedRank}</Tag>}
          </Space>
          <div
            style={{
              border: `1px solid ${token.colorBorderSecondary}`,
              background: token.colorFillAlter,
              borderRadius: 10,
              padding: '10px 12px',
            }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 12, rowGap: 6, fontSize: 13 }}>
              <Space wrap size={6} style={{ gridColumn: '1 / -1' }}>
                <Tag color="processing">{selectedSflowAgent?.device?.datacenter?.name || '未知机房'}</Tag>
                {selectedSflowInterface?.circuit?.operator_name && (
                  <Tag color={getOperatorColor(selectedSflowInterface.circuit.operator_name)}>
                    {selectedSflowInterface.circuit.operator_name}
                  </Tag>
                )}
                {selectedSflowInterface?.circuit?.line_type && <Tag>{selectedSflowInterface.circuit.line_type}</Tag>}
                {selectedSflowInterface?.oper_status && <Tag color={selectedSflowInterface.oper_status === 'up' ? 'success' : 'error'}>{String(selectedSflowInterface.oper_status).toUpperCase()}</Tag>}
              </Space>
              <Text strong ellipsis title={selectedSflowInterface?.label || selectedSflowAgent?.device?.name || ''}>
                {selectedSflowInterface?.label || selectedSflowAgent?.device?.name || '请选择 Agent 和接口'}
              </Text>
              <Text type="secondary">最近：{formatBps(selectedSflowInterface?.total_bps || 0)}</Text>
              <Text type="secondary" ellipsis title={selectedSflowInterface?.alias || ''}>描述：{selectedSflowInterface?.alias || '暂无'}</Text>
              <Text type="secondary" ellipsis title={selectedSflowInterface?.circuit?.name || ''}>线路：{selectedSflowInterface?.circuit?.name || '未匹配'}</Text>
              <Text type="secondary">Agent：{analyzerDeviceIp || '-'}</Text>
              <Text type="secondary">接口：{selectedSflowAgent?.interface_count ?? sflowInterfaces.length} 个</Text>
            </div>
          </div>
        </div>

        <Spin spinning={analyzerLoading}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 520px', gap: 16, alignItems: 'stretch' }}>
            <div
              style={{
                height: 360,
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 8,
                padding: '14px 12px 4px 4px',
                background: token.colorBgContainer,
              }}
            >
              {analyzerChartMeta.data.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={analyzerChartMeta.data} margin={{ top: 12, right: 22, left: 20, bottom: 18 }}>
                    <CartesianGrid stroke={token.colorBorderSecondary} horizontal vertical />
                    <XAxis
                      type="number"
                      dataKey="timestamp"
                      domain={analyzerChartMeta.xDomain}
                      ticks={analyzerChartMeta.xTicks}
                      tickFormatter={(value) => formatXAxisTick(Number(value), rangeValue)}
                      tick={{ fill: token.colorTextSecondary, fontSize: 12 }}
                      axisLine={{ stroke: token.colorBorder }}
                      tickLine={{ stroke: token.colorBorder }}
                    />
                    <YAxis
                      type="number"
                      domain={analyzerChartMeta.yDomain}
                      ticks={analyzerChartMeta.yTicks}
                      tickFormatter={(value) => formatBps(Number(value), analyzerChartMeta.scale)}
                      tick={{ fill: token.colorTextSecondary, fontSize: 12 }}
                      axisLine={{ stroke: token.colorBorder }}
                      tickLine={{ stroke: token.colorBorder }}
                      width={82}
                    />
                    <RechartsTooltip
                      labelFormatter={(value) => dayjs(Number(value)).format('YYYY-MM-DD HH:mm:ss')}
                      formatter={(value: any, name: any) => [formatBps(Number(value)), name]}
                    />
                    {analyzerChartMeta.chartIps.map((item, index) => (
                      <Line
                        key={item.ip}
                        type="linear"
                        dataKey={getAnalyzerDataKey(item.ip)}
                        name={item.ip}
                        stroke={lineColors[index % lineColors.length]}
                        strokeWidth={1.4}
                        dot={false}
                        activeDot={{ r: 4 }}
                        connectNulls={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="选择 Agent 和接口后点击分析Top10" style={{ paddingTop: 105 }} />
              )}
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              <Space size={8} wrap>
                {visibleTopIps.length ? (
                  <Button size="small" onClick={() => setVisibleTopIps([])}>显示全部</Button>
                ) : null}
                <Text type="secondary">未选择时显示全部 Top IP 折线；选择后只保留被选 IP。</Text>
              </Space>
              {renderTopIpTable('入向 Top10', inboundTopIps.length ? inboundTopIps : topIps.slice(0, 10), 'in_bps', '#70d34f')}
              {renderTopIpTable('出向 Top10', outboundTopIps.length ? outboundTopIps : topIps.slice(0, 10), 'out_bps', '#f4d000')}
            </div>
          </div>
        </Spin>
      </Card>
    </div>
  )
}
