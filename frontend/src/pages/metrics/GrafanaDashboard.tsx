import {
  DeleteOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import { AutoComplete, Button, Card, Empty, Select, Space, Spin, Tag, Typography, message } from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  getMonitorDeviceInterfaces,
  getMonitorInterfaceHistory,
  MonitorDevice,
  MonitorDeviceSearchOption,
  MonitorHistoryPoint,
  MonitorInterface,
  searchMonitorDevices,
} from '../../api/metrics'
import AutoRefreshControl from '../../components/AutoRefreshControl'

const { Text } = Typography

const INTERFACE_QUERY_STATE_KEY = 'network-monitor:interface-query:v1'

type HistoryChartPoint = MonitorHistoryPoint & {
  timestamp: number
}

interface InterfaceTarget {
  key: string
  device: MonitorDevice
  interface: MonitorInterface
  metricKey: string
  metricLabel: string
  timeFrom: string
  historyData?: HistoryChartPoint[]
  historyLoading?: boolean
  historyError?: string
}

type CircuitMonitorTarget = {
  deviceId: number
  deviceIp?: string
  deviceName?: string
  portName: string
}

type MetricSeries = {
  key: string
  name: string
  color: string
}

type MetricDefinition = {
  value: string
  label: string
  unit: 'bps' | 'percent' | 'packets'
  emptyLabel: string
  series: MetricSeries[]
}

const MONITOR_OPTIONS: MetricDefinition[] = [
  {
    value: 'traffic', label: '接口流量', unit: 'bps', emptyLabel: '当前时间范围暂无流量数据',
    series: [
      { key: 'in_bps', name: '入方向', color: '#35a800' },
      { key: 'out_bps', name: '出方向', color: '#5f7fd8' },
    ],
  },
  {
    value: 'utilization', label: '接口利用率', unit: 'percent', emptyLabel: '当前时间范围暂无接口利用率数据',
    series: [
      { key: 'in_utilization_percent', name: '入向使用率', color: '#35a800' },
      { key: 'out_utilization_percent', name: '出向使用率', color: '#5f7fd8' },
    ],
  },
  {
    value: 'discards', label: '接口丢弃包增量', unit: 'packets', emptyLabel: '当前时间范围暂无丢弃包增量数据',
    series: [
      { key: 'in_discards_delta', name: '接口入向丢弃', color: '#fa8c16' },
      { key: 'out_discards_delta', name: '接口出向丢弃', color: '#f5222d' },
      { key: 'queue_ingress_dropped_pkts_delta', name: '队列入向丢弃', color: '#722ed1' },
      { key: 'queue_egress_dropped_pkts_delta', name: '队列出向丢弃', color: '#13c2c2' },
    ],
  },
  {
    value: 'errors', label: '接口错误包增量', unit: 'packets', emptyLabel: '当前时间范围暂无错误包增量数据',
    series: [
      { key: 'in_errors_delta', name: '入向错误包', color: '#f5222d' },
      { key: 'out_errors_delta', name: '出向错误包', color: '#fa8c16' },
      { key: 'crc_errors_delta', name: 'CRC 错误', color: '#722ed1' },
    ],
  },
  {
    value: 'pfc', label: 'PFC 收发包增量', unit: 'packets', emptyLabel: '当前时间范围暂无 PFC 增量数据',
    series: [
      { key: 'pfc_rx_pkts_delta', name: 'PFC 接收', color: '#1677ff' },
      { key: 'pfc_tx_pkts_delta', name: 'PFC 发送', color: '#13c2c2' },
    ],
  },
  {
    value: 'ecn', label: 'ECN 标记包增量', unit: 'packets', emptyLabel: '当前时间范围暂无 ECN 标记增量数据',
    series: [{ key: 'ecn_marked_pkts_delta', name: 'ECN 标记', color: '#722ed1' }],
  },
]

const RANGE_OPTIONS = [
  { value: 'now-10m', label: '10分钟' },
  { value: 'now-1h', label: '1小时' },
  { value: 'now-6h', label: '6小时' },
  { value: 'now-24h', label: '24小时' },
  { value: 'now-7d', label: '7天' },
]

