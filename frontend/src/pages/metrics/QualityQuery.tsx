import { type Key, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Popover,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import {
  createQualityProbeTarget,
  deleteQualityProbeTarget,
  getQualityProbeHistory,
  getCircuitTrafficHistory,
  getMonitorDeviceInterfaces,
  getMonitorInterfaceHistory,
  getQualityMtrObservation,
  getQualityNqaInstances,
  getQualityTargetAlertSettings,
  getQualityProbeTargets,
  getQualityProbeTargetsSla,
  runQualityProbeMtr,
  saveQualityTargetAlertSettings,
  testQualityProbeTarget,
  updateQualityProbeTarget,
  type QualityProbeHistoryPoint,
  type MonitorInterface,
  type QualityNqaInstance,
  type QualityProbeTarget,
  type QualityMtrSnapshot,
  type QualityMtrEvent,
  type QualityMtrHop,
} from '../../api/metrics'
import { testAlertNotification } from '../../api/alerts'
import { getDatacenters, getDevices, type Datacenter, type Device } from '../../api/devices'
import { getCircuits, type Circuit } from '../../api/resources'

const { Text, Title } = Typography
const { RangePicker } = DatePicker

const nqaInstanceValue = (adminName?: string | null, operationTag?: string | null) =>
  `${encodeURIComponent(adminName || '')}|${encodeURIComponent(operationTag || '')}`

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const mtrHopColumns = [
  { title: 'Hop', dataIndex: 'hop', key: 'hop', width: 64 },
  { title: 'AS号', key: 'asn', width: 100, render: (_: unknown, hop: QualityMtrHop) => hop.asn || hop.as_info || '-' },
  { title: 'IP', dataIndex: 'ip', key: 'ip', render: (value?: string | null) => value || '???' },
  { title: 'Loss%', dataIndex: 'loss_percent', key: 'loss_percent', width: 90, render: (value?: number | null) => value ?? '-' },
  { title: 'Sent', dataIndex: 'sent', key: 'sent', width: 80, render: (value?: number | null) => value ?? '-' },
  { title: 'Avg(ms)', dataIndex: 'avg_ms', key: 'avg_ms', width: 95, render: (value?: number | null) => value ?? '-' },
  { title: 'Best(ms)', dataIndex: 'best_ms', key: 'best_ms', width: 95, render: (value?: number | null) => value ?? '-' },
  { title: 'Worst(ms)', dataIndex: 'worst_ms', key: 'worst_ms', width: 105, render: (value?: number | null) => value ?? '-' },
  { title: 'StDev(ms)', dataIndex: 'stdev_ms', key: 'stdev_ms', width: 105, render: (value?: number | null) => value ?? '-' },
]

const renderMtrHopTable = (hops?: QualityMtrHop[]) => (
  <Table<QualityMtrHop>
    size="small"
    rowKey={(hop) => `${hop.hop}-${hop.ip || 'unknown'}-${hop.asn || hop.as_info || 'as'}`}
    columns={mtrHopColumns}
    dataSource={hops || []}
    pagination={false}
    scroll={{ x: 820 }}
  />
)

type QualityHealthLevel = 'critical' | 'warning' | 'notice' | 'healthy' | 'no_data'

const getQualityAvailability = (record: QualityProbeTarget) => {
  if (record.sla_availability_percent !== null && record.sla_availability_percent !== undefined) {
    return Math.max(0, Math.min(100, Number(record.sla_availability_percent)))
  }
  if (record.last_availability_percent !== null && record.last_availability_percent !== undefined) {
    return Math.max(0, Math.min(100, Number(record.last_availability_percent)))
  }
  if (record.last_packet_loss_percent !== null && record.last_packet_loss_percent !== undefined) {
    return Math.max(0, Math.min(100, 100 - Number(record.last_packet_loss_percent)))
  }
  return null
}

const slaStyles: Record<QualityHealthLevel, { label: string; color: string; background: string; border: string; rank: number }> = {
  critical: { label: 'SLA严重', color: '#ffffff', background: '#d9363e', border: '#b4232c', rank: 0 },
  warning: { label: 'SLA异常', color: '#5c2b00', background: '#ff9c42', border: '#d46b08', rank: 1 },
  notice: { label: 'SLA关注', color: '#614700', background: '#ffd666', border: '#d4b106', rank: 2 },
  healthy: { label: 'SLA正常', color: '#ffffff', background: '#4f9f50', border: '#357a38', rank: 3 },
  no_data: { label: '无有效样本', color: '#434343', background: '#d9d9d9', border: '#bfbfbf', rank: 4 },
}

const getSlaLevel = (record: QualityProbeTarget): QualityHealthLevel => {
  const availability = getQualityAvailability(record)
  if (availability === null) return 'no_data'
  if (availability < 99) return 'critical'
  if (availability < 99.5) return 'warning'
  if (availability < 99.9) return 'notice'
  return 'healthy'
}

const getQualityHealthLevel = (record: QualityProbeTarget): QualityHealthLevel => {
  const latency = record.last_avg_latency_ms
  const loss = record.last_packet_loss_percent
  if (record.last_success === null || record.last_success === undefined || (latency == null && loss == null)) return 'no_data'
  if (record.last_success === false) return 'critical'

  const latencyThreshold = Math.max(1, Number(record.latency_threshold_ms || 1))
  const lossThreshold = Math.max(0.01, Number(record.loss_threshold_percent || 0.01))
  const latencyRatio = latency == null ? 0 : Number(latency) / latencyThreshold
  const lossRatio = loss == null ? 0 : Number(loss) / lossThreshold
  const maxRatio = Math.max(latencyRatio, lossRatio)
  if (maxRatio >= 3 || (loss != null && Number(loss) >= 10)) return 'critical'
  if (maxRatio >= 1) return 'warning'
  if (maxRatio >= 0.8) return 'notice'
  return 'healthy'
}

const formatDuration = (durationMs: number) => {
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000))
  if (totalSeconds < 60) return `${totalSeconds}秒`
  const minutes = Math.floor(totalSeconds / 60)
  if (minutes < 60) return `${minutes}分${totalSeconds % 60 ? `${totalSeconds % 60}秒` : ''}`
  const hours = Math.floor(minutes / 60)
  return `${hours}小时${minutes % 60 ? `${minutes % 60}分` : ''}`
}

const metricNumber = (value: unknown): number => {
  if (value === null || value === undefined || value === '') return Number.NaN
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : Number.NaN
}

const formatBps = (value?: number | null) => {
  const safe = Math.abs(Number(value || 0))
  if (safe >= 1_000_000_000) return `${(Number(value || 0) / 1_000_000_000).toFixed(2)} Gbps`
  if (safe >= 1_000_000) return `${(Number(value || 0) / 1_000_000).toFixed(2)} Mbps`
  if (safe >= 1_000) return `${(Number(value || 0) / 1_000).toFixed(2)} Kbps`
  return `${Number(value || 0).toFixed(0)} bps`
}

const circuitAddressFields = (circuit: Circuit) => [
  circuit.primary_remote_interconnect_ip,
  circuit.secondary_remote_interconnect_ip,
  circuit.remote_interconnect_address,
  circuit.primary_interconnect_ip,
  circuit.secondary_interconnect_ip,
  circuit.interconnect_address,
  circuit.primary_local_interconnect_ip,
  circuit.secondary_local_interconnect_ip,
  circuit.local_interconnect_address,
].map((item) => String(item || '').trim()).filter(Boolean)

const circuitAddressMatches = (circuit: Circuit, targetAddress: string) => {
  const normalizedTarget = String(targetAddress || '').trim()
  if (!normalizedTarget) return false
  return circuitAddressFields(circuit).some((address) => address === normalizedTarget || address.split('/')[0] === normalizedTarget)
}

const getCircuitDeviceBindings = (circuit: Circuit, deviceId?: number | null) => {
  const bindings: Array<{ role: string; port?: string; deviceIp?: string; localIp?: string; remoteIp?: string }> = []
  if (deviceId && circuit.primary_device_id === deviceId) {
    bindings.push({
      role: '主接入',
      port: circuit.primary_port_name,
      deviceIp: circuit.primary_device_ip,
      localIp: circuit.primary_local_interconnect_ip || circuit.primary_interconnect_ip || circuit.local_interconnect_address || circuit.interconnect_address,
      remoteIp: circuit.primary_remote_interconnect_ip || circuit.remote_interconnect_address,
    })
  }
  if (deviceId && circuit.secondary_device_id === deviceId) {
    bindings.push({
      role: '备接入',
      port: circuit.secondary_port_name,
      deviceIp: circuit.secondary_device_ip,
      localIp: circuit.secondary_local_interconnect_ip || circuit.secondary_interconnect_ip || circuit.local_interconnect_address || circuit.interconnect_address,
      remoteIp: circuit.secondary_remote_interconnect_ip || circuit.remote_interconnect_address,
    })
  }
  if (deviceId && circuit.aggregation_monitor_device_id === deviceId) {
    bindings.push({
      role: '聚合接口',
      port: circuit.aggregation_interface_name,
      deviceIp: circuit.aggregation_monitor_device_ip,
      localIp: circuit.local_interconnect_address || circuit.interconnect_address || circuit.primary_local_interconnect_ip || circuit.primary_interconnect_ip,
      remoteIp: circuit.remote_interconnect_address || circuit.primary_remote_interconnect_ip || circuit.secondary_remote_interconnect_ip,
    })
  }
  return bindings.filter((item) => item.port || item.deviceIp || item.localIp || item.remoteIp)
}

const buildCircuitOptionLabel = (circuit: Circuit, deviceId?: number | null) => {
  const bindings = getCircuitDeviceBindings(circuit, deviceId)
  const bindingText = bindings.length
    ? bindings.map((item) => [item.role, item.deviceIp, item.port].filter(Boolean).join(' ')).join('；')
    : [circuit.primary_device_ip, circuit.primary_port_name].filter(Boolean).join(' ')
  const interconnectText = bindings.length
    ? bindings.map((item) => [item.localIp, item.remoteIp].filter(Boolean).join('→')).filter(Boolean).join('；')
    : circuitAddressFields(circuit).slice(0, 2).join(' / ')
  return [circuit.datacenter_name, circuit.name, circuit.customer_name || circuit.operator_name, bindingText, interconnectText]
    .filter(Boolean)
    .join(' / ')
}

type LinkedTrafficPoint = {
  ts: number
  in_bps: number
  out_bps: number
}

const rangeOptions = [
  { label: '15分钟', value: '-15m', interval: '1s' },
  { label: '1小时', value: '-1h', interval: '5s' },
  { label: '6小时', value: '-6h', interval: '30s' },
  { label: '24小时', value: '-24h', interval: '2m' },
  { label: '7天', value: '-7d', interval: '15m' },
  { label: '30天', value: '-30d', interval: '1h' },
  { label: '365天', value: '-365d', interval: '1d' },
  { label: '自定义时间', value: 'custom', interval: '1m' },
]

