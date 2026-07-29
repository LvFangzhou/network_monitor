import {
  DeleteOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { AutoComplete, Button, Card, Empty, Select, Space, Spin, Tag, Typography, message } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  getMonitorInterfaceHistory,
  getMonitorDeviceInterfaces,
  MonitorDevice,
  MonitorDeviceSearchOption,
  MonitorHistoryPoint,
  MonitorInterface,
  searchMonitorDevices,
} from '../../api/metrics'

const { Text } = Typography

interface GrafanaTarget {
  key: string
  device: MonitorDevice
  interface: MonitorInterface
  metricKey: string
  metricLabel: string
  panelId: number
  timeFrom: string
  timeTo: string
  refreshInterval: string
  reloadKey: number
  trafficData?: TrafficChartPoint[]
  trafficLoading?: boolean
  trafficError?: string
}

type TrafficChartPoint = {
  timestamp: number
  timeLabel: string
  in_bps?: number | null
  out_bps?: number | null
}

type ChartScale = {
  divisor: number
  suffix: string
}

const MONITOR_OPTIONS = [
  { value: 'traffic', label: '接口流量', panelId: 1 },
  { value: 'utilization', label: '接口利用率', panelId: 2 },
  { value: 'discards', label: '接口丢弃包增量', panelId: 3 },
  { value: 'errors', label: '接口错误包增量', panelId: 4 },
  { value: 'pfc', label: 'PFC 收发包增量', panelId: 5 },
  { value: 'ecn', label: 'ECN 标记包增量', panelId: 6 },
]

const RANGE_OPTIONS = [
  { value: 'now-10m', label: '10分钟' },
  { value: 'now-1h', label: '1小时' },
  { value: 'now-6h', label: '6小时' },
  { value: 'now-24h', label: '24小时' },
  { value: 'now-7d', label: '7天' },
]

const getIntervalForGrafanaRange = (range: string) => {
  if (range === 'now-10m') return '10s'
  if (range === 'now-1h') return '30s'
  if (range === 'now-6h') return '1m'
  if (range === 'now-24h') return '5m'
  if (range === 'now-7d') return '1h'
  return '1m'
}

const grafanaRangeToApiRange = (range: string) => range.startsWith('now-') ? `-${range.slice(4)}` : '-6h'

const formatBps = (value?: number | null) => {
  const raw = Number(value || 0)
  const safe = Math.abs(raw)
  if (safe >= 1_000_000_000) return `${(raw / 1_000_000_000).toFixed(2)} Gbps`
  if (safe >= 1_000_000) return `${(raw / 1_000_000).toFixed(2)} Mbps`
  if (safe >= 1_000) return `${(raw / 1_000).toFixed(2)} Kbps`
  return `${raw.toFixed(0)} bps`
}

const normalizeChartData = (data: TrafficChartPoint[]) => data
  .filter((point) => Number.isFinite(point.timestamp))
  .map((point) => ({
    ...point,
    in_bps: Number.isFinite(Number(point.in_bps)) ? Number(point.in_bps) : 0,
    out_bps: Number.isFinite(Number(point.out_bps)) ? Number(point.out_bps) : 0,
  }))
  .sort((a, b) => a.timestamp - b.timestamp)

const getChartScale = (data: TrafficChartPoint[]): ChartScale => {
  const maxValue = Math.max(0, ...data.flatMap((point) => [Math.abs(Number(point.in_bps || 0)), Math.abs(Number(point.out_bps || 0))]))
  if (maxValue >= 1_000_000_000) return { divisor: 1_000_000_000, suffix: 'Gbps' }
  if (maxValue >= 1_000_000) return { divisor: 1_000_000, suffix: 'Mbps' }
  if (maxValue >= 1_000) return { divisor: 1_000, suffix: 'Kbps' }
  return { divisor: 1, suffix: 'bps' }
}

const formatScaledAxisValue = (value: number, scale: ChartScale) => {
  const scaled = Number(value) / scale.divisor
  const abs = Math.abs(scaled)
  const formatted = scaled.toFixed(abs >= 100 ? 0 : abs >= 10 ? 1 : 2).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
  return `${formatted}${scale.suffix}`
}