const getMetric = (metricKey?: string | null) => MONITOR_OPTIONS.find((item) => item.value === metricKey) || MONITOR_OPTIONS[0]

const getIntervalForRange = (range: string) => {
  if (range === 'now-10m') return '10s'
  if (range === 'now-1h') return '30s'
  if (range === 'now-6h') return '1m'
  if (range === 'now-24h') return '5m'
  if (range === 'now-7d') return '1h'
  return '1m'
}

const rangeToApiRange = (range: string) => range.startsWith('now-') ? `-${range.slice(4)}` : '-6h'

const isSupportedRange = (value?: string | null) => RANGE_OPTIONS.some((item) => item.value === value)

type UrlTarget = {
  deviceIp: string
  interfaceName: string
  timeFrom: string
}

const serializeUrlTarget = (target: Pick<InterfaceTarget, 'device' | 'interface' | 'timeFrom'>) => (
  `${target.device.ip_address}|${target.interface.name}|${target.timeFrom}`
)

const parseUrlTarget = (value: string): UrlTarget | null => {
  const parts = value.split('|')
  if (parts.length < 2) return null
  const deviceIp = parts.shift()?.trim() || ''
  const timeCandidate = parts.length > 1 ? parts.pop() : null
  const interfaceName = parts.join('|').trim()
  if (!deviceIp || !interfaceName) return null
  return {
    deviceIp,
    interfaceName,
    timeFrom: isSupportedRange(timeCandidate) ? String(timeCandidate) : 'now-6h',
  }
}

const normalizeInterfaceName = (value?: string | null) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/fourhundredgigabitethernet|fourhundredgige|fhgigabitethernet|fhgige|400ge/g, '400ge')
  .replace(/hundredgigabitethernet|hundredgige|100ge/g, '100ge')
  .replace(/twentyfivegigabitethernet|twentyfivegige|25ge/g, '25ge')
  .replace(/ten-gigabitethernet|tengigabitethernet|ten-gige|10ge/g, '10ge')
  .replace(/gigabitethernet|gige/g, 'ge')
  .replace(/[\s._-]+/g, '')

const statusRank = (item: MonitorInterface) => (String(item.oper_status).toLowerCase() === 'up' ? 0 : 1)

const toHistoryPoint = (point: MonitorHistoryPoint): HistoryChartPoint | null => {
  const rawTime = point._time || point.time
  const timestamp = rawTime ? dayjs(rawTime).valueOf() : NaN
  return Number.isFinite(timestamp) ? { ...point, timestamp } : null
}

const formatBps = (value?: number | null) => {
  const raw = Number(value || 0)
  const safe = Math.abs(raw)
  if (safe >= 1_000_000_000) return `${(raw / 1_000_000_000).toFixed(2)} Gbps`
  if (safe >= 1_000_000) return `${(raw / 1_000_000).toFixed(2)} Mbps`
  if (safe >= 1_000) return `${(raw / 1_000).toFixed(2)} Kbps`
  return `${raw.toFixed(0)} bps`
}

const formatPackets = (value?: number | null) => {
  const raw = Number(value || 0)
  const safe = Math.abs(raw)
  if (safe >= 1_000_000_000) return `${(raw / 1_000_000_000).toFixed(2)}G 包`
  if (safe >= 1_000_000) return `${(raw / 1_000_000).toFixed(2)}M 包`
  if (safe >= 1_000) return `${(raw / 1_000).toFixed(2)}K 包`
  return `${raw.toFixed(0)} 包`
}

const formatValue = (value: number, unit: MetricDefinition['unit']) => {
  if (unit === 'bps') return formatBps(value)
  if (unit === 'percent') return `${Number(value).toFixed(2)}%`
  return formatPackets(value)
}

const formatTimeAxisValue = (value: number, data: HistoryChartPoint[]) => {
  const span = Math.abs((data[data.length - 1]?.timestamp || 0) - (data[0]?.timestamp || 0))
  if (span <= 60 * 60 * 1000) return dayjs(value).format('HH:mm:ss')
  return dayjs(value).format('MM-DD HH:mm')
}