const formatChartTime = (value: string | number, rangeValue: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  if (rangeValue === '-15m' || rangeValue === '-1h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
  }
  if (rangeValue === '-6h' || rangeValue === '-24h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}-${date.getDate()}`
}

const formatZoomedChartTime = (value: string | number, visibleSpanMs: number, rangeValue: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  const second = String(date.getSeconds()).padStart(2, '0')
  if (visibleSpanMs <= 2 * 60 * 60 * 1000) return `${hour}:${minute}:${second}`
  if (visibleSpanMs <= 48 * 60 * 60 * 1000) return `${date.getMonth() + 1}-${date.getDate()} ${hour}:${minute}`
  return formatChartTime(value, rangeValue)
}

const chartTickCount = (rangeValue: string) => {
  if (rangeValue === '-15m') return 16
  if (rangeValue === '-1h') return 13
  if (rangeValue === '-6h') return 13
  if (rangeValue === '-24h') return 13
  if (rangeValue === '-7d') return 8
  if (rangeValue === '-30d') return 10
  return 13
}

const getCustomInterval = (customRange?: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
  if (!customRange?.[0] || !customRange?.[1]) return '1m'
  const seconds = Math.max(1, customRange[1].diff(customRange[0], 'second'))
  if (seconds <= 30 * 60) return '1s'
  if (seconds <= 2 * 60 * 60) return '5s'
  if (seconds <= 12 * 60 * 60) return '30s'
  if (seconds <= 3 * 24 * 60 * 60) return '2m'
  if (seconds <= 7 * 24 * 60 * 60) return '15m'
  if (seconds <= 30 * 24 * 60 * 60) return '1h'
  return '1d'
}

const intervalSeconds = (value: string) => {
  const match = String(value || '').trim().match(/^(\d+)(s|m|h|d)$/)
  if (!match) return 60
  const amount = Math.max(1, Number(match[1]))
  return amount * ({ s: 1, m: 60, h: 3600, d: 86400 }[match[2]] || 1)
}

const buildQualityDrilldownParams = (startTs: number, endTs: number, collectionIntervalSeconds: number) => {
  const start = dayjs(Math.min(startTs, endTs))
  const end = dayjs(Math.max(startTs, endTs))
  const adaptiveInterval = getCustomInterval([start, end])
  const effectiveSeconds = Math.max(1, intervalSeconds(adaptiveInterval), Number(collectionIntervalSeconds || 1))
  return {
    range: '-24h',
    interval: `${effectiveSeconds}s`,
    start_ts: start.valueOf(),
    end_ts: end.valueOf(),
  }
}

const buildQualityHistoryParams = (rangeValue: string, customRange?: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
  const option = rangeOptions.find((item) => item.value === rangeValue) || rangeOptions[1]
  if (rangeValue === 'custom' && customRange?.[0] && customRange?.[1]) {
    return {
      range: '-24h',
      interval: getCustomInterval(customRange),
      start_ts: customRange[0].valueOf(),
      end_ts: customRange[1].valueOf(),
    }
  }
  return { range: option.value === 'custom' ? '-24h' : option.value, interval: option.interval }
}

const hasMetricValue = (data: Array<Record<string, any>>, key: string) => data.some((point) => {
  const value = Number(point[key])
  return Number.isFinite(value)
})

const normalizeInterfaceName = (value?: string | null) => String(value || '').trim().replace(/\s+/g, '').toLowerCase()

const findInterfaceByName = (interfaces: MonitorInterface[], expectedName?: string | null) => {
  const expectedRaw = String(expectedName || '').trim().toLowerCase()
  const expectedNormalized = normalizeInterfaceName(expectedName)
  if (!expectedNormalized) return undefined
  return interfaces.find((item) => [item.name, item.alias, item.description].some((name) => {
    const raw = String(name || '').trim().toLowerCase()
    return raw === expectedRaw || normalizeInterfaceName(raw) === expectedNormalized
  }))
}

const QUALITY_CHART_MARGIN = { top: 16, right: 88, left: 8, bottom: 16 }
const LINKED_TRAFFIC_CHART_MARGIN = { top: 10, right: 88, left: 8, bottom: 8 }
const QUALITY_LEFT_AXIS_WIDTH = 82
const QUALITY_RIGHT_AXIS_WIDTH = 56

const QualityChartPanel = ({ target, initialRange = '-1h' }: { target: QualityProbeTarget; initialRange?: string }) => {
  const [rangeValue, setRangeValue] = useState(initialRange)
  const [customRange, setCustomRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>([dayjs().subtract(1, 'hour'), dayjs()])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyData, setHistoryData] = useState<QualityProbeHistoryPoint[]>([])
  const [trafficLoading, setTrafficLoading] = useState(false)
  const [trafficData, setTrafficData] = useState<LinkedTrafficPoint[]>([])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [refreshCountdown, setRefreshCountdown] = useState(10)
  const [zoomDomain, setZoomDomain] = useState<[number, number] | null>(null)
  const [selectionStart, setSelectionStart] = useState<number | null>(null)
  const [selectionEnd, setSelectionEnd] = useState<number | null>(null)
  const [displayMode, setDisplayMode] = useState<'realtime' | 'smooth5m'>('realtime')
  const [loadedInterval, setLoadedInterval] = useState<string>('')
  const historyRequestSeqRef = useRef(0)
  const trafficRequestSeqRef = useRef(0)

  useEffect(() => {
    setRangeValue(initialRange)
  }, [initialRange])

  const fetchHistory = useCallback(async (silent = false) => {
    const requestSeq = historyRequestSeqRef.current + 1
    historyRequestSeqRef.current = requestSeq
    const params = zoomDomain
      ? buildQualityDrilldownParams(zoomDomain[0], zoomDomain[1], target.interval_seconds)
      : buildQualityHistoryParams(rangeValue, customRange)
    if (!silent) setHistoryLoading(true)
    try {
      const response = await getQualityProbeHistory(target.id, params)
      if (historyRequestSeqRef.current !== requestSeq) return
      setHistoryData(response.data || [])
      setLoadedInterval(response.interval || params.interval)
    } catch (error: any) {
      if (historyRequestSeqRef.current !== requestSeq) return
      if (!silent) message.error(error?.response?.data?.detail || '获取延迟变化曲线失败')
      setHistoryData([])
    } finally {
      if (historyRequestSeqRef.current === requestSeq && !silent) setHistoryLoading(false)
    }
  }, [rangeValue, customRange, target.id, target.interval_seconds, zoomDomain])

  const fetchLinkedTraffic = useCallback(async (silent = false) => {
    const hasCircuitBinding = Boolean(target.circuit_id)
    const hasManualInterfaceBinding = Boolean(target.device_id && target.probe_interface_name)
    if (!hasCircuitBinding && !hasManualInterfaceBinding) {
      setTrafficData([])
      return
    }
    const requestSeq = trafficRequestSeqRef.current + 1
    trafficRequestSeqRef.current = requestSeq
    const params = zoomDomain
      ? buildQualityDrilldownParams(zoomDomain[0], zoomDomain[1], target.interval_seconds)
      : buildQualityHistoryParams(rangeValue, customRange)
    if (!silent) setTrafficLoading(true)
    try {
      let rows: Array<Record<string, any>> = []
      if (target.circuit_id) {
        const response = await getCircuitTrafficHistory(target.circuit_id, params)
        rows = response.aggregate || response.data || []
      } else if (target.device_id && target.probe_interface_name) {
        const interfaceResponse = await getMonitorDeviceInterfaces(target.device_id)
        if (trafficRequestSeqRef.current !== requestSeq) return
        const matchedInterface = findInterfaceByName(interfaceResponse.interfaces || [], target.probe_interface_name)
        if (!matchedInterface) {
          throw new Error(`未在采集设备接口缓存中找到 ${target.probe_interface_name}`)
        }
        const response = await getMonitorInterfaceHistory(target.device_id, matchedInterface.index, params)
        rows = response.data || []
      }
      if (trafficRequestSeqRef.current !== requestSeq) return
      setTrafficData(rows.map((point) => ({
        ts: new Date(String(point._time || point.time || '')).getTime(),
        in_bps: Number(point.in_bps || 0),
        out_bps: Number(point.out_bps || 0),
      })).filter((point) => Number.isFinite(point.ts)).sort((left, right) => left.ts - right.ts))
    } catch (error: any) {
      if (trafficRequestSeqRef.current !== requestSeq) return
      if (!silent) message.warning(error?.response?.data?.detail || error?.message || '关联接口流量读取失败')
      setTrafficData([])
    } finally {
      if (trafficRequestSeqRef.current === requestSeq && !silent) setTrafficLoading(false)
    }
  }, [customRange, rangeValue, target.circuit_id, target.device_id, target.interval_seconds, target.probe_interface_name, zoomDomain])

  useEffect(() => {
    void fetchHistory()
    void fetchLinkedTraffic()
    setRefreshCountdown(10)
  }, [fetchHistory, fetchLinkedTraffic])

  useEffect(() => {
    setZoomDomain(null)
    setSelectionStart(null)
    setSelectionEnd(null)
  }, [rangeValue, customRange, target.id])

  useEffect(() => {
    if (!autoRefresh) return undefined
    const timer = window.setInterval(() => {
      setRefreshCountdown((value) => {
        if (value <= 1) {
          void fetchHistory(true)
          void fetchLinkedTraffic(true)
          return 10
        }
        return value - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, fetchHistory, fetchLinkedTraffic])

  const chartData = useMemo(
    () => historyData
      .filter((point) => point._time)
      .map((point) => ({
        ...point,
        ts: new Date(point._time || '').getTime(),
      }))
      .filter((point) => Number.isFinite(point.ts)),
    [historyData]
  )
  const slaHeatmap = useMemo(() => {
    const useDailyBuckets = rangeValue === '-7d' || rangeValue === '-30d' || rangeValue === 'custom'
    const buckets = new Map<number, { ts: number; sent: number; received: number; samples: number }>()
    chartData.forEach((point) => {
      const date = new Date(point.ts)
      const bucketTs = useDailyBuckets
        ? new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
        : new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()).getTime()
      const bucket = buckets.get(bucketTs) || { ts: bucketTs, sent: 0, received: 0, samples: 0 }
      const sent = Math.max(0, Number(point.sent || 0))
      const received = Math.max(0, Math.min(Number(point.received || 0), sent))
      if (sent > 0) {
        bucket.sent += sent
        bucket.received += received
        bucket.samples += 1
      }
      buckets.set(bucketTs, bucket)
    })
    return [...buckets.values()].sort((left, right) => left.ts - right.ts).map((bucket) => ({
      ...bucket,
      availability: bucket.sent > 0 ? bucket.received * 100 / bucket.sent : null,
      label: dayjs(bucket.ts).format(useDailyBuckets ? 'MM-DD' : 'MM-DD HH:00'),
    }))
  }, [chartData, rangeValue])

  const displayChartData = useMemo(() => {
    if (displayMode === 'realtime' || chartData.length < 2) return chartData

    const windowMs = 5 * 60 * 1000
    let start = 0
    let latencySum = 0
    let latencyCount = 0
    return chartData.map((point, index) => {
      const latency = metricNumber(point.avg_latency_ms)
      const loss = metricNumber(point.packet_loss_percent)
      if (Number.isFinite(latency)) {
        latencySum += latency
        latencyCount += 1
      }
      while (start < index && chartData[start].ts < point.ts - windowMs) {
        const expiredLatency = metricNumber(chartData[start].avg_latency_ms)
        if (Number.isFinite(expiredLatency)) {
          latencySum -= expiredLatency
          latencyCount -= 1
        }
        start += 1
      }
      return {
        ...point,
        avg_latency_ms: latencyCount ? latencySum / latencyCount : null,
        packet_loss_percent: Number.isFinite(loss) ? loss : null,
      }
    })
  }, [chartData, displayMode])

  const visibleSpanMs = useMemo(() => {
    if (zoomDomain) return Math.max(1, zoomDomain[1] - zoomDomain[0])
    if (chartData.length < 2) return 0
    return Math.max(1, chartData[chartData.length - 1].ts - chartData[0].ts)
  }, [chartData, zoomDomain])

  const visibleRawData = useMemo(
    () => zoomDomain
      ? chartData.filter((point) => point.ts >= zoomDomain[0] && point.ts <= zoomDomain[1])
      : chartData,
    [chartData, zoomDomain]
  )

  const historySummary = useMemo(() => {
    const latencyValues = visibleRawData
      .map((point) => metricNumber(point.avg_latency_ms))
      .filter(Number.isFinite)
    const lossValues = visibleRawData
      .map((point) => metricNumber(point.packet_loss_percent))
      .filter(Number.isFinite)
    const availabilityValues = visibleRawData
      .map((point) => {
        const availability = metricNumber(point.availability_percent)
        if (Number.isFinite(availability)) return availability
        const loss = metricNumber(point.packet_loss_percent)
        return Number.isFinite(loss) ? Math.max(0, 100 - loss) : Number.NaN
      })
      .filter(Number.isFinite)
    const sortedLatency = [...latencyValues].sort((left, right) => left - right)
    const p95Index = Math.max(0, Math.ceil(sortedLatency.length * 0.95) - 1)
    return {
      availability: availabilityValues.length
        ? availabilityValues.reduce((sum, value) => sum + value, 0) / availabilityValues.length
        : null,
      averageLatency: latencyValues.length
        ? latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length
        : null,
      p95Latency: sortedLatency.length ? sortedLatency[p95Index] : null,
      maximumLatency: latencyValues.length ? Math.max(...latencyValues) : null,
      averageLoss: lossValues.length ? lossValues.reduce((sum, value) => sum + value, 0) / lossValues.length : null,
      maximumLoss: lossValues.length ? Math.max(...lossValues) : null,
    }
  }, [visibleRawData])

  const abnormalEvents = useMemo(() => {
    if (!visibleRawData.length) return []
    const ordered = [...visibleRawData].sort((left, right) => left.ts - right.ts)
    const gaps = ordered.slice(1)
      .map((point, index) => point.ts - ordered[index].ts)
      .filter((value) => value > 0)
      .sort((left, right) => left - right)
    const observedInterval = gaps.length ? gaps[Math.floor(gaps.length / 2)] : target.interval_seconds * 1000
    const nominalInterval = Math.max(1000, observedInterval || target.interval_seconds * 1000)
    const gapLimit = nominalInterval * 2.5
    type AbnormalEvent = {
      start: number
      end: number
      reasons: Set<string>
      maximumLatency: number
      maximumLoss: number
      durationMs: number
    }
    const events: AbnormalEvent[] = []
    let current: AbnormalEvent | null = null

    const closeCurrent = () => {
      if (!current) return
      current.durationMs = Math.max(nominalInterval, current.end - current.start + nominalInterval)
      events.push(current)
      current = null
    }

    ordered.forEach((point) => {
      const latency = metricNumber(point.avg_latency_ms)
      const loss = metricNumber(point.packet_loss_percent)
      const reasons: string[] = []
      if (Number.isFinite(latency) && latency >= target.latency_threshold_ms) reasons.push('延迟达到阈值')
      if (Number.isFinite(loss) && loss >= target.loss_threshold_percent) reasons.push('丢包达到阈值')
      if (!reasons.length) {
        closeCurrent()
        return
      }
      if (!current || point.ts - current.end > gapLimit) {
        closeCurrent()
        current = {
          start: point.ts,
          end: point.ts,
          reasons: new Set(reasons),
          maximumLatency: Number.isFinite(latency) ? latency : 0,
          maximumLoss: Number.isFinite(loss) ? loss : 0,
          durationMs: nominalInterval,
        }
        return
      }
      current.end = point.ts
      reasons.forEach((reason) => current?.reasons.add(reason))
      if (Number.isFinite(latency)) current.maximumLatency = Math.max(current.maximumLatency, latency)
      if (Number.isFinite(loss)) current.maximumLoss = Math.max(current.maximumLoss, loss)
    })
    closeCurrent()
    return events.reverse()
  }, [target.interval_seconds, target.latency_threshold_ms, target.loss_threshold_percent, visibleRawData])

  const trafficScale = useMemo(() => {
    const maximum = Math.max(0, ...trafficData.flatMap((point) => [point.in_bps, point.out_bps]))
    if (maximum >= 1_000_000_000) return { divisor: 1_000_000_000, unit: 'Gbps' }
    if (maximum >= 1_000_000) return { divisor: 1_000_000, unit: 'Mbps' }
    if (maximum >= 1_000) return { divisor: 1_000, unit: 'Kbps' }
    return { divisor: 1, unit: 'bps' }
  }, [trafficData])

  const trafficBindingAvailable = Boolean(target.circuit_id || (target.device_id && target.probe_interface_name))
  const trafficBindingTitle = target.circuit_id
    ? `关联${target.probe_source === 'device_nqa_snmp' ? '专线' : '出口'}流量：${target.circuit_name || `线路 #${target.circuit_id}`}`
    : `采集接口流量：${target.probe_interface_name || '-'}`
  const trafficBindingExtra = target.circuit_id
    ? [target.circuit_device_name || target.circuit_device_ip, target.circuit_port_name].filter(Boolean).join(' / ')
    : [target.device_name || target.device_ip, target.probe_interface_name].filter(Boolean).join(' / ')

  const correlation = useMemo(() => {
    const hasTrafficBinding = Boolean(target.circuit_id || (target.device_id && target.probe_interface_name))
    if (!hasTrafficBinding) return null
    const lineLabel = target.circuit_id
      ? (target.probe_source === 'device_nqa_snmp' ? '专线' : '公网线路')
      : '采集接口'
    if (!trafficData.length) return { tone: '#8c8c8c', text: `已关联${lineLabel}，但当前时间范围没有读取到接口流量。` }
    const latest = trafficData[trafficData.length - 1]
    const currentBps = Math.max(latest.in_bps, latest.out_bps)
    const capacityBps = Number(target.circuit_bandwidth_mbps || 0) * 1_000_000
    const utilization = capacityBps > 0 ? currentBps / capacityBps * 100 : null
    const level = getQualityHealthLevel(target)
    if (level === 'healthy' || level === 'notice') {
      return {
        tone: '#237804',
        text: `当前质量未明显异常；${lineLabel}流量 ${formatBps(currentBps)}${utilization == null ? '' : `，约占线路带宽 ${utilization.toFixed(1)}%`}。`,
      }
    }
    if (utilization !== null && utilization >= 80) {
      return {
        tone: '#cf1322',
        text: `质量异常时出口利用率约 ${utilization.toFixed(1)}%，疑似出口拥塞，建议先检查接口队列丢包和带宽利用率。`,
      }
    }
    return {
      tone: '#d46b08',
      text: `质量指标异常，但${lineLabel}流量${utilization == null ? '未配置带宽，无法计算利用率' : `仅约占带宽 ${utilization.toFixed(1)}%`}，建议优先检查路径并执行 MTR。`,
    }
  }, [target, trafficData])

  const finishZoomSelection = () => {
    if (selectionStart !== null && selectionEnd !== null && selectionStart !== selectionEnd) {
      const start = Math.min(selectionStart, selectionEnd)
      const end = Math.max(selectionStart, selectionEnd)
      setZoomDomain([start, end])
    }
    setSelectionStart(null)
    setSelectionEnd(null)
  }

  const hasJitterBreakdown = hasMetricValue(displayChartData as Array<Record<string, any>>, 'jitter_sd_ms')
    || hasMetricValue(displayChartData as Array<Record<string, any>>, 'jitter_ds_ms')
  const hasProbeCounts = target.probe_source === 'device_nqa_snmp'
    && hasJitterBreakdown
    && (
      hasMetricValue(displayChartData as Array<Record<string, any>>, 'sent')
      || hasMetricValue(displayChartData as Array<Record<string, any>>, 'received')
    )

  return (
    <Card
      size="small"
      title={`${target.name} / ${target.target} 质量变化`}
      extra={(
        <Space wrap>
          <Segmented
            size="small"
            value={displayMode}
            options={[
              { label: '实时值', value: 'realtime' },
              { label: '5分钟平滑', value: 'smooth5m' },
            ]}
            onChange={(value) => setDisplayMode(value as 'realtime' | 'smooth5m')}
          />
          <Select
            value={rangeValue}
            style={{ width: 120 }}
            options={rangeOptions.map((item) => ({ label: item.label, value: item.value }))}
            onChange={(value) => {
              setZoomDomain(null)
              setRangeValue(value)
            }}
          />
          {rangeValue === 'custom' && (
            <RangePicker
              showTime
              value={customRange as any}
              allowClear={false}
              onChange={(value) => {
                setZoomDomain(null)
                setCustomRange(value as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null)
              }}
              style={{ width: 360 }}
            />
          )}
          <Switch
            checked={autoRefresh}
            checkedChildren="自动"
            unCheckedChildren="手动"
            onChange={(checked) => {
              setAutoRefresh(checked)
              setRefreshCountdown(10)
            }}
          />
          <Text type="secondary" style={{ width: 92, textAlign: 'right' }}>
            {autoRefresh ? `${refreshCountdown}s 后刷新` : '手动刷新'}
          </Text>
          <Button
            icon={<ReloadOutlined />}
            loading={historyLoading}
            onClick={() => {
              setRefreshCountdown(10)
              void fetchHistory()
              void fetchLinkedTraffic()
            }}
          >
            刷新
          </Button>
          {zoomDomain ? (
            <>
              <Tag color="blue">已按 {loadedInterval || '原始'} 颗粒度重载</Tag>
              <Button onClick={() => setZoomDomain(null)}>重置缩放</Button>
            </>
          ) : null}
        </Space>
      )}
      styles={{ body: { paddingTop: 8 } }}
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        在曲线上拖选时间后，系统会按选中范围重新读取更细颗粒度的数据；继续框选可进一步下钻，点击“重置缩放”恢复完整范围。缩放只作用于当前线路。展开某条线路后才加载曲线，并默认 10 秒自动刷新；未展开的线路不会请求历史曲线。丢包率按滚动窗口内发送和收到包数汇总计算，避免单包丢失时在 0% 和 100% 之间跳变。
      </Text>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 8, marginTop: 10 }}>
        {[
          { label: '范围内可用率', value: historySummary.availability, unit: '%' },
          { label: '平均延迟', value: historySummary.averageLatency, unit: 'ms' },
          { label: 'P95 延迟', value: historySummary.p95Latency, unit: 'ms' },
          { label: '最大延迟', value: historySummary.maximumLatency, unit: 'ms' },
          { label: '平均丢包率', value: historySummary.averageLoss, unit: '%' },
          { label: '最大丢包率', value: historySummary.maximumLoss, unit: '%' },
        ].map((item) => (
          <div key={item.label} style={{ padding: '8px 10px', border: '1px solid #f0f0f0', borderRadius: 6, background: '#fafafa' }}>
            <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{item.label}</Text>
            <Text strong style={{ fontSize: 17 }}>
              {item.value == null ? '-' : `${item.value.toFixed(2)} ${item.unit}`}
            </Text>
          </div>
        ))}
      </div>
      {slaHeatmap.length ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <Text strong>SLA时间热力图</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>按{rangeValue === '-7d' || rangeValue === '-30d' || rangeValue === 'custom' ? '天' : '小时'}汇总，点击下方曲线查看细节</Text>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(58px, 1fr))', gap: 3 }}>
            {slaHeatmap.map((bucket) => {
              const pseudoTarget = { sla_availability_percent: bucket.availability } as QualityProbeTarget
              const appearance = slaStyles[getSlaLevel(pseudoTarget)]
              return (
                <div
                  key={bucket.ts}
                  title={`${bucket.label}｜SLA ${bucket.availability == null ? '-' : `${bucket.availability.toFixed(3)}%`}｜发送/收到 ${Math.round(bucket.sent)}/${Math.round(bucket.received)}｜有效样本 ${bucket.samples}`}
                  style={{
                    minHeight: 42,
                    padding: '5px 4px',
                    borderRadius: 3,
                    background: appearance.background,
                    color: appearance.color,
                    textAlign: 'center',
                  }}
                >
                  <div style={{ fontSize: 10, opacity: 0.85 }}>{bucket.label.replace(/\d{2}-\d{2} /, '')}</div>
                  <div style={{ fontSize: 11, fontWeight: 600 }}>{bucket.availability == null ? '-' : `${bucket.availability.toFixed(2)}%`}</div>
                </div>
              )
            })}
          </div>
        </div>
      ) : null}
      <div style={{ height: 360, width: '100%', marginTop: 8, cursor: 'crosshair', userSelect: 'none' }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={displayChartData}
            margin={QUALITY_CHART_MARGIN}
            onMouseDown={(event: any) => {
              if (Number.isFinite(Number(event?.activeLabel))) {
                const value = Number(event.activeLabel)
                setSelectionStart(value)
                setSelectionEnd(value)
              }
            }}
            onMouseMove={(event: any) => {
              if (selectionStart !== null && Number.isFinite(Number(event?.activeLabel))) {
                setSelectionEnd(Number(event.activeLabel))
              }
            }}
            onMouseUp={finishZoomSelection}
            onMouseLeave={() => {
              if (selectionStart !== null) finishZoomSelection()
            }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={zoomDomain || ['dataMin', 'dataMax']}
              allowDataOverflow={Boolean(zoomDomain)}
              tick={{ fontSize: 11 }}
              minTickGap={8}
              tickCount={chartTickCount(rangeValue)}
              tickFormatter={(value) => formatZoomedChartTime(Number(value), visibleSpanMs, rangeValue)}
            />
            <YAxis
              yAxisId="ms"
              width={QUALITY_LEFT_AXIS_WIDTH}
              tick={{ fontSize: 11 }}
              tickFormatter={(value) => `${value} ms`}
            />
            <YAxis
              yAxisId="percent"
              orientation="right"
              width={QUALITY_RIGHT_AXIS_WIDTH}
              tick={{ fontSize: 11 }}
              domain={[0, 100]}
              tickFormatter={(value) => `${value}%`}
            />
            {hasProbeCounts ? (
              <YAxis
                yAxisId="count"
                hide
                width={0}
              />
            ) : null}
            <ChartTooltip
              labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())}
              formatter={(value: any, name: string) => {
                if (name.includes('丢包')) return [`${Number(value).toFixed(2)} %`, name]
                if (name.includes('发送') || name.includes('收到')) return [`${Number(value).toFixed(0)} 个`, name]
                return [`${Number(value).toFixed(2)} ms`, name]
              }}
            />
            <Legend />
            {selectionStart !== null && selectionEnd !== null ? (
              <ReferenceArea
                x1={Math.min(selectionStart, selectionEnd)}
                x2={Math.max(selectionStart, selectionEnd)}
                yAxisId="ms"
                strokeOpacity={0.35}
                fill="#1677ff"
                fillOpacity={0.16}
              />
            ) : null}
            <Line yAxisId="ms" type="monotone" dataKey="min_latency_ms" name="最小RTT" stroke="#91caff" dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
            <Line yAxisId="ms" type="monotone" dataKey="avg_latency_ms" name="平均RTT" stroke="#1677ff" dot={false} strokeWidth={2.4} connectNulls isAnimationActive={false} />
            <Line yAxisId="ms" type="monotone" dataKey="max_latency_ms" name="最大RTT" stroke="#0958d9" dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
            {hasJitterBreakdown ? (
              <>
                <Line yAxisId="ms" type="monotone" dataKey="jitter_sd_ms" name="SD抖动" stroke="#722ed1" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
                <Line yAxisId="ms" type="monotone" dataKey="jitter_ds_ms" name="DS抖动" stroke="#eb2f96" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
              </>
            ) : null}
            <Line yAxisId="percent" type="monotone" dataKey="packet_loss_percent" name="丢包率" stroke="#f5222d" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
            {hasProbeCounts ? (
              <>
                <Line yAxisId="count" type="monotone" dataKey="sent" name="发送包数" stroke="#8c8c8c" dot={false} strokeWidth={1.6} connectNulls isAnimationActive={false} />
                <Line yAxisId="count" type="monotone" dataKey="received" name="收到包数" stroke="#52c41a" dot={false} strokeWidth={1.8} connectNulls isAnimationActive={false} />
              </>
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!historyLoading && !chartData.length ? (
        <Text type="secondary">当前时间范围内还没有曲线数据，后台任务采集到新点后会自动刷新。</Text>
      ) : null}
      {trafficBindingAvailable ? (
        <Card
          size="small"
          title={trafficBindingTitle}
          extra={<Text type="secondary">{trafficBindingExtra}</Text>}
          style={{ marginTop: 12 }}
          loading={trafficLoading}
        >
          {correlation ? (
            <div style={{ padding: '8px 10px', marginBottom: 8, borderRadius: 6, background: '#fafafa', borderLeft: `3px solid ${correlation.tone}` }}>
              <Text style={{ color: correlation.tone }}>{correlation.text}</Text>
            </div>
          ) : null}
          {trafficData.length ? (
            <div style={{ height: 220, width: '100%', cursor: 'crosshair', userSelect: 'none' }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={trafficData}
                  margin={LINKED_TRAFFIC_CHART_MARGIN}
                  onMouseDown={(event: any) => {
                    if (Number.isFinite(Number(event?.activeLabel))) {
                      const value = Number(event.activeLabel)
                      setSelectionStart(value)
                      setSelectionEnd(value)
                    }
                  }}
                  onMouseMove={(event: any) => {
                    if (selectionStart !== null && Number.isFinite(Number(event?.activeLabel))) {
                      setSelectionEnd(Number(event.activeLabel))
                    }
                  }}
                  onMouseUp={finishZoomSelection}
                  onMouseLeave={() => {
                    if (selectionStart !== null) finishZoomSelection()
                  }}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="ts"
                    type="number"
                    scale="time"
                    domain={zoomDomain || ['dataMin', 'dataMax']}
                    allowDataOverflow={Boolean(zoomDomain)}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(value) => formatZoomedChartTime(Number(value), visibleSpanMs, rangeValue)}
                  />
                  <YAxis
                    yAxisId="bps"
                    width={QUALITY_LEFT_AXIS_WIDTH}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(value) => `${(Number(value) / trafficScale.divisor).toFixed(1)} ${trafficScale.unit}`}
                  />
                  <YAxis
                    yAxisId="right-spacer"
                    orientation="right"
                    width={QUALITY_RIGHT_AXIS_WIDTH}
                    tick={false}
                    axisLine={false}
                    tickLine={false}
                  />
                  <ChartTooltip
                    labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())}
                    formatter={(value: any, name: string) => [formatBps(Number(value)), name]}
                  />
                  <Legend />
                  {selectionStart !== null && selectionEnd !== null ? (
                    <ReferenceArea
                      x1={Math.min(selectionStart, selectionEnd)}
                      x2={Math.max(selectionStart, selectionEnd)}
                      yAxisId="bps"
                      strokeOpacity={0.35}
                      fill="#1677ff"
                      fillOpacity={0.16}
                    />
                  ) : null}
                  <Line yAxisId="bps" type="monotone" dataKey="in_bps" name="入方向" stroke="#52c41a" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
                  <Line yAxisId="bps" type="monotone" dataKey="out_bps" name="出方向" stroke="#1677ff" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : <Text type="secondary">当前时间范围暂无关联接口流量。</Text>}
        </Card>
      ) : (
        <div style={{ marginTop: 10, padding: '8px 10px', background: '#fafafa', borderRadius: 6 }}>
          <Text type="secondary">
            {target.probe_source === 'device_nqa_snmp'
              ? '尚未关联专线或采集设备接口。编辑该探测目标并选择专线，或直接选择采集设备接口后，可同步查看接口流量。'
              : '尚未关联公网线路。编辑该探测目标并选择公网线路后，可同步查看出口流量和关联判断。'}
          </Text>
        </div>
      )}
      {chartData.length ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 7 }}>
            <Text strong>异常时间轴（当前显示范围）</Text>
            <Text type="secondary">{abnormalEvents.length} 段异常</Text>
          </div>
          {abnormalEvents.length ? (
            <div style={{ maxHeight: 210, overflowY: 'auto', border: '1px solid #f0f0f0', borderRadius: 6 }}>
              {abnormalEvents.slice(0, 30).map((event, index) => (
                <div
                  key={`${event.start}-${index}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(130px, 0.7fr) minmax(250px, 1.4fr) 100px minmax(220px, 1fr)',
                    gap: 12,
                    alignItems: 'center',
                    padding: '8px 10px',
                    borderBottom: index === Math.min(abnormalEvents.length, 30) - 1 ? 'none' : '1px solid #f5f5f5',
                  }}
                >
                  <Space size={4} wrap>
                    {[...event.reasons].map((reason) => <Tag color="red" key={reason}>{reason}</Tag>)}
                  </Space>
                  <Text>{formatTime(new Date(event.start).toISOString())} ～ {formatTime(new Date(event.end).toISOString())}</Text>
                  <Text>{formatDuration(event.durationMs)}</Text>
                  <Text type="secondary">
                    峰值延迟 {event.maximumLatency.toFixed(2)} ms / 丢包 {event.maximumLoss.toFixed(2)}%
                  </Text>
                </div>
              ))}
            </div>
          ) : <Text type="secondary">当前显示范围内没有超过该线路阈值的记录。</Text>}
        </div>
      ) : null}
    </Card>
  )
}

const QualityQuery = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [items, setItems] = useState<QualityProbeTarget[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [circuits, setCircuits] = useState<Circuit[]>([])
  const [keyword, setKeyword] = useState('')
  const [activeFilter, setActiveFilter] = useState<string>('all')
  const [slaRange, setSlaRange] = useState('-1h')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<QualityProbeTarget | null>(null)
  const [expandedRowKeys, setExpandedRowKeys] = useState<Key[]>([])
  const [openActionTargetId, setOpenActionTargetId] = useState<number | null>(null)
  const [mtrLoadingId, setMtrLoadingId] = useState<number | null>(null)
  const [mtrOpen, setMtrOpen] = useState(false)
  const [mtrTitle, setMtrTitle] = useState('')
  const [mtrCommand, setMtrCommand] = useState('')
  const [mtrOutput, setMtrOutput] = useState('')
  const [mtrObservationOpen, setMtrObservationOpen] = useState(false)
  const [mtrObservationLoading, setMtrObservationLoading] = useState(false)
  const [mtrObservationTarget, setMtrObservationTarget] = useState<QualityProbeTarget | null>(null)
  const [mtrLatestSnapshot, setMtrLatestSnapshot] = useState<QualityMtrSnapshot | null>(null)
  const [mtrSnapshots, setMtrSnapshots] = useState<QualityMtrSnapshot[]>([])
  const [mtrEvents, setMtrEvents] = useState<QualityMtrEvent[]>([])
  const [targetAlertSettingsLoading, setTargetAlertSettingsLoading] = useState(false)
  const [targetAlertTesting, setTargetAlertTesting] = useState(false)
  const [nqaDevices, setNqaDevices] = useState<Device[]>([])
  const [nqaDevicesLoading, setNqaDevicesLoading] = useState(false)
  const [nqaInstances, setNqaInstances] = useState<QualityNqaInstance[]>([])
  const [nqaInstancesLoading, setNqaInstancesLoading] = useState(false)
  const [nqaDeviceInterfaces, setNqaDeviceInterfaces] = useState<MonitorInterface[]>([])
  const [nqaDeviceInterfacesLoading, setNqaDeviceInterfacesLoading] = useState(false)
  const nqaDeviceSearchTimer = useRef<number | null>(null)
  const probeSource = Form.useWatch('probe_source', form) || 'server_icmp'
  const selectedNqaDeviceId = Form.useWatch('device_id', form)

  const testTargetAlertRobot = async () => {
    const values = await form.validateFields(['alert_webhook_url', 'alert_mention_users_text'])
    setTargetAlertTesting(true)
    try {
      const mentionUsers = String(values.alert_mention_users_text || '')
        .split(/[,，;；\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
      const result = await testAlertNotification(values.alert_webhook_url, mentionUsers)
      message.success(result.message || '测试消息发送成功')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试消息发送失败')
    } finally {
      setTargetAlertTesting(false)
    }
  }

  const fetchItems = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const response = await getQualityProbeTargets({
        search: keyword.trim() || undefined,
        active: activeFilter === 'all' ? undefined : activeFilter === 'active',
      })
      const slaResponse = await getQualityProbeTargetsSla(slaRange)
      const slaById = new Map((slaResponse.items || []).map((item) => [item.id, item]))
      const nextItems = (response.items || []).map((item) => ({ ...item, ...(slaById.get(item.id) || {}) }))
      setItems(nextItems)
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取质量探测目标失败')
      setItems([])
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchDatacenters = async () => {
    try {
      setDatacenters(await getDatacenters())
    } catch {
      setDatacenters([])
    }
  }

  const fetchCircuits = async () => {
    try {
      const response = await getCircuits({ status: 'active', limit: 1000 })
      setCircuits(response.items || [])
    } catch {
      setCircuits([])
    }
  }

  const fetchNqaDevices = async (search = '') => {
    setNqaDevicesLoading(true)
    try {
      const response = await getDevices({
        limit: 100,
        status: 'active',
        is_monitored: true,
        search: search.trim() || undefined,
      })
      setNqaDevices((response.items || []).filter((item) => (item.monitor_source || 'snmp') === 'snmp'))
    } catch {
      setNqaDevices([])
    } finally {
      setNqaDevicesLoading(false)
    }
  }

  const fetchNqaInstances = async (deviceId?: number, forceRefresh = false) => {
    if (!deviceId) {
      setNqaInstances([])
      return
    }
    setNqaInstancesLoading(true)
    try {
      const response = await getQualityNqaInstances(deviceId, forceRefresh)
      setNqaInstances(response.items || [])
      if (!(response.items || []).length) message.info('该设备没有识别到可用的NQA/SNMP实例')
    } catch (error: any) {
      setNqaInstances([])
      message.error(error?.response?.data?.detail || '读取设备NQA实例失败')
    } finally {
      setNqaInstancesLoading(false)
    }
  }

  const fetchNqaDeviceInterfaces = async (deviceId?: number) => {
    if (!deviceId) {
      setNqaDeviceInterfaces([])
      return
    }
    setNqaDeviceInterfacesLoading(true)
    try {
      const response = await getMonitorDeviceInterfaces(deviceId)
      setNqaDeviceInterfaces(response.interfaces || [])
    } catch (error: any) {
      setNqaDeviceInterfaces([])
      message.error(error?.response?.data?.detail || '读取采集设备接口失败')
    } finally {
      setNqaDeviceInterfacesLoading(false)
    }
  }

  useEffect(() => {
    void fetchDatacenters()
    void fetchCircuits()
    void fetchNqaDevices()
  }, [])

  useEffect(() => {
    void fetchItems()
  }, [activeFilter, slaRange])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void fetchItems(true)
    }, 10000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFilter, keyword, slaRange])

  const datacenterOptions = useMemo(
    () => datacenters.filter((item) => item.is_active !== false).map((item) => ({ label: item.name, value: item.id })),
    [datacenters]
  )
  const circuitOptions = useMemo(
    () => circuits
      .filter((circuit) => circuit.line_type === (probeSource === 'device_nqa_snmp' ? 'private_line' : 'internet'))
      .map((circuit) => ({
      value: circuit.id,
      label: buildCircuitOptionLabel(circuit, probeSource === 'device_nqa_snmp' ? selectedNqaDeviceId : undefined),
    })),
    [circuits, probeSource, selectedNqaDeviceId]
  )
  const selectedDeviceCircuitOptions = useMemo(
    () => circuits
      .filter((circuit) => circuit.line_type === 'private_line')
      .filter((circuit) => getCircuitDeviceBindings(circuit, selectedNqaDeviceId).length > 0)
      .map((circuit) => ({
        value: circuit.id,
        label: buildCircuitOptionLabel(circuit, selectedNqaDeviceId),
      })),
    [circuits, selectedNqaDeviceId]
  )
  const selectedDeviceInterfaceOptions = useMemo(
    () => nqaDeviceInterfaces.map((item) => {
      const alias = item.alias && item.alias !== item.name ? ` / ${item.alias}` : ''
      const status = item.oper_status || item.admin_status || ''
      const statusText = status ? `（${status}）` : ''
      return {
        value: item.name,
        label: `${item.name}${alias}${statusText}`,
      }
    }),
    [nqaDeviceInterfaces]
  )

  const summaryItems = useMemo(() => items.filter((item) => item.is_active), [items])
  const slaItems = useMemo(
    () => [...summaryItems].sort((left, right) => {
      const leftLevel = slaStyles[getSlaLevel(left)]
      const rightLevel = slaStyles[getSlaLevel(right)]
      if (leftLevel.rank !== rightLevel.rank) return leftLevel.rank - rightLevel.rank
      return (getQualityAvailability(left) ?? -1) - (getQualityAvailability(right) ?? -1)
    }),
    [summaryItems]
  )
  const internetSlaItems = useMemo(
    () => slaItems.filter((item) => (item.probe_source || 'server_icmp') === 'server_icmp'),
    [slaItems]
  )
  const privateSlaItems = useMemo(
    () => slaItems.filter((item) => item.probe_source === 'device_nqa_snmp'),
    [slaItems]
  )
  const buildSlaGroups = useCallback((field: 'datacenter_name' | 'operator_name') => {
    const groups = new Map<string, { name: string; sent: number; received: number; targets: number; noData: number }>()
    summaryItems.forEach((item) => {
      const name = String(item[field] || '未归类')
      const group = groups.get(name) || { name, sent: 0, received: 0, targets: 0, noData: 0 }
      group.targets += 1
      group.sent += Number(item.sla_sent || 0)
      group.received += Number(item.sla_received || 0)
      if (!item.sla_sample_count) group.noData += 1
      groups.set(name, group)
    })
    return [...groups.values()].map((group) => ({
      ...group,
      availability: group.sent > 0 ? group.received * 100 / group.sent : null,
    })).sort((left, right) => (left.availability ?? -1) - (right.availability ?? -1))
  }, [summaryItems])
  const datacenterSlaGroups = useMemo(() => buildSlaGroups('datacenter_name'), [buildSlaGroups])
  const operatorSlaGroups = useMemo(() => buildSlaGroups('operator_name'), [buildSlaGroups])
  const lowestSlaItems = useMemo(
    () => slaItems.filter((item) => getQualityAvailability(item) !== null).slice(0, 8),
    [slaItems]
  )
  const decliningSlaItems = useMemo(
    () => [...summaryItems]
      .filter((item) => item.sla_availability_change_percent !== null && item.sla_availability_change_percent !== undefined)
      .sort((left, right) => Number(left.sla_availability_change_percent) - Number(right.sla_availability_change_percent))
      .slice(0, 8),
    [summaryItems]
  )
  const qualityOverview = useMemo(() => ({
    abnormal: summaryItems.filter((item) => {
      const availability = getQualityAvailability(item)
      return availability !== null && availability < 99.9
    }).length,
    collectionIssues: summaryItems.filter((item) => item.collection_health && item.collection_health !== 'healthy').length,
    budgetExceeded: summaryItems.filter((item) => Number(item.sla_error_budget_used_percent || 0) >= 100).length,
    correlated: summaryItems.filter((item) => (item.root_cause_categories || []).some((category) => !category.includes('暂未发现'))).length,
  }), [summaryItems])
  const expandedTarget = useMemo(
    () => items.find((item) => item.id === Number(expandedRowKeys[0])),
    [expandedRowKeys, items]
  )

  const openCreate = async () => {
    setEditing(null)
    setNqaInstances([])
    setNqaDeviceInterfaces([])
    form.resetFields()
    form.setFieldsValue({
      probe_source: 'server_icmp',
      interval_seconds: 60,
      packet_count: 5,
      timeout_ms: 1500,
      latency_threshold_ms: 100,
      loss_threshold_percent: 1,
      jitter_threshold_ms: 30,
      mtr_enabled: false,
      mtr_interval_seconds: 300,
      is_active: true,
      alert_enabled: false,
      alert_loss_threshold_percent: 10,
      alert_consecutive_samples: 5,
      alert_webhook_url: '',
      alert_mention_users_text: '',
    })
    setModalOpen(true)
  }

  const openEdit = async (record: QualityProbeTarget) => {
    setEditing(record)
    form.resetFields()
    form.setFieldsValue({
      ...record,
      probe_source: record.probe_source || 'server_icmp',
      nqa_instance_key: record.probe_source === 'device_nqa_snmp'
        ? nqaInstanceValue(record.nqa_admin_name, record.nqa_operation_tag)
        : undefined,
      datacenter_id: record.datacenter_id || undefined,
      alert_enabled: false,
      alert_loss_threshold_percent: 10,
      alert_consecutive_samples: 5,
      alert_webhook_url: '',
      alert_mention_users_text: '',
    })
    setModalOpen(true)
    if (record.probe_source === 'device_nqa_snmp' && record.device_id) {
      void fetchNqaDevices(record.device_name || record.device_ip || '')
      void fetchNqaInstances(record.device_id)
      void fetchNqaDeviceInterfaces(record.device_id)
    } else {
      setNqaInstances([])
      setNqaDeviceInterfaces([])
    }
    setTargetAlertSettingsLoading(true)
    try {
      const settings = await getQualityTargetAlertSettings(record.id)
      form.setFieldsValue({
        alert_enabled: settings.enabled,
        alert_loss_threshold_percent: settings.loss_threshold_percent,
        alert_consecutive_samples: settings.consecutive_samples,
        alert_webhook_url: settings.webhook_url,
        alert_mention_users_text: (settings.mention_users || []).join(', '),
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取该目标告警配置失败')
    } finally {
      setTargetAlertSettingsLoading(false)
    }
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const {
        alert_enabled,
        alert_loss_threshold_percent,
        alert_consecutive_samples,
        alert_webhook_url,
        alert_mention_users_text,
        nqa_instance_key: _nqaInstanceKey,
        ...targetValues
      } = values
      const normalizedTargetValues = {
        ...targetValues,
        circuit_id: targetValues.circuit_id || null,
        probe_interface_name: targetValues.probe_interface_name || null,
      }
      let savedTarget: QualityProbeTarget
      if (editing) {
        savedTarget = await updateQualityProbeTarget(editing.id, normalizedTargetValues)
      } else {
        savedTarget = await createQualityProbeTarget(normalizedTargetValues)
      }
      await saveQualityTargetAlertSettings(savedTarget.id, {
        enabled: alert_enabled,
        loss_threshold_percent: alert_loss_threshold_percent,
        consecutive_samples: alert_consecutive_samples,
        webhook_url: alert_webhook_url,
        mention_users: String(alert_mention_users_text || '')
          .split(/[,，;；\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      message.success(editing ? '质量探测目标和告警配置已更新' : '质量探测目标和告警配置已添加')
      setModalOpen(false)
      setEditing(null)
      await fetchItems()
      setExpandedRowKeys([savedTarget.id])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (record: QualityProbeTarget) => {
    try {
      await deleteQualityProbeTarget(record.id)
      message.success('已删除')
      await fetchItems()
      setExpandedRowKeys((keys) => keys.filter((key) => key !== record.id))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleTest = async (record: QualityProbeTarget) => {
    setOpenActionTargetId(null)
    setTestingId(record.id)
    try {
      const response = await testQualityProbeTarget(record.id)
      const result = response.result
      if (result.success) {
        const currentLoss = result.current_packet_loss_percent ?? result.packet_loss_percent
        message.success(`测试成功：平均 ${result.avg_latency_ms ?? '-'} ms，本轮丢包 ${currentLoss ?? '-'}%`)
      } else {
        message.warning(result.error || '测试未收到响应')
      }
      await fetchItems(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const handleMtr = async (record: QualityProbeTarget) => {
    setOpenActionTargetId(null)
    setMtrLoadingId(record.id)
    setMtrTitle(`${record.name} / ${record.target}`)
    setMtrCommand('')
    setMtrOutput('')
    setMtrOpen(true)
    try {
      const result = await runQualityProbeMtr(record.id)
      setMtrCommand(result.command || result.tool || 'MTR')
      setMtrOutput(result.output || '无输出')
    } catch (error: any) {
      setMtrOutput(error?.response?.data?.detail || 'MTR 执行失败')
    } finally {
      setMtrLoadingId(null)
    }
  }

  const openMtrObservation = async (record: QualityProbeTarget) => {
    setOpenActionTargetId(null)
    setMtrObservationTarget(record)
    setMtrLatestSnapshot(null)
    setMtrSnapshots([])
    setMtrEvents([])
    setMtrObservationOpen(true)
    setMtrObservationLoading(true)
    try {
      const result = await getQualityMtrObservation(record.id)
      setMtrObservationTarget(result.target)
      setMtrLatestSnapshot(result.latest_snapshot || null)
      setMtrSnapshots(result.snapshots || [])
      setMtrEvents(result.events || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取路径观察失败')
    } finally {
      setMtrObservationLoading(false)
    }
  }

  const renderTargetActions = (record: QualityProbeTarget) => (
    <Space direction="vertical" size={8} onClick={(event) => event.stopPropagation()}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {slaRange.replace('-', '最近')}：发送 {record.sla_sent ?? '-'} / 收到 {record.sla_received ?? '-'} / 丢失 {record.sla_lost ?? '-'}
        {' · '}有效采样 {record.sla_sample_count ?? '-'} / 系统采集完整率 {record.sla_data_completeness_percent ?? '-'}%
        {' · '}较上一周期 {record.sla_availability_change_percent == null ? '-' : `${Number(record.sla_availability_change_percent) > 0 ? '+' : ''}${Number(record.sla_availability_change_percent).toFixed(3)}%`}
        {' · '}本月错误预算 {record.sla_error_budget_used_percent == null ? '-' : `已用${record.sla_error_budget_used_percent}%`}
        {' · '}最后采样 {formatTime(record.last_probe_at)}
      </Text>
      <Space size={4} wrap>
        <Button
          size="small"
          icon={<ThunderboltOutlined />}
          loading={testingId === record.id}
          onClick={() => { setOpenActionTargetId(null); handleTest(record) }}
        >
          立即测试
        </Button>
        <Button size="small" icon={<EditOutlined />} onClick={() => { setOpenActionTargetId(null); openEdit(record) }}>编辑</Button>
        <Button size="small" loading={mtrLoadingId === record.id} onClick={() => { setOpenActionTargetId(null); handleMtr(record) }}>MTR</Button>
        {(record.probe_source || 'server_icmp') === 'server_icmp' ? (
          <Button size="small" onClick={() => { setOpenActionTargetId(null); openMtrObservation(record) }}>路径观察</Button>
        ) : null}
        <Popconfirm title="确认删除这个探测目标？" onConfirm={() => { setOpenActionTargetId(null); handleDelete(record) }}>
          <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      </Space>
    </Space>
  )

  const renderSlaGroupSummary = (
    title: string,
    groups: Array<{ name: string; targets: number; noData: number; availability: number | null }>,
  ) => (
    <Card size="small" title={title} styles={{ body: { padding: 10 } }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(145px, 1fr))', gap: 6 }}>
        {groups.slice(0, 12).map((group) => {
          const pseudoTarget = { sla_availability_percent: group.availability } as QualityProbeTarget
          const appearance = slaStyles[getSlaLevel(pseudoTarget)]
          return (
            <div key={group.name} style={{ borderLeft: `4px solid ${appearance.background}`, padding: '4px 7px', background: '#fafafa' }}>
              <Text strong ellipsis={{ tooltip: group.name }} style={{ display: 'block', fontSize: 12 }}>{group.name}</Text>
              <Text style={{ color: appearance.border, fontSize: 16, fontWeight: 600 }}>
                {group.availability == null ? '-' : `${group.availability.toFixed(3)}%`}
              </Text>
              <Text type="secondary" style={{ display: 'block', fontSize: 10 }}>
                {group.targets}条线路{group.noData ? ` · ${group.noData}条无数据` : ''}
              </Text>
            </div>
          )
        })}
      </div>
    </Card>
  )

  const renderSlaCards = (records: QualityProbeTarget[], source: 'internet' | 'private') => (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(142px, 1fr))', gap: 4 }}>
      {records.map((record) => {
        const level = getSlaLevel(record)
        const appearance = slaStyles[level]
        const availability = getQualityAvailability(record)
        const isExpanded = Number(expandedRowKeys[0]) === record.id
        const binding = source === 'private'
          ? [record.circuit_name || '未关联专线', record.circuit_device_ip || record.device_ip, record.circuit_port_name || record.probe_interface_name].filter(Boolean).join(' / ')
          : [record.circuit_name, record.operator_name].filter(Boolean).join(' / ')
        return (
          <Popover
            key={record.id}
            trigger="click"
            placement="bottomLeft"
            title={record.name}
            content={renderTargetActions(record)}
            open={openActionTargetId === record.id}
            onOpenChange={(open) => setOpenActionTargetId(open ? record.id : null)}
          >
            <div
              role="button"
              tabIndex={0}
              onClick={() => setExpandedRowKeys([record.id])}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  setExpandedRowKeys([record.id])
                }
              }}
              style={{
                minWidth: 0,
                minHeight: 106,
                padding: '9px 10px',
                border: `1px solid ${appearance.border}`,
                borderRadius: 3,
                background: appearance.background,
                cursor: 'pointer',
                boxShadow: isExpanded ? `0 0 0 2px ${source === 'internet' ? '#1677ff' : '#722ed1'}` : 'none',
                color: appearance.color,
              }}
            >
              <Text
                strong
                ellipsis={{ tooltip: `${record.name}${binding ? ` / ${binding}` : ''}` }}
                style={{ display: 'block', minWidth: 0, color: appearance.color, fontSize: 12 }}
              >
                {record.name}
              </Text>
              <div style={{ marginTop: 8, lineHeight: 1 }}>
                <Text strong style={{ color: appearance.color, fontSize: 21 }}>
                  {availability === null ? '-' : availability.toFixed(3)}
                </Text>
                {availability !== null ? <Text style={{ color: appearance.color, fontSize: 12 }}> %</Text> : null}
              </div>
              <Text style={{ display: 'block', marginTop: 8, color: appearance.color, fontSize: 11, opacity: 0.92 }}>
                延迟 {record.last_avg_latency_ms == null ? '-' : `${Number(record.last_avg_latency_ms).toFixed(1)}ms`}
                {' · '}丢包 {record.last_packet_loss_percent == null ? '-' : `${Number(record.last_packet_loss_percent).toFixed(2)}%`}
              </Text>
              <Text style={{ display: 'block', color: appearance.color, fontSize: 10, marginTop: 4, opacity: 0.85 }}>
                {record.collection_health === 'healthy' ? '采集正常' : (record.collection_health_text || '采集状态未知')}
                {record.root_cause_categories?.length ? ` · ${record.root_cause_categories.join('/')}` : ''}
              </Text>
              <Text
                ellipsis={{ tooltip: binding || record.target }}
                style={{ display: 'block', color: appearance.color, fontSize: 10, marginTop: 5, opacity: 0.8 }}
              >
                {binding || record.target}
              </Text>
            </div>
          </Popover>
        )
      })}
      {!records.length ? <Text type="secondary">当前筛选范围内暂无探测目标</Text> : null}
    </div>
  )

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 质量查询</div>
      <Card
        title={(
          <Space direction="vertical" size={0}>
            <Title level={4} style={{ margin: 0 }}>质量探测</Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              公网使用服务器 ICMP 探测，专线使用设备 NQA/SNMP；点击卡片后可选择查看、立即测试、编辑、MTR或删除。
            </Text>
          </Space>
        )}
        extra={(
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchItems()}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增目标</Button>
          </Space>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Select
              value={activeFilter}
              style={{ width: 120 }}
              onChange={setActiveFilter}
              options={[
                { label: '全部状态', value: 'all' },
                { label: '启用', value: 'active' },
                { label: '停用', value: 'inactive' },
              ]}
            />
            <Input.Search
              allowClear
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onSearch={() => fetchItems()}
              placeholder="搜索名称、目标、机房、运营商"
              style={{ width: 360 }}
            />
            <Segmented
              value={slaRange}
              onChange={(value) => setSlaRange(String(value))}
              options={[
                { label: '1小时', value: '-1h' },
                { label: '6小时', value: '-6h' },
                { label: '24小时', value: '-24h' },
                { label: '7天', value: '-7d' },
                { label: '30天', value: '-30d' },
              ]}
            />
          </Space>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
            {[
              { label: '当前SLA异常', value: qualityOverview.abnormal, color: '#cf1322' },
              { label: '采集状态异常', value: qualityOverview.collectionIssues, color: '#722ed1' },
              { label: '错误预算已耗尽', value: qualityOverview.budgetExceeded, color: '#d46b08' },
              { label: '已关联设备侧原因', value: qualityOverview.correlated, color: '#1677ff' },
            ].map((item) => (
              <div key={item.label} style={{ padding: '10px 12px', border: '1px solid #f0f0f0', borderRadius: 6, background: '#fff' }}>
                <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{item.label}</Text>
                <Text strong style={{ color: item.color, fontSize: 22 }}>{item.value}</Text>
                <Text type="secondary"> 条</Text>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 10 }}>
            {renderSlaGroupSummary('机房SLA（按总发包/收包加权）', datacenterSlaGroups)}
            {renderSlaGroupSummary('运营商SLA（按总发包/收包加权）', operatorSlaGroups)}
            <Card size="small" title="低SLA线路 Top 8" styles={{ body: { padding: 10 } }}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {lowestSlaItems.map((item, index) => {
                  const availability = getQualityAvailability(item)
                  const appearance = slaStyles[getSlaLevel(item)]
                  return (
                    <div key={item.id} style={{ display: 'grid', gridTemplateColumns: '24px minmax(0, 1fr) 80px', gap: 6 }}>
                      <Text type="secondary">{index + 1}</Text>
                      <Text ellipsis={{ tooltip: item.name }}>{item.name}</Text>
                      <Text style={{ color: appearance.border, textAlign: 'right', fontWeight: 600 }}>
                        {availability == null ? '-' : `${availability.toFixed(3)}%`}
                      </Text>
                    </div>
                  )
                })}
                {!lowestSlaItems.length ? <Text type="secondary">当前范围暂无有效样本</Text> : null}
              </Space>
            </Card>
            <Card size="small" title="较上一等长周期下降 Top 8" styles={{ body: { padding: 10 } }}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {decliningSlaItems.map((item, index) => {
                  const change = Number(item.sla_availability_change_percent || 0)
                  return (
                    <div key={item.id} style={{ display: 'grid', gridTemplateColumns: '24px minmax(0, 1fr) 92px', gap: 6 }}>
                      <Text type="secondary">{index + 1}</Text>
                      <Text ellipsis={{ tooltip: item.name }}>{item.name}</Text>
                      <Text style={{ color: change < 0 ? '#cf1322' : '#237804', textAlign: 'right', fontWeight: 600 }}>
                        {change > 0 ? '+' : ''}{change.toFixed(3)}%
                      </Text>
                    </div>
                  )
                })}
                {!decliningSlaItems.length ? <Text type="secondary">上一周期暂无可比较样本</Text> : null}
              </Space>
            </Card>
          </div>

          <Space size={14} wrap>
            <Text type="secondary">SLA分档：</Text>
            {[
              ['#4f9f50', '绿 ≥ 99.9%'],
              ['#ffd666', '黄 99.5%～99.9%'],
              ['#ff9c42', '橙 99%～99.5%'],
              ['#d9363e', '红 < 99%'],
              ['#d9d9d9', '灰 无有效样本'],
            ].map(([color, label]) => (
              <Space size={5} key={label}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: 'inline-block' }} />
                <Text style={{ fontSize: 12 }}>{label}</Text>
              </Space>
            ))}
            <Text type="secondary" style={{ fontSize: 12 }}>低SLA线路自动排在前面</Text>
          </Space>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(520px, 1fr))', gap: 12, alignItems: 'start' }}>
            <Card
              size="small"
              title={<Space><Tag color="blue">服务器 ICMP</Tag><Text strong>互联网质量</Text></Space>}
              extra={<Text type="secondary">{internetSlaItems.length} 条</Text>}
              styles={{ body: { padding: 12, maxHeight: 430, overflowY: 'auto' } }}
            >
              {renderSlaCards(internetSlaItems, 'internet')}
            </Card>
            <Card
              size="small"
              title={<Space><Tag color="purple">设备 NQA / SNMP</Tag><Text strong>专线质量</Text></Space>}
              extra={<Text type="secondary">{privateSlaItems.length} 条</Text>}
              styles={{ body: { padding: 12, maxHeight: 430, overflowY: 'auto' } }}
            >
              {renderSlaCards(privateSlaItems, 'private')}
            </Card>
          </div>

          {expandedTarget ? (
            <Card
              size="small"
              title={`${expandedTarget.name} / ${expandedTarget.probe_source === 'device_nqa_snmp' ? '专线 NQA' : '公网 ICMP'}`}
              extra={(
                <Space wrap>
                  {expandedTarget.probe_source === 'server_icmp' ? (
                    <>
                      <Button size="small" loading={mtrLoadingId === expandedTarget.id} onClick={() => handleMtr(expandedTarget)}>立即MTR</Button>
                      <Button size="small" onClick={() => openMtrObservation(expandedTarget)}>历史路径对比</Button>
                    </>
                  ) : (
                    <Tag color={expandedTarget.circuit_id || expandedTarget.probe_interface_name ? 'green' : 'orange'}>
                      {expandedTarget.circuit_id || expandedTarget.probe_interface_name ? '已关联专线接口指标' : '尚未关联专线接口'}
                    </Tag>
                  )}
                  <Button size="small" onClick={() => setExpandedRowKeys([])}>收起曲线</Button>
                </Space>
              )}
            >
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8, marginBottom: 10 }}>
                <div style={{ padding: '9px 10px', background: '#fafafa', borderRadius: 6 }}>
                  <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>采集健康</Text>
                  <Text strong style={{ color: expandedTarget.collection_health === 'healthy' ? '#237804' : '#722ed1' }}>
                    {expandedTarget.collection_health_text || '未知'}
                  </Text>
                  <Text type="secondary" style={{ display: 'block', fontSize: 11 }}>
                    完整率 {expandedTarget.sla_data_completeness_percent ?? '-'}% · 最近更新 {expandedTarget.collection_age_seconds ?? '-'}秒
                  </Text>
                </div>
                <div style={{ padding: '9px 10px', background: '#fafafa', borderRadius: 6 }}>
                  <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>本月错误预算估算（目标99.9%）</Text>
                  <Text strong style={{ color: Number(expandedTarget.sla_error_budget_used_percent || 0) >= 100 ? '#cf1322' : '#1677ff' }}>
                    已用 {expandedTarget.sla_error_budget_used_percent ?? '-'}%
                  </Text>
                  <Text type="secondary" style={{ display: 'block', fontSize: 11 }}>
                    按当前所选周期折算 · 允许 {expandedTarget.sla_error_budget_allowed_minutes ?? '-'}分钟 · 剩余 {expandedTarget.sla_error_budget_remaining_minutes ?? '-'}分钟
                  </Text>
                </div>
                <div style={{ padding: '9px 10px', background: '#fafafa', borderRadius: 6 }}>
                  <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>自动关联判断</Text>
                  <Text strong>{expandedTarget.root_cause_categories?.join(' / ') || '未发现同周期设备侧异常'}</Text>
                  <Text type="secondary" style={{ display: 'block', fontSize: 11 }}>
                    同周期相关事件 {expandedTarget.related_alerts?.length || 0} 条
                  </Text>
                </div>
                {expandedTarget.probe_source === 'server_icmp' ? (
                  <div style={{ padding: '9px 10px', background: '#fafafa', borderRadius: 6 }}>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>最近MTR路径事件</Text>
                    <Text strong>{expandedTarget.latest_mtr_event?.title || '暂无路径变化事件'}</Text>
                    <Text type="secondary" style={{ display: 'block', fontSize: 11 }}>
                      {formatTime(expandedTarget.latest_mtr_event?.created_at)}
                    </Text>
                  </div>
                ) : null}
              </div>
              {(expandedTarget.related_alerts || []).length ? (
                <div style={{ marginBottom: 10, padding: '8px 10px', border: '1px solid #f0f0f0', borderRadius: 6 }}>
                  <Text strong>合并质量事件：</Text>
                  {(expandedTarget.related_alerts || []).slice(0, 5).map((alert) => (
                    <Tag key={`${alert.alarm_id}-${alert.started_at}`} color={alert.status === 'firing' ? 'red' : 'default'}>
                      {alert.name} · {formatTime(alert.started_at)}
                    </Tag>
                  ))}
                </div>
              ) : null}
              <QualityChartPanel target={expandedTarget} initialRange={slaRange} />
            </Card>
          ) : null}
        </Space>
      </Card>

      <Modal
        title={editing ? '编辑质量探测目标' : '新增质量探测目标'}
        open={modalOpen}
        onOk={handleSave}
        confirmLoading={saving}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
        }}
        width={820}
        destroyOnClose
      >
        <Spin spinning={targetAlertSettingsLoading}>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="probe_source" label="探测数据源" rules={[{ required: true }]}>
            <Segmented
              options={[
                { label: '服务器 ICMP', value: 'server_icmp' },
                { label: '设备 NQA / SNMP', value: 'device_nqa_snmp' },
              ]}
              onChange={(value) => {
                form.setFieldsValue({ circuit_id: null, probe_interface_name: null })
                if (value === 'server_icmp') {
                  setNqaInstances([])
                  setNqaDeviceInterfaces([])
                  form.setFieldsValue({
                    device_id: undefined,
                    probe_interface_name: undefined,
                    nqa_instance_key: undefined,
                    nqa_admin_name: undefined,
                    nqa_operation_tag: undefined,
                    target: undefined,
                    interval_seconds: 60,
                    packet_count: 5,
                    timeout_ms: 1500,
                  })
                } else {
                  form.setFieldsValue({ target: undefined, interval_seconds: 3 })
                  void fetchNqaDevices()
                }
              }}
            />
          </Form.Item>

          {probeSource === 'device_nqa_snmp' ? (
            <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
              <Space size="middle" align="start" wrap>
                <Form.Item
                  name="device_id"
                  label="NQA采集设备"
                  rules={[{ required: true, message: '请选择配置了NQA的设备' }]}
                  style={{ width: 360, marginBottom: 8 }}
                >
                  <Select
                    showSearch
                    filterOption={false}
                    loading={nqaDevicesLoading}
                    placeholder="输入设备名称或管理IP搜索"
                    options={nqaDevices.map((device) => ({
                      label: `${device.name}（${device.ip_address}）`,
                      value: device.id,
                    }))}
                    onSearch={(value) => {
                      if (nqaDeviceSearchTimer.current) window.clearTimeout(nqaDeviceSearchTimer.current)
                      nqaDeviceSearchTimer.current = window.setTimeout(() => void fetchNqaDevices(value), 300)
                    }}
                    onChange={(deviceId) => {
                      setNqaInstances([])
                      form.setFieldsValue({
                        circuit_id: undefined,
                        probe_interface_name: undefined,
                        nqa_instance_key: undefined,
                        nqa_admin_name: undefined,
                        nqa_operation_tag: undefined,
                        target: undefined,
                      })
                      void fetchNqaInstances(deviceId)
                      void fetchNqaDeviceInterfaces(deviceId)
                    }}
                  />
                </Form.Item>
                <Form.Item
                  name="nqa_instance_key"
                  label="已识别的NQA实例"
                  rules={[{ required: true, message: '请选择设备上已识别的NQA实例' }]}
                  style={{ width: 330, marginBottom: 8 }}
                >
                  <Select
                    loading={nqaInstancesLoading}
                    disabled={!selectedNqaDeviceId}
                    placeholder="选择后自动带出目标地址"
                    notFoundContent={nqaInstancesLoading ? <Spin size="small" /> : '未识别到NQA实例'}
                    options={nqaInstances.map((instance) => {
                      const existing = items.find((item) => (
                        item.probe_source === 'device_nqa_snmp'
                        && item.device_id === selectedNqaDeviceId
                        && item.nqa_admin_name === instance.admin_name
                        && item.nqa_operation_tag === instance.operation_tag
                        && item.id !== editing?.id
                      ))
                      return {
                        value: nqaInstanceValue(instance.admin_name, instance.operation_tag),
                        disabled: Boolean(existing),
                        label: `${instance.admin_name}/${instance.operation_tag} → ${instance.target || '未知目标'}${instance.has_result ? `（${instance.avg_latency_ms ?? '-'} ms / 丢包${instance.packet_loss_percent ?? '-'}%）` : '（暂无结果）'}${existing ? ` · 已生成图：${existing.name}` : ''}`,
                      }
                    })}
                    onChange={(value) => {
                      const instance = nqaInstances.find((item) => nqaInstanceValue(item.admin_name, item.operation_tag) === value)
                      const device = nqaDevices.find((item) => item.id === form.getFieldValue('device_id'))
                      if (!instance) return
                      const targetAddress = String(instance.target || '').trim()
                      const privateCircuits = circuits.filter((item) => item.line_type === 'private_line')
                      const exactCircuit = privateCircuits.find((circuit) => (
                        [circuit.primary_device_id, circuit.secondary_device_id, circuit.aggregation_monitor_device_id].includes(device?.id)
                        && circuitAddressMatches(circuit, targetAddress)
                      ))
                      const exactCircuitBindings = exactCircuit ? getCircuitDeviceBindings(exactCircuit, device?.id) : []
                      form.setFieldsValue({
                        name: form.getFieldValue('name') || `${device?.name || '设备'} NQA`,
                        target: instance.target,
                        nqa_admin_name: instance.admin_name,
                        nqa_operation_tag: instance.operation_tag,
                        interval_seconds: instance.frequency_seconds || 3,
                        packet_count: instance.packet_count || 1,
                        timeout_ms: instance.timeout_ms || 1000,
                        circuit_id: exactCircuit?.id || null,
                        probe_interface_name: exactCircuitBindings[0]?.port || form.getFieldValue('probe_interface_name') || null,
                        datacenter_id: device?.datacenter_id || form.getFieldValue('datacenter_id'),
                        operator_name: exactCircuit?.operator_name || form.getFieldValue('operator_name'),
                        description: form.getFieldValue('description') || `设备NQA ${instance.admin_name}/${instance.operation_tag}${instance.source ? `，源地址 ${instance.source}` : ''}`,
                      })
                      if (!exactCircuit) {
                        message.info('没有找到目标地址完全匹配的专线，请在下方手动选择该采集设备对应的端口/专线')
                      }
                    }}
                  />
                </Form.Item>
                <Button
                  style={{ marginTop: 30 }}
                  icon={<ReloadOutlined />}
                  loading={nqaInstancesLoading}
                  disabled={!selectedNqaDeviceId}
                  onClick={() => void fetchNqaInstances(selectedNqaDeviceId, true)}
                >
                  重新读取
                </Button>
              </Space>
              <Text type="secondary">只列出设备已经配置并能通过SNMP识别的NQA任务，系统不会从服务器直接 Ping 该目标。</Text>
              <Form.Item name="nqa_admin_name" hidden><Input /></Form.Item>
              <Form.Item name="nqa_operation_tag" hidden><Input /></Form.Item>
            </Card>
          ) : null}

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item
              name="name"
              label="探测名称"
              rules={[{ required: true, message: '请输入探测名称' }]}
              style={{ width: 330 }}
            >
              <Input placeholder="例如：湖北宜昌-电信DNS" />
            </Form.Item>
            <Form.Item
              name="target"
              label="目标 IP / 域名"
              rules={[{ required: true, message: '请输入目标 IP 或域名' }]}
              style={{ width: 330 }}
            >
              <Input
                readOnly={probeSource === 'device_nqa_snmp'}
                placeholder={probeSource === 'device_nqa_snmp' ? '选择NQA实例后自动带出' : '例如：114.114.114.114 或 www.example.com'}
              />
            </Form.Item>
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="datacenter_id" label="机房" style={{ width: 220 }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="选择机房" options={datacenterOptions} />
            </Form.Item>
            <Form.Item name="operator_name" label="运营商" style={{ width: 180 }}>
              <Input placeholder="电信/联通/移动/BGP" />
            </Form.Item>
            <Form.Item name="is_active" label="是否启用" valuePropName="checked" style={{ width: 120 }}>
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          </Space>

          <Form.Item
            name="circuit_id"
            label={probeSource === 'device_nqa_snmp' ? '手动选择端口 / 关联专线' : '关联公网线路'}
            extra={probeSource === 'device_nqa_snmp'
              ? '采集设备已确定后，只列出这台设备在专线管理中记录过的端口/专线。NQA目标地址能精确匹配时会自动带出，未记录时请手动选择，避免关联到别的专线。'
              : '关联后会在质量曲线下同步展示该公网出口的流量，并辅助判断是否存在带宽拥塞。'}
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              disabled={probeSource === 'device_nqa_snmp' && !selectedNqaDeviceId}
              placeholder={probeSource === 'device_nqa_snmp' ? '选择当前采集设备上的端口/专线' : '选择公网管理中已启用的线路'}
              notFoundContent={probeSource === 'device_nqa_snmp' && selectedNqaDeviceId ? '这台采集设备在专线管理中还没有记录端口' : undefined}
              options={probeSource === 'device_nqa_snmp' ? selectedDeviceCircuitOptions : circuitOptions}
              onChange={(value) => {
                if (!value) {
                  form.setFieldsValue({ circuit_id: null })
                  return
                }
                const circuit = circuits.find((item) => item.id === value)
                if (circuit) {
                  const bindings = getCircuitDeviceBindings(circuit, selectedNqaDeviceId)
                  form.setFieldsValue({
                    circuit_id: value,
                    probe_interface_name: bindings[0]?.port || form.getFieldValue('probe_interface_name'),
                    datacenter_id: circuit.datacenter_id || form.getFieldValue('datacenter_id'),
                    operator_name: circuit.operator_name || form.getFieldValue('operator_name'),
                  })
                }
              }}
              onClear={() => form.setFieldsValue({ circuit_id: null })}
            />
          </Form.Item>

          {probeSource === 'device_nqa_snmp' ? (
            <Form.Item
              name="probe_interface_name"
              label="采集设备接口"
              extra="没有录入专线、或不想关联专线时，可以只选择当前NQA采集设备上的接口；清空专线后不会再强制绑定别的专线。"
            >
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                disabled={!selectedNqaDeviceId}
                loading={nqaDeviceInterfacesLoading}
                placeholder="选择当前采集设备接口，可不关联专线"
                notFoundContent={selectedNqaDeviceId ? '暂无接口缓存，请先刷新设备连接/接口缓存' : '请先选择NQA采集设备'}
                options={selectedDeviceInterfaceOptions}
                onClear={() => form.setFieldsValue({ probe_interface_name: null })}
              />
            </Form.Item>
          ) : null}

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="interval_seconds" label={probeSource === 'device_nqa_snmp' ? '系统读取间隔(s)' : '采样间隔(s)'} rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={probeSource === 'server_icmp' ? 30 : 1} max={3600} style={{ width: '100%' }} />
            </Form.Item>
            {probeSource === 'server_icmp' ? (
              <>
                <Form.Item name="packet_count" label="每次包数" rules={[{ required: true }]} style={{ width: 150 }}>
                  <InputNumber min={5} max={20} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item name="timeout_ms" label="超时(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
                  <InputNumber min={1500} max={10000} style={{ width: '100%' }} />
                </Form.Item>
              </>
            ) : null}
            {probeSource === 'device_nqa_snmp' ? (
              <>
                <Form.Item name="packet_count" hidden><InputNumber /></Form.Item>
                <Form.Item name="timeout_ms" hidden><InputNumber /></Form.Item>
              </>
            ) : null}
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="latency_threshold_ms" label="延迟阈值(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="loss_threshold_percent" label="丢包阈值(%)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          {probeSource === 'server_icmp' ? (
            <Card size="small" title="公网路径观察（周期MTR）" style={{ background: '#f8fafc', marginBottom: 16 }}>
              <Space size="middle" align="start" wrap>
                <Form.Item name="mtr_enabled" label="开启路径观察" valuePropName="checked" style={{ width: 150 }}>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
                <Form.Item name="mtr_interval_seconds" label="观察间隔(秒)" rules={[{ required: true }]} style={{ width: 170 }}>
                  <InputNumber min={60} max={86400} precision={0} style={{ width: '100%' }} />
                </Form.Item>
              </Space>
              <Text type="secondary">建议公网目标先使用 300 秒；系统会记录路径变化和目标延迟明显升高事件。</Text>
            </Card>
          ) : null}

          <Form.Item name="description" label="备注">
            <Input.TextArea rows={3} placeholder="可填写用途、运营商、线路背景等信息" />
          </Form.Item>

          <Card size="small" title="告警设置" style={{ background: '#f8fafc' }}>
            <Space size="middle" align="start" wrap>
              <Form.Item name="alert_enabled" label="启用该目标告警" valuePropName="checked" style={{ width: 140 }}>
                <Switch checkedChildren="启用" unCheckedChildren="停用" />
              </Form.Item>
              <Form.Item name="alert_consecutive_samples" label="连续丢包周期" rules={[{ required: true }]} style={{ width: 170 }}>
                <InputNumber min={1} max={60} precision={0} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="alert_loss_threshold_percent" label="5分钟丢包率阈值(%)" rules={[{ required: true }]} style={{ width: 210 }}>
                <InputNumber min={0.01} max={100} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Form.Item
              name="alert_webhook_url"
              label="该目标机器人 Webhook"
              dependencies={['alert_enabled']}
              rules={[
                ({ getFieldValue }) => ({
                  required: Boolean(getFieldValue('alert_enabled')),
                  message: '启用告警时必须填写机器人 Webhook',
                }),
                { type: 'url', message: 'Webhook 地址格式不正确' },
              ]}
            >
              <Input placeholder="支持企业微信、飞书、钉钉和通用 Webhook" />
            </Form.Item>
            <Form.Item name="alert_mention_users_text" label="该目标 @负责人" extra="多个对象用逗号分隔，企业微信支持手机号或 @all。">
              <Input placeholder="例如：13800138000, 13900139000" />
            </Form.Item>
            <Button loading={targetAlertTesting} onClick={testTargetAlertRobot}>发送测试消息</Button>
          </Card>
        </Form>
        </Spin>
      </Modal>
      <Modal
        title={`路径观察 - ${mtrObservationTarget?.name || '公网目标'}`}
        open={mtrObservationOpen}
        width={1120}
        footer={<Button onClick={() => setMtrObservationOpen(false)}>关闭</Button>}
        onCancel={() => setMtrObservationOpen(false)}
      >
        <Spin spinning={mtrObservationLoading}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Card size="small">
              <Space size="large" wrap>
                <span>目标：<Text strong>{mtrObservationTarget?.target || '-'}</Text></span>
                <span>状态：{mtrObservationTarget?.mtr_enabled ? <Tag color="green">已开启</Tag> : <Tag>未开启</Tag>}</span>
                <span>间隔：{mtrObservationTarget?.mtr_interval_seconds || 300} 秒</span>
                <span>最近观察：{formatTime(mtrObservationTarget?.last_mtr_at)}</span>
                <span>最后一跳延迟：{mtrObservationTarget?.last_mtr_final_latency_ms ?? '-'} ms</span>
              </Space>
            </Card>

            <Card size="small" title="当前路径">
              {mtrLatestSnapshot ? (
                <>
                  <Space size="large" wrap style={{ marginBottom: 10 }}>
                    <span>采样时间：{formatTime(mtrLatestSnapshot.created_at)}</span>
                    <span>跳数：{mtrLatestSnapshot.hop_count || 0}</span>
                    <span>末跳：{mtrLatestSnapshot.final_hop_ip || '-'}</span>
                    <span>末跳延迟：{mtrLatestSnapshot.final_avg_latency_ms ?? '-'} ms</span>
                    <span>末跳丢包：{mtrLatestSnapshot.final_loss_percent ?? '-'}%</span>
                    <span>Path Hash：{mtrLatestSnapshot.path_hash ? <Text code>{mtrLatestSnapshot.path_hash.slice(0, 12)}</Text> : '-'}</span>
                  </Space>
                  <Text type="secondary" style={{ display: 'block', marginBottom: 10 }}>
                    说明：中间跳的 Loss% 多数是运营商/路由器对 ICMP TTL 超时报文限速，不等于真实业务丢包；判断链路质量优先看末跳丢包、末跳延迟和路径/AS变化。
                  </Text>
                  {renderMtrHopTable(mtrLatestSnapshot.hops)}
                </>
              ) : <Text type="secondary">暂无路径快照。开启路径观察后，后台会按间隔自动采集。</Text>}
            </Card>

            <Card size="small" title="路径变化/延迟升高事件">
              <List
                size="small"
                dataSource={mtrEvents}
                locale={{ emptyText: '暂无事件' }}
                renderItem={(event) => (
                  <List.Item>
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag color={event.event_type === 'path_changed' ? 'orange' : 'red'}>{event.title}</Tag>
                        <Text type="secondary">{formatTime(event.created_at)}</Text>
                        {event.latency_delta_ms !== null && event.latency_delta_ms !== undefined ? (
                          <Text>延迟变化：{event.previous_final_latency_ms ?? '-'} → {event.current_final_latency_ms ?? '-'} ms（{event.latency_delta_ms.toFixed(1)} ms）</Text>
                        ) : null}
                      </Space>
                      {event.event_type === 'path_changed' ? (
                        <Text type="secondary">路径：{event.previous_path_hash?.slice(0, 10) || '-'} → {event.current_path_hash?.slice(0, 10) || '-'}</Text>
                      ) : null}
                    </Space>
                  </List.Item>
                )}
              />
            </Card>

            <Card size="small" title="历史采样（展开可查看当时完整路径）">
              <Table<QualityMtrSnapshot>
                size="small"
                dataSource={mtrSnapshots}
                rowKey="id"
                locale={{ emptyText: '暂无快照' }}
                pagination={{ pageSize: 8, showSizeChanger: false }}
                expandable={{
                  expandedRowRender: (snapshot) => renderMtrHopTable(snapshot.hops),
                  rowExpandable: (snapshot) => Boolean(snapshot.hops?.length),
                }}
                columns={[
                  { title: '采样时间', dataIndex: 'created_at', key: 'created_at', width: 170, render: (value?: string | null) => formatTime(value) },
                  { title: '状态', dataIndex: 'success', key: 'success', width: 90, render: (success: boolean) => <Tag color={success ? 'green' : 'red'}>{success ? '成功' : '失败'}</Tag> },
                  { title: '跳数', dataIndex: 'hop_count', key: 'hop_count', width: 80, render: (value?: number | null) => value || 0 },
                  { title: '末跳', dataIndex: 'final_hop_ip', key: 'final_hop_ip', render: (value?: string | null) => value || '-' },
                  { title: '末跳延迟', dataIndex: 'final_avg_latency_ms', key: 'final_avg_latency_ms', width: 110, render: (value?: number | null) => value == null ? '-' : `${value} ms` },
                  { title: '末跳丢包', dataIndex: 'final_loss_percent', key: 'final_loss_percent', width: 110, render: (value?: number | null) => value == null ? '-' : `${value}%` },
                  { title: 'Path Hash', dataIndex: 'path_hash', key: 'path_hash', width: 150, render: (value?: string | null) => value ? <Text code>{value.slice(0, 10)}</Text> : '-' },
                ]}
              />
            </Card>
          </Space>
        </Spin>
      </Modal>
      <Modal
        title={`MTR - ${mtrTitle || '探测目标'}`}
        open={mtrOpen}
        width={900}
        footer={<Button onClick={() => setMtrOpen(false)}>关闭</Button>}
        onCancel={() => setMtrOpen(false)}
      >
        {mtrCommand ? <Text type="secondary">{mtrCommand}</Text> : null}
        <pre style={{
          marginTop: 12,
          padding: 12,
          minHeight: 260,
          maxHeight: 520,
          overflow: 'auto',
          borderRadius: 8,
          background: '#0f172a',
          color: '#d1e7ff',
          whiteSpace: 'pre-wrap',
        }}
        >
          {mtrOutput || (mtrLoadingId ? 'MTR 执行中...' : '暂无输出')}
        </pre>
      </Modal>
    </Space>
  )
}

export default QualityQuery