const formatTimeAxisValue = (value: number, data: TrafficChartPoint[]) => {
  const first = data[0]?.timestamp
  const last = data[data.length - 1]?.timestamp
  const span = Number.isFinite(first) && Number.isFinite(last) ? Math.abs(last - first) : 0
  if (span <= 60 * 60 * 1000) return dayjs(value).format('HH:mm:ss')
  if (span <= 24 * 60 * 60 * 1000) return dayjs(value).format('MM-DD HH:mm')
  return dayjs(value).format('MM-DD HH:mm')
}

const toTrafficChartPoint = (point: MonitorHistoryPoint): TrafficChartPoint | null => {
  const rawTime = point._time || point.time
  const timestamp = rawTime ? dayjs(rawTime).valueOf() : NaN
  if (!Number.isFinite(timestamp)) return null
  return {
    timestamp,
    timeLabel: dayjs(timestamp).format('MM-DD HH:mm'),
    in_bps: point.in_bps ?? null,
    out_bps: point.out_bps ?? null,
  }
}

const TrafficChart = ({ data }: { data: TrafficChartPoint[] }) => {
  const [left, setLeft] = useState<number | null>(null)
  const [right, setRight] = useState<number | null>(null)
  const [domain, setDomain] = useState<[number | 'dataMin', number | 'dataMax']>(['dataMin', 'dataMax'])
  const chartData = useMemo(() => normalizeChartData(data), [data])
  const scale = useMemo(() => getChartScale(chartData), [chartData])

  useEffect(() => {
    setDomain(['dataMin', 'dataMax'])
    setLeft(null)
    setRight(null)
  }, [data])

  const areaData = chartData.map((point) => ({
    ...point,
    in_area_bps: Math.max(Number(point.in_bps || 0), 0),
    out_line_bps: Math.max(Number(point.out_bps || 0), 0),
  }))

  const zoom = () => {
    if (left === null || right === null || left === right) {
      setLeft(null)
      setRight(null)
      return
    }
    setDomain(left < right ? [left, right] : [right, left])
    setLeft(null)
    setRight(null)
  }

  const resetZoom = () => {
    setDomain(['dataMin', 'dataMax'])
    setLeft(null)
    setRight(null)
  }

  if (!chartData.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前时间范围暂无流量数据" />

  return (
    <div style={{ height: '100%', position: 'relative' }}>
      {domain[0] !== 'dataMin' ? (
        <Button size="small" type="link" onClick={resetZoom} style={{ position: 'absolute', top: 2, right: 8, zIndex: 2 }}>
          还原
        </Button>
      ) : null}
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={areaData}
          margin={{ top: 18, right: 30, bottom: 28, left: 8 }}
          onMouseDown={(event) => setLeft(Number(event?.activeLabel) || null)}
          onMouseMove={(event) => left !== null ? setRight(Number(event?.activeLabel) || null) : undefined}
          onMouseUp={zoom}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#d9f7be" />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={domain}
            allowDataOverflow
            tickFormatter={(value) => formatTimeAxisValue(Number(value), chartData)}
            minTickGap={36}
            tick={{ fontSize: 12 }}
          />
          <YAxis tickFormatter={(value) => formatScaledAxisValue(Number(value), scale)} width={82} tick={{ fontSize: 12 }} />
          <RechartsTooltip
            labelFormatter={(value) => dayjs(Number(value)).format('YYYY-MM-DD HH:mm:ss')}
            formatter={(value: any, name: string, props: any) => [formatBps(Number(value)), props?.dataKey === 'in_area_bps' ? '入方向' : name || '出方向']}
          />
          <Area type="monotone" dataKey="in_area_bps" name="入方向" stroke="#35a800" fill="#35a800" fillOpacity={0.82} strokeWidth={1.2} dot={false} connectNulls={false} />
          <Area type="monotone" dataKey="out_line_bps" name="出方向" stroke="#5f7fd8" fill="transparent" strokeWidth={1.5} dot={false} connectNulls={false} />
          {left !== null && right !== null ? <ReferenceArea x1={left} x2={right} strokeOpacity={0.2} fill="#1677ff" fillOpacity={0.12} /> : null}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

const statusRank = (item: MonitorInterface) => (String(item.oper_status).toLowerCase() === 'up' ? 0 : 1)

const normalizeInterfaceName = (value?: string | null) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/fourhundredgigabitethernet|fourhundredgige|fhgigabitethernet|fhgige|400ge/g, '400ge')
  .replace(/hundredgigabitethernet|hundredgige|100ge/g, '100ge')
  .replace(/twentyfivegigabitethernet|twentyfivegige|25ge/g, '25ge')
  .replace(/ten-gigabitethernet|tengigabitethernet|ten-gige|10ge/g, '10ge')
  .replace(/gigabitethernet|gigabitethernet|gige/g, 'ge')
  .replace(/[\s._-]+/g, '')

type CircuitMonitorTarget = {
  deviceId: number
  deviceIp?: string
  deviceName?: string
  portName: string
}

const buildPanelUrl = (target: GrafanaTarget) => {
  const params = new URLSearchParams({
    orgId: '1',
    from: target.timeFrom,
    to: target.timeTo,
    refresh: target.refreshInterval,
    theme: 'light',
    'var-device_name': target.device.name,
    'var-device_ip': target.device.ip_address,
    'var-interface_name': target.interface.name,
    viewPanel: String(target.panelId),
    _: String(target.reloadKey),
  })
  return `/grafana-app/d/network-interface-overview/network-interface-overview?${params.toString()}&kiosk`
}

const GrafanaDashboard = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const [deviceKeyword, setDeviceKeyword] = useState('')
  const [deviceOptions, setDeviceOptions] = useState<MonitorDeviceSearchOption[]>([])
  const [selectedDevice, setSelectedDevice] = useState<MonitorDevice | null>(null)
  const [interfaces, setInterfaces] = useState<MonitorInterface[]>([])
  const [selectedInterfaceIndex, setSelectedInterfaceIndex] = useState<number | null>(null)
  const [selectedMetricKey, setSelectedMetricKey] = useState('traffic')
  const [targets, setTargets] = useState<GrafanaTarget[]>([])
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [loadingInterfaces, setLoadingInterfaces] = useState(false)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const iframeRefs = useRef(new Map<string, HTMLIFrameElement>())
  const routeTargetsHandledRef = useRef(false)

  const loadTrafficTarget = async (target: GrafanaTarget, silent = false) => {
    const apiRange = grafanaRangeToApiRange(target.timeFrom)
    const interval = getIntervalForGrafanaRange(target.timeFrom)
    if (!silent) {
      setTargets((current) => current.map((item) => item.key === target.key ? { ...item, trafficLoading: true, trafficError: undefined } : item))
    }
    try {
      const response = await getMonitorInterfaceHistory(target.device.id, target.interface.index, {
        range: apiRange,
        interval,
        group: 'traffic',
      })
      const trafficData = response.data.map(toTrafficChartPoint).filter(Boolean) as TrafficChartPoint[]
      setTargets((current) => current.map((item) => item.key === target.key ? {
        ...item,
        trafficData,
        trafficLoading: false,
        trafficError: undefined,
      } : item))
    } catch (error: any) {
      setTargets((current) => current.map((item) => item.key === target.key ? {
        ...item,
        trafficLoading: false,
        trafficError: error?.response?.data?.detail || '读取接口流量失败',
      } : item))
    }
  }

  const makeTarget = (
    device: MonitorDevice,
    selectedInterface: MonitorInterface,
    metric = MONITOR_OPTIONS.find((item) => item.value === selectedMetricKey) || MONITOR_OPTIONS[0],
    offset = 0,
  ): GrafanaTarget => ({
    key: `${device.id}:${selectedInterface.index}`,
    device,
    interface: selectedInterface,
    metricKey: metric.value,
    metricLabel: metric.label,
    panelId: metric.panelId,
    timeFrom: 'now-6h',
    timeTo: 'now',
    refreshInterval: '30s',
    reloadKey: Date.now() + offset,
    trafficLoading: metric.value === 'traffic',
  })

  const sortedInterfaces = useMemo(
    () => [...interfaces].sort((a, b) => statusRank(a) - statusRank(b) || a.index - b.index),
    [interfaces],
  )

  useEffect(() => {
    const keyword = deviceKeyword.trim()
    if (!keyword || selectedDevice && keyword === `${selectedDevice.ip_address} / ${selectedDevice.name}`) {
      setDeviceOptions([])
      return
    }
    const timer = window.setTimeout(async () => {
      setLoadingDevices(true)
      try {
        setDeviceOptions(await searchMonitorDevices(keyword))
      } catch (error: any) {
        setDeviceOptions([])
        message.error(error?.response?.data?.detail || '设备搜索失败')
      } finally {
        setLoadingDevices(false)
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [deviceKeyword, selectedDevice])

  useEffect(() => {
    const routeTargets = (location.state?.circuitMonitorTargets || []) as CircuitMonitorTarget[]
    if (!routeTargets.length || routeTargetsHandledRef.current) return
    routeTargetsHandledRef.current = true

    const loadRouteTargets = async () => {
      const metric = MONITOR_OPTIONS.find((item) => item.value === selectedMetricKey) || MONITOR_OPTIONS[0]
      const nextTargets: GrafanaTarget[] = []
      const missing: string[] = []
      const byDevice = new Map<number, CircuitMonitorTarget[]>()
      routeTargets.forEach((item) => byDevice.set(item.deviceId, [...(byDevice.get(item.deviceId) || []), item]))

      for (const [deviceId, requestedTargets] of byDevice.entries()) {
        try {
          const response = await getMonitorDeviceInterfaces(deviceId)
          requestedTargets.forEach((requested) => {
            const requestedKey = normalizeInterfaceName(requested.portName)
            const matched = response.interfaces.find((item) => [item.name, item.alias, item.description]
              .some((value) => normalizeInterfaceName(value) === requestedKey))
            if (!matched) {
              missing.push(`${requested.deviceIp || response.device.ip_address} / ${requested.portName}`)
              return
            }
            nextTargets.push(makeTarget(response.device, matched, metric, nextTargets.length))
          })
        } catch {
          requestedTargets.forEach((item) => missing.push(`${item.deviceIp || item.deviceName || deviceId} / ${item.portName}`))
        }
      }

      if (nextTargets.length) {
        setTargets((current) => {
          const existing = new Set(current.map((item) => item.key))
          return [...current, ...nextTargets.filter((item) => !existing.has(item.key))]
        })
        nextTargets.filter((item) => item.metricKey === 'traffic').forEach((item) => void loadTrafficTarget(item, true))
        setSelectedDevice(nextTargets[0].device)
        setDeviceKeyword(`${nextTargets[0].device.ip_address} / ${nextTargets[0].device.name}`)
        setInterfaces([])
        setSelectedInterfaceIndex(null)
      }
      if (missing.length) message.warning(`以下线路接口未识别：${missing.join('；')}`)
      navigate('/grafana', { replace: true, state: null })
    }

    void loadRouteTargets()
  }, [location.state, navigate, selectedMetricKey])

  const selectDevice = async (deviceId: number) => {
    const option = deviceOptions.find((item) => item.id === deviceId)
    if (option) setDeviceKeyword(`${option.ip_address} / ${option.name}`)
    setLoadingInterfaces(true)
    setSelectedInterfaceIndex(null)
    setInterfaces([])
    try {
      const response = await getMonitorDeviceInterfaces(deviceId)
      const sorted = [...response.interfaces].sort((a, b) => statusRank(a) - statusRank(b) || a.index - b.index)
      setSelectedDevice(response.device)
      setDeviceKeyword(`${response.device.ip_address} / ${response.device.name}`)
      setInterfaces(response.interfaces)
      setSelectedInterfaceIndex(sorted[0]?.index ?? null)
    } catch (error: any) {
      setSelectedDevice(null)
      message.error(error?.response?.data?.detail || '读取接口信息失败')
    } finally {
      setLoadingInterfaces(false)
    }
  }

  const addTarget = () => {
    if (!selectedDevice || selectedInterfaceIndex === null) {
      message.warning('请先选择设备和接口')
      return
    }
    const selectedInterface = interfaces.find((item) => item.index === selectedInterfaceIndex)
    if (!selectedInterface) return
    const metric = MONITOR_OPTIONS.find((item) => item.value === selectedMetricKey) || MONITOR_OPTIONS[0]
    const key = `${selectedDevice.id}:${selectedInterface.index}`
    if (targets.some((item) => item.key === key)) {
      message.info('该接口已经添加')
      return
    }
    const target = makeTarget(selectedDevice, selectedInterface, metric)
    setTargets((current) => [...current, target])
    if (target.metricKey === 'traffic') void loadTrafficTarget(target, true)
  }

  const changeMonitorMetric = (metricKey: string) => {
    const metric = MONITOR_OPTIONS.find((item) => item.value === metricKey) || MONITOR_OPTIONS[0]
    const now = Date.now()
    setSelectedMetricKey(metric.value)
    const nextTargets = targets.map((target, index) => {
      let timeFrom = target.timeFrom
      let timeTo = target.timeTo
      try {
        const frameUrl = iframeRefs.current.get(target.key)?.contentWindow?.location.href
        if (frameUrl) {
          const params = new URL(frameUrl).searchParams
          timeFrom = params.get('from') || timeFrom
          timeTo = params.get('to') || timeTo
        }
      } catch {
        // 同源代理正常时可读取当前缩放范围；读取失败则保留上一次范围。
      }
      return {
        ...target,
        metricKey: metric.value,
        metricLabel: metric.label,
        panelId: metric.panelId,
        timeFrom,
        timeTo,
        reloadKey: now + index,
        trafficLoading: metric.value === 'traffic',
        trafficError: undefined,
      }
    })
    setTargets(nextTargets)
    nextTargets.filter((item) => item.metricKey === 'traffic').forEach((item) => void loadTrafficTarget(item, true))
  }

  const changeTrafficRange = (target: GrafanaTarget, timeFrom: string) => {
    const nextTarget = { ...target, timeFrom, timeTo: 'now', reloadKey: Date.now(), trafficLoading: true, trafficError: undefined }
    setTargets((current) => current.map((item) => item.key === target.key ? nextTarget : item))
    void loadTrafficTarget(nextTarget, true)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: 'calc(100vh - 116px)' }}>
      <Card size="small" styles={{ body: { padding: 12 } }}>
        <Space.Compact style={{ width: '100%', maxWidth: 1320 }}>
          <AutoComplete
            value={deviceKeyword}
            options={deviceOptions.map((item) => ({
              value: String(item.id),
              label: `${item.ip_address} / ${item.name}`,
            }))}
            onSearch={(value) => {
              setDeviceKeyword(value)
              if (selectedDevice && value !== `${selectedDevice.ip_address} / ${selectedDevice.name}`) {
                setSelectedDevice(null)
                setInterfaces([])
                setSelectedInterfaceIndex(null)
              }
            }}
            onSelect={(value) => void selectDevice(Number(value))}
            placeholder="输入设备名称或管理 IP"
            notFoundContent={loadingDevices ? <Spin size="small" /> : '未匹配到设备'}
            style={{ width: 360 }}
          />
          <Select
            showSearch
            value={selectedInterfaceIndex}
            loading={loadingInterfaces}
            disabled={!selectedDevice || loadingInterfaces}
            placeholder={selectedDevice ? '选择接口' : '请先选择设备'}
            optionFilterProp="label"
            onChange={setSelectedInterfaceIndex}
            options={sortedInterfaces.map((item) => ({
              value: item.index,
              label: item.name,
              status: String(item.oper_status || 'unknown').toLowerCase(),
              adminStatus: String(item.admin_status || 'unknown').toLowerCase(),
            }))}
            optionRender={(option) => {
              const status = String(option.data.status)
              const adminStatus = String(option.data.adminStatus)
              return (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <span>{String(option.label)}</span>
                  <Space size={4}>
                    <Tag color={status === 'up' ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {status === 'up' ? 'UP' : 'DOWN'}
                    </Tag>
                    {adminStatus === 'down' ? <Tag style={{ marginInlineEnd: 0 }}>管理DOWN</Tag> : null}
                  </Space>
                </div>
              )
            }}
            style={{ width: 390 }}
          />
          <Select
            value={selectedMetricKey}
            options={MONITOR_OPTIONS}
            onChange={changeMonitorMetric}
            style={{ width: 220 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={addTarget} disabled={!selectedDevice || selectedInterfaceIndex === null}>
            添加图表
          </Button>
        </Space.Compact>
        {selectedDevice ? (
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
            已识别 {interfaces.length} 个接口，列表按 UP、DOWN 排序；切换监控项会同步更新全部已添加图表。
          </Text>
        ) : null}
      </Card>

      {targets.length === 0 ? (
        <Card><Empty description="请选择设备和接口，然后点击“添加图表”" /></Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
          {targets.map((target) => {
            const expanded = expandedTargetKey === target.key
            return (
            <Card
              key={target.key}
              size="small"
              title={(
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={`${target.device.ip_address} / ${target.interface.name}`}>
                    {target.device.ip_address} / {target.interface.name}
                  </div>
                  <div style={{ marginTop: 2, fontSize: 12, color: '#8c8c8c' }}>{target.metricLabel}</div>
                </div>
              )}
              extra={(
                <Space size={6} wrap={false}>
                  {target.metricKey === 'traffic' && !expanded ? (
                    <Select
                      size="small"
                      value={target.timeFrom}
                      options={RANGE_OPTIONS}
                      onChange={(value) => changeTrafficRange(target, value)}
                      style={{ width: 86 }}
                    />
                  ) : null}
                  {target.metricKey === 'traffic' && !expanded ? (
                    <Button size="small" icon={<ReloadOutlined />} onClick={() => void loadTrafficTarget(target)} />
                  ) : null}
                  {!expanded ? (
                    <Tag color={String(target.interface.oper_status).toLowerCase() === 'up' ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {String(target.interface.oper_status).toLowerCase() === 'up' ? 'UP' : 'DOWN'}
                    </Tag>
                  ) : null}
                  <Button
                    size="small"
                    title={expanded ? '退出全屏' : '当前页面全屏'}
                    icon={expanded ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                    onClick={() => setExpandedTargetKey(expanded ? null : target.key)}
                  />
                  {!expanded ? (
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => setTargets((current) => current.filter((item) => item.key !== target.key))}
                    />
                  ) : null}
                </Space>
              )}
              style={expanded ? {
                position: 'fixed',
                top: 58,
                right: 12,
                bottom: 12,
                left: 12,
                zIndex: 1200,
                boxShadow: '0 12px 36px rgba(0, 0, 0, 0.24)',
              } : undefined}
              styles={{
                header: { minHeight: 54, padding: '8px 12px' },
                body: { padding: target.metricKey === 'traffic' ? '6px 8px 8px' : 0, height: expanded ? 'calc(100vh - 116px)' : 470, overflow: 'hidden', position: 'relative' },
              }}
            >
              {target.metricKey === 'traffic' ? (
                target.trafficLoading ? (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin tip="读取清洗后的接口流量" /></div>
                ) : target.trafficError ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={target.trafficError} />
                ) : (
                  <TrafficChart data={target.trafficData || []} />
                )
              ) : (
                <iframe
                  key={target.reloadKey}
                  ref={(element) => {
                    if (element) iframeRefs.current.set(target.key, element)
                    else iframeRefs.current.delete(target.key)
                  }}
                  title={`${target.device.ip_address} / ${target.interface.name}`}
                  src={buildPanelUrl(target)}
                  style={{ width: '100%', height: '100%', border: 0, display: 'block' }}
                  allowFullScreen
                />
              )}
            </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default GrafanaDashboard