const MetricChart = ({ data, metric }: { data: HistoryChartPoint[]; metric: MetricDefinition }) => {
  const [left, setLeft] = useState<number | null>(null)
  const [right, setRight] = useState<number | null>(null)
  const [domain, setDomain] = useState<[number | 'dataMin', number | 'dataMax']>(['dataMin', 'dataMax'])
  const chartData = useMemo(() => [...data].filter((point) => Number.isFinite(point.timestamp)).sort((a, b) => a.timestamp - b.timestamp), [data])
  const availableSeries = useMemo(
    () => metric.series.filter((series) => chartData.some((point) => point[series.key] !== null && point[series.key] !== undefined && Number.isFinite(Number(point[series.key])))),
    [chartData, metric],
  )

  useEffect(() => {
    setDomain(['dataMin', 'dataMax'])
    setLeft(null)
    setRight(null)
  }, [data, metric.value])

  const zoom = () => {
    if (left !== null && right !== null && left !== right) setDomain(left < right ? [left, right] : [right, left])
    setLeft(null)
    setRight(null)
  }

  if (!chartData.length || !availableSeries.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={metric.emptyLabel} />
  }

  return (
    <div style={{ height: '100%', position: 'relative' }}>
      {domain[0] !== 'dataMin' ? (
        <Button size="small" type="link" onClick={() => setDomain(['dataMin', 'dataMax'])} style={{ position: 'absolute', top: 2, right: 8, zIndex: 2 }}>
          还原
        </Button>
      ) : null}
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={chartData}
          margin={{ top: 18, right: 30, bottom: 28, left: 8 }}
          onMouseDown={(event) => setLeft(Number(event?.activeLabel) || null)}
          onMouseMove={(event) => left !== null ? setRight(Number(event?.activeLabel) || null) : undefined}
          onMouseUp={zoom}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#edf0f2" />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={domain}
            allowDataOverflow
            tickFormatter={(value) => formatTimeAxisValue(Number(value), chartData)}
            minTickGap={36}
            tick={{ fontSize: 12 }}
          />
          <YAxis
            domain={metric.unit === 'percent' ? [0, (max: number) => Math.max(100, Math.ceil(max))] : [0, 'auto']}
            tickFormatter={(value) => formatValue(Number(value), metric.unit)}
            width={88}
            tick={{ fontSize: 12 }}
          />
          <Legend verticalAlign="top" height={28} />
          <RechartsTooltip
            labelFormatter={(value) => dayjs(Number(value)).format('YYYY-MM-DD HH:mm:ss')}
            formatter={(value: any, name: string) => [formatValue(Number(value), metric.unit), name]}
          />
          {availableSeries.map((series, index) => (
            <Area
              key={series.key}
              type="monotone"
              dataKey={series.key}
              name={series.name}
              stroke={series.color}
              fill={index === 0 ? series.color : 'transparent'}
              fillOpacity={index === 0 ? 0.16 : 0}
              strokeWidth={1.6}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
          {left !== null && right !== null ? <ReferenceArea x1={left} x2={right} strokeOpacity={0.2} fill="#1677ff" fillOpacity={0.12} /> : null}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
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
  const [targets, setTargets] = useState<InterfaceTarget[]>([])
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [loadingInterfaces, setLoadingInterfaces] = useState(false)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const initializedRef = useRef(false)

  const makeTarget = useCallback((device: MonitorDevice, selectedInterface: MonitorInterface, metricKey = selectedMetricKey, timeFrom = 'now-6h'): InterfaceTarget => {
    const metric = getMetric(metricKey)
    return {
      key: `${device.id}:${selectedInterface.index}`,
      device,
      interface: selectedInterface,
      metricKey: metric.value,
      metricLabel: metric.label,
      timeFrom,
      historyLoading: true,
    }
  }, [selectedMetricKey])

  const loadTargetHistory = useCallback(async (target: InterfaceTarget, background = false) => {
    if (!background) {
      setTargets((current) => current.map((item) => item.key === target.key ? { ...item, historyLoading: true, historyError: undefined } : item))
    }
    try {
      const response = await getMonitorInterfaceHistory(target.device.id, target.interface.index, {
        range: rangeToApiRange(target.timeFrom),
        interval: getIntervalForRange(target.timeFrom),
        group: target.metricKey === 'traffic' ? 'traffic' : 'errors',
      })
      const historyData = response.data.map(toHistoryPoint).filter(Boolean) as HistoryChartPoint[]
      setTargets((current) => current.map((item) => item.key === target.key ? {
        ...item,
        historyData,
        historyLoading: false,
        historyError: undefined,
      } : item))
    } catch (error: any) {
      setTargets((current) => current.map((item) => item.key === target.key ? {
        ...item,
        historyLoading: false,
        historyError: error?.response?.data?.detail || '读取接口历史指标失败',
      } : item))
    }
  }, [])

  const applyTargets = useCallback((nextTargets: InterfaceTarget[]) => {
    setTargets(nextTargets)
    nextTargets.forEach((target) => void loadTargetHistory(target, true))
    if (nextTargets[0]) {
      setSelectedDevice(nextTargets[0].device)
      setDeviceKeyword(`${nextTargets[0].device.ip_address} / ${nextTargets[0].device.name}`)
      setSelectedInterfaceIndex(nextTargets[0].interface.index)
    }
  }, [loadTargetHistory])

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
    if (initializedRef.current) return
    initializedRef.current = true

    const restore = async () => {
      const routeTargets = (location.state?.circuitMonitorTargets || []) as CircuitMonitorTarget[]
      const params = new URLSearchParams(location.search)
      const urlDeviceIp = params.get('device')
      const urlInterface = params.get('interface')
      const urlTargets = params.getAll('target').map(parseUrlTarget).filter(Boolean) as UrlTarget[]
      const expandedUrlTarget = params.get('expanded') ? parseUrlTarget(params.get('expanded') || '') : null
      const metric = getMetric(params.get('metric'))
      setSelectedMetricKey(metric.value)

      if (routeTargets.length) {
        const restored: InterfaceTarget[] = []
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
              if (matched) restored.push(makeTarget(response.device, matched, metric.value))
              else missing.push(`${requested.deviceIp || response.device.ip_address} / ${requested.portName}`)
            })
          } catch {
            requestedTargets.forEach((item) => missing.push(`${item.deviceIp || item.deviceName || deviceId} / ${item.portName}`))
          }
        }
        if (restored.length) applyTargets(restored)
        if (missing.length) message.warning(`以下线路接口未识别：${missing.join('；')}`)
        return
      }

      if (urlTargets.length) {
        const restored: InterfaceTarget[] = []
        const missing: string[] = []
        let primaryInterfaces: MonitorInterface[] = []
        const byDeviceIp = new Map<string, UrlTarget[]>()
        urlTargets.forEach((item) => byDeviceIp.set(item.deviceIp, [...(byDeviceIp.get(item.deviceIp) || []), item]))
        for (const [deviceIp, requestedTargets] of byDeviceIp.entries()) {
          try {
            const options = await searchMonitorDevices(deviceIp)
            const deviceOption = options.find((item) => item.ip_address === deviceIp)
            if (!deviceOption) throw new Error('设备不存在或未加入监控')
            const response = await getMonitorDeviceInterfaces(deviceOption.id)
            if (!primaryInterfaces.length) primaryInterfaces = response.interfaces
            requestedTargets.forEach((requested) => {
              const requestedKey = normalizeInterfaceName(requested.interfaceName)
              const matched = response.interfaces.find((item) => [item.name, item.alias, item.description]
                .some((value) => normalizeInterfaceName(value) === requestedKey))
              if (matched) restored.push(makeTarget(response.device, matched, metric.value, requested.timeFrom))
              else missing.push(`${deviceIp} / ${requested.interfaceName}`)
            })
          } catch (error: any) {
            requestedTargets.forEach((item) => missing.push(`${deviceIp} / ${item.interfaceName}`))
          }
        }
        if (restored.length) {
          setInterfaces(primaryInterfaces)
          applyTargets(restored)
          if (expandedUrlTarget) {
            const expanded = restored.find((item) => (
              item.device.ip_address === expandedUrlTarget.deviceIp
              && normalizeInterfaceName(item.interface.name) === normalizeInterfaceName(expandedUrlTarget.interfaceName)
            ))
            setExpandedTargetKey(expanded?.key || null)
          }
        }
        if (missing.length) message.warning(`以下接口未识别：${missing.join('；')}`)
        return
      }

      if (urlDeviceIp && urlInterface) {
        try {
          const options = await searchMonitorDevices(urlDeviceIp)
          const deviceOption = options.find((item) => item.ip_address === urlDeviceIp) || options[0]
          if (!deviceOption) throw new Error('设备不存在或未加入监控')
          const response = await getMonitorDeviceInterfaces(deviceOption.id)
          const requestedKey = normalizeInterfaceName(urlInterface)
          const matched = response.interfaces.find((item) => [item.name, item.alias, item.description]
            .some((value) => normalizeInterfaceName(value) === requestedKey))
          if (!matched) throw new Error(`未识别接口 ${urlInterface}`)
          setInterfaces(response.interfaces)
          applyTargets([makeTarget(response.device, matched, metric.value)])
          return
        } catch (error: any) {
          message.error(error?.response?.data?.detail || error?.message || '无法按 URL 打开接口')
        }
      }

      try {
        const persisted = JSON.parse(localStorage.getItem(INTERFACE_QUERY_STATE_KEY) || 'null') as { metricKey?: string; targets?: InterfaceTarget[] } | null
        if (persisted?.targets?.length) {
          const persistedMetric = getMetric(persisted.metricKey)
          setSelectedMetricKey(persistedMetric.value)
          const restored: InterfaceTarget[] = []
          const byDevice = new Map<number, InterfaceTarget[]>()
          persisted.targets.forEach((target) => byDevice.set(target.device.id, [...(byDevice.get(target.device.id) || []), target]))
          let primaryInterfaces: MonitorInterface[] = []
          for (const [deviceId, savedTargets] of byDevice.entries()) {
            try {
              const response = await getMonitorDeviceInterfaces(deviceId)
              if (!primaryInterfaces.length) primaryInterfaces = response.interfaces
              savedTargets.forEach((saved) => {
                const matched = response.interfaces.find((item) => item.index === saved.interface.index)
                  || response.interfaces.find((item) => normalizeInterfaceName(item.name) === normalizeInterfaceName(saved.interface.name))
                if (matched) restored.push(makeTarget(response.device, matched, persistedMetric.value, saved.timeFrom))
              })
            } catch {
              savedTargets.forEach((saved) => restored.push(makeTarget(saved.device, saved.interface, persistedMetric.value, saved.timeFrom)))
            }
          }
          if (restored.length) {
            setInterfaces(primaryInterfaces)
            applyTargets(restored)
          }
        }
      } catch {
        localStorage.removeItem(INTERFACE_QUERY_STATE_KEY)
      }
    }

    void restore()
  }, [applyTargets, location.search, location.state, makeTarget])

  useEffect(() => {
    if (!initializedRef.current || !targets.length) return
    const persisted = {
      metricKey: selectedMetricKey,
      targets: targets.map(({ device, interface: targetInterface, metricKey, metricLabel, timeFrom, key }) => ({
        device, interface: targetInterface, metricKey, metricLabel, timeFrom, key,
      })),
    }
    localStorage.setItem(INTERFACE_QUERY_STATE_KEY, JSON.stringify(persisted))

    const params = new URLSearchParams()
    params.set('metric', selectedMetricKey)
    targets.forEach((target) => params.append('target', serializeUrlTarget(target)))
    const expandedTarget = targets.find((target) => target.key === expandedTargetKey)
    if (expandedTarget) params.set('expanded', serializeUrlTarget(expandedTarget))
    const nextUrl = `/grafana?${params.toString()}`
    if (`${location.pathname}${location.search}` !== nextUrl || location.state) {
      navigate(nextUrl, { replace: true, state: null })
    }
  }, [expandedTargetKey, location.pathname, location.search, location.state, navigate, selectedMetricKey, targets])

  const sortedInterfaces = useMemo(
    () => [...interfaces].sort((a, b) => statusRank(a) - statusRank(b) || a.index - b.index),
    [interfaces],
  )

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
    const key = `${selectedDevice.id}:${selectedInterface.index}`
    if (targets.some((item) => item.key === key)) {
      message.info('该接口已经添加')
      return
    }
    const target = makeTarget(selectedDevice, selectedInterface)
    setTargets((current) => [...current, target])
    void loadTargetHistory(target, true)
  }

  const changeMonitorMetric = (metricKey: string) => {
    const metric = getMetric(metricKey)
    setSelectedMetricKey(metric.value)
    const nextTargets = targets.map((target) => ({
      ...target,
      metricKey: metric.value,
      metricLabel: metric.label,
      historyLoading: true,
      historyError: undefined,
    }))
    setTargets(nextTargets)
    nextTargets.forEach((target) => void loadTargetHistory(target, true))
  }

  const changeTargetRange = (target: InterfaceTarget, timeFrom: string) => {
    const nextTarget = { ...target, timeFrom, historyLoading: true, historyError: undefined }
    setTargets((current) => current.map((item) => item.key === target.key ? nextTarget : item))
    void loadTargetHistory(nextTarget, true)
  }

  const removeTarget = (targetKey: string) => {
    setTargets((current) => {
      const next = current.filter((item) => item.key !== targetKey)
      if (!next.length) {
        localStorage.removeItem(INTERFACE_QUERY_STATE_KEY)
        navigate('/grafana', { replace: true })
      }
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: 'calc(100vh - 116px)' }}>
      <Card size="small" styles={{ body: { padding: 12 } }}>
        <Space.Compact style={{ width: '100%', maxWidth: 1320 }}>
          <AutoComplete
            value={deviceKeyword}
            options={deviceOptions.map((item) => ({ value: String(item.id), label: `${item.ip_address} / ${item.name}` }))}
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
            optionRender={(option) => (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span>{String(option.label)}</span>
                <Space size={4}>
                  <Tag color={String(option.data.status) === 'up' ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                    {String(option.data.status) === 'up' ? 'UP' : 'DOWN'}
                  </Tag>
                  {String(option.data.adminStatus) === 'down' ? <Tag style={{ marginInlineEnd: 0 }}>管理DOWN</Tag> : null}
                </Space>
              </div>
            )}
            style={{ width: 390 }}
          />
          <Select value={selectedMetricKey} options={MONITOR_OPTIONS} onChange={changeMonitorMetric} style={{ width: 220 }} />
          <Button type="primary" icon={<PlusOutlined />} onClick={addTarget} disabled={!selectedDevice || selectedInterfaceIndex === null}>
            添加图表
          </Button>
        </Space.Compact>
        {selectedDevice ? (
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
            已识别 {interfaces.length} 个接口，列表按 UP、DOWN 排序；选择和图表会自动保存，当前接口也会写入浏览器地址。
          </Text>
        ) : null}
      </Card>

      {targets.length === 0 ? (
        <Card><Empty description="请选择设备和接口，然后点击“添加图表”" /></Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
          {targets.map((target) => {
            const expanded = expandedTargetKey === target.key
            const metric = getMetric(target.metricKey)
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
                    {!expanded ? (
                      <AutoRefreshControl
                        onRefresh={() => loadTargetHistory(target, true)}
                        tip="只刷新当前接口图表，不改变监控项和时间范围，也不影响其他图表。"
                      />
                    ) : null}
                    {!expanded ? (
                      <Select size="small" value={target.timeFrom} options={RANGE_OPTIONS} onChange={(value) => changeTargetRange(target, value)} style={{ width: 86 }} />
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
                    {!expanded ? <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeTarget(target.key)} /> : null}
                  </Space>
                )}
                style={expanded ? {
                  position: 'fixed', top: 58, right: 12, bottom: 12, left: 12, zIndex: 1200,
                  boxShadow: '0 12px 36px rgba(0, 0, 0, 0.24)',
                } : undefined}
                styles={{
                  header: { minHeight: 54, padding: '8px 12px' },
                  body: { padding: '6px 8px 8px', height: expanded ? 'calc(100vh - 116px)' : 470, overflow: 'hidden', position: 'relative' },
                }}
              >
                {target.historyLoading ? (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin tip="读取接口历史指标" /></div>
                ) : target.historyError ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={target.historyError} />
                ) : (
                  <MetricChart data={target.historyData || []} metric={metric} />
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
